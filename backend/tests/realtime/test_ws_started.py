"""auction_started (brief 12.1): published once per activation through publish_started_once.lua,
also from the repair pass, and delivered by every instance to its subscribers."""

import asyncio
from typing import Any

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services import scheduler as scheduler_module
from app.services.live_state import outbox_key, proxies_key, state_key, to_ms
from app.services.scheduler import STARTED_SENT_TTL_S, Scheduler, started_sent_key
from tests.realtime.client import Client
from tests.realtime.conftest import Instance

KEYS = {
    "type",
    "auction_id",
    "status",
    "start_time_ms",
    "end_time_ms",
    "start_price",
    "min_increment",
    "min_next",
    "sequence_no",
    "extension_count",
    "server_time_ms",
}


async def _quiet(clients: list[Client], seconds: float = 0.3) -> None:
    """Give a duplicate time to arrive before counting."""
    await asyncio.sleep(seconds)
    for c in clients:
        assert not c._reader.done(), c.frames


async def test_auction_started_once_on_both_instances(
    instances: tuple[Instance, Instance],
    make_bidder,
    auction,
    db,
    redis: Redis,
    sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = await auction(status="scheduled", starts_in_ms=-1_000, start_price=9_000)
    b = await auction(status="scheduled", starts_in_ms=-1_000)
    clients = []
    for instance in instances:  # one subscriber per instance, subscribed while scheduled
        _, token = await make_bidder()
        client = await Client.open(instance.ws, token)
        for auction_id in (a, b):
            assert (await client.subscribe(auction_id))["snapshot"]["status"] == "scheduled"
        clients.append(client)

    # b's first publish hits a Redis error after its commit: activation must not fail
    script = Scheduler(redis, sessions)._publish_started
    calls: list[Any] = []

    async def flaky(*, keys: list[str], args: list[Any]) -> Any:
        calls.append(keys[0])
        if keys[0] == started_sent_key(b) and calls.count(keys[0]) == 1:
            raise RedisError("lost before the publish")
        return await script(keys=keys, args=args)

    scheduler = Scheduler(redis, sessions)
    monkeypatch.setattr(scheduler, "_publish_started", flaky)
    assert {a, b} <= set(await scheduler.activate_due())  # the shared database has others

    started = [await c.expect("auction_started", auction_id=str(a)) for c in clients]
    row = await db.fetchrow("SELECT start_time, end_time FROM auctions WHERE id = $1", a)
    for e in started:
        assert set(e) == KEYS
        assert (e["status"], e["sequence_no"], e["extension_count"]) == ("live", 0, 0)
        assert e["start_price"] == e["min_next"] == 9_000 and e["min_increment"] == 500
        assert e["start_time_ms"] == to_ms(row["start_time"])
        assert e["end_time_ms"] == to_ms(row["end_time"])
    assert not any(
        c.of_type("auction_started") and f["auction_id"] == str(b)
        for c in clients
        for f in c.of_type("auction_started")
    )

    # a repeated activation, a direct repeat and the repair pass send nothing more for a;
    # the repair publishes b exactly once, and a second repair nothing
    assert not {a, b} & set(await scheduler.activate_due())
    assert await scheduler._announce(a) is False
    first, second = (await scheduler.run()).announced, (await scheduler.run()).announced
    assert b in first and a not in first
    assert not {a, b} & set(second)
    for c in clients:
        await c.expect("auction_started", auction_id=str(b))
    await _quiet(clients)
    for c in clients:
        counts = {
            str(x): sum(f["auction_id"] == str(x) for f in c.of_type("auction_started"))
            for x in (a, b)
        }
        assert counts == {str(a): 1, str(b): 1}, c.frames

    # the marker lives outside auction:{id}: and survives live-state cleanup
    await redis.hset(state_key(a), "status", "closed_no_sale")
    await scheduler._cleanup(keys=[state_key(a), outbox_key(a), proxies_key(a)])
    assert not await redis.exists(state_key(a))
    assert 0 < await redis.ttl(started_sent_key(a)) <= STARTED_SENT_TTL_S
    for c in clients:
        await c.close()


async def test_repair_window_and_non_live(auction, db, redis: Redis, sessions) -> None:
    """Only live auctions that started within the window are repaired; a cancelled one never."""
    old = await auction(starts_in_ms=-2 * 3_600_000)  # live, started two hours ago
    recent = await auction(starts_in_ms=-60_000)
    cancelled = await auction(starts_in_ms=-60_000)
    await db.execute("UPDATE auctions SET status = 'cancelled' WHERE id = $1", cancelled)
    scheduler = Scheduler(redis, sessions)
    repaired = await scheduler.announce_missing()  # the shared database has other auctions
    assert recent in repaired and old not in repaired and cancelled not in repaired
    assert not await redis.exists(started_sent_key(old))
    assert await scheduler._announce(cancelled) is False


def test_marker_key_is_outside_the_live_state_prefix() -> None:
    assert not started_sent_key(scheduler_module.UUID(int=1)).startswith("auction:")
