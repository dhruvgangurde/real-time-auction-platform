"""Readiness checks (OR-10). Each check returns None when healthy, else raises."""

import asyncio

from sqlalchemy import text

from app.core.db import engine
from app.core.redis import redis

TIMEOUT_S = 2


async def check_postgres() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_redis() -> None:
    await redis.ping()


async def readiness() -> str | None:
    """Return the name of the first unreachable dependency, or None when all are up."""
    for name, check in (("postgres", check_postgres), ("redis", check_redis)):
        try:
            await asyncio.wait_for(check(), TIMEOUT_S)
        except Exception:
            return name
    return None
