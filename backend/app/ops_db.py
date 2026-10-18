"""The isolated stack's database (docker-compose.isolated.yml): e2e and load-test runs use their
own database, so they never add accounts or lots to the development one. Postgres creates only
POSTGRES_DB on its first start, so the migrate service creates this one when it is missing,
before `alembic upgrade head`."""

import re

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

PLAIN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


async def ensure_database(url: str) -> bool:
    """Create the database `url` names unless it exists; True when it was created. The name
    cannot be a bound parameter in CREATE DATABASE, so only a plain lower-case identifier is
    accepted, and it is quoted as one (NFR-20)."""
    target = make_url(url)
    name = target.database or ""
    if not PLAIN.match(name):
        raise ValueError(f"not a plain database name: {name!r}")
    engine = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            found = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if found:
                return False
            quoted = engine.dialect.identifier_preparer.quote_identifier(name)
            await conn.execute(text(f"CREATE DATABASE {quoted}"))
            return True
    finally:
        await engine.dispose()
