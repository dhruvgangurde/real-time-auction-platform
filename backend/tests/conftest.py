"""Shared fixtures: the FastAPI app under httpx, and the real Postgres/Redis test resources."""

from collections.abc import AsyncIterator

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.redis import create_redis
from app.main import app as _app
from tests.db.conftest import owner_dsn, test_db  # noqa: F401  shared session fixture

TEST_REDIS_URL = settings.redis_url.rsplit("/", 1)[0] + "/15"


class ValidateBody(BaseModel):
    n: int


def _register_test_routes(app: FastAPI) -> None:
    @app.post("/_test/validate")
    async def validate(body: ValidateBody) -> dict[str, int]:
        return {"n": body.n}

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("boom")


_register_test_routes(_app)  # once per test session


@pytest.fixture
async def client():
    # raise_app_exceptions=False lets the 500 handler run instead of re-raising into the test
    transport = ASGITransport(app=_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def new_redis() -> Redis:
    return create_redis(TEST_REDIS_URL)


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    """Real Redis on db 15, flushed per test."""
    client = new_redis()
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture(scope="session")
async def sessions(test_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:  # noqa: F811
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
async def db(test_db: str) -> AsyncIterator[asyncpg.Connection]:  # noqa: F811
    """Owner connection in autocommit mode, for setup and for reading results."""
    conn = await asyncpg.connect(owner_dsn(test_db))
    yield conn
    await conn.close()
