"""What the "Try to break it" page polls (brief 8.3): a run's status every second, from the
runner's live tally while it runs and from the stored record once it ends (failing checks first,
with their values and SQL), cached 1 s per instance with a version so an older poll can be
dropped; and the stress block of GET /demo, which says before any click whether a run can start,
one is in progress, the cooldown is on, or why it is unavailable."""

import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from app.core.config import settings
from app.services.demo import RUNNER_ALIVE_KEY, DemoService, stress_key
from app.services.demo_checks import evaluate
from tests.bidding.conftest import AuctionFactory
from tests.db.conftest import make_auction


def status_url(run_id: Any) -> str:
    return f"/api/v1/demo/stress/{run_id}"


async def _run(db: asyncpg.Connection, state: str, auction_id: UUID | None, **extra: Any) -> str:
    finished = "now()" if state in ("verified", "failed", "aborted") else "NULL"
    run_id = await db.fetchval(
        "INSERT INTO demo_runs (kind, state, auction_id, version, counters, checks, reason, "
        f"finished_at) VALUES ('stress', $1, $2, $3, $4::jsonb, $5::jsonb, $6, {finished}) "
        "RETURNING id",
        state,
        auction_id,
        extra.get("version", 0),
        json.dumps(extra.get("counters", {})),
        json.dumps(extra["checks"]) if "checks" in extra else None,
        extra.get("reason"),
    )
    return str(run_id)


async def test_an_unknown_run_is_not_found(
    api: AsyncClient, demo_service: DemoService, demo_on: None
) -> None:
    response = await api.get(status_url(uuid4()))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_a_running_run_reads_the_live_tally(
    api: AsyncClient,
    demo_service: DemoService,
    demo_on: None,
    db: asyncpg.Connection,
    redis: Redis,
    auction: AuctionFactory,
) -> None:
    lot = await auction(start_price=500_000, min_increment=5_000)  # live, with live state
    await db.execute("UPDATE auctions SET is_demo = true WHERE id = $1", lot)
    run_id = await _run(db, "running", lot, version=4)
    await redis.hset(
        stress_key(run_id),
        mapping={
            "planned": 50,
            "connected": 48,
            "sent": 120,
            "accepted": 70,
            "rejected:BID_TOO_LOW": 45,
            "rejected:SELF_OUTBID_NOT_ALLOWED": 5,
            "version": 9,
        },
    )
    body = (await api.get(status_url(run_id))).json()
    assert (body["run_id"], body["auction_id"], body["state"]) == (run_id, str(lot), "running")
    assert body["version"] == 9  # the newer of the row's and the tally's
    assert body["bots"] == {"planned": 50, "connected": 48}
    assert body["submissions"] == {
        "sent": 120,
        "accepted": 70,
        "rejected": {"BID_TOO_LOW": 45, "SELF_OUTBID_NOT_ALLOWED": 5},
    }
    assert (body["current_price"], body["sequence_no"], body["recent_bids"]) == (None, 0, [])
    assert body["end_time_ms"] > body["server_time_ms"]  # the countdown
    assert body["checks"] is None and body["watcher"]["auction_closed_received"] is None
    assert "celery_queue_peak" not in json.dumps(body)  # operator measurements stay private


async def test_a_finished_run_reads_its_record_failing_checks_first(
    api: AsyncClient, demo_service: DemoService, demo_on: None, db: asyncpg.Connection
) -> None:
    lot = await make_auction(db, is_demo=True, status="closed_sold")
    rows: dict[str, list[dict[str, Any]]] = {
        "gapless": [{"n": 2, "lowest": 1, "highest": 3}],
        "missing": [{"missing": 2}],
        "distinct": [{"n": 2, "distinct_n": 2}],
        "winner": [
            {
                "winner_id": None,
                "final_price": None,
                "expected_winner_id": None,
                "expected_price": None,
                "sequence_no": None,
            }
        ],
        "one_settlement": [{"settlements": 1}],
        "closed_once": [{"closes": 1}],
        "acked": [{"acknowledged": 3, "missing": 1}],
    }
    checks = evaluate(lot, rows)
    counters = {
        "bots": {"planned": 50, "connected": 50},
        "submissions": {"sent": 900, "accepted": 412, "rejected": {"BID_TOO_LOW": 488}},
        "watcher": {"auction_closed_received": 1, "expected": 1},
        "celery_queue_peak": 37,
        "runner_rss_peak_kb": 98_000,
    }
    run_id = await _run(db, "failed", lot, version=31, counters=counters, checks=checks)
    body = (await api.get(status_url(run_id))).json()  # no live tally: it expired
    assert body["state"] == "failed" and body["version"] == 31
    assert [c["id"] for c in body["checks"]][:2] == ["gapless", "acked"]  # the failures first
    assert [c["passed"] for c in body["checks"]] == [False, False, True, True, True, True]
    assert body["checks"][0]["value"]["missing"] == [2] and body["checks"][0]["sql"]
    assert body["submissions"]["accepted"] == 412
    assert body["watcher"] == {"auction_closed_received": 1, "expected": 1}
    assert "celery_queue_peak" not in json.dumps(body)
    assert body["cooldown_until_ms"] > body["server_time_ms"]  # just finished: "Next run in"


async def test_the_status_is_cached_for_a_second_and_counted(
    api: AsyncClient,
    demo_service: DemoService,
    demo_on: None,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = await _run(db, "requested", None)
    assert (await api.get(status_url(run_id))).json()["state"] == "requested"
    await db.execute(
        "UPDATE demo_runs SET state = 'starting', version = 1 WHERE id = $1", UUID(run_id)
    )
    assert (await api.get(status_url(run_id))).json()["state"] == "requested"  # cached
    monkeypatch.setattr(settings, "rate_limit_anon_read_ip", "1/1")
    limited = await api.get(status_url(run_id))
    assert limited.status_code == 429


async def test_the_overview_says_whether_a_run_can_start(
    demo_service: DemoService,
    demo_on: None,
    db: asyncpg.Connection,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read from the service: the endpoint's 1 s cache is tested with the overview itself."""

    async def stress() -> dict[str, Any]:
        return (await demo_service.overview())["stress"]

    assert await stress() == {  # no runner: "Unavailable"
        "enabled": True,
        "active_run_id": None,
        "cooldown_until_ms": None,
        "unavailable_reason": "runner_offline",
    }
    await redis.set(RUNNER_ALIVE_KEY, "1", ex=10)
    assert (await stress())["unavailable_reason"] is None  # "Ready"
    run_id = await _run(db, "running", None)
    assert (await stress())["active_run_id"] == run_id  # "A run is in progress. Watching it."
    await db.execute("UPDATE demo_runs SET state = 'verified', finished_at = now()")
    cooling = await stress()
    assert cooling["active_run_id"] is None and cooling["cooldown_until_ms"] is not None
    monkeypatch.setattr(settings, "demo_stress_daily_cap", 1)
    assert (await stress())["unavailable_reason"] == "daily_cap"
    monkeypatch.setattr(settings, "demo_stress_enabled", False)  # the kill switch
    assert await stress() == {
        "enabled": False,
        "active_run_id": None,
        "cooldown_until_ms": cooling["cooldown_until_ms"],
        "unavailable_reason": "disabled",
    }
