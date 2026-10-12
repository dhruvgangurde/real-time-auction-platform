"""Listing and search (FR-21, FR-22, FR-23, FR-30): default statuses, ranking, descendants,
filters, stable pages, live values from Redis. Every test scopes itself by a fresh category or
a unique search word, because auction_test is shared."""

from datetime import timedelta
from uuid import uuid4

import asyncpg
from httpx import AsyncClient
from redis.asyncio import Redis

from app.services.live_state import state_key


def _ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


async def test_default_statuses_and_never_cancelled_or_draft(
    api: AsyncClient, make_user, seeded, category
) -> None:
    """FR-30 and the listing rule: scheduled, live and closing by default; closed only when
    asked for; cancelled and draft never."""
    seller_id, _ = await make_user("seller")
    cat = await category()
    ids = {
        status: str(await seeded(seller_id, status, category_id=cat))
        for status in (
            "draft",
            "scheduled",
            "live",
            "closing",
            "closed_sold",
            "closed_no_sale",
            "cancelled",
        )
    }
    listed = set(_ids(await api.get("/api/v1/auctions", params={"category": cat})))
    assert listed == {ids["scheduled"], ids["live"], ids["closing"]}
    closed = await api.get(
        "/api/v1/auctions",
        params=[("category", cat), ("status", "closed_sold"), ("status", "closed_no_sale")],
    )
    assert set(_ids(closed)) == {ids["closed_sold"], ids["closed_no_sale"]}
    for forbidden in ("cancelled", "draft"):
        r = await api.get("/api/v1/auctions", params={"category": cat, "status": forbidden})
        assert r.status_code == 422


async def test_title_match_ranks_above_description_match(
    api: AsyncClient, make_user, seeded
) -> None:
    """FR-22: same word, title beats description, even though the description match is newer."""
    seller_id, _ = await make_user("seller")
    word = f"zq{uuid4().hex[:10]}"
    in_title = await seeded(seller_id, "scheduled", title=f"Rare {word} lamp")
    in_description = await seeded(
        seller_id, "scheduled", title="Old lamp", description=f"Comes with a {word} shade"
    )
    assert _ids(await api.get("/api/v1/auctions", params={"q": word})) == [
        str(in_title),
        str(in_description),
    ]


async def test_category_filter_includes_descendants(
    api: AsyncClient, make_user, seeded, category
) -> None:
    """FR-21."""
    seller_id, _ = await make_user("seller")
    root = await category()
    child = await category(parent_id=root)
    grandchild = await category(parent_id=child)
    sibling = await category()
    deep = await seeded(seller_id, "scheduled", category_id=grandchild)
    await seeded(seller_id, "scheduled", category_id=sibling)
    assert _ids(await api.get("/api/v1/auctions", params={"category": root})) == [str(deep)]
    assert _ids(await api.get("/api/v1/auctions", params={"category": child})) == [str(deep)]


async def test_filters_combine(
    api: AsyncClient, make_user, seeded, category, db: asyncpg.Connection, clock
) -> None:
    """FR-23: price on coalesce(current_price, starting_price), closing_before, query and
    category together."""
    seller_id, _ = await make_user("seller")
    cat = await category()
    word = f"zq{uuid4().hex[:10]}"
    cheap = await seeded(seller_id, "scheduled", category_id=cat, title=f"{word} a")
    mid = await seeded(
        seller_id, "scheduled", category_id=cat, title=f"{word} b", starting_price=20_000
    )
    bid_up = await seeded(seller_id, "scheduled", category_id=cat, title=f"{word} c")
    await db.execute("UPDATE auctions SET current_price = 50000 WHERE id = $1", bid_up)
    late = await seeded(
        seller_id,
        "scheduled",
        category_id=cat,
        title=f"{word} d",
        starting_price=20_000,
        duration_s=10 * 3600,
    )
    base = {"category": cat, "q": word}
    priced = await api.get(
        "/api/v1/auctions", params=base | {"min_price": 15_000, "max_price": 30_000}
    )
    assert set(_ids(priced)) == {str(mid), str(late)}
    soon = (clock.now() + timedelta(hours=3)).isoformat()
    closing = await api.get(
        "/api/v1/auctions", params=base | {"min_price": 15_000, "closing_before": soon}
    )
    assert set(_ids(closing)) == {str(mid), str(bid_up)}  # late ends after `soon`
    high = await api.get("/api/v1/auctions", params=base | {"min_price": 40_000})
    assert _ids(high) == [str(bid_up)]  # its bids count, not its starting price
    assert str(cheap) in _ids(await api.get("/api/v1/auctions", params=base))


async def test_bad_filters(api: AsyncClient) -> None:
    r = await api.get("/api/v1/auctions", params={"min_price": 10, "max_price": 5})
    assert r.status_code == 422 and "min_price" in r.json()["error"]["fields"]
    r = await api.get("/api/v1/auctions", params={"category": 99_999_999})
    assert r.status_code == 422 and r.json()["error"]["code"] == "CATEGORY_NOT_FOUND"
    r = await api.get("/api/v1/auctions", params={"q": "x" * 201})
    assert r.status_code == 422
    r = await api.get("/api/v1/auctions", params={"page_size": 51})
    assert r.status_code == 422


async def test_pages_are_stable_without_repeats_or_gaps(
    api: AsyncClient, make_user, seeded, category, db: asyncpg.Connection
) -> None:
    """FR-23: equal created_at values break ties on id."""
    seller_id, _ = await make_user("seller")
    cat = await category()
    ids = [str(await seeded(seller_id, "scheduled", category_id=cat)) for _ in range(7)]
    await db.execute(
        "UPDATE auctions SET created_at = '2026-01-01T00:00:00Z' WHERE category_id = $1", cat
    )
    seen: list[str] = []
    for page in (1, 2, 3):
        r = await api.get(
            "/api/v1/auctions", params={"category": cat, "page": page, "page_size": 3}
        )
        seen += _ids(r)
        assert r.json()["has_more"] is (page < 3)
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen))
    again = _ids(await api.get("/api/v1/auctions", params={"category": cat, "page_size": 3}))
    assert again == seen[:3]


async def test_live_rows_show_the_redis_price(
    api: AsyncClient, make_user, seeded, category, redis: Redis
) -> None:
    """D7: Postgres may lag; a live item shows the Redis value."""
    seller_id, _ = await make_user("seller")
    cat = await category()
    auction_id = await seeded(seller_id, "live", category_id=cat)
    await redis.hset(state_key(auction_id), mapping={"highest_amount": "64000", "sequence": "3"})
    [item] = (await api.get("/api/v1/auctions", params={"category": cat})).json()["items"]
    assert item["current_price"] == 64_000 and item["bid_count"] == 3


async def test_thumbnail_is_the_first_image(api: AsyncClient, make_user, seeded, category) -> None:
    seller_id, _ = await make_user("seller")
    cat = await category()
    await seeded(seller_id, "scheduled", category_id=cat, images=3)
    [item] = (await api.get("/api/v1/auctions", params={"category": cat})).json()["items"]
    assert item["thumbnail_url"].endswith("/seed0.png")
