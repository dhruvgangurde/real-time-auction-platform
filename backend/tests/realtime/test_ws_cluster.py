"""Two API instances on one Redis (FR-31, CI-7, NFR-5, NFR-28, FR-6; DC-7: events between instances
through Redis publish/subscribe)."""

import asyncio

from app.schemas.ws import CloseCode
from app.services.auth import AuthService
from app.services.users import UserService
from tests.realtime.client import Client, reorder
from tests.realtime.conftest import Instance


def _bid(auction_id, amount: int, rid: str) -> dict:
    return {"type": "place_bid", "auction_id": str(auction_id), "amount": amount, "request_id": rid}


async def test_events_cross_instances(
    instances: tuple[Instance, Instance], make_bidder, auction
) -> None:
    a_node, b_node = instances
    auction_id = await auction(self_outbid=False)
    _, alice_token = await make_bidder()
    _, bob_token = await make_bidder()
    alice = await Client.open(b_node.ws, token=alice_token)  # on instance B
    bob = await Client.open(a_node.ws, token=bob_token)  # on instance A
    await alice.subscribe(auction_id)
    await bob.subscribe(auction_id)

    await alice.send(_bid(auction_id, 10_000, "a1"))
    await alice.expect("bid_accepted")
    await bob.expect("bid_placed", sequence_no=1)  # B -> A
    await bob.send(_bid(auction_id, 10_500, "b1"))
    await bob.expect("bid_accepted")
    await alice.expect("bid_placed", sequence_no=2)  # A -> B
    await alice.expect("outbid_notice", sequence_no=2)  # user channel A -> B


async def test_logout_and_suspension_close_sockets_on_other_instances(
    instances: tuple[Instance, Instance],
    make_bidder,
    new_session,
    auth_service: AuthService,
    user_service: UserService,
) -> None:
    """FR-5, FR-6: session_revoked on the user channel closes the right sockets everywhere:
    logout closes that session's socket only, suspension closes every session's socket."""
    a_node, b_node = instances
    alice_id, alice_token = await make_bidder()
    on_a = await Client.open(a_node.ws, token=alice_token)
    on_b = await Client.open(b_node.ws, token=(await new_session(alice_id)).access_token)
    await auth_service.logout(await auth_service.authenticate(alice_token))
    assert await on_a.closed() == CloseCode.SESSION_REVOKED
    await on_b.send({"type": "ping"})
    await on_b.expect("pong")  # alice's other session is untouched

    bob_id, bob_token = await make_bidder()
    admin_id, _ = await make_bidder()
    bob_a = await Client.open(a_node.ws, token=bob_token)
    bob_b = await Client.open(b_node.ws, token=(await new_session(bob_id)).access_token)
    await user_service.suspend_user(admin_id, bob_id, "fraud review")
    assert await bob_a.closed() == CloseCode.SESSION_REVOKED
    assert await bob_b.closed() == CloseCode.SESSION_REVOKED


async def test_revoked_session_cannot_bid_even_without_the_close_event(
    instances: tuple[Instance, Instance], make_bidder, auction, auth_service: AuthService
) -> None:
    """FR-6: the denylist re-check on each bid denies a revoked session at its next action."""
    node, _ = instances
    auction_id = await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.subscribe(auction_id)
    auth_service._notify_revoked = None  # the session_revoked broadcast is lost
    await auth_service.logout(await auth_service.authenticate(token))
    await client.send(_bid(auction_id, 10_000, "late"))
    rejected = await client.expect("bid_rejected", request_id="late")
    assert rejected["code"] == "INVALID_TOKEN"
    assert await client.closed() == CloseCode.SESSION_REVOKED
    assert client.of_type("bid_accepted") == []


async def test_revoked_session_cannot_subscribe_even_without_the_close_event(
    instances: tuple[Instance, Instance], make_bidder, auction, auth_service: AuthService
) -> None:
    """FR-6: subscribe re-checks the session too, not only bids."""
    node, _ = instances
    auction_id = await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    auth_service._notify_revoked = None  # the session_revoked broadcast is lost
    await auth_service.logout(await auth_service.authenticate(token))
    await client.send({"type": "subscribe", "auction_id": str(auction_id)})
    assert (await client.expect("error"))["code"] == "INVALID_TOKEN"
    assert await client.closed() == CloseCode.SESSION_REVOKED
    assert client.of_type("subscribed") == []


async def test_200_concurrent_bids_reach_every_subscriber_exactly_once(
    instances: tuple[Instance, Instance], make_bidder, auction
) -> None:
    """NFR-28, NFR-5: every subscriber on either instance receives exactly 1..N."""
    a_node, b_node = instances
    auction_id = await auction(start_price=10_000, min_increment=1, self_outbid=False)
    per_bidder, nodes = 50, [a_node, a_node, b_node, b_node]
    bidders = [await Client.open(n.ws, token=(await make_bidder())[1]) for n in nodes]
    watchers = [await Client.open(n.ws, token=(await make_bidder())[1]) for n in (a_node, b_node)]
    for c in bidders + watchers:
        await c.subscribe(auction_id)

    async def run(client: Client, tag: str) -> None:
        amount, accepted, attempt = 10_000, 0, 0
        while accepted < per_bidder:
            attempt += 1
            rid = f"{tag}-{attempt}"
            await client.send(_bid(auction_id, amount, rid))
            reply = await client.wait(lambda f, rid=rid: f.get("request_id") == rid, timeout=10)
            if reply["type"] == "bid_accepted":
                accepted += 1
                amount = reply["amount"] + 1
            else:
                assert reply["code"] == "BID_TOO_LOW", reply
                amount = reply["min_next"]

    await asyncio.gather(*(run(c, f"b{i}") for i, c in enumerate(bidders)))
    total = per_bidder * len(bidders)
    for c in bidders + watchers:
        await c.expect("bid_placed", sequence_no=total, timeout=10)
        placed = c.of_type("bid_placed")
        seqs = [e["sequence_no"] for e in placed]
        assert sorted(seqs) == list(range(1, total + 1)), "lost or duplicated event"
        ordered = reorder(placed)
        assert [e["sequence_no"] for e in ordered] == list(range(1, total + 1))
        amounts = [e["amount"] for e in ordered]
        assert all(x < y for x, y in zip(amounts, amounts[1:], strict=False))
