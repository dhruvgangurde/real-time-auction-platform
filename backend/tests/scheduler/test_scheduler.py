"""Lifecycle scheduler (FR-15, FR-16, FR-58, FR-77, FR-78, D2, D9), against real Redis TIME."""

import asyncio
import logging
from uuid import UUID

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import BidService
from app.services.live_state import init_live_state, outbox_key, state_key
from app.services.scheduler import Scheduler
from tests.db.conftest import make_user


async def _status(db: asyncpg.Connection, auction_id: UUID) -> str:
    return await db.fetchval("SELECT status FROM auctions WHERE id = $1", auction_id)


async def _transitions(db: asyncpg.Connection, auction_id: UUID) -> list[tuple[str, str]]:
    rows = await db.fetch(
        "SELECT before->>'status' AS b, after->>'status' AS a, actor_id FROM audit_log "
        "WHERE entity_type = 'auction' AND entity_id = $1 AND action = 'status_changed' "
        "ORDER BY id",
        str(auction_id),
    )
    assert all(r["actor_id"] is None for r in rows)  # the system acted (FR-77)
    return [(r["b"], r["a"]) for r in rows]


def _scheduler(redis: Redis, sessions, closed: list[UUID] | None = None) -> Scheduler:
    async def record(auction_id: UUID) -> None:
        if closed is not None:
            closed.append(auction_id)

    return Scheduler(redis, sessions, on_closing=record)


async def test_concurrent_runs_activate_once_and_close_once(
    timed_auction, db: asyncpg.Connection, redis: Redis, sessions
) -> None:
    """FR-16: four runs at once; one activation, one closing, one audit row each."""
    auction_id = await timed_auction("scheduled", start_s=-2, end_s=-1)
    closed: list[UUID] = []
    runs = [_scheduler(redis, sessions, closed) for _ in range(4)]

    activated = await asyncio.gather(*(s.activate_due() for s in runs))
    assert sum(r.count(auction_id) for r in activated) == 1
    assert await _status(db, auction_id) == "live"
    assert await redis.hget(state_key(auction_id), "status") == "live"  # FR-15

    finished = await asyncio.gather(*(s.close_ended() for s in runs))
    assert sum(r.count(auction_id) for r in finished) == 1 and closed == [auction_id]
    assert await _status(db, auction_id) == "closing"
    assert await redis.hget(state_key(auction_id), "status") == "closing"
    assert await _transitions(db, auction_id) == [("scheduled", "live"), ("live", "closing")]


async def test_late_activation_is_warned(
    timed_auction, redis: Redis, sessions, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-16: a start time well in the past (a service interruption) still activates, with a
    warning; an on-time one does not warn."""
    late = await timed_auction("scheduled", start_s=-60, end_s=600)
    on_time = await timed_auction("scheduled", start_s=-1, end_s=600)
    with caplog.at_level(logging.WARNING, logger="scheduler"):
        activated = await _scheduler(redis, sessions).activate_due()
    assert set(activated) == {late, on_time}
    warned = [r.fields["auction_id"] for r in caplog.records if r.msg == "auction activated late"]
    assert warned == [str(late)]


async def test_bids_are_refused_until_activation(
    timed_auction, db: asyncpg.Connection, redis: Redis, sessions, bid_service: BidService
) -> None:
    """FR-15: before the scheduler runs the auction is not live; after it, bids are accepted."""
    auction_id = await timed_auction("scheduled", start_s=-1, end_s=600)
    bidder = await make_user(db)
    before = await bid_service.place_bid(auction_id, bidder, 10_000, "early")
    assert before.code == "AUCTION_NOT_STARTED"
    assert await redis.exists(state_key(auction_id)) == 0  # D2: nothing written
    await _scheduler(redis, sessions).run()
    after = await bid_service.place_bid(auction_id, bidder, 10_000, "on-time")
    assert after.accepted and after.sequence_no == 1


async def test_auction_extended_past_its_postgres_end_time_stays_live(
    timed_auction, db: asyncpg.Connection, redis: Redis, sessions, bid_service: BidService
) -> None:
    """FR-58, D9: a late bid extends the end time in Redis; Postgres still has the old end
    time (the persister has not run), so the auction is a candidate but must stay live."""
    auction_id = await timed_auction(
        "live", start_s=-60, end_s=30, antisnipe_threshold_s=60, antisnipe_extension_s=120
    )
    await init_live_state(auction_id, redis, sessions)
    late_bid = await bid_service.place_bid(auction_id, await make_user(db), 10_000, "snipe")
    assert late_bid.extended
    await db.execute(  # Postgres has not caught up and believes the auction already ended
        "UPDATE auctions SET end_time = now() - interval '1 second' WHERE id = $1", auction_id
    )
    assert await _scheduler(redis, sessions).close_ended() == []
    assert await _status(db, auction_id) == "live"
    assert await redis.hget(state_key(auction_id), "status") == "live"
    assert await _transitions(db, auction_id) == []


async def test_crash_after_the_activation_commit_is_repaired(
    timed_auction, db: asyncpg.Connection, redis: Redis, sessions
) -> None:
    """Postgres says live but the process died before installing the live state."""
    auction_id = await timed_auction("live", start_s=-5, end_s=600)
    report = await _scheduler(redis, sessions).run()
    assert report.repaired == [auction_id]
    assert await redis.hget(state_key(auction_id), "status") == "live"


async def test_crash_between_the_redis_flip_and_postgres_is_repaired(
    timed_auction, db: asyncpg.Connection, redis: Redis, sessions
) -> None:
    """Redis already says closing; Postgres still says live."""
    auction_id = await timed_auction("live", start_s=-60, end_s=-1)
    await init_live_state(auction_id, redis, sessions)
    await redis.hset(state_key(auction_id), "status", "closing")
    closed: list[UUID] = []
    assert await _scheduler(redis, sessions, closed).close_ended() == [auction_id]
    assert await _status(db, auction_id) == "closing" and closed == [auction_id]
    assert await _transitions(db, auction_id) == [("live", "closing")]


async def test_missing_live_state_is_rebuilt_before_closing(
    timed_auction, db: asyncpg.Connection, redis: Redis, sessions
) -> None:
    auction_id = await timed_auction("live", start_s=-60, end_s=-1)
    assert await _scheduler(redis, sessions).close_ended() == [auction_id]
    assert await _status(db, auction_id) == "closing"


async def test_pending_outbox_defers_closing(
    timed_auction, db: asyncpg.Connection, redis: Redis, sessions, caplog
) -> None:
    """Lost live state with unpersisted entries cannot be rebuilt yet (FR-43): wait a run."""
    auction_id = await timed_auction("live", start_s=-60, end_s=-1)
    await redis.xadd(outbox_key(auction_id), {"kind": "bid", "sequence_no": "1"})
    with caplog.at_level(logging.WARNING, logger="scheduler"):
        assert await _scheduler(redis, sessions).close_ended() == []
    assert await _status(db, auction_id) == "live"
    assert any(r.msg == "closing deferred to the next run" for r in caplog.records)


async def test_stale_hash_is_replaced_on_activation(
    timed_auction, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """D2: a non-live hash left by older code is removed before the live state is built."""
    auction_id = await timed_auction("scheduled", start_s=-1, end_s=600)
    await redis.hset(state_key(auction_id), mapping={"status": "scheduled", "sequence": "0"})
    await _scheduler(redis, sessions).activate_due()
    assert await redis.hget(state_key(auction_id), "status") == "live"


async def test_not_yet_due_and_not_yet_ended_are_left_alone(
    timed_auction, db: asyncpg.Connection, redis: Redis, sessions
) -> None:
    upcoming = await timed_auction("scheduled", start_s=60, end_s=600)
    running = await timed_auction("live", start_s=-60, end_s=600)
    await init_live_state(running, redis, sessions)
    report = await _scheduler(redis, sessions).run()
    assert report.activated == [] and report.closed == []
    assert await _status(db, upcoming) == "scheduled" and await _status(db, running) == "live"
