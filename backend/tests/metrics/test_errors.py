"""FR-81's error rate: every 5xx response is counted, per minute; other statuses are not."""

import asyncio

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from app.services.metrics import Metrics
from tests.conftest import _app


async def test_a_server_error_is_counted_and_a_not_found_is_not(
    client: AsyncClient, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_app.state, "metrics", Metrics(redis), raising=False)
    assert (await client.get("/api/v1/nothing-here")).status_code == 404
    assert (await client.get("/_test/boom")).status_code == 500
    for _ in range(40):
        if sum((await Metrics(redis).errors())["per_minute"]) == 1:
            break
        await asyncio.sleep(0.05)
    assert sum((await Metrics(redis).errors())["per_minute"]) == 1
