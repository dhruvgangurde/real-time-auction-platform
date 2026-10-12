"""Auction lifecycle scheduler (FR-15, FR-16, D2, D9).

Beat runs it every scheduler_interval_s (5 s). Every step is idempotent and safe with
concurrent runs, so a crash between steps is repaired by the next run:

1. Activate: scheduled auctions whose start time has passed are claimed with
   FOR UPDATE SKIP LOCKED and moved to live by a conditional UPDATE with its audit row, in one
   transaction (FR-16, FR-77, FR-78). Only after the commit is the live state installed: a stale
   non-live hash is removed (cleanup_state.lua) and init_live_state builds the state from
   Postgres (FR-15). A bid arriving in between finds no state and is rebuilt lazily by the bid
   path, which checks the Postgres status first, so Redis never says live before Postgres does.
2. Repair: live auctions without Redis state get it built (a crash after step 1's commit).
3. Close: live auctions whose Postgres end_time has passed are candidates (Postgres lags Redis,
   never leads it). set_status.lua flips the Redis status live -> closing only once Redis TIME
   has reached end_time_ms, atomically with bid arbitration (D9, FR-58), so an extended auction
   stays live. Then a conditional UPDATE live -> closing with its audit row. A Redis closing /
   Postgres live pair (a crash between the two) is completed by the next run.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import lua
from app.core.config import settings
from app.repositories import AuctionRepository, AuditLogRepository
from app.services.live_state import (
    AuctionNotLive,
    LiveStateUnavailable,
    init_live_state,
    outbox_key,
    proxies_key,
    state_key,
)

log = logging.getLogger("scheduler")

BATCH = 200  # auctions per step per run; the rest wait for the next run
OnClosing = Callable[[UUID], Awaitable[None]]


async def log_closing(auction_id: UUID) -> None:
    """Phase 8 replaces this with the hand-off to settlement."""
    log.info("auction closing", extra={"fields": {"auction_id": str(auction_id)}})


@dataclass
class RunReport:
    activated: list[UUID] = field(default_factory=list)
    repaired: list[UUID] = field(default_factory=list)
    closed: list[UUID] = field(default_factory=list)


class Scheduler:
    def __init__(
        self,
        redis: Redis,
        sessions: async_sessionmaker[AsyncSession],
        on_closing: OnClosing = log_closing,
    ) -> None:
        self._redis, self._sessions, self._on_closing = redis, sessions, on_closing
        self._cleanup = redis.register_script(lua.source("cleanup_state"))
        self._set_status = redis.register_script(lua.source("set_status"))

    async def run(self) -> RunReport:
        report = RunReport()
        report.activated = await self.activate_due()
        report.repaired = await self.repair_missing_state()
        report.closed = await self.close_ended()
        return report

    # ---------------------------------------------------------------- 1. activation

    async def activate_due(self) -> list[UUID]:
        late = timedelta(seconds=2 * settings.scheduler_interval_s)
        activated: list[UUID] = []
        async with self._sessions() as session, session.begin():
            auctions, audit = AuctionRepository(session), AuditLogRepository(session)
            now = await auctions.db_now()
            for auction_id, start_time in await auctions.lock_due_to_start(BATCH):
                if not await auctions.transition(auction_id, "scheduled", "live"):
                    continue
                await audit.add(
                    entity_type="auction",
                    entity_id=str(auction_id),
                    action="status_changed",
                    before={"status": "scheduled"},
                    after={"status": "live"},
                )
                activated.append(auction_id)
                if now - start_time > late:  # e.g. after a service interruption (FR-16)
                    log.warning(
                        "auction activated late",
                        extra={
                            "fields": {
                                "auction_id": str(auction_id),
                                "late_s": round((now - start_time).total_seconds(), 1),
                            }
                        },
                    )
        for auction_id in activated:  # Postgres first, then Redis (D2)
            await self._install(auction_id)
        return activated

    async def _install(self, auction_id: UUID) -> bool:
        """Live state for an auction Postgres already calls live."""
        keys = [state_key(auction_id), outbox_key(auction_id), proxies_key(auction_id)]
        try:
            await self._cleanup(keys=keys)  # a stale non-live hash; live state is kept
            await init_live_state(auction_id, self._redis, self._sessions)
        except (LiveStateUnavailable, AuctionNotLive, RedisError) as exc:
            log.warning(
                "live state not installed; the repair pass retries",
                extra={"fields": {"auction_id": str(auction_id), "error": repr(exc)}},
            )
            return False
        return True

    # ---------------------------------------------------------------- 2. repair

    async def repair_missing_state(self) -> list[UUID]:
        async with self._sessions() as session:
            live = await AuctionRepository(session).list_live_ids()
        if not live:
            return []
        async with self._redis.pipeline(transaction=False) as pipe:
            for auction_id in live:
                pipe.exists(state_key(auction_id))
            present = await pipe.execute()
        missing = [a for a, found in zip(live, present, strict=True) if not found]
        return [a for a in missing if await self._install(a)]

    # ---------------------------------------------------------------- 3. closing

    async def close_ended(self) -> list[UUID]:
        async with self._sessions() as session:
            candidates = await AuctionRepository(session).live_ended_ids(BATCH)
        closed: list[UUID] = []
        for auction_id in candidates:
            outcome = await self._flip_to_closing(auction_id)
            if outcome in ("OK", "WRONG_STATUS:closing"):  # the latter: crash repair
                if await self._commit_closing(auction_id):
                    closed.append(auction_id)
                    await self._on_closing(auction_id)
            elif outcome == "NOT_ENDED":
                # a late bid extended it past the Postgres end_time (FR-58); repeats every run
                log.debug(
                    "auction stays live after extension",
                    extra={"fields": {"auction_id": str(auction_id)}},
                )
        return closed

    async def _flip_to_closing(self, auction_id: UUID) -> str:
        keys, args = [state_key(auction_id)], ["live", "closing", "1"]
        try:
            outcome = str(await self._set_status(keys=keys, args=args))
            if outcome == "MISSING":  # lost state: rebuild first (refused while outbox pending)
                await init_live_state(auction_id, self._redis, self._sessions)
                outcome = str(await self._set_status(keys=keys, args=args))
        except (LiveStateUnavailable, AuctionNotLive, RedisError) as exc:
            log.warning(
                "closing deferred to the next run",
                extra={"fields": {"auction_id": str(auction_id), "error": repr(exc)}},
            )
            return "DEFERRED"
        return outcome

    async def _commit_closing(self, auction_id: UUID) -> bool:
        async with self._sessions() as session, session.begin():
            if not await AuctionRepository(session).transition(auction_id, "live", "closing"):
                return False  # another run committed it: one audit row per transition
            await AuditLogRepository(session).add(
                entity_type="auction",
                entity_id=str(auction_id),
                action="status_changed",
                before={"status": "live"},
                after={"status": "closing"},
            )
        return True
