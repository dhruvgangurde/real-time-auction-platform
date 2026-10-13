"""Administration: force-close and cancel of auctions, user search, audit query
(FR-63, FR-75, FR-76, FR-80, BR-9, BR-12, NFR-14). Suspension, reinstatement and roles live in
UserService. Every state change carries the administrator and a non-empty reason in its audit
row, written in the same transaction (FR-77, FR-78).

Force-close and a cancel of a live auction flip the Redis status first, so no bid is accepted
after it (the same serialisation point as arbitration), then drain the outbox, then commit
Postgres. A flip left behind by a failed attempt counts as done, so the call can be repeated.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import lua
from app.core.errors import AppError
from app.models import Auction
from app.repositories import (
    AuctionRepository,
    AuditLogRepository,
    ProxyBidRepository,
    SettlementRepository,
    UserRepository,
)
from app.services.auth import CurrentUser
from app.services.closing import TERMINAL, Closer, Outcome, settlement_dict
from app.services.ledger import persist_outbox
from app.services.live_state import (
    AuctionNotLive,
    LiveStateUnavailable,
    from_ms,
    init_live_state,
    outbox_key,
    proxies_key,
    state_key,
    to_ms,
)

log = logging.getLogger("admin")

OPEN = frozenset({"draft", "scheduled"})


def _already_closed(status: str) -> AppError:
    return AppError("ALREADY_CLOSED", f"the auction is {status}", 409)


class AdminService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        closer: Closer,
        enqueue_close: Callable[[UUID], None],
    ) -> None:
        self._sessions, self._redis, self._closer = sessions, redis, closer
        self._enqueue_close = enqueue_close
        self._set_status = redis.register_script(lua.source("set_status"))
        self._cleanup = redis.register_script(lua.source("cleanup_state"))

    async def _load(self, auction_id: UUID) -> Auction:
        async with self._sessions() as session:
            auction = await AuctionRepository(session).get(auction_id)
        if auction is None:
            raise AppError("NOT_FOUND", "auction not found", 404)
        return auction

    async def _flip(self, auction_id: UUID, to_status: str, mode: str) -> int:
        """Redis live -> to_status; returns the Redis end_time_ms after it. A status already at
        to_status is a flip left by an earlier attempt: continue."""
        keys, args = [state_key(auction_id)], ["live", to_status, mode]
        try:
            result = str(await self._set_status(keys=keys, args=args))
            if result == "MISSING":  # lost state: rebuild from Postgres first
                await init_live_state(auction_id, self._redis, self._sessions)
                result = str(await self._set_status(keys=keys, args=args))
            if result in ("OK", f"WRONG_STATUS:{to_status}"):
                return int(await self._redis.hget(state_key(auction_id), "end_time_ms"))
        except AuctionNotLive as exc:
            raise AppError("ALREADY_CLOSED", "the auction is no longer live", 409) from exc
        except (RedisError, LiveStateUnavailable) as exc:
            raise AppError("SERVICE_UNAVAILABLE", "live state unavailable; retry", 503) from exc
        if result.startswith("WRONG_STATUS:") and result != "WRONG_STATUS:rebuilding":
            raise _already_closed(result.split(":", 1)[1])  # ended, or another action is underway
        raise AppError("SERVICE_UNAVAILABLE", "live state is being rebuilt; retry", 503)

    async def _drain(self, auction_id: UUID) -> None:
        """Every bid accepted before the flip reaches the ledger first (FR-55, NFR-15)."""
        try:
            await persist_outbox(auction_id, self._redis, self._sessions)
        except (RedisError, SQLAlchemyError, OSError) as exc:
            raise AppError(
                "SERVICE_UNAVAILABLE", "accepted bids could not be persisted yet; retry", 503
            ) from exc

    # ---------------------------------------------------------------- auctions

    async def force_close(
        self, admin: CurrentUser, auction_id: UUID, reason: str
    ) -> dict[str, Any]:
        """FR-63: end a live auction now and settle it through the same path as the scheduler,
        so no second settlement can arise. A closing auction just runs that path."""
        status = (await self._load(auction_id)).status
        if status in TERMINAL:
            raise _already_closed(status)
        if status in OPEN:
            raise AppError("AUCTION_NOT_LIVE", "only a live auction can be force-closed", 409)
        forced_ms = await self._flip(auction_id, "closing", "end_now") if status == "live" else None
        await self._drain(auction_id)
        async with self._sessions() as session, session.begin():
            auctions = AuctionRepository(session)
            auction = await auctions.get_for_update(auction_id)
            assert auction is not None
            if auction.status in TERMINAL:
                raise _already_closed(auction.status)
            before = {"status": auction.status, "end_time_ms": to_ms(auction.end_time)}
            if auction.status == "live":
                await auctions.transition(auction_id, "live", "closing")
            if forced_ms is not None:  # Redis ended it at this instant (end_now)
                await auctions.set_end_time(auction_id, from_ms(forced_ms))
            await session.refresh(auction)
            await AuditLogRepository(session).add(
                entity_type="auction",
                entity_id=str(auction_id),
                action="auction_force_closed",
                actor_id=admin.id,
                before=before,
                after={"status": auction.status, "end_time_ms": to_ms(auction.end_time)},
                reason=reason,
            )
        outcome = await self._closer.close(auction_id)
        if outcome in (Outcome.DEFERRED, Outcome.LOCKED):
            self._enqueue_close(auction_id)  # finished by the close task; the sweep backs it
        log.info(
            "auction force-closed",
            extra={
                "fields": {
                    "auction_id": str(auction_id),
                    "actor_id": str(admin.id),
                    "outcome": outcome.value,
                }
            },
        )
        async with self._sessions() as session:
            auction = await AuctionRepository(session).get(auction_id)
            settlement = await SettlementRepository(session).for_auction(auction_id)
            winner = None
            if settlement is not None and settlement.winner_id is not None:
                winner = await UserRepository(session).get(settlement.winner_id)
        assert auction is not None
        return {
            "auction_id": str(auction_id),
            "status": auction.status,
            "outcome": outcome.value,
            "settlement": settlement_dict(settlement, winner) if settlement else None,
        }

    async def cancel(self, admin: CurrentUser, auction_id: UUID, reason: str) -> dict[str, Any]:
        """BR-9: draft, scheduled or live -> cancelled with a reason; no settlement. A live
        auction is flipped in Redis first, its outbox drained and its maxima deactivated
        (FR-51); auction_closed tells the room."""
        status = (await self._load(auction_id)).status
        if status == "live":
            await self._flip(auction_id, "cancelled", "0")
            await self._drain(auction_id)
        elif status not in OPEN:
            raise _already_closed(status)
        async with self._sessions() as session, session.begin():
            moved = await AuctionRepository(session).transition(auction_id, status, "cancelled")
            if moved:
                proxies = await ProxyBidRepository(session).deactivate_all(auction_id)
                await AuditLogRepository(session).add(
                    entity_type="auction",
                    entity_id=str(auction_id),
                    action="status_changed",
                    actor_id=admin.id,
                    before={"status": status},
                    after={"status": "cancelled", "proxies_deactivated": proxies},
                    reason=reason,
                )
        if not moved:  # it went live, or another request changed it meanwhile: decide again
            return await self.cancel(admin, auction_id, reason)
        log.info(
            "auction cancelled by an administrator",
            extra={"fields": {"auction_id": str(auction_id), "actor_id": str(admin.id)}},
        )
        if status == "live":
            await self._after_cancel(auction_id)
        return {"auction_id": str(auction_id), "status": "cancelled"}

    async def _after_cancel(self, auction_id: UUID) -> None:
        auction = await self._load(auction_id)  # the committed row: cancelled
        keys = [state_key(auction_id), outbox_key(auction_id), proxies_key(auction_id)]
        await self._closer.broadcast_closed(auction, final_price=None)  # once per marker (FR-62)
        try:
            await self._cleanup(keys=keys)  # Redis says cancelled, so nothing is kept
        except RedisError as exc:
            log.warning(  # reconciliation removes it later (remove_stale_state)
                "live state of a cancelled auction not removed",
                extra={"fields": {"auction_id": str(auction_id), "error": repr(exc)}},
            )

    # ---------------------------------------------------------------- users (FR-76)

    async def list_users(
        self, query: str | None, role: str | None, status: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        async with self._sessions() as session:
            users = UserRepository(session)
            rows = await users.search(
                query=query,
                role=role,
                status=status,
                limit=page_size + 1,
                offset=(page - 1) * page_size,
            )
            roles = await users.roles_for([u.id for u in rows[:page_size]])
        return {
            "items": [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "full_name": u.full_name,
                    "status": u.status,
                    "roles": roles[u.id],
                    "created_at_ms": to_ms(u.created_at),
                }
                for u in rows[:page_size]
            ],
            "page": page,
            "page_size": page_size,
            "has_more": len(rows) > page_size,
        }

    # ---------------------------------------------------------------- audit (FR-80)

    async def audit(
        self,
        *,
        actor_id: UUID | None,
        entity_type: str | None,
        entity_id: str | None,
        since: datetime | None,
        until: datetime | None,
        before_id: int | None,
        limit: int,
    ) -> dict[str, Any]:
        async with self._sessions() as session:
            rows = await AuditLogRepository(session).query(
                actor_id=actor_id,
                entity_type=entity_type,
                entity_id=entity_id,
                since=since,
                until=until,
                before_id=before_id,
                limit=limit + 1,
            )
        page = rows[:limit]
        return {
            "items": [
                {
                    "id": r.id,
                    "actor_id": str(r.actor_id) if r.actor_id else None,
                    "entity_type": r.entity_type,
                    "entity_id": r.entity_id,
                    "action": r.action,
                    "before": r.before,
                    "after": r.after,
                    "reason": r.reason,
                    "created_at_ms": to_ms(r.created_at),
                }
                for r in page
            ],
            "next_before_id": page[-1].id if len(rows) > limit else None,
        }
