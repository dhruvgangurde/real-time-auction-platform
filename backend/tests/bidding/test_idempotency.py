"""A retried submission returns the original result and consumes no new sequence (FR-40, DC-5)."""

from redis.asyncio import Redis

from app.services.bidding import BidService
from app.services.live_state import outbox_key, state_key
from tests.bidding.conftest import AuctionFactory


async def test_same_request_id_returns_identical_result(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    a = await auction()
    alice = await new_user()
    first = await service.place_bid(a, alice, 10_000, "retry-me")
    again = await service.place_bid(a, alice, 10_000, "retry-me")
    assert first.accepted and again == first
    assert await redis.hget(state_key(a), "sequence") == "1"
    assert await redis.xlen(outbox_key(a)) == 1


async def test_same_request_id_from_another_bidder_is_independent(
    service: BidService, auction: AuctionFactory, new_user
) -> None:
    """The request record is scoped by bidder, so results never leak across bidders."""
    a = await auction()
    first = await service.place_bid(a, await new_user(), 10_000, "same")
    second = await service.place_bid(a, await new_user(), 10_500, "same")
    assert first.sequence_no == 1 and second.accepted and second.sequence_no == 2
