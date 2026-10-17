"""Demo mode fixtures (B2): the app in process from tests/lifecycle, the demo service, and a
clean demo_runs table per test (runs are global state: one active run per kind)."""

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.ratelimit import RateLimiter
from app.services.demo import DemoService
from tests.bidding.conftest import auction  # noqa: F401  shared fixture
from tests.closing.conftest import ended  # noqa: F401  shared fixture
from tests.lifecycle.conftest import (  # noqa: F401  shared fixtures
    admin_service,
    api,
    auction_service,
    auth_service,
    bid_service,
    catalog_service,
    category,
    clock,
    close_queue,
    email,
    limiter,
    make_user,
    storage,
    user_service,
)
from tests.notifications.support import notification_service, outbox  # noqa: F401  fixtures
from tests.payments.support import gateway, payment_service  # noqa: F401  fixtures


@pytest.fixture(autouse=True)
async def no_runs(db: asyncpg.Connection) -> None:
    await db.execute("DELETE FROM demo_runs")  # the owner may; the app role never deletes


@pytest.fixture
def demo_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)


@pytest.fixture
def demo_service(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    limiter: RateLimiter,  # noqa: F811  the shared fixture, imported above
) -> DemoService:
    return DemoService(sessions, redis, limiter)
