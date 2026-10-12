"""Arbitration rules (FR-36, FR-37, FR-38, BR-14). Rejections write nothing (FR-41)."""

from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.services.bidding import BidService
from app.services.live_state import outbox_key, state_key
from tests.bidding.conftest import AuctionFactory


async def test_first_bid_must_meet_start_price(
    service: BidService, auction: AuctionFactory, new_user
) -> None:
    """FR-37: with no bids yet the minimum is the starting price."""
    a = await auction(start_price=10_000)
    r = await service.place_bid(a, await new_user(), 9_999, "r1")
    assert (r.accepted, r.code, r.min_next, r.current_highest) == (
        False,
        "BID_TOO_LOW",
        10_000,
        None,
    )
    r = await service.place_bid(a, await new_user(), 10_000, "r2")
    assert r.accepted and r.sequence_no == 1 and r.amount == 10_000


async def test_increment_boundary(service: BidService, auction: AuctionFactory, new_user) -> None:
    """FR-37: exactly highest + increment is accepted, one paise less is rejected."""
    a = await auction(start_price=10_000, min_increment=500)
    assert (await service.place_bid(a, await new_user(), 10_000, "r1")).accepted
    low = await service.place_bid(a, await new_user(), 10_499, "r2")
    assert (low.code, low.current_highest, low.min_next) == ("BID_TOO_LOW", 10_000, 10_500)
    ok = await service.place_bid(a, await new_user(), 10_500, "r3")
    assert ok.accepted and ok.sequence_no == 2


async def test_above_limit(service: BidService, auction: AuctionFactory, new_user) -> None:
    """FR-37, BR-14: more than 100x the starting price is rejected; exactly 100x is fine."""
    a = await auction(start_price=10_000)
    r = await service.place_bid(a, await new_user(), 1_000_001, "r1")
    assert r.code == "BID_ABOVE_LIMIT"
    r = await service.place_bid(a, await new_user(), 1_000_000, "r2")
    assert r.accepted


async def test_self_outbid_rejected_when_enabled(
    service: BidService, auction: AuctionFactory, new_user
) -> None:
    """FR-38: the current leader cannot raise their own manual bid."""
    a = await auction(self_outbid=True)
    alice = await new_user()
    assert (await service.place_bid(a, alice, 10_000, "r1")).accepted
    r = await service.place_bid(a, alice, 10_500, "r2")
    assert r.code == "SELF_OUTBID_NOT_ALLOWED"


async def test_self_outbid_allowed_when_disabled(
    service: BidService, auction: AuctionFactory, new_user
) -> None:
    a = await auction(self_outbid=False)
    alice = await new_user()
    assert (await service.place_bid(a, alice, 10_000, "r1")).accepted
    r = await service.place_bid(a, alice, 10_500, "r2")
    assert r.accepted and r.sequence_no == 2


async def test_not_started_when_scheduled(
    service: BidService, auction: AuctionFactory, new_user
) -> None:
    """FR-36"""
    a = await auction(status="scheduled")
    assert (await service.place_bid(a, await new_user(), 10_000, "r1")).code == (
        "AUCTION_NOT_STARTED"
    )


async def test_not_started_before_start_time(
    service: BidService, auction: AuctionFactory, new_user
) -> None:
    """FR-36: status live but the start time (Redis TIME) has not arrived."""
    a = await auction(starts_in_ms=60_000)
    assert (await service.place_bid(a, await new_user(), 10_000, "r1")).code == (
        "AUCTION_NOT_STARTED"
    )


async def test_closed_after_end_time(
    service: BidService, auction: AuctionFactory, new_user
) -> None:
    """FR-36: now >= end_time rejects, judged by Redis TIME."""
    a = await auction(starts_in_ms=-7_200_000, ends_in_ms=-1_000)
    assert (await service.place_bid(a, await new_user(), 10_000, "r1")).code == "AUCTION_CLOSED"


@pytest.mark.parametrize("status", ["closing", "closed_sold", "cancelled"])
async def test_closed_when_not_live(
    service: BidService, auction: AuctionFactory, new_user, status: str
) -> None:
    a = await auction(status=status)
    assert (await service.place_bid(a, await new_user(), 10_000, "r1")).code == "AUCTION_CLOSED"


async def test_rejections_leave_state_and_outbox_untouched(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    """FR-41: rejected bids are not recorded anywhere."""
    a = await auction(self_outbid=True)
    alice = await new_user()
    assert (await service.place_bid(a, alice, 10_000, "ok")).accepted
    before_state = await redis.hgetall(state_key(a))
    before_outbox = await redis.xrange(outbox_key(a))
    before_keys = set(await redis.keys("*"))

    rejected = [
        await service.place_bid(a, await new_user(), 10_499, "low"),
        await service.place_bid(a, await new_user(), 2_000_000, "high"),
        await service.place_bid(a, alice, 11_000, "self"),
    ]
    assert [r.code for r in rejected] == [
        "BID_TOO_LOW",
        "BID_ABOVE_LIMIT",
        "SELF_OUTBID_NOT_ALLOWED",
    ]
    assert await redis.hgetall(state_key(a)) == before_state
    assert await redis.xrange(outbox_key(a)) == before_outbox
    assert set(await redis.keys("*")) == before_keys  # no request records for rejections


@pytest.mark.parametrize(
    ("amount", "request_id"),
    [
        (0, "r"),
        (-5, "r"),
        (2**53, "r"),
        (True, "r"),
        ("10000", "r"),
        (10_000, ""),
        (10_000, "x" * 65),
        (10_000, "has space"),
    ],
)
async def test_invalid_input_never_reaches_redis(
    service: BidService, redis: Redis, amount: object, request_id: str
) -> None:
    """DC-11: amounts are positive integers <= 2^53-1; request ids are short and safe."""
    r = await service.place_bid(uuid4(), uuid4(), amount, request_id)  # type: ignore[arg-type]
    assert r.code == "VALIDATION_ERROR"
    assert await redis.dbsize() == 0
