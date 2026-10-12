"""Reconciliation of Redis sequence vs ledger (FR-43, NFR-10)."""

import logging

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import BidService
from app.services.ledger import persist_outbox
from app.services.live_state import state_key
from app.services.reconciliation import reconcile, reconcile_auction
from tests.bidding.conftest import AuctionFactory


async def _two_bids(service: BidService, a, new_user) -> None:
    assert (await service.place_bid(a, await new_user(), 10_000, "a")).accepted
    assert (await service.place_bid(a, await new_user(), 10_500, "b")).accepted


def _alerts(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [r.fields for r in caplog.records if "alert" in getattr(r, "fields", {})]


async def test_gap_not_covered_by_outbox_raises_alert(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    a = await auction()
    await _two_bids(service, a, new_user)
    await persist_outbox(a, redis, sessions)
    await redis.hset(state_key(a), "sequence", "5")  # Redis 3 ahead, outbox empty
    with caplog.at_level(logging.CRITICAL, logger="reconciliation"):
        assert await reconcile_auction(a, redis, sessions) == "SEQUENCE_GAP"
    [alert] = _alerts(caplog)
    assert alert["alert"] == "SEQUENCE_GAP"
    assert alert["auction_id"] == str(a)
    assert alert["missing_ranges"] == [[3, 5]]


async def test_gap_covered_by_outbox_is_fine(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Accepted but not yet persisted is normal: no alert."""
    a = await auction()
    await _two_bids(service, a, new_user)
    with caplog.at_level(logging.CRITICAL, logger="reconciliation"):
        assert await reconcile_auction(a, redis, sessions) is None
    assert _alerts(caplog) == []


async def test_ledger_ahead_of_redis_raises_regression(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    a = await auction()
    await _two_bids(service, a, new_user)
    await persist_outbox(a, redis, sessions)
    await redis.hset(state_key(a), "sequence", "1")  # Redis lost a write (AOF rollback)
    with caplog.at_level(logging.CRITICAL, logger="reconciliation"):
        assert await reconcile_auction(a, redis, sessions) == "SEQUENCE_REGRESSION"
    assert _alerts(caplog)[0]["alert"] == "SEQUENCE_REGRESSION"


async def test_reconcile_covers_all_live_auctions(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a = await auction()
    await _two_bids(service, a, new_user)
    await persist_outbox(a, redis, sessions)
    await redis.hset(state_key(a), "sequence", "4")
    alerts = await reconcile(redis, sessions)
    assert alerts[str(a)] == "SEQUENCE_GAP"
