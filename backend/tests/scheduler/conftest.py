"""Scheduler fixtures. A run looks at every auction in the database, so each test starts by
moving the leftovers of earlier tests out of scheduled, live and closing (tests run one at a
time, so those are finished)."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from tests.db.conftest import make_auction, make_user
from tests.lifecycle.conftest import (  # noqa: F401  shared fixtures
    auth_service,
    bid_service,
    clock,
    email,
    limiter,
)


@pytest.fixture(autouse=True)
async def quiet(db: asyncpg.Connection) -> None:
    await db.execute(
        "UPDATE auctions SET status = 'cancelled' WHERE status IN ('scheduled', 'live', 'closing')"
    )


async def db_now(db: asyncpg.Connection) -> datetime:
    return await db.fetchval("SELECT now()")


@pytest.fixture
def timed_auction(db: asyncpg.Connection) -> Callable[..., Awaitable[UUID]]:
    """An auction timed against the database clock, offsets in seconds from now."""

    async def make(status: str, start_s: float, end_s: float, **overrides: Any) -> UUID:
        now = await db_now(db)
        end = now + timedelta(seconds=end_s)
        return await make_auction(
            db,
            seller_id=await make_user(db),
            status=status,
            start_time=now + timedelta(seconds=start_s),
            end_time=end,
            original_end_time=end,
            **overrides,
        )

    return make
