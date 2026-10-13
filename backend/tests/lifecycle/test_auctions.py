"""Create, edit, publish, cancel (FR-11, FR-12, FR-14, FR-17, FR-18, FR-77, FR-78, BR-1, BR-4,
BR-9, NFR-22, D1, D8)."""

import json
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimiter
from app.core.redis import create_redis
from app.core.storage import LocalStorage
from app.services.auctions import AuctionService
from app.services.auth import AuthService, CurrentUser
from app.services.bidding import BidService
from app.services.denylist import Denylist
from app.services.users import UserService
from tests.auth.conftest import PASSWORD, FakeClock
from tests.lifecycle.conftest import auction_body, bearer, png


def _code(response: Any) -> str:
    return response.json()["error"]["code"]


async def _audits(db: asyncpg.Connection, auction_id: Any) -> list[dict]:
    rows = await db.fetch(
        "SELECT action, actor_id, before, after FROM audit_log "
        "WHERE entity_type = 'auction' AND entity_id = $1 ORDER BY id",
        str(auction_id),
    )
    return [
        {
            "action": r["action"],
            "actor_id": r["actor_id"],
            "before": json.loads(r["before"]) if r["before"] else None,
            "after": json.loads(r["after"]) if r["after"] else None,
        }
        for r in rows
    ]


async def test_seller_creates_a_draft_with_defaults_and_audit(
    api: AsyncClient, make_user, category, clock: FakeClock, db: asyncpg.Connection
) -> None:
    """FR-11, FR-14 (defaults 60/120, original_end_time), FR-77."""
    seller_id, token = await make_user("seller")
    r = await api.post(
        "/api/v1/auctions",
        json=auction_body(clock, await category(), reserve_price=50_000),
        headers=bearer(token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft" and body["seller_id"] == str(seller_id)
    assert body["antisnipe_threshold_s"] == 60 and body["antisnipe_extension_s"] == 120
    assert body["original_end_time_ms"] == body["end_time_ms"]
    assert body["reserve_price"] == 50_000  # the owner's own view
    [audit] = await _audits(db, body["id"])
    assert audit["action"] == "auction_created" and audit["actor_id"] == seller_id
    assert audit["after"]["status"] == "draft"


async def test_only_sellers_create(api: AsyncClient, make_user, category, clock) -> None:
    """BR-1, FR-7."""
    _, token = await make_user()
    r = await api.post(
        "/api/v1/auctions", json=auction_body(clock, await category()), headers=bearer(token)
    )
    assert r.status_code == 403 and _code(r) == "FORBIDDEN"


async def test_validation_reports_each_failing_field(
    api: AsyncClient, make_user, category, clock: FakeClock
) -> None:
    """FR-12: per-field errors, all at once."""
    _, token = await make_user("seller")
    start = clock.now() - timedelta(minutes=5)
    body = auction_body(
        clock,
        await category(),
        start_time=start.isoformat(),
        end_time=(start - timedelta(minutes=1)).isoformat(),
        reserve_price=5_000,
        antisnipe_threshold_s=1,
    )
    r = await api.post("/api/v1/auctions", json=body, headers=bearer(token))
    assert r.status_code == 422 and _code(r) == "VALIDATION_ERROR"
    assert set(r.json()["error"]["fields"]) == {
        "start_time",
        "end_time",
        "reserve_price",
        "antisnipe_threshold_s",
    }


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"starting_price": 0}, "starting_price"),
        ({"starting_price": 99}, "starting_price"),  # below the gateway minimum (FR-12)
        ({"min_increment": 0}, "min_increment"),
        ({"starting_price": 10.5}, "starting_price"),
        ({"title": "   "}, "title"),
        ({"start_time": "2030-01-01T10:00:00"}, "start_time"),  # no time zone
        ({"surprise": 1}, "surprise"),
    ],
)
async def test_schema_rejects_bad_values(
    api: AsyncClient, make_user, category, clock: FakeClock, overrides: dict, field: str
) -> None:
    """FR-12, DC-11: increment above zero, money as integers, aware datetimes, no extras."""
    _, token = await make_user("seller")
    r = await api.post(
        "/api/v1/auctions",
        json=auction_body(clock, await category(), **overrides),
        headers=bearer(token),
    )
    assert r.status_code == 422 and _code(r) == "VALIDATION_ERROR"
    assert field in r.json()["error"]["fields"]


async def test_unknown_category(api: AsyncClient, make_user, clock) -> None:
    _, token = await make_user("seller")
    r = await api.post(
        "/api/v1/auctions", json=auction_body(clock, 9_999_999), headers=bearer(token)
    )
    assert r.status_code == 422 and _code(r) == "CATEGORY_NOT_FOUND"


async def test_creation_is_rate_limited_per_user(
    api: AsyncClient, make_user, category, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-22."""
    monkeypatch.setattr(settings, "rate_limit_auction_create", "2/3600")
    _, token = await make_user("seller")
    cat = await category()
    codes = [
        (
            await api.post("/api/v1/auctions", json=auction_body(clock, cat), headers=bearer(token))
        ).status_code
        for _ in range(3)
    ]
    assert codes == [201, 201, 429]


async def test_ownership(api: AsyncClient, make_user, seeded) -> None:
    """FR-18: another seller gets FORBIDDEN; a hidden draft is NOT_FOUND; an admin who is not
    the owner may see the draft but not change it."""
    owner_id, _ = await make_user("seller")
    _, other = await make_user("seller")
    _, admin = await make_user("admin")
    draft = await seeded(owner_id, "draft")
    scheduled = await seeded(owner_id, "scheduled")
    patch = {"title": "Mine now"}
    r = await api.patch(f"/api/v1/auctions/{draft}", json=patch, headers=bearer(other))
    assert r.status_code == 404 and _code(r) == "NOT_FOUND"
    r = await api.patch(f"/api/v1/auctions/{scheduled}", json=patch, headers=bearer(other))
    assert r.status_code == 403 and _code(r) == "FORBIDDEN"
    r = await api.patch(f"/api/v1/auctions/{draft}", json=patch, headers=bearer(admin))
    assert r.status_code == 403 and _code(r) == "FORBIDDEN"
    r = await api.post(f"/api/v1/auctions/{scheduled}/cancel", headers=bearer(other))
    assert r.status_code == 403


# ------------------------------------------------------------------ status x action matrix

ACTIONS = ("edit_title", "edit_price", "publish", "cancel", "add_image", "remove_image")
OK = {"edit_title": 200, "edit_price": 200, "publish": 200, "cancel": 200}
EXPECTED: dict[str, dict[str, Any]] = {
    "draft": OK | {"add_image": 201, "remove_image": 204},
    "scheduled": {
        "edit_title": 200,
        "edit_price": 200,
        "publish": "NOT_DRAFT",
        "cancel": 200,
        "add_image": 201,
        "remove_image": "VALIDATION_ERROR",  # the last image of an auction that left draft
    },
    "live": {
        "edit_title": 200,
        "edit_price": "AUCTION_ALREADY_LIVE",
        "publish": "AUCTION_ALREADY_LIVE",
        "cancel": "AUCTION_ALREADY_LIVE",
        "add_image": 201,
        "remove_image": "VALIDATION_ERROR",
    },
    "live_with_bids": dict.fromkeys(ACTIONS, "AUCTION_ALREADY_LIVE"),
    "closing": {
        "edit_title": "ALREADY_CLOSED",
        "edit_price": "ALREADY_CLOSED",
        "publish": "AUCTION_ALREADY_LIVE",
        "cancel": "AUCTION_ALREADY_LIVE",
        "add_image": "ALREADY_CLOSED",
        "remove_image": "ALREADY_CLOSED",
    },
    **{s: dict.fromkeys(ACTIONS, "ALREADY_CLOSED") for s in ("closed_sold", "closed_no_sale")},
    "cancelled": dict.fromkeys(ACTIONS, "ALREADY_CLOSED"),
}


async def _act(api: AsyncClient, token: str, auction_id: Any, action: str, db: Any) -> Any:
    url = f"/api/v1/auctions/{auction_id}"
    if action == "edit_title":
        return await api.patch(url, json={"title": "Renamed"}, headers=bearer(token))
    if action == "edit_price":
        return await api.patch(url, json={"starting_price": 12_000}, headers=bearer(token))
    if action in ("publish", "cancel"):
        return await api.post(f"{url}/{action}", headers=bearer(token))
    if action == "add_image":
        return await api.post(f"{url}/images", content=png(), headers=bearer(token))
    image_id = await db.fetchval("SELECT id FROM auction_images WHERE auction_id = $1", auction_id)
    return await api.delete(f"{url}/images/{image_id}", headers=bearer(token))


@pytest.mark.parametrize("state", list(EXPECTED))
async def test_status_action_matrix(
    api: AsyncClient, make_user, seeded, db: asyncpg.Connection, state: str
) -> None:
    """FR-17, FR-18, BR-4, BR-9, D8: every action against every status."""
    seller_id, token = await make_user("seller")
    status = "live" if state == "live_with_bids" else state
    for action in ACTIONS:
        auction_id = await seeded(
            seller_id, status, images=1, bids=1 if state == "live_with_bids" else 0
        )
        r = await _act(api, token, auction_id, action, db)
        expected = EXPECTED[state][action]
        if isinstance(expected, int):
            assert r.status_code == expected, (state, action, r.text)
        else:
            assert _code(r) == expected, (state, action, r.text)


async def test_live_edit_fails_closed_without_redis(
    make_user,
    seeded,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    limiter: RateLimiter,
    storage: LocalStorage,
) -> None:
    """D8: if Redis cannot say whether a bid exists, a live edit is refused."""
    seller_id, _ = await make_user("seller")
    auction_id = await seeded(seller_id, "live")
    dead = create_redis("redis://redis:6399/15")
    try:
        service = AuctionService(sessions, dead, clock, limiter, storage)
        user = CurrentUser(seller_id, ("bidder", "seller"), seller_id)
        with pytest.raises(AppError) as info:
            await service.update(user, auction_id, {"title": "Late rename"})
        assert info.value.code == "SERVICE_UNAVAILABLE"
    finally:
        await dead.aclose()


async def test_live_edit_rebuilds_missing_state_before_deciding(
    api: AsyncClient, make_user, seeded, redis: Redis
) -> None:
    """D8 with lost live state: rebuilt from Postgres, then the sequence decides."""
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "live")
    await redis.flushdb()
    r = await api.patch(
        f"/api/v1/auctions/{auction_id}", json={"title": "Still fine"}, headers=bearer(token)
    )
    assert r.status_code == 200 and r.json()["title"] == "Still fine"


async def test_pre_live_end_time_edit_moves_original_end_time(
    api: AsyncClient, make_user, seeded, clock: FakeClock
) -> None:
    """D1: original_end_time equals end_time on every pre-live edit."""
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "scheduled")
    new_end = clock.now() + timedelta(hours=5)
    r = await api.patch(
        f"/api/v1/auctions/{auction_id}",
        json={"end_time": new_end.isoformat()},
        headers=bearer(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["original_end_time_ms"] == r.json()["end_time_ms"]


async def test_edit_revalidates_the_merged_auction(
    api: AsyncClient, make_user, seeded, clock: FakeClock
) -> None:
    """FR-12 on edit: an end before the stored start, and null for a required field."""
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "draft")
    url = f"/api/v1/auctions/{auction_id}"
    early = (clock.now() + timedelta(minutes=30)).isoformat()  # before the seeded start
    r = await api.patch(url, json={"end_time": early}, headers=bearer(token))
    assert r.status_code == 422 and "end_time" in r.json()["error"]["fields"]
    r = await api.patch(url, json={"title": None}, headers=bearer(token))
    assert r.status_code == 422
    r = await api.patch(url, json={"reserve_price": None}, headers=bearer(token))
    assert r.status_code == 200 and r.json()["has_reserve"] is False


async def test_every_change_is_audited_with_before_and_after(
    api: AsyncClient, make_user, seeded, db: asyncpg.Connection
) -> None:
    """FR-77, FR-78: edit, publish and cancel each leave one audit row."""
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "draft", images=1, title="Old title")
    url = f"/api/v1/auctions/{auction_id}"
    assert (await api.patch(url, json={"title": "New title"}, headers=bearer(token))).is_success
    assert (await api.post(f"{url}/publish", headers=bearer(token))).is_success
    assert (await api.post(f"{url}/cancel", headers=bearer(token))).is_success
    audits = await _audits(db, auction_id)
    assert [(a["action"], a["before"], a["after"]) for a in audits] == [
        ("auction_updated", {"title": "Old title"}, {"title": "New title"}),
        ("status_changed", {"status": "draft"}, {"status": "scheduled"}),
        ("status_changed", {"status": "scheduled"}, {"status": "cancelled"}),
    ]
    assert {a["actor_id"] for a in audits} == {seller_id}


async def test_publish_needs_an_image_and_a_future_start(
    api: AsyncClient, make_user, seeded, clock: FakeClock
) -> None:
    """FR-13, FR-12 re-checked at publish."""
    seller_id, token = await make_user("seller")
    bare = await seeded(seller_id, "draft")
    r = await api.post(f"/api/v1/auctions/{bare}/publish", headers=bearer(token))
    assert r.status_code == 422 and "images" in r.json()["error"]["fields"]
    stale = await seeded(seller_id, "draft", images=1, start_in_s=-60)
    r = await api.post(f"/api/v1/auctions/{stale}/publish", headers=bearer(token))
    assert r.status_code == 422 and "start_time" in r.json()["error"]["fields"]


async def test_revoked_seller_can_only_cancel(
    api: AsyncClient,
    make_user,
    seeded,
    auth_service: AuthService,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    redis: Redis,
    db: asyncpg.Connection,
) -> None:
    """BR-1: edit, publish and image changes need the seller role as well as ownership; cancel
    needs ownership only, so a former seller can still withdraw a listing."""
    seller_id, _ = await make_user("seller")
    draft = await seeded(seller_id, "draft", images=1)
    scheduled = await seeded(seller_id, "scheduled", images=2)
    address = await db.fetchval("SELECT email FROM users WHERE id = $1", seller_id)
    users = UserService(sessions, clock, Denylist(redis, None))
    assert await users.change_role(address, "seller", grant=False)  # what the CLI calls
    token = (await auth_service.login(address, PASSWORD, "10.7.7.7")).access_token  # fresh roles
    auth = bearer(token)

    image_id = await db.fetchval("SELECT id FROM auction_images WHERE auction_id = $1", draft)
    refused = [
        await api.patch(f"/api/v1/auctions/{draft}", json={"title": "Mine"}, headers=auth),
        await api.post(f"/api/v1/auctions/{draft}/publish", headers=auth),
        await api.post(f"/api/v1/auctions/{draft}/images", content=png(), headers=auth),
        await api.delete(f"/api/v1/auctions/{draft}/images/{image_id}", headers=auth),
    ]
    assert [(r.status_code, _code(r)) for r in refused] == [(403, "FORBIDDEN")] * 4
    for auction_id in (draft, scheduled):
        r = await api.post(f"/api/v1/auctions/{auction_id}/cancel", headers=auth)
        assert r.status_code == 200 and r.json()["status"] == "cancelled"


async def test_starting_price_below_gateway_minimum_refused_on_edit(
    api: AsyncClient, make_user, seeded
) -> None:
    """FR-12: create and edit both keep the starting price at or above the payment gateway's
    100 paise minimum, so every sale can be paid."""
    seller_id, token = await make_user("seller")
    draft = await seeded(seller_id, "draft")
    r = await api.patch(
        f"/api/v1/auctions/{draft}", json={"starting_price": 99}, headers=bearer(token)
    )
    assert r.status_code == 422 and "starting_price" in r.json()["error"]["fields"]
    ok = await api.patch(
        f"/api/v1/auctions/{draft}", json={"starting_price": 100}, headers=bearer(token)
    )
    assert ok.status_code == 200


@pytest.mark.parametrize("first", ["bids", "proxy"])
async def test_seller_bid_refused_over_rest(
    api: AsyncClient, make_user, seeded, bid_service: BidService, first: str
) -> None:
    """Decision 2026-10-13: SELLER_CANNOT_BID (403) on the REST fallback for bids and maxima, in
    either order: the first with the seller lookup cold, the second warm (cached)."""
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "live", start_in_s=-60)
    bodies = {
        "bids": {"amount": 10_000, "request_id": "own-1"},
        "proxy": {"max_amount": 50_000, "request_id": "own-2"},
    }
    assert auction_id not in bid_service._sellers  # cold
    for path in (first, "proxy" if first == "bids" else "bids"):
        r = await api.post(
            f"/api/v1/auctions/{auction_id}/{path}", json=bodies[path], headers=bearer(token)
        )
        assert (r.status_code, _code(r)) == (403, "SELLER_CANNOT_BID")
        assert bid_service._sellers[auction_id] == seller_id  # warm from here on


async def test_image_upload_body_is_declared_in_openapi(api: AsyncClient) -> None:
    """NFR-24: the raw image body is documented, so Swagger offers a file upload."""
    spec = (await api.get("/openapi.json")).json()
    body = spec["paths"]["/api/v1/auctions/{auction_id}/images"]["post"]["requestBody"]
    assert body["required"] is True
    assert set(body["content"]) == {"image/jpeg", "image/png", "image/webp"}
    assert {c["schema"]["format"] for c in body["content"].values()} == {"binary"}
