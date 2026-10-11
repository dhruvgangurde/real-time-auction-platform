"""OR-10: liveness and readiness."""

import pytest
from httpx import AsyncClient

from app.services import health


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("down")


async def test_live_returns_200(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_ready_200_when_dependencies_up(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "check_postgres", _ok)
    monkeypatch.setattr(health, "check_redis", _ok)
    r = await client.get("/api/v1/health/ready")
    assert r.status_code == 200


@pytest.mark.parametrize("failing", ["postgres", "redis"])
async def test_ready_503_names_failing_dependency(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    monkeypatch.setattr(health, "check_postgres", _fail if failing == "postgres" else _ok)
    monkeypatch.setattr(health, "check_redis", _fail if failing == "redis" else _ok)
    r = await client.get("/api/v1/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert failing in body["error"]["message"]
