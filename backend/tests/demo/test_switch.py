"""The demo switch (B2 Q3): every demo route answers 404, exactly as an unknown path does, while
DEMO_MODE is off; GET /api/v1/demo is how the landing learns the demo is on, and which recorded
run its hero can replay (brief 8.1 L324)."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest
from httpx import AsyncClient

from app.api.demo import router
from app.core.config import settings
from app.services.demo import DemoService
from tests.db.conftest import make_auction

NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "Not Found"}}


def _routes() -> list[tuple[str, str]]:
    return [
        (method, route.path.replace("{run_id}", str(uuid4())))
        for route in router.routes
        for method in sorted(route.methods)  # type: ignore[attr-defined]
    ]


@pytest.mark.parametrize(("method", "path"), _routes())
async def test_every_demo_route_is_404_while_off(
    api: AsyncClient, demo_service: DemoService, method: str, path: str
) -> None:
    assert settings.demo_mode is False  # the default
    response = await api.request(method, path, headers={"X-Requested-With": "test"})
    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    unknown = await api.get("/api/v1/demo-nothing-here")
    assert unknown.json() == NOT_FOUND  # indistinguishable from a path that does not exist


async def _run(db: asyncpg.Connection, state: str, finished_ago_s: int | None) -> object:
    auction = await make_auction(db, is_demo=True)
    finished = None if finished_ago_s is None else timedelta(seconds=finished_ago_s)
    await db.execute(
        "INSERT INTO demo_runs (kind, state, auction_id, finished_at) "
        "VALUES ('stress', $1, $2, now() - $3::interval)",
        state,
        auction,
        finished,
    )
    return auction


async def test_overview_when_on(
    api: AsyncClient, demo_service: DemoService, demo_on: None, db: asyncpg.Connection
) -> None:
    response = await api.get("/api/v1/demo")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "enabled",
        "auction_id",
        "next_start_ms",
        "last_closed_auction_id",
        "stress",
        "server_time_ms",
    }
    assert (body["enabled"], body["auction_id"], body["next_start_ms"]) == (True, None, None)
    assert body["last_closed_auction_id"] is None  # nothing recorded yet; no hero loop (Q9)


async def test_replay_points_at_the_latest_verified_stress_run(
    api: AsyncClient, demo_service: DemoService, demo_on: None, db: asyncpg.Connection
) -> None:
    await _run(db, "verified", 120)
    latest = await _run(db, "verified", 60)
    await _run(db, "failed", 10)  # a failed run is never replayed as proof
    await _run(db, "running", None)
    body = (await api.get("/api/v1/demo")).json()
    assert body["last_closed_auction_id"] == str(latest)


async def test_overview_is_cached_for_a_second(
    api: AsyncClient, demo_service: DemoService, demo_on: None, db: asyncpg.Connection
) -> None:
    assert (await api.get("/api/v1/demo")).json()["last_closed_auction_id"] is None
    recorded = await _run(db, "verified", 1)
    assert (await api.get("/api/v1/demo")).json()["last_closed_auction_id"] is None
    await asyncio.sleep(1.1)
    assert (await api.get("/api/v1/demo")).json()["last_closed_auction_id"] == str(recorded)


async def test_overview_counts_as_an_anonymous_read(
    api: AsyncClient, demo_service: DemoService, demo_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_anon_read_ip", "1/1")
    assert (await api.get("/api/v1/demo")).status_code == 200
    limited = await api.get("/api/v1/demo")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
