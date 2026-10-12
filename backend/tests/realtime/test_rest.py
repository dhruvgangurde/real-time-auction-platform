"""Public live snapshot and the REST fallback (FR-26, FR-30, FR-33, DC-9, UC-01 2a)."""

import httpx
import pytest
from redis.asyncio import Redis

from app.services.live_state import state_key
from tests.realtime.client import Client
from tests.realtime.conftest import Instance


@pytest.fixture
async def node(start_instance) -> Instance:
    return await start_instance()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_live_snapshot_rebuilds_and_includes_unpersisted_bids(
    node: Instance, make_bidder, auction, redis: Redis
) -> None:
    a = await auction(start_price=10_000, min_increment=500, self_outbid=False)
    _, token = await make_bidder()
    async with httpx.AsyncClient(base_url=node.http) as http:
        for i, amount in enumerate((10_000, 10_500, 11_000)):
            r = await http.post(
                f"/api/v1/auctions/{a}/bids",
                json={"amount": amount, "request_id": f"r{i}"},
                headers=_auth(token),
            )
            assert r.status_code == 200, r.text
        snap = (await http.get(f"/api/v1/auctions/{a}/live")).json()  # anonymous
        assert snap["current_price"] == 11_000 and snap["sequence_no"] == 3
        assert snap["min_next"] == 11_500 and snap["leader_alias"].startswith("Bidder-")
        assert [b["sequence_no"] for b in snap["recent_bids"]] == [3, 2, 1]  # from the outbox
        assert "bidder_id" not in str(snap) and snap["degraded"] is False

        await redis.delete(state_key(a))  # live state lost; outbox still pending
        r = await http.get(f"/api/v1/auctions/{a}/live")
        assert r.status_code == 503  # rebuild refused until the outbox is persisted (FR-43)


async def test_draft_snapshot_only_for_its_seller(node: Instance, make_bidder, auction, db) -> None:
    """FR-30"""
    seller_id, seller_token = await make_bidder()
    _, other_token = await make_bidder()
    draft = await auction(status="draft", init=False)
    await db.execute("UPDATE auctions SET seller_id = $1 WHERE id = $2", seller_id, draft)
    async with httpx.AsyncClient(base_url=node.http) as http:
        assert (await http.get(f"/api/v1/auctions/{draft}/live")).status_code == 404
        other = await http.get(f"/api/v1/auctions/{draft}/live", headers=_auth(other_token))
        assert other.status_code == 404
        own = await http.get(f"/api/v1/auctions/{draft}/live", headers=_auth(seller_token))
        assert own.status_code == 200 and own.json()["status"] == "draft"


async def test_rest_fallback_publishes_the_same_events_and_is_idempotent(
    node: Instance, make_bidder, auction
) -> None:
    a = await auction(start_price=10_000, min_increment=500, self_outbid=False)
    _, token = await make_bidder()
    _, watcher_token = await make_bidder()
    watcher = await Client.open(node.ws, token=watcher_token)
    await watcher.subscribe(a)
    async with httpx.AsyncClient(base_url=node.http) as http:
        body = {"amount": 10_000, "request_id": "rest-1"}
        first = await http.post(f"/api/v1/auctions/{a}/bids", json=body, headers=_auth(token))
        again = await http.post(f"/api/v1/auctions/{a}/bids", json=body, headers=_auth(token))
        assert first.status_code == again.status_code == 200
        assert first.json()["sequence_no"] == again.json()["sequence_no"] == 1
        low = await http.post(
            f"/api/v1/auctions/{a}/bids",
            json={"amount": 10_100, "request_id": "rest-2"},
            headers=_auth(token),
        )
        assert low.status_code == 409 and low.json()["error"]["code"] == "BID_TOO_LOW"
        assert "10500" in low.json()["error"]["message"]

        proxy = await http.post(
            f"/api/v1/auctions/{a}/proxy",
            json={"max_amount": 50_000, "request_id": "rest-3"},
            headers=_auth(token),
        )
        assert proxy.status_code == 200 and proxy.json()["max_amount"] == 50_000
        anonymous = await http.post(f"/api/v1/auctions/{a}/bids", json=body)
        assert anonymous.status_code == 401
    await watcher.expect("bid_placed", sequence_no=1)
    placed = [e["sequence_no"] for e in watcher.of_type("bid_placed")]
    assert placed.count(1) >= 1  # a retry may republish; clients drop seq <= last applied
