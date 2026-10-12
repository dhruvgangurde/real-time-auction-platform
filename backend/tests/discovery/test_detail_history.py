"""Auction detail (FR-26, FR-30, BR-6), bid history (FR-27, BR-10), watchlist (FR-24, FR-25)
and the seller dashboard (FR-19)."""

import asyncpg
from httpx import AsyncClient
from redis.asyncio import Redis

from app.core.redis import create_redis
from app.services.bidding import BidService
from app.services.catalog import CatalogService
from app.services.ledger import persist_outbox
from tests.db.conftest import make_user as make_plain_user
from tests.lifecycle.conftest import bearer

RESERVE = 777_777  # distinctive, so a leak would show up anywhere in the JSON


def _values(node: object) -> list[object]:
    """Every scalar anywhere in a JSON document."""
    if isinstance(node, dict):
        return [v for value in node.values() for v in _values(value)]
    if isinstance(node, list):
        return [v for value in node for v in _values(value)]
    return [node]


async def test_detail_has_listing_details_and_live_values(
    api: AsyncClient, make_user, seeded, db: asyncpg.Connection
) -> None:
    """FR-26 (N10): title, description, category, images, seller name plus snapshot fields."""
    seller_id, _ = await make_user("seller")
    auction_id = await seeded(seller_id, "live", images=2, title="Pocket watch")
    body = (await api.get(f"/api/v1/auctions/{auction_id}")).json()
    assert body["title"] == "Pocket watch" and body["status"] == "live"
    assert body["seller"]["name"] == "Phase Seven" and body["category"]["id"]
    assert [i["position"] for i in body["images"]] == [0, 1]
    assert body["images"][0]["url"].startswith("/media/auctions/")
    for field in ("current_price", "min_next", "end_time_ms", "sequence_no", "server_time_ms"):
        assert field in body


async def test_reserve_price_only_for_the_owner_and_reserve_met_for_all(
    api: AsyncClient, make_user, seeded, bid_service: BidService, db: asyncpg.Connection
) -> None:
    """BR-6: the reserve is never disclosed to bidders, only whether it is met."""
    seller_id, seller = await make_user("seller")
    _, bidder = await make_user()
    _, admin = await make_user("admin")
    auction_id = await seeded(
        seller_id, "live", start_in_s=-60, reserve_price=RESERVE, starting_price=10_000
    )
    url = f"/api/v1/auctions/{auction_id}"
    for token in (None, bidder, admin):
        r = await api.get(url, headers=bearer(token) if token else {})
        assert "reserve_price" not in r.json() and RESERVE not in _values(r.json())
        assert r.json()["has_reserve"] is True and r.json()["reserve_met"] is False
    assert (await api.get(url, headers=bearer(seller))).json()["reserve_price"] == RESERVE
    buyer = await make_plain_user(db)
    assert (await bid_service.place_bid(auction_id, buyer, 800_000, "big")).accepted
    assert (await api.get(url)).json()["reserve_met"] is True


async def test_draft_detail_only_for_owner_and_admin(api: AsyncClient, make_user, seeded) -> None:
    """FR-30."""
    seller_id, seller = await make_user("seller")
    _, other = await make_user()
    _, admin = await make_user("admin")
    draft = await seeded(seller_id, "draft")
    url = f"/api/v1/auctions/{draft}"
    assert (await api.get(url)).status_code == 404
    assert (await api.get(url, headers=bearer(other))).status_code == 404
    assert (await api.get(url, headers=bearer(seller))).status_code == 200
    assert (await api.get(url, headers=bearer(admin))).status_code == 200


async def test_history_pages_newest_first_and_merges_unpersisted_bids(
    api: AsyncClient,
    make_user,
    seeded,
    bid_service: BidService,
    redis: Redis,
    sessions,
    db: asyncpg.Connection,
) -> None:
    """FR-27: public, keyset on sequence_no, aliases only; bids not yet persisted included;
    admins also see bidder ids (BR-10)."""
    seller_id, _ = await make_user("seller")
    _, admin = await make_user("admin")
    auction_id = await seeded(seller_id, "live", start_in_s=-60, min_increment=100)
    bidders = [await make_plain_user(db), await make_plain_user(db)]
    for i in range(3):
        assert (
            await bid_service.place_bid(auction_id, bidders[i % 2], 10_000 + i * 100, f"a{i}")
        ).accepted
    await persist_outbox(auction_id, redis, sessions)  # 1..3 in the ledger
    for i in range(3, 5):
        assert (
            await bid_service.place_bid(auction_id, bidders[i % 2], 10_000 + i * 100, f"a{i}")
        ).accepted
    url = f"/api/v1/auctions/{auction_id}/bids"
    first = (await api.get(url, params={"limit": 2})).json()
    assert [b["sequence_no"] for b in first["items"]] == [5, 4]  # from the outbox
    assert first["next_before"] == 4 and "bidder_id" not in first["items"][0]
    assert first["items"][0]["bidder_alias"].startswith("Bidder-")
    second = (await api.get(url, params={"limit": 2, "before": 4})).json()
    assert [b["sequence_no"] for b in second["items"]] == [3, 2]
    last = (await api.get(url, params={"limit": 2, "before": 2})).json()
    assert [b["sequence_no"] for b in last["items"]] == [1] and last["next_before"] is None
    as_admin = (await api.get(url, params={"limit": 1}, headers=bearer(admin))).json()
    assert as_admin["items"][0]["bidder_id"] == str(bidders[0])


async def test_history_without_redis_is_the_ledger_marked_degraded(
    make_user, seeded, bid_service: BidService, redis: Redis, sessions, storage, db
) -> None:
    """NFR-13: history stays readable while Redis is down."""
    seller_id, _ = await make_user("seller")
    auction_id = await seeded(seller_id, "live", start_in_s=-60)
    assert (
        await bid_service.place_bid(auction_id, await make_plain_user(db), 10_000, "x")
    ).accepted
    await persist_outbox(auction_id, redis, sessions)
    dead = create_redis("redis://redis:6399/15")
    try:
        page = await CatalogService(sessions, dead, storage).bid_history(None, auction_id, None, 20)
    finally:
        await dead.aclose()
    assert page["degraded"] is True and [b["sequence_no"] for b in page["items"]] == [1]


async def test_watchlist(api: AsyncClient, make_user, seeded) -> None:
    """FR-24, FR-25: idempotent add, view with price and time, not-watching removal,
    cancelled refused, hidden drafts not found."""
    seller_id, _ = await make_user("seller")
    _, token = await make_user()
    live = await seeded(seller_id, "live")
    cancelled = await seeded(seller_id, "cancelled")
    draft = await seeded(seller_id, "draft")
    auth = bearer(token)
    for _ in range(2):
        assert (await api.put(f"/api/v1/watchlist/{live}", headers=auth)).status_code == 204
    r = await api.put(f"/api/v1/watchlist/{cancelled}", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_CLOSED"
    assert (await api.put(f"/api/v1/watchlist/{draft}", headers=auth)).status_code == 404
    view = (await api.get("/api/v1/watchlist", headers=auth)).json()
    [item] = view["items"]
    assert item["id"] == str(live) and "end_time_ms" in item and "current_price" in item
    assert "server_time_ms" in view
    assert (await api.delete(f"/api/v1/watchlist/{live}", headers=auth)).status_code == 204
    r = await api.delete(f"/api/v1/watchlist/{live}", headers=auth)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_WATCHING"
    assert (await api.get("/api/v1/watchlist")).status_code == 401


async def test_seller_dashboard(
    api: AsyncClient, make_user, seeded, db: asyncpg.Connection
) -> None:
    """FR-19: own auctions only, drafts included, with outcome once closed."""
    seller_id, seller = await make_user("seller")
    other_id, _ = await make_user("seller")
    _, bidder = await make_user()
    draft = await seeded(seller_id, "draft")
    sold = await seeded(seller_id, "closed_sold")
    await db.execute("UPDATE auctions SET current_price = 42000, bid_count = 3 WHERE id = $1", sold)
    await seeded(other_id, "scheduled")
    body = (await api.get("/api/v1/seller/auctions", headers=bearer(seller))).json()
    by_id = {item["id"]: item for item in body["items"]}
    assert set(by_id) == {str(draft), str(sold)}
    assert by_id[str(sold)]["outcome"] == "closed_sold"
    assert by_id[str(sold)]["current_price"] == 42_000 and by_id[str(sold)]["bid_count"] == 3
    assert by_id[str(draft)]["outcome"] is None
    assert (await api.get("/api/v1/seller/auctions", headers=bearer(bidder))).status_code == 403
