"""The anonymous start of "Try to break it" is limited per client address (brief 12.4), and on a
real server a spoofed X-Forwarded-For buys nothing: only the web proxy's address is trusted to
set it (compose's --forwarded-allow-ips), so straight to an api instance the peer counts."""

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import deps
from app.core.config import settings
from app.core.ratelimit import RateLimiter
from app.services.demo import RUNNER_ALIVE_KEY, DemoService
from tests.realtime.conftest import StartInstance

START = "/api/v1/demo/stress"


async def test_a_spoofed_header_buys_no_extra_runs(
    start_instance: StartInstance,
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    limiter: RateLimiter,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "rate_limit_demo_stress_ip", "1/3600")
    await db.execute("DELETE FROM demo_runs")
    await redis.set(RUNNER_ALIVE_KEY, "1", ex=10)
    node = await start_instance(
        overrides={deps.get_demo_service: DemoService(sessions, redis, limiter)}
    )
    try:
        async with httpx.AsyncClient(base_url=node.http) as http:
            spoofed = {"X-Requested-With": "test", "X-Forwarded-For": "198.51.100.1"}
            first = await http.post(START, headers=spoofed)
            assert first.status_code == 202
            await db.execute(  # long finished: the cooldown is not what refuses the next one
                "UPDATE demo_runs SET state = 'verified', "
                "finished_at = now() - interval '2 minutes'"
            )
            spoofed["X-Forwarded-For"] = "198.51.100.2"  # another "address", same peer
            second = await http.post(START, headers=spoofed)
            assert second.status_code == 429
            assert second.json()["error"]["code"] == "RATE_LIMITED"
        keys = [k.split(":")[2] for k in await redis.keys("rl:demo_stress_ip:*")]
        assert keys == ["127.0.0.1"]  # the peer, never the header
    finally:
        await db.execute("DELETE FROM demo_runs")
