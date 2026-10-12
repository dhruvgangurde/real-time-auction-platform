"""Bidding over the WebSocket (FR-33, FR-34, FR-40, FR-41, FR-50, FR-51, FR-54, NFR-27, NFR-32)."""

import logging

import pytest
from redis.asyncio import Redis

from app.services.live_events import alias
from app.services.live_state import state_key
from tests.auth.conftest import PASSWORD
from tests.realtime.client import Client
from tests.realtime.conftest import Instance

SECRET_MAX = 777_001


@pytest.fixture
async def node(start_instance) -> Instance:
    return await start_instance()


def _bid(auction_id, amount: int, rid: str) -> dict:
    return {"type": "place_bid", "auction_id": str(auction_id), "amount": amount, "request_id": rid}


async def test_ack_precedes_own_room_event_and_rejection_is_private(
    node: Instance, make_bidder, auction
) -> None:
    a = await auction(start_price=10_000, min_increment=500)
    _, alice_token = await make_bidder()
    _, watcher_token = await make_bidder()
    alice = await Client.open(node.ws, token=alice_token)
    watcher = await Client.open(node.ws, token=watcher_token)
    sub = await alice.subscribe(a)
    assert sub["your_alias"].startswith("Bidder-") and sub["snapshot"]["sequence_no"] == 0
    await watcher.subscribe(a)

    await alice.send(_bid(a, 10_000, "r1"))
    ack = await alice.expect("bid_accepted", request_id="r1")
    placed = await alice.expect("bid_placed", sequence_no=1)
    assert alice.frames.index(ack) < alice.frames.index(placed)  # FR-40: ack first
    seen = await watcher.expect("bid_placed", sequence_no=1)
    assert seen["bidder_alias"] == sub["your_alias"] and seen["bid_type"] == "manual"
    assert "server_time_ms" in seen and "end_time_ms" in seen

    await alice.send(_bid(a, 10_500, "r2"))  # self-outbid policy is on by default
    rejected = await alice.expect("bid_rejected", request_id="r2")
    assert rejected["code"] == "SELF_OUTBID_NOT_ALLOWED"
    low = await watcher_low(watcher, a)
    assert low["code"] == "BID_TOO_LOW" and low["min_next"] == 10_500
    assert low["current_highest"] == 10_000
    assert not [f for f in alice.frames if f.get("request_id") == "w1"]  # FR-41: submitter only
    assert not [
        f for f in watcher.frames if f.get("type") == "bid_rejected" and f["request_id"] == "r2"
    ]


async def watcher_low(watcher: Client, a) -> dict:
    await watcher.send(_bid(a, 10_100, "w1"))
    return await watcher.expect("bid_rejected", request_id="w1")


async def test_automatic_bids_are_separate_events_and_maxima_never_leak(
    node: Instance, make_bidder, auction, caplog: pytest.LogCaptureFixture
) -> None:
    a = await auction(start_price=10_000, min_increment=100, self_outbid=False)
    alice_id, alice_token = await make_bidder()
    bob_id, bob_token = await make_bidder()
    _, watcher_token = await make_bidder()
    alice = await Client.open(node.ws, token=alice_token)
    bob = await Client.open(node.ws, token=bob_token)
    watcher = await Client.open(node.ws, token=watcher_token)
    for c in (alice, bob, watcher):
        await c.subscribe(a)

    with caplog.at_level(logging.DEBUG):
        await alice.send(
            {
                "type": "set_proxy",
                "auction_id": str(a),
                "max_amount": SECRET_MAX,
                "request_id": "p1",
            }
        )
        armed = await alice.expect("proxy_armed", request_id="p1")
        assert armed["max_amount"] == SECRET_MAX and armed["active"] is True  # owner only
        await bob.send(_bid(a, 20_000, "b1"))
        await bob.expect("bid_accepted", request_id="b1")
        for c in (alice, bob, watcher):
            manual = await c.expect("bid_placed", sequence_no=2)
            auto = await c.expect("bid_placed", sequence_no=3)
            assert manual["bid_type"] == "manual" and manual["bidder_alias"] == alias(a, bob_id)
            assert auto["bid_type"] == "automatic" and auto["amount"] == 20_100
            assert auto["bidder_alias"] == alias(a, alice_id)
        await bob.expect("outbid_notice", sequence_no=3, current_price=20_100)

    # FR-51, NFR-27: the leading maximum and user ids appear in no frame anyone else received
    for c in (bob, watcher):
        text = "".join(c.raw)
        assert str(SECRET_MAX) not in text
        assert str(alice_id) not in text and str(bob_id) not in "".join(watcher.raw)
    assert str(SECRET_MAX) not in caplog.text
    for token in (alice_token, bob_token, watcher_token):
        assert token not in caplog.text  # NFR-32 logs carry no tokens


async def test_outbid_notice_goes_once_to_each_displaced_bidder(
    node: Instance, make_bidder, auction
) -> None:
    """({previous_leader} ∪ exhausted) − new leader, each once (FR-51)."""
    a = await auction(start_price=10_000, min_increment=100, self_outbid=False)
    clients = {}
    for name in ("alice", "bob", "carol"):
        _, token = await make_bidder()
        clients[name] = await Client.open(node.ws, token=token)
        await clients[name].subscribe(a)
    alice, bob, carol = clients["alice"], clients["bob"], clients["carol"]

    await alice.send(_bid(a, 10_000, "a1"))
    await alice.expect("bid_accepted")
    await bob.send(
        {"type": "set_proxy", "auction_id": str(a), "max_amount": 15_000, "request_id": "p1"}
    )
    await bob.expect("proxy_armed")  # bob leads at 10_100; alice displaced
    await alice.expect("outbid_notice", sequence_no=2)
    await carol.send(_bid(a, 20_000, "c1"))  # exhausts bob's maximum
    await carol.expect("bid_accepted")
    await bob.expect("outbid_notice", sequence_no=3, current_price=20_000)
    await carol.expect("bid_placed", sequence_no=3)
    await alice.expect("bid_placed", sequence_no=3)
    assert len(bob.of_type("outbid_notice")) == 1
    assert len(alice.of_type("outbid_notice")) == 1  # not re-notified for seq 3
    assert carol.of_type("outbid_notice") == []


async def test_timer_extended_marks_the_final_extension(
    node: Instance, make_bidder, auction
) -> None:
    """FR-54, UI-4: the tenth extension tells clients no further extension will happen."""
    a = await auction(ends_in_ms=59_000, threshold_s=600, extension_s=60, extension_count=9)
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.subscribe(a)
    await client.send(_bid(a, 10_000, "late"))
    ack = await client.expect("bid_accepted")
    ext = await client.expect("timer_extended", sequence_no=1)
    assert ext["extension_count"] == 10 and ext["final"] is True
    assert ext["end_time_ms"] == ack["end_time_ms"]


async def test_bid_requires_subscription_and_bidder_role(
    node: Instance, make_bidder, auction, auth_service, db
) -> None:
    """FR-34: subscribe first; FR-7: bidder role required."""
    a = await auction()
    user_id, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.send(_bid(a, 10_000, "n1"))
    assert (await client.expect("bid_rejected", request_id="n1"))["code"] == "NOT_SUBSCRIBED"

    await db.execute("UPDATE user_roles SET role = 'seller' WHERE user_id = $1", user_id)
    email = await db.fetchval("SELECT email FROM users WHERE id = $1", user_id)
    seller_token = (await auth_service.login(email, PASSWORD, "10.9.9.9")).access_token
    seller = await Client.open(node.ws, token=seller_token)
    await seller.subscribe(a)
    await seller.send(_bid(a, 10_000, "n2"))
    assert (await seller.expect("bid_rejected", request_id="n2"))["code"] == "FORBIDDEN"


async def test_spectating_works_but_drafts_stay_hidden(
    node: Instance, make_bidder, auction, redis: Redis
) -> None:
    """FR-30: anyone authenticated may watch a scheduled auction; a draft is NOT_FOUND.
    D2: watching an auction that is not live writes no live state."""
    scheduled = await auction(status="scheduled", starts_in_ms=60_000)
    draft = await auction(status="draft")
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    sub = await client.subscribe(scheduled)
    assert sub["snapshot"]["status"] == "scheduled" and sub["snapshot"]["degraded"] is False
    await client.send({"type": "subscribe", "auction_id": str(draft)})
    assert (await client.expect("error"))["code"] == "NOT_FOUND"
    await client.send(_bid(scheduled, 10_000, "s1"))
    assert (await client.expect("bid_rejected", request_id="s1"))["code"] == "AUCTION_NOT_STARTED"
    assert await redis.exists(state_key(scheduled), state_key(draft)) == 0


async def test_proxy_rejection_carries_min_next(node: Instance, make_bidder, auction) -> None:
    """FR-46: a maximum below the minimum next bid is BELOW_CURRENT_PRICE, with min_next."""
    a = await auction(start_price=10_000, min_increment=500)
    client = await Client.open(node.ws, token=(await make_bidder())[1])
    await client.subscribe(a)
    proxy = {"type": "set_proxy", "auction_id": str(a), "request_id": "low"}
    await client.send({**proxy, "max_amount": 9_000})
    rejected = await client.expect("proxy_rejected", request_id="low")
    assert rejected["code"] == "BELOW_CURRENT_PRICE" and rejected["min_next"] == 10_000
    assert "max_amount" not in rejected


async def test_unsubscribe_stops_room_events(node: Instance, make_bidder, auction) -> None:
    """FR-29: only the events of rooms the client is currently subscribed to."""
    a = await auction(self_outbid=False)
    watcher = await Client.open(node.ws, token=(await make_bidder())[1])
    bidder = await Client.open(node.ws, token=(await make_bidder())[1])
    await watcher.subscribe(a)
    await bidder.subscribe(a)
    await bidder.send(_bid(a, 10_000, "b1"))
    await watcher.expect("bid_placed", sequence_no=1)
    await watcher.send({"type": "unsubscribe", "auction_id": str(a)})
    await watcher.expect("unsubscribed", auction_id=str(a))
    await bidder.send(_bid(a, 10_500, "b2"))
    await bidder.expect("bid_placed", sequence_no=2)
    await watcher.send({"type": "ping"})
    await watcher.expect("pong")  # anything for seq 2 would have arrived before this
    assert [e["sequence_no"] for e in watcher.of_type("bid_placed")] == [1]


async def test_every_bid_decision_is_logged_once(
    node: Instance, make_bidder, auction, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-44, NFR-32: one line per bid decision, refusals before arbitration included;
    automatic bids are traceable by their sequence numbers; maxima are never logged."""
    a = await auction(start_price=10_000, min_increment=100, self_outbid=False)
    other = await auction()
    alice_id, alice_token = await make_bidder()
    bob_id, bob_token = await make_bidder()
    alice = await Client.open(node.ws, token=alice_token)
    bob = await Client.open(node.ws, token=bob_token)
    await alice.subscribe(a)
    await bob.subscribe(a)
    with caplog.at_level(logging.INFO, logger="bidding"):
        proxy = {"type": "set_proxy", "auction_id": str(a), "max_amount": SECRET_MAX}
        await alice.send({**proxy, "request_id": "p1"})
        await alice.expect("proxy_armed", request_id="p1")
        await bob.send(_bid(a, 20_000, "b1"))
        await bob.expect("bid_accepted", request_id="b1")
        await bob.send(_bid(other, 20_000, "b2"))
        await bob.expect("bid_rejected", request_id="b2")
    lines = [r.fields for r in caplog.records if r.msg in ("bid", "proxy")]
    by_request = {line["bid_request_id"]: line for line in lines}
    assert len(lines) == len(by_request) == 3
    assert by_request["p1"]["decision"] == "armed"
    assert by_request["p1"]["automatic_sequence_nos"] == [1]
    accepted = by_request["b1"]
    assert accepted["decision"] == "accepted" and accepted["sequence_no"] == 2
    assert accepted["automatic_sequence_nos"] == [3]
    refused = by_request["b2"]
    assert refused["decision"] == "rejected" and refused["code"] == "NOT_SUBSCRIBED"
    assert refused["auction_id"] == str(other) and refused["bidder_id"] == str(bob_id)
    assert refused["amount"] == 20_000
    assert str(SECRET_MAX) not in caplog.text and alice_id != bob_id
