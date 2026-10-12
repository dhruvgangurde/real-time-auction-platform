"""Integration fixtures for bid arbitration: real Redis (DB index 15) and Postgres `auction_test`.

Rows are committed (not rolled back) because Redis scripts, the persister and the rebuild all
use their own connections. Every test creates fresh auctions and users, so nothing collides.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.redis import create_redis
from app.services.bidding import BidService
from app.services.live_state import from_ms, init_live_state, state_key
from tests.db.conftest import make_auction, make_category, make_user, owner_dsn

TEST_REDIS_URL = settings.redis_url.rsplit("/", 1)[0] + "/15"


def new_redis() -> Redis:
    return create_redis(TEST_REDIS_URL)


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = new_redis()
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture(scope="session")
async def sessions(test_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """App-role sessions on auction_test, as api and worker use in production (DC-4)."""
    url = make_url(settings.migration_database_url).set(
        drivername="postgresql+asyncpg",
        database=test_db,
        username=settings.app_db_user,
        password=settings.app_db_password,
    )
    engine = create_async_engine(url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(scope="session")
async def db(test_db: str) -> AsyncIterator[asyncpg.Connection]:
    """Owner connection in autocommit mode, for setup and for reading results."""
    conn = await asyncpg.connect(owner_dsn(test_db))
    yield conn
    await conn.close()


async def redis_now_ms(redis: Redis) -> int:
    sec, usec = await redis.time()
    return sec * 1000 + usec // 1000


@pytest.fixture
def new_user(db: asyncpg.Connection) -> Callable[[], Awaitable[UUID]]:
    async def make() -> UUID:
        return await make_user(db)

    return make


AuctionFactory = Callable[..., Awaitable[UUID]]


@pytest.fixture
def auction(
    db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> AuctionFactory:
    """Create an auction timed against Redis TIME and (by default) initialise its live state."""

    async def make(
        *,
        starts_in_ms: int = -60_000,
        ends_in_ms: int = 3_600_000,
        status: str = "live",
        start_price: int = 10_000,
        min_increment: int = 500,
        threshold_s: int = 60,
        extension_s: int = 120,
        extension_count: int = 0,
        self_outbid: bool | None = None,
        init: bool = True,
    ) -> UUID:
        now = await redis_now_ms(redis)
        end = from_ms(now + ends_in_ms)
        auction_id = await make_auction(
            db,
            seller_id=await make_user(db),
            category_id=await make_category(db),
            status=status,
            start_time=from_ms(now + starts_in_ms),
            end_time=end,
            original_end_time=end,
            starting_price=start_price,
            min_increment=min_increment,
            antisnipe_threshold_s=threshold_s,
            antisnipe_extension_s=extension_s,
            extension_count=extension_count,
        )
        if init:
            assert await init_live_state(auction_id, redis, sessions)
            if self_outbid is not None:
                await redis.hset(state_key(auction_id), "self_outbid", "1" if self_outbid else "0")
        return auction_id

    return make


class PersistRecorder:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    def __call__(self, auction_id: UUID) -> None:
        self.calls.append(auction_id)


@pytest.fixture
def persist_calls() -> PersistRecorder:
    return PersistRecorder()


@pytest.fixture
def service(
    redis: Redis, sessions: async_sessionmaker[AsyncSession], persist_calls: PersistRecorder
) -> BidService:
    return BidService(redis, sessions, persist_calls)


async def state(redis: Redis, auction_id: UUID) -> dict[str, Any]:
    return await redis.hgetall(state_key(auction_id))
