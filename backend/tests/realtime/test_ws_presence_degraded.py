"""Presence, lost-event recovery and degraded mode (FR-32, FR-42, NFR-13)."""

import pytest
from redis.asyncio import Redis

from app.services import live_events
from tests.realtime.client import Client
from tests.realtime.conftest import Instance


def _bid(auction_id, amount: int, rid: str) -> dict:
    return {"type": "place_bid", "auction_id": str(auction_id), "amount": amount, "request_id": rid}


async def test_presence_counts_participants_across_instances(
    instances: tuple[Instance, Instance], make_bidder, auction
) -> None:
    a_node, b_node = instances
    a = await auction()
    one = await Client.open(a_node.ws, token=(await make_bidder())[1])
    two = await Client.open(b_node.ws, token=(await make_bidder())[1])
    await one.subscribe(a)
    await two.subscribe(a)
    presence = await one.wait(lambda f: f.get("type") == "presence" and f["participants"] == 2)
    assert presence["sequence_no"] == 0 and presence["end_time_ms"] > 0
    await two.close()
    await one.wait(lambda f: f.get("type") == "presence" and f["participants"] == 1)


async def test_lost_publish_is_detected_through_presence(
    start_instance, make_bidder, auction, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pub/sub is at-most-once: a quiet room learns of a missed bid from the next presence tick
    (client rule: presence.sequence_no > last applied means refetch /live)."""
    node = await start_instance()
    a = await auction()
    watcher = await Client.open(node.ws, token=(await make_bidder())[1])
    bidder = await Client.open(node.ws, token=(await make_bidder())[1])
    await watcher.subscribe(a)
    await bidder.subscribe(a)

    async def dropped(*_: object) -> None:
        return None

    monkeypatch.setattr(live_events, "publish_bid_events", dropped)
    await bidder.send(_bid(a, 10_000, "lost"))
    await bidder.expect("bid_accepted", request_id="lost")
    tick = await watcher.wait(
        lambda f: f.get("type") == "presence" and f["sequence_no"] == 1, timeout=3
    )
    assert watcher.of_type("bid_placed") == []
    last_applied = 0
    assert tick["sequence_no"] > last_applied  # => refetch /live


async def test_unreachable_redis_puts_the_instance_in_degraded_mode(
    start_instance, make_bidder, auction
) -> None:
    node = await start_instance(redis_url="redis://redis:6399/15")
    a = await auction()
    client = await Client.open(node.ws, token=(await make_bidder())[1])
    status = await client.expect("system_status")
    assert status["degraded"] is True
    await client.subscribe(a)  # viewing still works (NFR-13)
    await client.send(_bid(a, 10_000, "d1"))
    rejected = await client.expect("bid_rejected", request_id="d1")
    assert rejected["code"] == "SERVICE_UNAVAILABLE"


async def test_reconnect_resubscribes_before_reporting_recovery(
    start_instance, make_bidder, auction, redis: Redis
) -> None:
    node = await start_instance()
    a = await auction()
    watcher = await Client.open(node.ws, token=(await make_bidder())[1])
    bidder = await Client.open(node.ws, token=(await make_bidder())[1])
    await watcher.subscribe(a)
    await bidder.subscribe(a)

    await redis.execute_command("CLIENT", "KILL", "TYPE", "pubsub")
    await watcher.wait(lambda f: f.get("type") == "system_status" and f["degraded"] is True)
    recovered = await watcher.wait(
        lambda f: f.get("type") == "system_status" and f["degraded"] is False
    )
    assert recovered  # client rule: on degraded:false refetch /live
    await bidder.send(_bid(a, 10_000, "after"))
    await bidder.expect("bid_accepted", request_id="after")
    await watcher.expect("bid_placed", sequence_no=1)  # the room channel was resubscribed
