"""The demo runner process (brief 12.3, 12.4): one at a time by a Postgres advisory lock, alive in
Redis for the API to see, aborting what a previous runner left unfinished, and creating each
stress run's fresh lot through the real lifecycle service, flagged as a demo."""

import asyncio
from datetime import timedelta

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.ratelimit import RateLimiter
from app.core.storage import LocalStorage
from app.services.auctions import AuctionService
from app.services.demo import RUNNER_ALIVE_KEY
from app.services.demo_accounts import DemoAccounts
from app.services.denylist import Denylist
from app.workers.demo_runner import DemoRunner, hold_runner_lock, refusal
from tests.auth.conftest import FakeClock


@pytest.fixture
def runner(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    clock: FakeClock,
    storage: LocalStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> DemoRunner:
    monkeypatch.setattr(settings, "rate_limit_auction_create", "1000/3600")
    auctions = AuctionService(sessions, redis, clock, RateLimiter(redis, clock), storage)
    accounts = DemoAccounts(sessions, clock, Denylist(redis, None))
    return DemoRunner(sessions, redis, clock, auctions, accounts)


async def test_only_one_runner_holds_the_lock(sessions: async_sessionmaker[AsyncSession]) -> None:
    engine = sessions.kw["bind"]
    async with engine.connect() as first, engine.connect() as second:
        assert await hold_runner_lock(first) is True
        assert await hold_runner_lock(second) is False  # a second runner exits
        await first.invalidate()  # the first runner dies: its connection really closes
    async with engine.connect() as third:  # and the lock is free again
        assert await hold_runner_lock(third) is True
        await third.invalidate()  # leave no lock behind on a pooled connection


async def test_startup_aborts_what_a_previous_runner_left(
    runner: DemoRunner, db: asyncpg.Connection
) -> None:
    left = {}
    for state in ("requested", "running", "settling", "verified"):
        kind = "stress" if state != "running" else "hero"
        if state == "settling":  # one active run per kind: settling gets its own lot-less row
            await db.execute("UPDATE demo_runs SET state = 'failed' WHERE state = 'requested'")
        left[state] = await db.fetchval(
            "INSERT INTO demo_runs (kind, state) VALUES ($1, $2) RETURNING id", kind, state
        )
    await runner.start(bots=2)
    rows = {
        r["id"]: r
        for r in await db.fetch("SELECT id, state, reason, finished_at, version FROM demo_runs")
    }
    for state in ("running", "settling"):
        row = rows[left[state]]
        assert (row["state"], row["reason"]) == ("aborted", "runner restarted")
        assert row["finished_at"] is not None and row["version"] == 1
    assert rows[left["verified"]]["state"] == "verified"


async def test_the_heartbeat_tells_the_api_a_runner_is_alive(
    runner: DemoRunner, redis: Redis
) -> None:
    assert await redis.get(RUNNER_ALIVE_KEY) is None
    await runner.beat()
    assert await redis.get(RUNNER_ALIVE_KEY) is not None
    assert 0 < await redis.ttl(RUNNER_ALIVE_KEY) <= 10  # gone 10 s after the runner stops


async def test_each_run_gets_a_fresh_demo_lot_through_the_lifecycle_service(
    runner: DemoRunner, db: asyncpg.Connection, clock: FakeClock
) -> None:
    cast = await runner.start(bots=1)
    lot = await runner.new_lot(cast.seller)
    row = await db.fetchrow(
        "SELECT status, is_demo, seller_id, starting_price, min_increment, reserve_price, "
        "start_time, end_time, antisnipe_threshold_s, antisnipe_extension_s FROM auctions "
        "WHERE id = $1",
        lot,
    )
    assert (row["status"], row["is_demo"], row["seller_id"]) == ("scheduled", True, cast.seller.id)
    assert (row["starting_price"], row["min_increment"], row["reserve_price"]) == (
        500_000,
        5_000,
        None,
    )
    # bidding window plus the stop before the end, which exceeds the 10 s soft close
    live = settings.demo_stress_seconds + settings.demo_stress_stop_before_end_s
    assert row["end_time"] - row["start_time"] == timedelta(seconds=live)
    assert row["start_time"] - clock.now() == timedelta(seconds=settings.demo_stress_start_delay_s)
    assert (row["antisnipe_threshold_s"], row["antisnipe_extension_s"]) == (10, 10)
    assert await db.fetchval("SELECT count(*) FROM auction_images WHERE auction_id = $1", lot) == 1
    created = await db.fetchval(
        "SELECT after->>'is_demo' FROM audit_log WHERE entity_id = $1 AND action = "
        "'auction_created'",
        str(lot),
    )
    assert created == "true"


def test_the_runner_refuses_to_start_while_demo_mode_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "demo_mode", False)
    assert refusal() == "DEMO_MODE is off: the demo runner does not start"
    monkeypatch.setattr(settings, "demo_mode", True)
    assert refusal() is None


async def test_the_backlog_sampler_reads_waiting_and_in_flight_messages(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    clock: FakeClock,
    storage: LocalStorage,
) -> None:
    """Celery's Redis broker keeps waiting messages in the list `celery` and taken-but-not-yet-
    acknowledged ones in the hash `unacked`; the eager worker drains the list, so both count."""
    auctions = AuctionService(sessions, redis, clock, RateLimiter(redis, clock), storage)
    accounts = DemoAccounts(sessions, clock, Denylist(redis, None))
    runner = DemoRunner(sessions, redis, clock, auctions, accounts, broker=redis)  # db 15 here
    await redis.rpush("celery", "a", "b", "c")
    await redis.hset("unacked", mapping={"t1": "x", "t2": "y"})
    peak: dict[str, int] = {}
    sampler = asyncio.create_task(runner._sample(peak))
    await asyncio.sleep(0.3)
    await redis.lpop("celery", 3)
    await asyncio.sleep(0.3)
    sampler.cancel()
    assert peak == {"waiting": 3, "unacked": 2}  # the peaks, not the last reading
