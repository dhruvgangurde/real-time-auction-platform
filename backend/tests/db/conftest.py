"""Integration fixtures against a real Postgres.

A separate `auction_test` database is created and migrated once per session, so dev data is
never touched. Alembic runs in a subprocess to keep its own event loop out of pytest's.
`owner` connects as the migration owner; `app` connects as the restricted application role.
Each connection runs inside a transaction that is rolled back after the test.
"""

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DB = "auction_test"


def owner_dsn(database: str) -> str:
    url = make_url(settings.migration_database_url).set(drivername="postgresql", database=database)
    return url.render_as_string(hide_password=False)


def app_dsn(database: str) -> str:
    url = make_url(settings.migration_database_url).set(
        drivername="postgresql",
        database=database,
        username=settings.app_db_user,
        password=settings.app_db_password,
    )
    return url.render_as_string(hide_password=False)


def alembic_url(database: str) -> str:
    url = make_url(settings.migration_database_url).set(database=database)
    return url.render_as_string(hide_password=False)


def run_alembic(database: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "MIGRATION_DATABASE_URL": alembic_url(database)}
    return subprocess.run(
        ["alembic", *args], cwd=BACKEND_DIR, env=env, capture_output=True, text=True, check=True
    )


async def _recreate(database: str, drop_only: bool = False) -> None:
    conn = await asyncpg.connect(owner_dsn("postgres"))
    try:
        # identifiers are fixed test constants, never user input
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{database}"')
    finally:
        await conn.close()


def recreate_database(database: str) -> None:
    asyncio.run(_recreate(database))


def drop_database(database: str) -> None:
    asyncio.run(_recreate(database, drop_only=True))


@pytest.fixture(scope="session")
def test_db() -> str:
    recreate_database(TEST_DB)
    run_alembic(TEST_DB, "upgrade", "head")
    return TEST_DB


async def _tx_connection(dsn: str) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(dsn)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


@pytest.fixture
async def owner(test_db: str) -> AsyncIterator[asyncpg.Connection]:
    async for conn in _tx_connection(owner_dsn(test_db)):
        yield conn


@pytest.fixture
async def app(test_db: str) -> AsyncIterator[asyncpg.Connection]:
    async for conn in _tx_connection(app_dsn(test_db)):
        yield conn


async def expect_error(
    conn: asyncpg.Connection, exc: type[Exception], sql: str, *args: Any
) -> pytest.ExceptionInfo[Exception]:
    """Run one statement inside a savepoint and assert it raises `exc`."""
    with pytest.raises(exc) as info:
        async with conn.transaction():
            await conn.execute(sql, *args)
    return info


# --- minimal row factories -------------------------------------------------------------

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


async def make_user(conn: asyncpg.Connection, email: str | None = None) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO users (email, password_hash, full_name) VALUES ($1, 'x', 'Test User') "
        "RETURNING id",
        email or f"{uuid.uuid4().hex}@example.com",
    )


async def make_category(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(
        "INSERT INTO categories (name, slug) VALUES ('Cat', $1) RETURNING id", uuid.uuid4().hex
    )


async def make_auction(conn: asyncpg.Connection, **overrides: Any) -> uuid.UUID:
    seller = overrides.pop("seller_id", None) or await make_user(conn)
    category = overrides.pop("category_id", None) or await make_category(conn)
    row = {
        "seller_id": seller,
        "category_id": category,
        "title": "Vintage watch",
        "description": "A watch",
        "starting_price": 10_000,
        "reserve_price": None,
        "min_increment": 500,
        "start_time": NOW,
        "end_time": NOW + timedelta(hours=1),
        "original_end_time": NOW + timedelta(hours=1),
    } | overrides
    cols = ", ".join(row)
    params = ", ".join(f"${i}" for i in range(1, len(row) + 1))
    return await conn.fetchval(
        f"INSERT INTO auctions ({cols}) VALUES ({params}) RETURNING id", *row.values()
    )


async def make_bid(
    conn: asyncpg.Connection, auction_id: uuid.UUID, bidder_id: uuid.UUID, sequence_no: int
) -> int:
    return await conn.fetchval(
        "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type) "
        "VALUES ($1, $2, 11000, $3, 'manual') RETURNING id",
        auction_id,
        bidder_id,
        sequence_no,
    )


async def make_settlement(
    conn: asyncpg.Connection, auction_id: uuid.UUID, winner_id: uuid.UUID, key: str | None = None
) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO settlements (auction_id, winner_id, final_price, status, idempotency_key) "
        "VALUES ($1, $2, 11000, 'pending_payment', $3) RETURNING id",
        auction_id,
        winner_id,
        key or uuid.uuid4().hex,
    )
