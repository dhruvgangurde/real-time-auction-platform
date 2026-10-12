"""Reconciliation of Redis sequence vs ledger, and stale live-state cleanup (FR-43, NFR-10, D2)."""

import logging

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import BidService
from app.services.ledger import persist_outbox
from app.services.live_state import outbox_key, remove_stale_state, state_key
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


async def test_pending_proxy_entries_are_not_bids(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """N1: proxy_set and proxy_deactivated entries in the outbox carry no sequence number."""
    a = await auction(start_price=10_000, min_increment=500)
    assert (await service.set_proxy(a, await new_user(), 20_000, "p1")).armed
    assert (await service.set_proxy(a, await new_user(), 15_000, "p2")).armed  # exhausted
    kinds = {f.get("kind", "bid") for _, f in await redis.xrange(outbox_key(a))}
    assert kinds == {"bid", "proxy_set", "proxy_deactivated"}
    with caplog.at_level(logging.CRITICAL, logger="reconciliation"):
        assert await reconcile_auction(a, redis, sessions) is None
    assert _alerts(caplog) == []


async def test_one_failing_auction_never_hides_another_gap(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken, gapped = await auction(), await auction()
    await redis.hset(state_key(broken), "sequence", "not-a-number")
    await _two_bids(service, gapped, new_user)
    await persist_outbox(gapped, redis, sessions)
    await redis.hset(state_key(gapped), "sequence", "4")
    with caplog.at_level(logging.CRITICAL, logger="reconciliation"):
        alerts = await reconcile(redis, sessions)
    assert alerts[str(broken)] == "RECONCILE_FAILED"
    assert alerts[str(gapped)] == "SEQUENCE_GAP"
    failed = [a for a in _alerts(caplog) if a["alert"] == "RECONCILE_FAILED"]
    assert [a["auction_id"] for a in failed] == [str(broken)]


async def test_stale_live_state_is_removed_only_when_nothing_uses_it(
    auction: AuctionFactory, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """D2: Redis state of auctions that are not live or closing in Postgres is dropped by the
    reconciliation run, but never state that may be in use."""
    stale = await auction(status="cancelled")
    await redis.hset(state_key(stale), mapping={"status": "scheduled", "sequence": "0"})
    pending = await auction(status="cancelled")
    await redis.hset(state_key(pending), mapping={"status": "cancelled", "sequence": "1"})
    await redis.xadd(outbox_key(pending), {"kind": "bid", "sequence_no": "1"})
    # (a) Postgres still says scheduled, but the auction was activated in Redis since
    activated = await auction(status="scheduled")
    await redis.hset(state_key(activated), mapping={"status": "live", "sequence": "0"})
    # (b) a rebuild between init and finalize
    rebuilding = await auction(status="scheduled")
    await redis.hset(state_key(rebuilding), mapping={"status": "rebuilding", "sequence": "0"})
    live = await auction()

    assert await remove_stale_state(redis, sessions) == 1
    assert await redis.exists(state_key(stale)) == 0
    for kept in (pending, activated, rebuilding, live):
        assert await redis.exists(state_key(kept)) == 1, kept
    assert await redis.xlen(outbox_key(pending)) == 1

    await redis.hset(state_key(stale), mapping={"status": "scheduled", "sequence": "0"})
    await reconcile(redis, sessions)  # every reconciliation run cleans up
    assert await redis.exists(state_key(stale)) == 0
