"""NFR-8 counters and the FR-81 overview's figures, on real Redis (db 15): bids accepted per
second and rejected by reason in buckets keyed by Redis TIME (DC-3), sockets per instance, queue
depth, reconciliation gaps, 5xx per minute. Metrics are rebuildable, never data of record (DC-1)."""

import asyncio

import pytest
from redis.asyncio import Redis

from app.services import metrics as metrics_module
from app.services.metrics import Metrics


async def redis_second(redis: Redis) -> int:
    return int((await redis.time())[0])


async def test_a_bid_bump_lands_in_the_second_redis_says(redis: Redis) -> None:
    m = Metrics(redis)
    before = await redis_second(redis)
    await m.bump_bids(accepted=2)
    await m.bump_bids(rejected="BID_TOO_LOW")
    after = await redis_second(redis)
    keys = [k async for k in redis.scan_iter(match="metrics:bids:*")]
    assert len(keys) in (1, 2)  # the same second, or the next one
    assert all(before <= int(k.rsplit(":", 1)[1]) <= after for k in keys)
    assert 0 < await redis.ttl(keys[0]) <= 120  # housekeeping only


async def test_the_last_minute_of_bids(redis: Redis) -> None:
    m = Metrics(redis)
    now = await redis_second(redis)
    await redis.hset(f"metrics:bids:{now - 2}", mapping={"accepted": 3, "rejected:BID_TOO_LOW": 1})
    await redis.hset(
        f"metrics:bids:{now - 30}", mapping={"accepted": 5, "rejected:RATE_LIMITED": 1}
    )
    await redis.hset(f"metrics:bids:{now - 61}", mapping={"accepted": 100})  # outside the minute
    bids = await m.bids()
    assert len(bids["per_second"]) == 60
    assert bids["per_second"][-3] == 3 and bids["per_second"][-31] == 5
    assert bids["accepted_60s"] == 8
    assert bids["rejected_60s"] == {"BID_TOO_LOW": 1, "RATE_LIMITED": 1}
    assert bids["rejection_rate"] == pytest.approx(2 / 10)
    assert bids["max_per_second"] == 5


async def test_no_bids_is_a_rate_of_zero_not_an_error(redis: Redis) -> None:
    bids = await Metrics(redis).bids()
    assert (bids["accepted_60s"], bids["rejected_60s"], bids["rejection_rate"]) == (0, {}, 0.0)


async def test_server_errors_per_minute(redis: Redis) -> None:
    m = Metrics(redis)
    await m.bump_5xx()
    await m.bump_5xx()
    minutes = (await m.errors())["per_minute"]
    assert len(minutes) == 10 and sum(minutes) == 2 and minutes[-1] in (0, 2)


async def test_each_instance_reports_its_own_sockets_and_expires(redis: Redis) -> None:
    m = Metrics(redis)
    await m.publish_instance("api", sockets=3)
    await m.publish_instance("api-2", sockets=2)
    assert await m.instances() == [
        {"instance": "api", "sockets": 3, "dropped": 0},
        {"instance": "api-2", "sockets": 2, "dropped": 0},
    ]
    assert 0 < await redis.ttl("metrics:instance:api") <= 15  # a stopped instance drops out


async def test_queue_depth_counts_the_broker_and_the_outboxes_not_dead_letters(
    redis: Redis,
) -> None:
    await redis.rpush("celery", "a", "b", "c")
    await redis.hset("unacked", mapping={"t1": "x"})
    await redis.xadd("auction:a1:bids", {"k": "1"})
    await redis.xadd("auction:a1:bids", {"k": "2"})
    await redis.xadd("auction:a2:bids", {"k": "1"})
    await redis.xadd("auction:a3:bids:dead", {"k": "1"})  # kept for an operator, not a backlog
    queues = await Metrics(redis, broker=redis).queues()
    assert queues == {"celery_waiting": 3, "celery_unacked": 1, "outbox_backlog": 3}


async def test_reconciliation_gaps_add_up_and_the_last_run_is_kept(redis: Redis) -> None:
    m = Metrics(redis)
    await m.record_reconciliation(checked=4, gaps=1, regressions=0, failed=0)
    await m.record_reconciliation(checked=4, gaps=2, regressions=1, failed=0)
    r = await m.reconciliation()
    assert r["gaps_total"] == 3
    assert {k: r["last_run"][k] for k in ("checked", "gaps", "regressions", "failed")} == {
        "checked": 4,
        "gaps": 2,
        "regressions": 1,
        "failed": 0,
    }
    assert r["last_run"]["at_ms"] > 0


async def test_fire_and_forget_holds_its_tasks_and_drops_over_the_cap(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metrics_module, "MAX_PENDING", 3)
    m = Metrics(redis)
    gate = asyncio.Event()

    async def slow() -> None:
        await gate.wait()

    for _ in range(5):
        m.spawn(slow())
    assert len(m.pending) == 3  # held, so none is garbage-collected mid-flight
    assert m.dropped == 2
    gate.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(m.pending) == 0


async def test_a_failing_bump_is_logged_and_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)  # nothing listens
    m = Metrics(broken)
    m.record_bid(accepted=1, code=None)
    while m.pending:
        await asyncio.sleep(0.05)
    assert any("metrics bump failed" in r.message for r in caplog.records)
    await broken.aclose()
