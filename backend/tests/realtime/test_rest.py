"""Public live snapshot and the REST fallback (FR-26, FR-30, FR-33, DC-9, UC-01 2a)."""

import logging

import httpx
import pytest
from redis.asyncio import Redis

from app.core.config import settings
from app.services.ledger import persist_outbox
from app.services.live_state import state_key
from tests.realtime.client import Client
from tests.realtime.conftest import DEAD_REDIS_URL, Instance, dead_redis_services


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


async def test_draft_snapshot_only_for_its_seller(
    node: Instance, make_bidder, auction, db, redis: Redis
) -> None:
    """FR-30; D2: viewing a draft writes nothing to Redis."""
    seller_id, seller_token = await make_bidder()
    _, other_token = await make_bidder()
    draft = await auction(status="draft")
    await db.execute("UPDATE auctions SET seller_id = $1 WHERE id = $2", seller_id, draft)
    async with httpx.AsyncClient(base_url=node.http) as http:
        assert (await http.get(f"/api/v1/auctions/{draft}/live")).status_code == 404
        other = await http.get(f"/api/v1/auctions/{draft}/live", headers=_auth(other_token))
        assert other.status_code == 404
        own = await http.get(f"/api/v1/auctions/{draft}/live", headers=_auth(seller_token))
        assert own.status_code == 200 and own.json()["status"] == "draft"
        assert own.json()["degraded"] is False
    assert await redis.exists(state_key(draft)) == 0


async def test_viewing_before_go_live_never_blocks_bidding_after_it(
    node: Instance, make_bidder, auction, db, redis: Redis
) -> None:
    """D2, FR-15: a scheduled auction is viewed over REST and WebSocket, then goes live; the
    first bid after go-live is accepted (no stale 'scheduled' live state left behind)."""
    a = await auction(status="scheduled")
    _, token = await make_bidder()
    async with httpx.AsyncClient(base_url=node.http) as http:
        snap = (await http.get(f"/api/v1/auctions/{a}/live")).json()
        assert snap["status"] == "scheduled" and snap["degraded"] is False
        watcher = await Client.open(node.ws, token=token)
        assert (await watcher.subscribe(a))["snapshot"]["status"] == "scheduled"
        assert await redis.exists(state_key(a)) == 0
        await db.execute("UPDATE auctions SET status = 'live' WHERE id = $1", a)
        r = await http.post(
            f"/api/v1/auctions/{a}/bids",
            json={"amount": 10_000, "request_id": "first"},
            headers=_auth(token),
        )
        assert r.status_code == 200 and r.json()["sequence_no"] == 1


async def test_snapshot_never_runs_ahead_of_its_own_sequence(
    node: Instance, make_bidder, auction, bid_service, new_user, redis: Redis, sessions, db
) -> None:
    """N7: a bid accepted after the snapshot's state read may already be in the ledger; it is
    left to the live events, so recent_bids never shows more than sequence_no."""
    a = await auction(self_outbid=False)
    bidder = await new_user()
    for i, amount in enumerate((10_000, 10_500)):
        assert (await bid_service.place_bid(a, bidder, amount, f"r{i}")).accepted
    await persist_outbox(a, redis, sessions)
    await db.execute(
        "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type) "
        "VALUES ($1, $2, 11000, 3, 'manual')",
        a,
        bidder,
    )
    async with httpx.AsyncClient(base_url=node.http) as http:
        snap = (await http.get(f"/api/v1/auctions/{a}/live")).json()
    assert snap["sequence_no"] == 2 and snap["current_price"] == 10_500
    assert [b["sequence_no"] for b in snap["recent_bids"]] == [2, 1]


async def test_rest_limiter_rejection_is_logged_as_a_bid_decision(
    node: Instance, make_bidder, auction, monkeypatch, caplog
) -> None:
    """FR-44: one line per bid decision, also for a REST bid the limiter refuses."""
    monkeypatch.setattr(settings, "rate_limit_ws_place_bid", "1/1")
    a = await auction()
    _, token = await make_bidder()
    async with httpx.AsyncClient(base_url=node.http) as http:
        with caplog.at_level(logging.INFO, logger="bidding"):
            for rid in ("one", "two"):
                await http.post(
                    f"/api/v1/auctions/{a}/bids",
                    json={"amount": 10_000, "request_id": rid},
                    headers=_auth(token),
                )
    lines = {r.fields["bid_request_id"]: r.fields for r in caplog.records if r.msg == "bid"}
    assert lines["one"]["decision"] == "accepted"
    assert lines["two"]["decision"] == "rejected" and lines["two"]["code"] == "RATE_LIMITED"
    assert lines["two"]["amount"] == 10_000 and lines["two"]["auction_id"] == str(a)


async def test_redis_down_live_from_postgres_and_subscribe_fails_closed(
    start_instance, make_bidder, auction, bid_service, new_user, redis: Redis, sessions, clock
) -> None:
    """NFR-13, D11: with the command Redis unreachable (not only pub/sub), /live is served from
    Postgres with degraded true, while a WebSocket subscribe fails closed."""
    a = await auction(self_outbid=False)
    bidder = await new_user()
    for i, amount in enumerate((10_000, 10_500)):
        assert (await bid_service.place_bid(a, bidder, amount, f"r{i}")).accepted
    await persist_outbox(a, redis, sessions)
    node = await start_instance(
        redis_url=DEAD_REDIS_URL, overrides=dead_redis_services(sessions, clock)
    )
    async with httpx.AsyncClient(base_url=node.http) as http:
        r = await http.get(f"/api/v1/auctions/{a}/live")
    snap = r.json()
    assert r.status_code == 200 and snap["degraded"] is True
    assert snap["status"] == "live" and snap["current_price"] == 10_500
    assert snap["sequence_no"] == 2 and snap["participants"] is None

    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)  # the denylist check falls back to Postgres
    assert (await client.expect("system_status"))["degraded"] is True
    await client.send({"type": "subscribe", "auction_id": str(a)})
    assert (await client.expect("error"))["code"] == "SERVICE_UNAVAILABLE"
    await client.send({"type": "ping"})
    await client.expect("pong")  # harmless types still work


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
