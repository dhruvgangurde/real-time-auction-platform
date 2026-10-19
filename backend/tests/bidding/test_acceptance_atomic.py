"""FR-39: on acceptance, one atomic operation records the new highest bid and bidder, increments
the sequence and evaluates and applies soft close (FR-53, FR-55: the outbox entry carries the new
end time). Each bid here is exactly one call of the arbitration script; the state hash and the
single outbox entry it adds are read before and after that call.
"""

from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.services.bidding import BidResult, BidService
from app.services.live_state import outbox_key, state_key
from tests.bidding.conftest import AuctionFactory


def _count_script_calls(service: BidService) -> list[int]:
    """Wrap the service's place_bid script; the list grows by one per script call."""
    calls: list[int] = []
    script = service._place_bid

    async def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return await script(*args, **kwargs)

    service._place_bid = counted  # type: ignore[method-assign]
    return calls


async def _bid_once(
    service: BidService, redis: Redis, auction_id: UUID, bidder: UUID, amount: int
) -> tuple[BidResult, dict[str, str], dict[str, str], dict[str, str]]:
    """(result, state before, state after, the one outbox entry) of one bid, one script call."""
    before = await redis.hgetall(state_key(auction_id))
    last = await redis.xrevrange(outbox_key(auction_id), count=1)
    calls = _count_script_calls(service)
    result = await service.place_bid(auction_id, bidder, amount, f"r-{amount}")
    assert len(calls) == 1, "the acceptance took more than one script call"
    after = await redis.hgetall(state_key(auction_id))
    start = f"({last[0][0]}" if last else "-"
    added = await redis.xrange(outbox_key(auction_id), min=start)
    assert len(added) == 1, added  # one acceptance, one outbox entry
    return result, before, after, added[0][1]


async def test_a_bid_inside_the_window_sets_highest_and_sequence_and_extends_in_one_call(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    """FR-39, FR-53, FR-55: inside the soft-close window one acceptance (1) sets the new highest
    bid and bidder, (2) increments the sequence by exactly 1, (3) extends the end time and the
    extension count; the outbox entry it writes carries all three."""
    a = await auction(ends_in_ms=30_000, threshold_s=60, extension_s=10, min_increment=500)
    bob, alice = await new_user(), await new_user()
    first = await service.place_bid(a, bob, 10_000, "r-first")  # a sequence to increment from
    assert first.accepted and first.extended

    result, before, after, entry = await _bid_once(service, redis, a, alice, 10_500)
    assert result.accepted and result.extended
    seq = int(before["sequence"]) + 1
    end = int(before["end_time_ms"]) + 10_000
    count = int(before["extension_count"]) + 1
    # (1) the new highest bid and bidder
    assert (after["highest_amount"], after["highest_bidder"]) == ("10500", str(alice))
    # (2) the sequence, by exactly one
    assert int(after["sequence"]) == seq == result.sequence_no == 2
    # (3) the soft close: the end time and the extension count
    assert (int(after["end_time_ms"]), int(after["extension_count"])) == (end, count)
    assert (result.end_time_ms, result.extension_count) == (end, count)
    # the outbox entry of that same call carries all three
    assert entry["kind"] == "bid" and entry["bid_type"] == "manual"
    assert (entry["bidder_id"], entry["amount"]) == (str(alice), "10500")
    assert int(entry["sequence_no"]) == seq
    assert (int(entry["end_time_after"]), int(entry["extension_count"])) == (end, count)


async def test_the_same_bid_outside_the_window_sets_highest_and_sequence_only(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    """FR-39, FR-53: outside the window the same acceptance changes (1) the highest bid and
    bidder and (2) the sequence, but not (3): the end time and the extension count stay, and
    the outbox entry carries no new end time."""
    a = await auction(ends_in_ms=3_600_000, threshold_s=60, extension_s=10, min_increment=500)
    bob, alice = await new_user(), await new_user()
    first = await service.place_bid(a, bob, 10_000, "r-first")
    assert first.accepted and not first.extended

    result, before, after, entry = await _bid_once(service, redis, a, alice, 10_500)
    assert result.accepted and not result.extended
    seq = int(before["sequence"]) + 1
    assert (after["highest_amount"], after["highest_bidder"]) == ("10500", str(alice))
    assert int(after["sequence"]) == seq == result.sequence_no == 2
    assert after["end_time_ms"] == before["end_time_ms"]
    assert after["extension_count"] == before["extension_count"] == "0"
    assert (entry["bidder_id"], entry["amount"], int(entry["sequence_no"])) == (
        str(alice),
        "10500",
        seq,
    )
    assert "end_time_after" not in entry and "extension_count" not in entry
