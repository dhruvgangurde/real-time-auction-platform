"""Starting "Try to break it" (brief 12.4; anonymous by the 2026-10-13 decision, a recorded
deviation from NFR-21). No cookie, no session and no body, but X-Requested-With is required, so a
plain cross-site form cannot start a run. The decision order: a run already active is joined at no
cost; then the kill switch, a live runner and the daily cap; then the cooldown after the last run;
only then the per-address limit (3 an hour), which therefore counts runs actually created."""

import asyncpg
import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from app.core.config import settings
from app.services.demo import RUNNER_ALIVE_KEY, DemoService

START = "/api/v1/demo/stress"
XRW = {"X-Requested-With": "test"}


@pytest.fixture
async def runner_alive(redis: Redis) -> None:
    await redis.set(RUNNER_ALIVE_KEY, "1", ex=10)


async def _runs(db: asyncpg.Connection) -> list[tuple[str, str]]:
    rows = await db.fetch("SELECT id, state FROM demo_runs ORDER BY requested_at")
    return [(str(r["id"]), r["state"]) for r in rows]


async def _finish(db: asyncpg.Connection, run_id: str, ago_s: float) -> None:
    await db.execute(
        "UPDATE demo_runs SET state = 'verified', "
        "finished_at = now() - make_interval(secs => $2) WHERE id = $1::uuid",
        run_id,
        float(ago_s),
    )


async def _ip_counted(redis: Redis) -> list[str]:
    return await redis.keys("rl:demo_stress_ip:*")


async def test_the_header_is_required(
    api: AsyncClient, demo_service: DemoService, demo_on: None, runner_alive: None, db
) -> None:
    refused = await api.post(START)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "FORBIDDEN"
    assert await _runs(db) == []


async def test_no_body_or_an_empty_object_only(
    api: AsyncClient, demo_service: DemoService, demo_on: None, runner_alive: None, db
) -> None:
    refused = await api.post(
        START, headers=XRW, json={"bots": 500}
    )  # nothing is chosen by a visitor
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await _runs(db) == []
    assert (await api.post(START, headers=XRW, json={})).status_code == 202


async def test_one_run_at_a_time_and_a_second_start_joins_it(
    api: AsyncClient, demo_service: DemoService, demo_on: None, runner_alive: None, db
) -> None:
    first = await api.post(START, headers=XRW)
    assert first.status_code == 202
    started = first.json()
    assert (started["joined"], started["state"]) == (False, "requested")
    second = await api.post(START, headers=XRW)
    assert second.status_code == 200
    assert second.json() == {**started, "joined": True}  # "A run is in progress. Watching it."
    assert await _runs(db) == [(started["run_id"], "requested")]


@pytest.mark.parametrize("reason", ["disabled", "runner_offline", "daily_cap"])
async def test_unavailable_says_why_and_costs_nothing(
    api: AsyncClient,
    demo_service: DemoService,
    demo_on: None,
    db: asyncpg.Connection,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    if reason != "runner_offline":
        await redis.set(RUNNER_ALIVE_KEY, "1", ex=10)
    if reason == "disabled":
        monkeypatch.setattr(settings, "demo_stress_enabled", False)  # the kill switch
    if reason == "daily_cap":
        monkeypatch.setattr(settings, "demo_stress_daily_cap", 2)
        for _ in range(2):  # two runs today, long finished: no cooldown in the way
            run = await db.fetchval(
                "INSERT INTO demo_runs (kind, state) VALUES ('stress', 'requested') RETURNING id"
            )
            await _finish(db, str(run), 3600)
    before = await _runs(db)
    refused = await api.post(START, headers=XRW)
    assert refused.status_code == 503
    error = refused.json()["error"]
    assert (error["code"], error["fields"]) == ("DEMO_UNAVAILABLE", {"reason": reason})
    assert await _runs(db) == before
    assert await _ip_counted(redis) == []


async def test_the_cooldown_says_when_and_costs_nothing(
    api: AsyncClient,
    demo_service: DemoService,
    demo_on: None,
    runner_alive: None,
    db: asyncpg.Connection,
    redis: Redis,
) -> None:
    run = await db.fetchval(
        "INSERT INTO demo_runs (kind, state) VALUES ('stress', 'requested') RETURNING id"
    )
    await _finish(db, str(run), 10)  # finished 10 s ago; the cooldown is 60 s
    refused = await api.post(START, headers=XRW)
    assert refused.status_code == 429
    error = refused.json()["error"]
    assert error["code"] == "DEMO_COOLING_DOWN"
    assert 45 <= int(refused.headers["retry-after"]) <= 50  # about 50 s, measured under load
    finished = await db.fetchval(
        "SELECT floor(extract(epoch FROM finished_at) * 1000)::bigint FROM demo_runs "
        "WHERE id = $1",  # floored, as to_ms does (a cast would round)
        run,
    )
    assert int(error["fields"]["retry_at_ms"]) == finished + 60_000  # "Next run in 0:50"
    assert await _ip_counted(redis) == []
    await _finish(db, str(run), 61)
    assert (await api.post(START, headers=XRW)).status_code == 202


async def test_the_address_limit_counts_runs_created_never_joins(
    api: AsyncClient,
    demo_service: DemoService,
    demo_on: None,
    runner_alive: None,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_demo_stress_ip", "2/3600")
    first = (await api.post(START, headers=XRW)).json()  # counted: 1
    assert (await api.post(START, headers=XRW)).status_code == 200  # a join: not counted
    await _finish(db, first["run_id"], 120)
    second = await api.post(START, headers=XRW)  # counted: 2
    assert second.status_code == 202
    await _finish(db, second.json()["run_id"], 120)
    third = await api.post(START, headers=XRW)
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "RATE_LIMITED"
