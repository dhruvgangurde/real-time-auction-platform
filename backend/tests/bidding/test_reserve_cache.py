"""The reserve feeding reserve_met is cached only once an auction is live: while scheduled it can
still be edited (BR-6, brief 12.2 item 3)."""

from redis.asyncio import Redis

from app.services.bidding import BidService
from app.services.live_state import init_live_state


async def test_scheduled_reserve_is_not_cached(
    service: BidService, auction, new_user, db, redis: Redis, sessions
) -> None:
    a = await auction(status="scheduled", start_price=9_000, init=False)
    await db.execute("UPDATE auctions SET reserve_price = 20000 WHERE id = $1", a)
    bidder = await new_user()

    early = await service.place_bid(a, bidder, 9_000, "early")
    assert early.code == "AUCTION_NOT_STARTED"
    assert a not in service._reserves and a not in service._sellers

    # the seller lowers the reserve before go-live, then the auction starts
    await db.execute("UPDATE auctions SET reserve_price = 9000, status = 'live' WHERE id = $1", a)
    assert await init_live_state(a, redis, sessions)
    first = await service.place_bid(a, bidder, 9_000, "first")
    assert first.accepted and first.reserve_met is True
    assert service._reserves[a] == 9_000  # cached from the live row
