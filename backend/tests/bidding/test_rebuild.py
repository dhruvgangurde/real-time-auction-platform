"""Rebuild live state from Postgres (FR-43, DC-1, D2)."""

import asyncio
import logging
from uuid import uuid4

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import live_state
from app.services.bidding import BidService
from app.services.ledger import persist_outbox
from app.services.live_state import (
    AuctionNotLive,
    LiveStateUnavailable,
    init_live_state,
    state_key,
)
from tests.bidding.conftest import AuctionFactory

COMPARED = ("highest_amount", "highest_bidder", "sequence", "end_time_ms", "extension_count")


async def _bids_then_persist(service: BidService, a, new_user, redis, sessions) -> None:
    alice, bob = await new_user(), await new_user()
    for i, (who, amount) in enumerate([(alice, 10_000), (bob, 10_500), (alice, 11_000)]):
        assert (await service.place_bid(a, who, amount, f"b{i}")).accepted
    await persist_outbox(a, redis, sessions)


async def test_rebuild_matches_lost_state(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a = await auction(ends_in_ms=59_000)  # the first bid extends, so end time is checked too
    await _bids_then_persist(service, a, new_user, redis, sessions)
    before = await redis.hgetall(state_key(a))
    assert before["extension_count"] != "0"

    await redis.delete(state_key(a))  # Redis state lost
    assert await init_live_state(a, redis, sessions) is True
    after = await redis.hgetall(state_key(a))
    assert {k: after[k] for k in COMPARED} == {k: before[k] for k in COMPARED}
    assert after["status"] == "live" and "rebuild_token" not in after


async def test_concurrent_rebuilds_do_not_clobber(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a = await auction()
    await _bids_then_persist(service, a, new_user, redis, sessions)
    await redis.delete(state_key(a))
    results = await asyncio.gather(*(init_live_state(a, redis, sessions) for _ in range(5)))
    assert results.count(True) == 1
    final = await redis.hgetall(state_key(a))
    assert final["status"] == "live" and final["sequence"] == "3"


async def test_rebuild_refused_while_outbox_pending(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis, sessions
) -> None:
    """An unpersisted accepted bid would be missing from the snapshot: refuse, fail closed."""
    a = await auction()
    assert (await service.place_bid(a, await new_user(), 10_000, "x")).accepted
    await redis.delete(state_key(a))
    with pytest.raises(LiveStateUnavailable):
        await init_live_state(a, redis, sessions)
    assert await redis.exists(state_key(a)) == 0


async def test_service_rebuilds_missing_state_then_accepts(
    service: BidService, auction: AuctionFactory, new_user
) -> None:
    a = await auction(init=False)
    r = await service.place_bid(a, await new_user(), 10_000, "x")
    assert r.accepted and r.sequence_no == 1


@pytest.mark.parametrize("status", ["draft", "scheduled", "cancelled", "closed_no_sale"])
async def test_init_refuses_auctions_that_are_not_live(
    auction: AuctionFactory,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    status: str,
) -> None:
    """D2: only live and closing auctions ever get Redis state."""
    a = await auction(status=status)
    with pytest.raises(AuctionNotLive):
        await init_live_state(a, redis, sessions)
    assert await redis.exists(state_key(a)) == 0


async def test_service_missing_state_scheduled_or_unknown(
    service: BidService, auction: AuctionFactory, new_user
) -> None:
    scheduled = await auction(status="scheduled", init=False)
    assert (await service.place_bid(scheduled, await new_user(), 10_000, "x")).code == (
        "AUCTION_NOT_STARTED"
    )
    assert (await service.place_bid(uuid4(), await new_user(), 10_000, "x")).code == "NOT_FOUND"


def _racing_snapshot(db: asyncpg.Connection, times: int):
    """Wrap _snapshot so a 'persister' commits a ledger row right after each of the first
    `times` snapshots, i.e. between the snapshot read and the init script."""
    real = live_state._snapshot
    calls = {"n": 0}

    async def snapshot(sessions, auction_id):
        fields, status, sequence, proxies = await real(sessions, auction_id)
        calls["n"] += 1
        if calls["n"] <= times:
            bidder = await db.fetchval(
                "INSERT INTO users (email, password_hash, full_name) "
                "VALUES ($1, 'x', 'Racer') RETURNING id",
                f"{uuid4().hex}@example.com",
            )
            await db.execute(
                "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type) "
                "VALUES ($1, $2, $3, $4, 'manual')",
                auction_id,
                bidder,
                20_000 + sequence * 1_000,
                sequence + 1,
            )
        return fields, status, sequence, proxies

    return snapshot


async def test_rebuild_retries_when_ledger_moves(
    auction: AuctionFactory,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A row committed between snapshot and init is detected; the stale hash is discarded and
    the rebuild retried, so no bid is ever arbitrated against a stale sequence."""
    a = await auction(init=False)
    monkeypatch.setattr(live_state, "_snapshot", _racing_snapshot(db, times=1))
    with caplog.at_level(logging.WARNING, logger="live_state"):
        assert await init_live_state(a, redis, sessions) is True
    final = await redis.hgetall(state_key(a))
    assert final["sequence"] == "1" and final["highest_amount"] == "20000"
    assert final["status"] == "live"
    assert any(r.getMessage() == "ledger moved during rebuild, retrying" for r in caplog.records)


async def test_rebuild_fails_closed_when_ledger_keeps_moving(
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = await auction(init=False)
    monkeypatch.setattr(live_state, "_snapshot", _racing_snapshot(db, times=99))
    with pytest.raises(LiveStateUnavailable):
        await init_live_state(a, redis, sessions)
    assert await redis.exists(state_key(a)) == 0

    r = await BidService(redis, sessions, lambda _: None).place_bid(
        a, await new_user(), 99_000, "x"
    )
    assert r.code == "SERVICE_UNAVAILABLE"
