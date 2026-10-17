"""A whole "Try to break it" run, end to end on two real uvicorn instances (brief 8.3, 12.4): the
runner claims the requested run, creates a fresh demo lot, signs the bots and the watcher in, and
the bots bid on both instances once the scheduler opens the lot; the real close task settles it,
the six ledger checks pass in Postgres, the watcher saw auction_closed once, and the record is
stored. A stand-in loop does beat's work (persist, schedule, close) every 0.2 s. Smaller than a
real run (5 bots, 5 bids each, a 10 s window) so it takes about half a minute."""

import asyncio
import contextlib
import json
from typing import Any

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import SystemClock
from app.core.config import settings
from app.core.ratelimit import RateLimiter
from app.core.storage import LocalStorage
from app.services.auctions import AuctionService
from app.services.closing import Closer
from app.services.demo_accounts import DemoAccounts
from app.services.denylist import Denylist
from app.services.ledger import persist_all_pending
from app.services.scheduler import Scheduler
from app.workers.demo_runner import DemoRunner
from tests.auth.conftest import FakeClock
from tests.realtime.conftest import Instance
from tests.scheduler.conftest import quiet  # noqa: F401  autouse: no other test's open lots


@pytest.fixture
def small_run(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "demo_mode": True,
        "demo_stress_bots": 5,
        "demo_stress_bids_per_bot": 5,
        "demo_stress_seconds": 10,
        "demo_stress_stop_before_end_s": 11,
        "demo_stress_start_delay_s": 1,
        "ws_send_queue_size": 256,  # every bot reads every bid_placed
        "rate_limit_auction_create": "1000/3600",
    }.items():
        monkeypatch.setattr(settings, name, value)


async def _beat(redis: Redis, sessions: async_sessionmaker[AsyncSession], db: Any) -> None:
    """What beat and the worker do in production: persist, schedule, close."""
    scheduler, closer = Scheduler(redis, sessions), Closer(redis, sessions)
    while True:
        with contextlib.suppress(Exception):
            await persist_all_pending(redis, sessions)
            await scheduler.run()
            for row in await db.fetch(
                "SELECT id FROM auctions WHERE status = 'closing' AND is_demo"
            ):
                await closer.close(row["id"])
        await asyncio.sleep(0.2)


async def test_a_stress_run_from_request_to_verified(
    small_run: None,
    instances: tuple[Instance, Instance],
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    clock: FakeClock,  # the instances' authentication clock: the bots' tokens use it too
    db: asyncpg.Connection,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "demo_ws_urls", ",".join(node.ws for node in instances))
    await db.execute("DELETE FROM demo_runs")
    real = SystemClock()  # the lot's timing is real time, as the scheduler's is
    runner = DemoRunner(
        sessions,
        redis,
        real,
        AuctionService(
            sessions, redis, real, RateLimiter(redis, real), LocalStorage(str(tmp_path))
        ),
        DemoAccounts(sessions, clock, Denylist(redis, None)),
    )
    cast = await runner.start(settings.demo_stress_bots)
    requested = await db.fetchval("INSERT INTO demo_runs (kind) VALUES ('stress') RETURNING id")
    beat = asyncio.create_task(_beat(redis, sessions, db))
    try:
        run_id = await runner.next_run()
        assert run_id == requested
        state = await asyncio.wait_for(runner.run_stress(run_id, cast), 120)
    finally:
        beat.cancel()
    row = await db.fetchrow("SELECT * FROM demo_runs WHERE id = $1", run_id)
    checks = json.loads(row["checks"])
    assert state == "verified", checks
    assert (row["state"], row["reason"]) == ("verified", None)
    assert row["finished_at"] is not None and row["version"] > 0
    assert [c["id"] for c in checks if c["passed"]] == [
        "gapless", "distinct", "winner", "one_settlement", "closed_once", "acked",
    ]  # fmt: skip
    counters = json.loads(row["counters"])
    assert counters["bots"] == {"planned": 5, "connected": 5}
    sent, accepted = counters["submissions"]["sent"], counters["submissions"]["accepted"]
    assert 0 < accepted <= sent <= 5 * 5
    rejected = counters["submissions"]["rejected"]
    assert set(rejected) <= {"BID_TOO_LOW", "SELF_OUTBID_NOT_ALLOWED"}  # races, never limits
    assert accepted + sum(rejected.values()) == sent
    assert counters["watcher"] == {"auction_closed_received": 1, "expected": 1}
    assert counters["runner_rss_peak_kb"] > 0
    assert counters["celery_queue_peak"] is None  # no broker here; the stack measures it
    lot = row["auction_id"]
    auction = await db.fetchrow(
        "SELECT status, is_demo, extension_count, bid_count FROM auctions WHERE id = $1", lot
    )
    assert tuple(auction) == ("closed_sold", True, 0, accepted)  # no extension: bots stop early
    assert await db.fetchval("SELECT status FROM settlements WHERE auction_id = $1", lot) == "demo"
    assert len(json.loads(row["acks"])) == accepted
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE auction_id = $1", lot) == 0
    live_sessions = await db.fetchval(
        "SELECT count(*) FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE u.is_demo AND s.revoked_at IS NULL"
    )
    assert live_sessions == 0  # signed out after the run
