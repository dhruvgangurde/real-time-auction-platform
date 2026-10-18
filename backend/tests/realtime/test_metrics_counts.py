"""NFR-8: bids accepted and rejected by reason are counted at the decision, through both API
instances, over the WebSocket and REST (FR-44's decision points), only after the answer is
queued (FR-40), and a counter that hangs or fails never changes a bid's result or its ack."""

import asyncio
import logging

import httpx
import pytest
from redis.asyncio import Redis

from app.services.metrics import Metrics
from tests.realtime.client import Client
from tests.realtime.conftest import Instance


def _bid(auction_id, amount: int, rid: str) -> dict:
    return {"type": "place_bid", "auction_id": str(auction_id), "amount": amount, "request_id": rid}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _counted(redis: Redis, accepted: int, rejected: dict[str, int]) -> dict:
    """The counts land in the background: wait for them (2 s at most)."""
    bids: dict = {}
    for _ in range(40):
        bids = await Metrics(redis).bids()
        if bids["accepted_60s"] == accepted and bids["rejected_60s"] == rejected:
            return bids
        await asyncio.sleep(0.05)
    raise AssertionError(f"counted {bids.get('accepted_60s')}, {bids.get('rejected_60s')}")


async def test_bids_through_both_instances_and_both_paths_are_counted(
    instances: tuple[Instance, Instance], make_bidder, auction, redis: Redis
) -> None:
    a_node, b_node = instances
    auction_id = await auction(start_price=10_000, min_increment=500, self_outbid=False)
    _, alice_token = await make_bidder()
    _, bob_token = await make_bidder()
    _, carol_token = await make_bidder()
    alice = await Client.open(b_node.ws, token=alice_token)
    bob = await Client.open(a_node.ws, token=bob_token)
    await alice.subscribe(auction_id)
    await bob.subscribe(auction_id)

    await alice.send(_bid(auction_id, 10_000, "a1"))
    await alice.expect("bid_accepted", request_id="a1")
    await bob.send(_bid(auction_id, 10_500, "b1"))
    await bob.expect("bid_accepted", request_id="b1")
    await alice.send(_bid(auction_id, 10_000, "a2"))  # below the minimum now
    assert (await alice.expect("bid_rejected", request_id="a2"))["code"] == "BID_TOO_LOW"
    async with httpx.AsyncClient(base_url=a_node.http) as http:
        ok = await http.post(
            f"/api/v1/auctions/{auction_id}/bids",
            json={"amount": 11_000, "request_id": "c1"},
            headers=_auth(carol_token),
        )
        assert ok.status_code == 200, ok.text
        low = await http.post(
            f"/api/v1/auctions/{auction_id}/bids",
            json={"amount": 10_000, "request_id": "c2"},
            headers=_auth(carol_token),
        )
        assert low.status_code == 409 and low.json()["error"]["code"] == "BID_TOO_LOW"

    bids = await _counted(redis, accepted=3, rejected={"BID_TOO_LOW": 2})
    assert bids["rejection_rate"] == pytest.approx(2 / 5)


async def test_a_counter_that_never_finishes_delays_no_answer(
    start_instance, make_bidder, auction, redis: Redis
) -> None:
    node: Instance = await start_instance()
    never = asyncio.Event()

    async def hang(*_: object, **__: object) -> None:
        await never.wait()

    metrics: Metrics = node.app.state.metrics
    metrics._bump = hang  # type: ignore[method-assign]
    auction_id = await auction(start_price=10_000, min_increment=500, self_outbid=False)
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.subscribe(auction_id)
    await client.send(_bid(auction_id, 10_000, "w1"))
    ack = await asyncio.wait_for(client.expect("bid_accepted", request_id="w1"), 2)
    assert ack["sequence_no"] == 1
    async with httpx.AsyncClient(base_url=node.http) as http:
        r = await asyncio.wait_for(
            http.post(
                f"/api/v1/auctions/{auction_id}/bids",
                json={"amount": 9_000, "request_id": "w2"},
                headers=_auth(token),
            ),
            2,
        )
    assert r.json()["error"]["code"] in ("BID_TOO_LOW", "SELF_OUTBID_NOT_ALLOWED")
    for _ in range(40):
        if len(metrics.pending) == 2:
            break
        await asyncio.sleep(0.05)
    assert len(metrics.pending) == 2  # both counts still held, neither answer waited for them
    never.set()


async def test_a_failing_counter_changes_no_result_and_no_ack(
    start_instance, make_bidder, auction, caplog: pytest.LogCaptureFixture
) -> None:
    node: Instance = await start_instance()
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    node.app.state.metrics = Metrics(broken)  # every count fails
    auction_id = await auction(start_price=10_000, min_increment=500, self_outbid=False)
    _, token = await make_bidder()
    _, other_token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.subscribe(auction_id)
    await client.send(_bid(auction_id, 10_000, "f1"))
    ack = await client.expect("bid_accepted", request_id="f1")
    assert (ack["sequence_no"], ack["amount"]) == (1, 10_000)
    async with httpx.AsyncClient(base_url=node.http) as http:
        ok = await http.post(
            f"/api/v1/auctions/{auction_id}/bids",
            json={"amount": 10_500, "request_id": "f2"},
            headers=_auth(other_token),
        )
        low = await http.post(
            f"/api/v1/auctions/{auction_id}/bids",
            json={"amount": 10_000, "request_id": "f3"},
            headers=_auth(other_token),
        )
    assert ok.status_code == 200 and ok.json()["sequence_no"] == 2
    assert low.status_code == 409 and set(low.json()) == {"error"}
    with caplog.at_level(logging.WARNING, logger="metrics"):
        for _ in range(40):
            if any("metrics bump failed" in r.message for r in caplog.records):
                break
            await asyncio.sleep(0.05)
    assert any("metrics bump failed" in r.message for r in caplog.records)
    await broken.aclose()
