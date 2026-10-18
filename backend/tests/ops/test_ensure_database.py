"""The isolated stack's database (docker-compose.isolated.yml): `ensure-database` creates the
database a URL names when it is missing, does nothing when it exists, and refuses a name that is
not a plain identifier, since a database name cannot be a bound parameter (NFR-20)."""

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.ops_db import ensure_database
from tests.db.conftest import owner_dsn

PROBE = "auction_ensure_probe"


def _url(name: str) -> str:
    return (
        make_url(settings.migration_database_url)
        .set(database=name)
        .render_as_string(hide_password=False)
    )


async def _exists(name: str) -> bool:
    conn = await asyncpg.connect(owner_dsn("postgres"))
    try:
        return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name))
    finally:
        await conn.close()


async def _drop(name: str) -> None:
    conn = await asyncpg.connect(owner_dsn("postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')  # a fixed test name
    finally:
        await conn.close()


async def test_a_missing_database_is_created_once() -> None:
    await _drop(PROBE)
    try:
        assert await ensure_database(_url(PROBE)) is True
        assert await _exists(PROBE)
        assert await ensure_database(_url(PROBE)) is False  # already there: nothing to do
    finally:
        await _drop(PROBE)


@pytest.mark.parametrize(
    "name", ['auction"; DROP DATABASE auction; --', "Auction", "1auction", "a-b", ""]
)
async def test_a_name_that_is_not_a_plain_identifier_is_refused(name: str) -> None:
    with pytest.raises(ValueError):
        await ensure_database(_url(name) if name else _url("x").replace("/x", "/"))
