"""The caps of "Try to break it" hold under load (brief 12.4): twenty visitors pressing at once
make one run; one address pressing ten times at once gets through the address limit three times
at most; and fifty bots at the production socket limits are never limited, struck or closed,
because they pace themselves far below them, so every rejection is a real race."""

import asyncio
import json
from typing import Any

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import deps
from app.core.clock import SystemClock
from app.core.config import Settings, settings
from app.core.ratelimit import RateLimiter
from app.core.storage import LocalStorage
from app.services.auctions import AuctionService
from app.services.demo import RUNNER_ALIVE_KEY, DemoService
from app.services.demo_accounts import DemoAccounts
from app.services.denylist import Denylist
from app.workers.demo_runner import DemoRunner
from tests.auth.conftest import FakeClock
from tests.realtime.conftest import MESSAGE_KINDS, Instance, StartInstance
from tests.realtime.test_demo_runner import _beat
from tests.scheduler.conftest import quiet  # noqa: F401  autouse: no other test's open lots

START = "/api/v1/demo/stress"
PROXY = "127.0.0.1"  # trusted here, standing in for nginx, so each request has its own address


@pytest.fixture
async def node(
    start_instance: StartInstance,
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    limiter: RateLimiter,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> Instance:
    monkeypatch.setattr(settings, "demo_mode", True)
    await db.execute("DELETE FROM demo_runs")
    await redis.set(RUNNER_ALIVE_KEY, "1", ex=60)
    return await start_instance(
        forwarded_allow_ips=PROXY,
        overrides={deps.get_demo_service: DemoService(sessions, redis, limiter)},
    )


async def _press(http: httpx.AsyncClient, address: str) -> httpx.Response:
    return await http.post(START, headers={"X-Requested-With": "x", "X-Forwarded-For": address})


async def test_twenty_visitors_at_once_make_one_run(node: Instance, db: asyncpg.Connection) -> None:
    async with httpx.AsyncClient(base_url=node.http) as http:
        answers = await asyncio.gather(*(_press(http, f"203.0.113.{i}") for i in range(20)))
    assert sorted(a.status_code for a in answers) == [200] * 19 + [202]
    assert len({a.json()["run_id"] for a in answers}) == 1
    assert await db.fetchval("SELECT count(*) FROM demo_runs") == 1
    await db.execute("DELETE FROM demo_runs")


async def test_one_address_at_once_gets_through_three_times_at_most(
    node: Instance, db: asyncpg.Connection, redis: Redis
) -> None:
    async with httpx.AsyncClient(base_url=node.http) as http:
        answers = await asyncio.gather(*(_press(http, "198.51.100.7") for _ in range(10)))
    codes = sorted(a.status_code for a in answers)
    assert codes.count(202) == 1  # one run, whatever the race
    assert await db.fetchval("SELECT count(*) FROM demo_runs") == 1
    [key] = await redis.keys("rl:demo_stress_ip:198.51.100.7:*")
    counted = int(await redis.get(key))  # presses that reached the address limit
    assert counted > 3  # the presses really raced past the "already running" check
    limited = [a for a in answers if a.status_code == 429]
    assert len(limited) == counted - 3  # the limit is atomic: no fourth press got past it
    assert {a.json()["error"]["code"] for a in limited} == {"RATE_LIMITED"}
    await db.execute("DELETE FROM demo_runs")


def _production_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the realtime harness's raised limits: the bots meet exactly what production sets."""
    defaults = Settings.model_fields
    for name in (
        "ws_auth_timeout_s",  # production's 5 s for the first frame (the harness shortens it)
        "ws_max_messages_per_s",
        "ws_max_strikes",
        "ws_send_queue_size",
        "ws_max_sessions_per_user",
        *(f"rate_limit_ws_{kind}" for kind in MESSAGE_KINDS),
    ):
        monkeypatch.setattr(settings, name, defaults[name].default)


async def test_fifty_bots_at_the_production_limits_are_never_limited(
    instances: tuple[Instance, Instance],
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    clock: FakeClock,
    db: asyncpg.Connection,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_limits(monkeypatch)
    for name, value in {
        "demo_mode": True,
        "demo_stress_bots": 50,
        "demo_stress_bids_per_bot": 5,
        "demo_stress_seconds": 10,
        "demo_stress_stop_before_end_s": 11,
        "demo_stress_start_delay_s": 1,
        "rate_limit_auction_create": "1000/3600",
        "demo_ws_urls": ",".join(node.ws for node in instances),
    }.items():
        monkeypatch.setattr(settings, name, value)
    await db.execute("DELETE FROM demo_runs")
    real = SystemClock()
    runner = DemoRunner(
        sessions,
        redis,
        real,
        AuctionService(
            sessions, redis, real, RateLimiter(redis, real), LocalStorage(str(tmp_path))
        ),
        DemoAccounts(sessions, clock, Denylist(redis, None)),
    )
    cast = await runner.start(50)
    await db.execute("INSERT INTO demo_runs (kind) VALUES ('stress')")
    beat = asyncio.create_task(_beat(redis, sessions, db))
    try:
        run_id = await runner.next_run()
        assert run_id is not None
        state = await asyncio.wait_for(runner.run_stress(run_id, cast), 180)
    finally:
        beat.cancel()
    row = await db.fetchrow("SELECT counters, checks FROM demo_runs WHERE id = $1", run_id)
    counters, checks = json.loads(row["counters"]), json.loads(row["checks"])
    assert state == "verified", checks
    assert counters["bots"] == {"planned": 50, "connected": 50}
    submissions = counters["submissions"]
    assert 0 < submissions["sent"] <= 50 * 5
    # only real races: never RATE_LIMITED, never a socket closed or a reply lost
    assert set(submissions["rejected"]) <= {"BID_TOO_LOW", "SELF_OUTBID_NOT_ALLOWED"}
    assert submissions["accepted"] + sum(submissions["rejected"].values()) == submissions["sent"]
    await db.execute("DELETE FROM demo_runs")
