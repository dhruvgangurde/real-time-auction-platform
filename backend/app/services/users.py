"""Profile, bid history, suspension and operator role changes (FR-6, FR-9, FR-10, FR-78,
BR-12, D6)."""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.errors import AppError
from app.models.user import ROLES
from app.repositories import AuditLogRepository, BidRepository, SessionRepository, UserRepository
from app.services.auth import Profile, RevokeNotifier, normalise_email, profile_of
from app.services.denylist import SUSPENDED_ACTION, Denylist, sid_key, user_key
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

    async def change_role(
        self,
        user: str | UUID,
        role: str,
        *,
        grant: bool,
        actor_id: UUID | None = None,
        reason: str = "cli",
    ) -> bool:
        """Grant or revoke a role, from the operator CLI by email (D6, actor NULL, reason 'cli')
        or from the admin API by user id (actor and reason, FR-75). The change and its audit row
        commit together (FR-77, FR-78). Returns False when nothing changed.

        Roles travel in the access token. A grant takes effect at the user's next login. A
        revocation takes effect at once: every session of the user is revoked in the same
        transaction and denylisted, as on suspension (FR-6), so no token still carrying the role
        is accepted and the user must log in again."""
        if role not in ROLES:
            raise AppError("VALIDATION_ERROR", f"unknown role {role!r}", 422)
        now = self._clock.now()
        sids: list[UUID] = []
        async with self._sessions() as session, session.begin():
            users = UserRepository(session)
            if isinstance(user, UUID):
                found = await users.get(user)
            else:
                found = await users.get_by_email(normalise_email(user))
            if found is None:
                raise AppError("NOT_FOUND", "user not found", 404)
            user_id = found.id
            before = list(await users.roles(user_id))
            changed = await (users.add_role if grant else users.remove_role)(user_id, role)
            if changed:
                after: dict[str, object] = {"roles": list(await users.roles(user_id))}
                if not grant:
                    sids = await SessionRepository(session).revoke_all_for_user(user_id, now)
                    after |= {"role": role, "sessions_revoked": len(sids)}
                await AuditLogRepository(session).add(
                    actor_id=actor_id,
                    entity_type="user",
                    entity_id=str(user_id),
                    action="role_granted" if grant else "role_revoked",
                    before={"roles": before},
                    after=after,
                    reason=reason,
                )
        if changed and not grant:
            await self._deny(user_id, [sid_key(s) for s in sids])
            log.info(
                "role revoked; sessions revoked",
                extra={"fields": {"user_id": str(user_id), "role": role, "sessions": len(sids)}},
            )
        return changed

    async def _deny(self, user_id: UUID, keys: list[str]) -> None:
        """After the Postgres commit: denylist the keys (retry, then the worker, then
        sync_denylist), then close the user's sockets on every instance (FR-6)."""
        if keys:
            await self._denylist.revoke(keys)
        if self._notify_revoked is not None:
            await self._notify_revoked(user_id, None)

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
        # the sessions too, so a later reinstatement never revives an old access token (FR-76)
        await self._deny(user_id, [user_key(user_id), *(sid_key(s) for s in sids)])
        log.info(
            "user suspended",
            extra={"fields": {"user_id": str(user_id), "actor_id": str(admin_id)}},
        )

    async def reinstate_user(self, admin_id: UUID, user_id: UUID, reason: str) -> None:
        """Suspended -> active with its audit row (FR-76, BR-12), then the denylist entry goes
        (sync_denylist also removes it for anyone no longer suspended). Sessions revoked by the
        suspension stay revoked: the user logs in again."""
        async with self._sessions() as session, session.begin():
            users = UserRepository(session)
            user = await users.get(user_id)
            if user is None:
                raise AppError("NOT_FOUND", "user not found", 404)
            if user.status != "suspended":
                raise AppError("NOT_SUSPENDED", "the account is not suspended", 409)
            await users.set_status(user_id, "active")
            await AuditLogRepository(session).add(
                actor_id=admin_id,
                entity_type="user",
                entity_id=str(user_id),
                action="user_reinstated",
                before={"status": "suspended"},
                after={"status": "active"},
                reason=reason,
            )
        await self._denylist.clear_user(user_id)
        log.info(
            "user reinstated",
            extra={"fields": {"user_id": str(user_id), "actor_id": str(admin_id)}},
        )
