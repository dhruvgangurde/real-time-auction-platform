"""Soft close inside the arbitration script (FR-52 to FR-56, OR-15, BR-8).

Times are relative to Redis TIME, the same clock the script uses. Nothing is mocked.
"""

from redis.asyncio import Redis

from app.services.bidding import BidService
from app.services.live_state import state_key
from tests.bidding.conftest import AuctionFactory


async def _end(redis: Redis, auction_id) -> int:
    return int(await redis.hget(state_key(auction_id), "end_time_ms"))


async def test_bid_61s_before_end_does_not_extend(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    """FR-53 boundary: remaining time above the 60 s threshold."""
    a = await auction(ends_in_ms=61_000, threshold_s=60, extension_s=120)
    end = await _end(redis, a)
    r = await service.place_bid(a, await new_user(), 10_000, "r1")
    assert r.accepted and not r.extended
    assert r.end_time_ms == end and r.extension_count == 0


async def test_bid_59s_before_end_extends_by_extension(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    """FR-53: new end = current end + extension; FR-55: the acceptance carries it."""
    a = await auction(ends_in_ms=59_000, threshold_s=60, extension_s=120)
    end = await _end(redis, a)
    r = await service.place_bid(a, await new_user(), 10_000, "r1")
    assert r.accepted and r.extended
    assert r.end_time_ms == end + 120_000 and r.extension_count == 1
    assert await _end(redis, a) == end + 120_000


async def test_three_consecutive_late_bids_each_extend(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    """FR-53: each bid is evaluated against the then-current end time.

    Threshold 300 s and extension 60 s keep every bid inside the window after each extension.
    """
    a = await auction(ends_in_ms=59_000, threshold_s=300, extension_s=60)
    end = await _end(redis, a)
    alice, bob = await new_user(), await new_user()
    for i, (who, amount) in enumerate([(alice, 10_000), (bob, 10_500), (alice, 11_000)], 1):
        r = await service.place_bid(a, who, amount, f"r{i}")
        assert r.accepted and r.extended
        assert r.end_time_ms == end + i * 60_000 and r.extension_count == i


async def test_extension_cap_of_ten(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    """FR-54: the 10th extension applies, the next late bid is accepted without extending.
    FR-56: soft close never rejects a bid."""
    a = await auction(ends_in_ms=59_000, threshold_s=600, extension_s=60, extension_count=9)
    end = await _end(redis, a)
    tenth = await service.place_bid(a, await new_user(), 10_000, "r1")
    assert tenth.accepted and tenth.extended and tenth.extension_count == 10
    assert tenth.end_time_ms == end + 60_000

    after_cap = await service.place_bid(a, await new_user(), 10_500, "r2")
    assert after_cap.accepted and not after_cap.extended
    assert after_cap.extension_count == 10 and after_cap.end_time_ms == end + 60_000
