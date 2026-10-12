"""Rebuild of armed maxima from Postgres (FR-43, FR-45)."""

import logging

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import live_state
from app.services.bidding import BidService
from app.services.ledger import persist_outbox
from app.services.live_state import LiveStateUnavailable, init_live_state, proxies_key, state_key
from tests.bidding.conftest import AuctionFactory
from tests.bidding.proxy_model import AuctionModel
from tests.bidding.test_rebuild import _racing_snapshot

COMPARED = ("highest_amount", "highest_bidder", "sequence", "extension_count", "arm_seq")


async def test_rebuild_restores_maxima_and_identical_resolution(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a = await auction(start_price=10_000, min_increment=500)
    alice, bob, carol, dave = [await new_user() for _ in range(4)]
    model = AuctionModel(start_price=10_000, inc=500)

    assert (await service.set_proxy(a, alice, 50_000, "a1")).armed
    model.set_proxy("alice", 50_000)
    assert (await service.place_bid(a, bob, 20_000, "b1")).accepted
    model.place_bid("bob", 20_000)
    p = await service.set_proxy(a, carol, 30_000, "c1")  # record bid at 30_000, alice 30_500
    assert p.armed and not p.active
    model.set_proxy("carol", 30_000)
    await persist_outbox(a, redis, sessions)

    before_state = await redis.hgetall(state_key(a))
    before_proxies = await redis.hgetall(proxies_key(a))
    assert before_proxies == {str(alice): "50000|1"} and before_state["arm_seq"] == "2"

    await redis.delete(state_key(a), proxies_key(a))
    assert await init_live_state(a, redis, sessions) is True
    after_state = await redis.hgetall(state_key(a))
    assert {k: after_state[k] for k in COMPARED} == {k: before_state[k] for k in COMPARED}
    assert await redis.hgetall(proxies_key(a)) == before_proxies

    # the rebuilt state resolves exactly as the model (and the un-rebuilt state) would
    r = await service.place_bid(a, dave, 40_000, "d1")
    expected = model.place_bid("dave", 40_000)
    assert r.accepted
    assert [(x.bidder_id, x.amount) for x in r.automatic_bids] == [
        (alice, amount) for _, amount in expected.autos
    ]
    # a re-arm after the rebuild takes an arm_seq above everything ever persisted
    p = await service.set_proxy(a, carol, 60_000, "c2")
    assert p.armed and p.arm_seq == 3


async def test_rebuild_refused_while_proxy_change_pending(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis, sessions
) -> None:
    a = await auction()
    assert (await service.set_proxy(a, await new_user(), 20_000, "arm")).armed
    await redis.delete(state_key(a), proxies_key(a))
    with pytest.raises(LiveStateUnavailable):
        await init_live_state(a, redis, sessions)
    assert await redis.exists(state_key(a), proxies_key(a)) == 0


async def test_aborted_rebuild_discards_proxies_then_retries(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    a = await auction()
    alice = await new_user()
    assert (await service.set_proxy(a, alice, 20_000, "arm")).armed
    await persist_outbox(a, redis, sessions)
    await redis.delete(state_key(a), proxies_key(a))
    monkeypatch.setattr(live_state, "_snapshot", _racing_snapshot(db, times=1))
    with caplog.at_level(logging.WARNING, logger="live_state"):
        assert await init_live_state(a, redis, sessions) is True
    assert any(r.getMessage() == "ledger moved during rebuild, retrying" for r in caplog.records)
    assert (await redis.hgetall(state_key(a)))["status"] == "live"
    assert await redis.hgetall(proxies_key(a)) == {str(alice): "20000|1"}
