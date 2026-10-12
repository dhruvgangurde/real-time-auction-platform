"""Message validation, limits, strikes and slow consumers (CI-3, DC-11, NFR-5, NFR-22)."""

import asyncio
from typing import Any

import pytest

from app.api import deps
from app.core.config import Settings, settings
from app.core.ratelimit import RateLimit, RateLimiter
from app.core.redis import create_redis
from app.schemas.ws import CloseCode
from app.services import fanout
from tests.auth.conftest import FakeClock
from tests.realtime.client import Client
from tests.realtime.conftest import DEAD_REDIS_URL, MESSAGE_KINDS, Instance


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


async def test_in_memory_cap_closes_a_flooding_socket(
    node: Instance, make_bidder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI-3: a per-connection cap counted in memory, before any Redis work."""
    monkeypatch.setattr(settings, "ws_max_messages_per_s", 5)
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    for _ in range(20):
        await client.send({"type": "ping"})
    assert await client.closed() == CloseCode.POLICY_VIOLATION
    assert [e["code"] for e in client.of_type("error")] == ["RATE_LIMITED"] * 3
    assert len(client.of_type("pong")) == 5


async def test_every_message_type_is_rate_limited(
    node: Instance, make_bidder, auction, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI-3, NFR-22: all six message types have a per-user limit in shared state."""
    for kind in MESSAGE_KINDS:
        monkeypatch.setattr(settings, f"rate_limit_ws_{kind}", "1/1")
    monkeypatch.setattr(settings, "ws_max_strikes", 100)
    a, b = await auction(), await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    first = [
        {"type": "ping"},
        {"type": "subscribe", "auction_id": str(a)},
        {"type": "unsubscribe", "auction_id": str(b)},
        {"type": "authenticate", "token": token},
    ]
    second = [{**first[0]}, {**first[1], "auction_id": str(b)}, first[2], first[3]]
    for message in first + second:
        await client.send(message)
    for _ in second:
        await client.expect("error", code="RATE_LIMITED")
    assert len(client.of_type("error")) == len(second)  # the first of each type went through
    await client.send(_bid(a, 10_000, "b1"))
    await client.send(_bid(a, 10_000, "b2"))
    await client.expect("bid_accepted", request_id="b1")
    assert (await client.expect("bid_rejected", request_id="b2"))["code"] == "RATE_LIMITED"
    proxy = {"type": "set_proxy", "auction_id": str(a), "max_amount": 90_000}
    await client.send({**proxy, "request_id": "p1"})
    await client.send({**proxy, "request_id": "p2"})
    assert (await client.expect("proxy_rejected", request_id="p2"))["code"] == "RATE_LIMITED"


def test_bid_submission_has_the_strictest_default_limit() -> None:
    """CI-3: limits are stricter for bid submission than for any other message type."""
    fresh = Settings()

    def rate(kind: str) -> float:
        limit = RateLimit.parse(getattr(fresh, f"rate_limit_ws_{kind}"))
        return limit.count / limit.window_s

    bids = max(rate("place_bid"), rate("set_proxy"))
    assert rate("place_bid") == 10  # FR-44 default
    assert all(rate(k) > bids for k in MESSAGE_KINDS if k not in ("place_bid", "set_proxy"))


async def test_refused_bids_count_as_strikes(node: Instance, make_bidder, auction) -> None:
    """D12: NOT_SUBSCRIBED (and FORBIDDEN) are strikes, so a flood of them closes the socket."""
    a = await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    for i in range(settings.ws_max_strikes):
        await client.send(_bid(a, 10_000, f"n{i}"))
    assert await client.closed() == CloseCode.POLICY_VIOLATION
    assert {e["code"] for e in client.of_type("bid_rejected")} == {"NOT_SUBSCRIBED"}


async def test_strikes_decay(node: Instance, make_bidder, clock: FakeClock) -> None:
    """D12: one strike is forgiven per ws_strike_decay_s."""
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    for _ in range(settings.ws_max_strikes - 1):
        await client.send("{bad json")
        await client.expect("error", code="VALIDATION_ERROR")
    clock.advance(settings.ws_strike_decay_s + 1)
    await client.send("{bad json")  # one was forgiven, so this is not the last strike
    await client.send({"type": "ping"})
    await client.expect("pong")
    await client.send("{bad json")
    assert await client.closed() == CloseCode.POLICY_VIOLATION


async def test_limiter_outage_never_counts_as_a_strike(
    start_instance, make_bidder, auction, clock: FakeClock
) -> None:
    """D12, FR-42: with the limiter down, bids fail closed without strikes (the client retries
    with the same request_id), and harmless types still work."""
    dead = RateLimiter(create_redis(DEAD_REDIS_URL), clock)
    node = await start_instance(overrides={deps.get_limiter: dead})
    a = await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    for i in range(settings.ws_max_strikes + 2):
        await client.send(_bid(a, 10_000, f"r{i}"))
        rejected = await client.expect("bid_rejected", request_id=f"r{i}")
        assert rejected["code"] == "SERVICE_UNAVAILABLE"
    await client.send({"type": "ping"})
    await client.expect("pong")  # still open: no strikes
    assert len(client.of_type("bid_rejected")) == settings.ws_max_strikes + 2
