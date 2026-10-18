"""Fail closed when the real-time layer is unreachable (FR-42, NFR-9, BR-14; DP-2)."""

import asyncpg
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.redis import create_redis
from app.services.bidding import BidService
from tests.bidding.conftest import AuctionFactory, PersistRecorder


async def test_unreachable_redis_rejects_and_writes_nothing(
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    a = await auction(init=False)
    dead = create_redis("redis://redis:6399/15")  # nothing listens on this port
    persist = PersistRecorder()
    try:
        r = await BidService(dead, sessions, persist).place_bid(a, await new_user(), 10_000, "r1")
    finally:
        await dead.aclose()
    assert (r.accepted, r.code) == (False, "SERVICE_UNAVAILABLE")
    assert persist.calls == []
    assert await redis.dbsize() == 0
    assert await db.fetchval("SELECT count(*) FROM bids WHERE auction_id = $1", a) == 0
