"""Profile, bid history and suspension (FR-6, FR-9, FR-10, FR-78, BR-12)."""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.errors import AppError
from app.repositories import AuditLogRepository, BidRepository, SessionRepository, UserRepository
from app.services.auth import Profile, RevokeNotifier, profile_of
from app.services.denylist import SUSPENDED_ACTION, Denylist, user_key
from app.services.live_state import from_ms, to_ms

log = logging.getLogger("users")


@dataclass(frozen=True)
class BidHistoryItem:
    id: int
    auction_id: UUID
    amount: int
    sequence_no: int
    bid_type: str
    created_at: datetime


@dataclass(frozen=True)
class BidHistoryPage:
    items: list[BidHistoryItem]
    next_cursor: str | None


def _cursor(created_at: datetime, bid_id: int) -> str:
    return f"{to_ms(created_at)}:{bid_id}"


def parse_cursor(cursor: str) -> tuple[datetime, int]:
    ms, bid_id = cursor.split(":")
    return from_ms(int(ms)), int(bid_id)


class UserService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Clock,
        denylist: Denylist,
        notify_revoked: RevokeNotifier | None = None,
    ) -> None:
        self._notify_revoked = notify_revoked
        self._sessions = sessions
        self._clock = clock
        self._denylist = denylist

    async def get_profile(self, user_id: UUID) -> Profile:
        async with self._sessions() as session:
            users = UserRepository(session)
            user = await users.get(user_id)
            if user is None:
                raise AppError("NOT_FOUND", "user not found", 404)
            return profile_of(user, await users.roles(user_id))

    async def update_profile(
        self, user_id: UUID, full_name: str | None, avatar_url: str | None, clear_avatar: bool
    ) -> Profile:
        async with self._sessions() as session, session.begin():
            users = UserRepository(session)
            if await users.get(user_id) is None:
                raise AppError("NOT_FOUND", "user not found", 404)
            await users.update_profile(user_id, full_name, avatar_url, clear_avatar)
        return await self.get_profile(user_id)

    async def list_bids(self, user_id: UUID, limit: int, cursor: str | None) -> BidHistoryPage:
        """Own bids across auctions, most recent first (FR-10)."""
        before = parse_cursor(cursor) if cursor else None
        async with self._sessions() as session:
            rows = await BidRepository(session).list_for_bidder(user_id, limit + 1, before)
        page = rows[:limit]
        items = [
            BidHistoryItem(b.id, b.auction_id, b.amount, b.sequence_no, b.bid_type, b.created_at)
            for b in page
        ]
        next_cursor = _cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
        return BidHistoryPage(items, next_cursor)

    async def suspend_user(self, admin_id: UUID, user_id: UUID, reason: str) -> None:
        """Suspend, revoke every session and audit it in one transaction (FR-6, FR-78, BR-12)."""
        if not reason.strip():
            raise AppError("VALIDATION_ERROR", "a reason is required", 422)
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            users = UserRepository(session)
            user = await users.get(user_id)
            if user is None:
                raise AppError("NOT_FOUND", "user not found", 404)
            before = user.status
            await users.set_status(user_id, "suspended")
            sids = await SessionRepository(session).revoke_all_for_user(user_id, now)
            await AuditLogRepository(session).add(
                actor_id=admin_id,
                entity_type="user",
                entity_id=str(user_id),
                action=SUSPENDED_ACTION,
                before={"status": before},
                after={"status": "suspended", "sessions_revoked": len(sids)},
                reason=reason.strip(),
            )
        await self._denylist.revoke([user_key(user_id)])
        if self._notify_revoked is not None:
            await self._notify_revoked(user_id, None)  # close live sockets on every instance
        log.info(
            "user suspended",
            extra={"fields": {"user_id": str(user_id), "actor_id": str(admin_id)}},
        )
