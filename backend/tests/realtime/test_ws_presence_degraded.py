"""Presence, lost-event recovery and degraded mode (FR-32, FR-42, NFR-13)."""

from typing import Any

import pytest
from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from app.services import live_events
from tests.realtime.client import Client
from tests.realtime.conftest import DEAD_REDIS_URL, Instance


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


async def test_lost_pubsub_puts_the_instance_in_degraded_mode(
    start_instance, make_bidder, auction
) -> None:
    """A pub/sub-only outage: the hub cannot reach Redis but commands still can, so subscribing
    works while bids are refused. A full Redis outage, where subscribe fails closed (D11), is
    test_redis_down_live_from_postgres_and_subscribe_fails_closed."""
    node = await start_instance(redis_url=DEAD_REDIS_URL)
    a = await auction()
    client = await Client.open(node.ws, token=(await make_bidder())[1])
    status = await client.expect("system_status")
    assert status["degraded"] is True
    await client.subscribe(a)
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


async def test_a_room_joined_while_resubscribing_is_not_lost(
    start_instance, make_bidder, auction, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N3: a socket joins a room while the reconnect's SUBSCRIBE is in flight; recovery is
    reported only once that room's channel is subscribed too."""
    node = await start_instance()
    a, late = await auction(self_outbid=False), await auction(self_outbid=False)
    watcher_id, watcher_token = await make_bidder()
    watcher = await Client.open(node.ws, token=watcher_token)
    bidder = await Client.open(node.ws, token=(await make_bidder())[1])
    await watcher.subscribe(a)
    hub = node.hub
    conn = next(iter(hub._users[watcher_id]))
    real_subscribe = PubSub.subscribe
    raced: list[bool] = []

    async def subscribe(self: PubSub, *channels: Any, **kwargs: Any) -> None:
        await real_subscribe(self, *channels, **kwargs)
        if hub.degraded and not raced:
            raced.append(True)
            await hub.join(conn, late)  # its _ref sees degraded and skips subscribing

    monkeypatch.setattr(PubSub, "subscribe", subscribe)
    await redis.execute_command("CLIENT", "KILL", "TYPE", "pubsub")
    await watcher.wait(lambda f: f.get("type") == "system_status" and f["degraded"] is False)
    assert raced
    await bidder.subscribe(late)
    await bidder.send(_bid(late, 10_000, "late"))
    await bidder.expect("bid_accepted", request_id="late")
    placed = await watcher.expect("bid_placed", sequence_no=1, auction_id=str(late))
    assert placed["amount"] == 10_000
