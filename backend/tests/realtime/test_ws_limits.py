"""Message validation, limits and slow consumers (CI-3, DC-11, NFR-5, NFR-22)."""

import asyncio
from typing import Any

import pytest

from app.core.config import settings
from app.schemas.ws import CloseCode
from app.services import fanout
from tests.realtime.client import Client
from tests.realtime.conftest import Instance


@pytest.fixture
async def node(start_instance) -> Instance:
    return await start_instance()


def _bid(auction_id, amount: int, rid: str) -> dict:
    return {"type": "place_bid", "auction_id": str(auction_id), "amount": amount, "request_id": rid}


async def test_invalid_messages_get_errors_then_the_socket_closes(
    node: Instance, make_bidder
) -> None:
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.send("x" * (settings.ws_max_message_bytes + 1))
    assert (await client.expect("error"))["code"] == "MESSAGE_TOO_LARGE"
    await client.send({"type": "teleport"})
    assert (await client.expect("error"))["code"] == "UNKNOWN_TYPE"
    await client.send("{not json")
    assert (await client.expect("error"))["code"] == "VALIDATION_ERROR"
    assert await client.closed() == CloseCode.POLICY_VIOLATION  # third strike


async def test_schema_limits_are_enforced(node: Instance, make_bidder, auction) -> None:
    a = await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.subscribe(a)
    # two of the three allowed strikes: extra field, amount above 2^53-1
    for bad in ({**_bid(a, 10_000, "ok"), "extra": 1}, _bid(a, 2**53, "big")):
        await client.send(bad)
        error = await client.wait(lambda f: f.get("type") == "error")
        assert error["code"] == "VALIDATION_ERROR"
    await client.send({"type": "ping"})
    await client.expect("pong")  # still connected


async def test_bid_rate_limit(
    node: Instance, make_bidder, auction, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-44: per-user bid rate limit, shared across instances through Redis."""
    monkeypatch.setattr(settings, "rate_limit_ws_place_bid", "2/1")
    a = await auction(self_outbid=False)
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.subscribe(a)
    for i, amount in enumerate((10_000, 10_500, 11_000)):
        await client.send(_bid(a, amount, f"r{i}"))
    third = await client.expect("bid_rejected", request_id="r2")
    assert third["code"] == "RATE_LIMITED"
    assert (await client.expect("bid_accepted", request_id="r1"))["sequence_no"] == 2


async def test_subscription_limit(node: Instance, make_bidder, auction) -> None:
    rooms = [await auction() for _ in range(settings.ws_max_subscriptions + 1)]
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    for room in rooms[:-1]:
        await client.subscribe(room)
    await client.send({"type": "subscribe", "auction_id": str(rooms[-1])})
    assert (await client.expect("error"))["code"] == "TOO_MANY_SUBSCRIPTIONS"


async def test_per_user_connection_limit_across_instances(
    instances: tuple[Instance, Instance], make_bidder
) -> None:
    a_node, b_node = instances
    _, token = await make_bidder()
    clients = [
        await Client.open(n.ws, token=token)
        for n in [a_node, b_node, a_node][: settings.ws_max_connections_per_user]
    ]
    extra = await Client.open(b_node.ws)
    await extra.send({"type": "authenticate", "token": token})
    assert await extra.closed() == CloseCode.TOO_MANY_CONNECTIONS
    await clients[0].send({"type": "ping"})
    await clients[0].expect("pong")


async def test_slow_consumer_is_closed_and_others_keep_receiving(
    node: Instance, make_bidder, auction, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-5: a client that stops reading overflows its own queue and is closed (4008)."""
    a = await auction(start_price=10_000, min_increment=1, self_outbid=False)
    slow_id, slow_token = await make_bidder()
    _, fast_token = await make_bidder()
    _, bidder_token = await make_bidder()
    stalled = asyncio.Event()
    real_send = fanout.Connection.send_frame

    async def send_frame(self: fanout.Connection, message: dict[str, Any]) -> None:
        if self.user.id == slow_id and stalled.is_set():
            await asyncio.Event().wait()  # this client never reads again
        await real_send(self, message)

    monkeypatch.setattr(fanout.Connection, "send_frame", send_frame)
    slow = await Client.open(node.ws, token=slow_token)
    fast = await Client.open(node.ws, token=fast_token)
    bidder = await Client.open(node.ws, token=bidder_token)
    for c in (slow, fast, bidder):
        await c.subscribe(a)
    stalled.set()

    bids = settings.ws_send_queue_size + 10
    for i in range(bids):
        await bidder.send(_bid(a, 10_000 + i, f"r{i}"))
        await bidder.expect("bid_accepted", request_id=f"r{i}")
    assert await slow.closed() == CloseCode.SLOW_CONSUMER
    await fast.expect("bid_placed", sequence_no=bids)
    assert len(fast.of_type("bid_placed")) == bids


async def test_idle_connection_is_closed(
    node: Instance, make_bidder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ws_idle_timeout_s", 0.4)
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    assert await client.closed() == CloseCode.IDLE_TIMEOUT
