"""NFR-8: WebSocket connections per instance, each instance publishing its own count with a
short expiry, so the overview sees both instances and a stopped one drops out."""

import asyncio

import pytest
from redis.asyncio import Redis

from app.core.config import settings
from app.services.metrics import Metrics
from tests.realtime.client import Client


async def _instances(redis: Redis, want: list[dict]) -> list[dict]:
    seen: list[dict] = []
    for _ in range(60):
        seen = [
            {k: r[k] for k in ("instance", "sockets")} for r in await Metrics(redis).instances()
        ]
        if seen == want:
            return seen
        await asyncio.sleep(0.05)
    raise AssertionError(f"instances {seen}")


async def test_sockets_are_counted_per_instance(
    start_instance, make_bidder, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_publish_interval_s", 0.1)
    a_node = await start_instance(instance_name="api")
    b_node = await start_instance(instance_name="api-2")
    tokens = [(await make_bidder())[1] for _ in range(3)]
    on_a = [await Client.open(a_node.ws, token=t) for t in tokens[:2]]
    await Client.open(b_node.ws, token=tokens[2])
    await _instances(
        redis, [{"instance": "api", "sockets": 2}, {"instance": "api-2", "sockets": 1}]
    )
    await on_a[0].close()
    await _instances(
        redis, [{"instance": "api", "sockets": 1}, {"instance": "api-2", "sockets": 1}]
    )
