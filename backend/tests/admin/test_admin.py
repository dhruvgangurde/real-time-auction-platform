"""Administrator endpoints and the settlement read (FR-51, FR-63, FR-64, FR-75, FR-76, FR-80,
BR-9, BR-10, BR-12, NFR-14), through the app in process against real Postgres and Redis."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.services import admin as admin_module
from app.services.bidding import BidService
from app.services.closing import Closer, Outcome
from app.services.denylist import resync
from app.services.live_events import user_channel
from app.services.live_state import from_ms, outbox_key, proxies_key, state_key
from tests.auth.conftest import PASSWORD, FakeClock
from tests.bidding.conftest import redis_now_ms
from tests.closing.conftest import Ended, Room, settlement_rows, terminal_audits
from tests.lifecycle.conftest import auction_body, bearer

REASON = {"reason": "Seller reported the item as damaged"}


async def _audits(db: asyncpg.Connection, entity_id: Any, action: str) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT actor_id, before, after, reason FROM audit_log WHERE entity_id = $1 "
        "AND action = $2 ORDER BY id",
        str(entity_id),
        action,
    )


async def _status(db: asyncpg.Connection, auction_id: UUID) -> str:
    return await db.fetchval("SELECT status FROM auctions WHERE id = $1", auction_id)


async def _ids(db: asyncpg.Connection, *users: tuple[UUID, str]) -> list[UUID]:
    return [u[0] for u in users]


# ------------------------------------------------------------------ access (FR-75)

ENDPOINTS = [
    ("POST", "/api/v1/admin/auctions/{a}/force-close", REASON),
    ("POST", "/api/v1/admin/auctions/{a}/cancel", REASON),
    ("GET", "/api/v1/admin/users", None),
    ("POST", "/api/v1/admin/users/{u}/suspend", REASON),
    ("POST", "/api/v1/admin/users/{u}/reinstate", REASON),
    ("PUT", "/api/v1/admin/users/{u}/roles/seller", REASON),
    ("DELETE", "/api/v1/admin/users/{u}/roles/seller", REASON),
    ("GET", "/api/v1/admin/audit", None),
]
STATE_CHANGES = [e for e in ENDPOINTS if e[2] is not None]


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
async def test_non_admin_is_forbidden(
    api: AsyncClient, make_user: Any, method: str, path: str, body: dict | None
) -> None:
    """FR-75, FR-80: every administrative endpoint, audit reads included, needs the admin role;
    sellers and bidders get FORBIDDEN and anonymous callers INVALID_TOKEN."""
    _, token = await make_user("seller")
    url = path.format(a=uuid4(), u=uuid4())
    response = await api.request(method, url, json=body, headers=bearer(token))
    assert (response.status_code, response.json()["error"]["code"]) == (403, "FORBIDDEN")
    response = await api.request(method, url, json=body)
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), STATE_CHANGES)
@pytest.mark.parametrize(
    "bad", [None, {}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 501}]
)
async def test_reason_is_required(
    api: AsyncClient,
    make_user: Any,
    method: str,
    path: str,
    body: dict,
    bad: dict | None,
) -> None:
    """FR-75, BR-12: no state change without a reason of 1 to 500 characters."""
    _, token = await make_user("admin")
    url = path.format(a=uuid4(), u=uuid4())
    response = await api.request(method, url, json=bad, headers=bearer(token))
    assert (response.status_code, response.json()["error"]["code"]) == (422, "VALIDATION_ERROR")


# ------------------------------------------------------------------ force-close (FR-63)


async def test_force_close_live_auction_end_to_end(
    api: AsyncClient,
    make_user: Any,
    ended: Ended,
    bid_service: BidService,
    db: asyncpg.Connection,
    redis: Redis,
) -> None:
    """FR-63, NFR-14: a live auction ends now. Redis is flipped first (no bid after it), the
    outbox drained, Postgres moved to closing with the forced end time and an audit row carrying
    the administrator and the reason, then the same close path settles it."""
    admin_id, token = await make_user("admin")
    b1, b2 = (await make_user())[0], (await make_user())[0]
    auction_id = await ended(status="live", end_s=3600)
    original = await db.fetchval("SELECT end_time FROM auctions WHERE id = $1", auction_id)
    assert (await bid_service.place_bid(auction_id, b1, 10_000, "a")).accepted
    assert (await bid_service.place_bid(auction_id, b2, 10_500, "b")).accepted
    assert await redis.xlen(outbox_key(auction_id)) == 2  # not yet in the ledger

    before = await redis_now_ms(redis)
    async with Room(auction_id) as room:
        response = await api.post(
            f"/api/v1/admin/auctions/{auction_id}/force-close", json=REASON, headers=bearer(token)
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["outcome"]) == ("closed_sold", "settled")
    assert body["settlement"]["winner"]["id"] == str(b2)
    assert body["settlement"]["final_price"] == 10_500
    assert (await bid_service.place_bid(auction_id, b1, 20_000, "c")).code == "AUCTION_CLOSED"

    row = await db.fetchrow(
        "SELECT end_time, original_end_time FROM auctions WHERE id = $1", auction_id
    )
    assert from_ms(before) <= row["end_time"] < original  # ended now, not at the scheduled time
    assert row["original_end_time"] == original  # FR-14, NFR-15
    (forced,) = await _audits(db, auction_id, "auction_force_closed")
    assert (forced["actor_id"], forced["reason"]) == (admin_id, REASON["reason"])
    assert json.loads(forced["before"])["status"] == "live"
    assert json.loads(forced["after"])["status"] == "closing"
    assert len(await terminal_audits(db, auction_id)) == 1
    assert await db.fetchval("SELECT count(*) FROM bids WHERE auction_id = $1", auction_id) == 2
    assert [e["outcome"] for e in room.events] == ["closed_sold"]
    assert not await redis.exists(state_key(auction_id))


async def test_force_close_retry_after_failure_between_redis_and_postgres(
    api: AsyncClient,
    make_user: Any,
    ended: Ended,
    bid_service: BidService,
    db: asyncpg.Connection,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-63, NFR-11: a failure after the Redis flip and before the Postgres commit. The flip
    stands (no bid accepted), Postgres is unchanged; the repeated call completes with one audit
    row and one settlement, at the instant of the first flip."""
    admin_id, token = await make_user("admin")
    (b1,) = await _ids(db, await make_user())
    auction_id = await ended(status="live", end_s=3600)
    assert (await bid_service.place_bid(auction_id, b1, 10_000, "a")).accepted

    async def drain_fails(*_: Any) -> int:
        raise RedisConnectionError("redis down")

    monkeypatch.setattr(admin_module, "persist_outbox", drain_fails)
    url = f"/api/v1/admin/auctions/{auction_id}/force-close"
    response = await api.post(url, json=REASON, headers=bearer(token))
    assert (response.status_code, response.json()["error"]["code"]) == (503, "SERVICE_UNAVAILABLE")
    assert await redis.hget(state_key(auction_id), "status") == "closing"
    forced_ms = int(await redis.hget(state_key(auction_id), "end_time_ms"))
    assert await _status(db, auction_id) == "live"
    assert await _audits(db, auction_id, "auction_force_closed") == []
    assert (await bid_service.place_bid(auction_id, b1, 20_000, "b")).code == "AUCTION_CLOSED"

    monkeypatch.undo()
    response = await api.post(url, json=REASON, headers=bearer(token))
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "settled"
    assert len(await _audits(db, auction_id, "auction_force_closed")) == 1
    assert len(await settlement_rows(db, auction_id)) == 1
    end_time = await db.fetchval("SELECT end_time FROM auctions WHERE id = $1", auction_id)
    assert end_time == from_ms(forced_ms)


async def test_force_close_by_status(
    api: AsyncClient, make_user: Any, ended: Ended, db: asyncpg.Connection, close_queue: list
) -> None:
    """FR-63: draft and scheduled are not live; terminal is ALREADY_CLOSED; an auction already
    closing runs the same close path, audited with the reason."""
    admin_id, token = await make_user("admin")

    async def call(auction_id: Any) -> Any:
        return await api.post(
            f"/api/v1/admin/auctions/{auction_id}/force-close", json=REASON, headers=bearer(token)
        )

    for status, code in (
        ("draft", "AUCTION_NOT_LIVE"),
        ("scheduled", "AUCTION_NOT_LIVE"),
        ("closed_sold", "ALREADY_CLOSED"),
        ("closed_no_sale", "ALREADY_CLOSED"),
        ("cancelled", "ALREADY_CLOSED"),
    ):
        response = await call(await ended(status=status, live_state=False))
        assert (response.status_code, response.json()["error"]["code"]) == (409, code), status
    response = await call(uuid4())
    assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")

    closing = await ended()
    response = await call(closing)
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["outcome"]) == ("closed_no_sale", "settled")
    (forced,) = await _audits(db, closing, "auction_force_closed")
    assert (forced["actor_id"], forced["reason"]) == (admin_id, REASON["reason"])
    assert close_queue == []


async def test_force_close_hands_a_deferred_close_to_the_task(
    api: AsyncClient,
    make_user: Any,
    ended: Ended,
    redis: Redis,
    close_queue: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, token = await make_user("admin")
    auction_id = await ended()

    async def deferred(self: Closer, _: UUID) -> Outcome:
        return Outcome.DEFERRED

    monkeypatch.setattr(Closer, "close", deferred)
    response = await api.post(
        f"/api/v1/admin/auctions/{auction_id}/force-close", json=REASON, headers=bearer(token)
    )
    assert (response.json()["status"], response.json()["outcome"]) == ("closing", "deferred")
    assert close_queue == [auction_id]


# ------------------------------------------------------------------ cancel (BR-9)


async def test_admin_cancel_of_live_auction(
    api: AsyncClient,
    make_user: Any,
    ended: Ended,
    bid_service: BidService,
    db: asyncpg.Connection,
    redis: Redis,
) -> None:
    """BR-9, FR-51: Redis first (bids refused), outbox drained into the ledger, Postgres
    cancelled with the reason, maxima deactivated, the room told, live state removed, and no
    settlement."""
    admin_id, token = await make_user("admin")
    (b1,) = await _ids(db, await make_user())
    auction_id = await ended(status="live", end_s=3600, proxies=[b1])
    assert (await bid_service.place_bid(auction_id, b1, 10_000, "a")).accepted

    async with Room(auction_id) as room:
        response = await api.post(
            f"/api/v1/admin/auctions/{auction_id}/cancel", json=REASON, headers=bearer(token)
        )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert await _status(db, auction_id) == "cancelled"
    assert await db.fetchval("SELECT count(*) FROM bids WHERE auction_id = $1", auction_id) == 1
    assert not await db.fetchval(
        "SELECT bool_or(is_active) FROM proxy_bids WHERE auction_id = $1", auction_id
    )
    assert await settlement_rows(db, auction_id) == []
    (row,) = await _audits(db, auction_id, "status_changed")
    assert (row["actor_id"], row["reason"]) == (admin_id, REASON["reason"])
    assert json.loads(row["after"])["status"] == "cancelled"
    (event,) = room.events
    assert (event["type"], event["outcome"], event["final_sequence_no"]) == (
        "auction_closed",
        "cancelled",
        1,
    )
    assert not await redis.exists(state_key(auction_id), proxies_key(auction_id))
    assert (await bid_service.place_bid(auction_id, b1, 20_000, "b")).code == "AUCTION_CLOSED"


async def test_admin_cancel_retry_after_failure_between_redis_and_postgres(
    api: AsyncClient,
    make_user: Any,
    ended: Ended,
    bid_service: BidService,
    db: asyncpg.Connection,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BR-9: the Redis flip stands after a failure (bids refused); the repeated call completes
    with one audit row and no settlement."""
    _, token = await make_user("admin")
    (b1,) = await _ids(db, await make_user())
    auction_id = await ended(status="live", end_s=3600)

    async def drain_fails(*_: Any) -> int:
        raise RedisConnectionError("redis down")

    monkeypatch.setattr(admin_module, "persist_outbox", drain_fails)
    url = f"/api/v1/admin/auctions/{auction_id}/cancel"
    response = await api.post(url, json=REASON, headers=bearer(token))
    assert response.status_code == 503
    assert await redis.hget(state_key(auction_id), "status") == "cancelled"
    assert await _status(db, auction_id) == "live"
    assert (await bid_service.place_bid(auction_id, b1, 10_000, "a")).code == "AUCTION_CLOSED"

    monkeypatch.undo()
    response = await api.post(url, json=REASON, headers=bearer(token))
    assert response.status_code == 200, response.text
    assert await _status(db, auction_id) == "cancelled"
    assert len(await _audits(db, auction_id, "status_changed")) == 1
    assert await settlement_rows(db, auction_id) == []
    assert not await redis.exists(state_key(auction_id))


async def test_admin_cancel_by_status(
    api: AsyncClient, make_user: Any, ended: Ended, db: asyncpg.Connection
) -> None:
    """BR-9: draft and scheduled are cancelled with the reason; closing and terminal are
    ALREADY_CLOSED."""
    admin_id, token = await make_user("admin")
    for status in ("draft", "scheduled"):
        auction_id = await ended(status=status, end_s=3600, live_state=False)
        response = await api.post(
            f"/api/v1/admin/auctions/{auction_id}/cancel", json=REASON, headers=bearer(token)
        )
        assert response.status_code == 200
        (row,) = await _audits(db, auction_id, "status_changed")
        assert (row["actor_id"], json.loads(row["before"])["status"]) == (admin_id, status)
    for status in ("closing", "closed_sold", "cancelled"):
        auction_id = await ended(status=status, live_state=False)
        response = await api.post(
            f"/api/v1/admin/auctions/{auction_id}/cancel", json=REASON, headers=bearer(token)
        )
        assert (response.status_code, response.json()["error"]["code"]) == (409, "ALREADY_CLOSED")


# ------------------------------------------------------------------ users (FR-76)


async def test_suspend_then_reinstate(
    api: AsyncClient,
    make_user: Any,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    auth_service: Any,
    clock: FakeClock,
) -> None:
    """FR-76, FR-6: suspension blocks every request at once; reinstatement lets a fresh login
    work immediately, and the denylist resync does not block it again. The old session stays
    revoked."""
    admin_id, token = await make_user("admin")
    user_id, old_token = await make_user()
    email = await db.fetchval("SELECT email FROM users WHERE id = $1", user_id)
    url = f"/api/v1/admin/users/{user_id}"

    response = await api.post(f"{url}/suspend", json=REASON, headers=bearer(token))
    assert response.json()["status"] == "suspended"
    me = await api.get("/api/v1/users/me", headers=bearer(old_token))
    assert me.json()["error"]["code"] == "ACCOUNT_SUSPENDED"

    response = await api.post(f"{url}/reinstate", json=REASON, headers=bearer(token))
    assert response.status_code == 200 and response.json()["status"] == "active"
    fresh = (await auth_service.login(email, PASSWORD, uuid4().hex)).access_token
    assert (await api.get("/api/v1/users/me", headers=bearer(fresh))).status_code == 200
    # a resync that raced the reinstatement and rewrote the key: the next resync removes it
    await redis.set(f"revoked:user:{user_id}", "1")
    await resync(redis, sessions, clock)
    assert (await api.get("/api/v1/users/me", headers=bearer(fresh))).status_code == 200
    old = await api.get("/api/v1/users/me", headers=bearer(old_token))
    assert old.json()["error"]["code"] == "INVALID_TOKEN"

    actions = await db.fetch(
        "SELECT action, actor_id, reason FROM audit_log WHERE entity_id = $1 "
        "AND action IN ('user_suspended', 'user_reinstated') ORDER BY id",
        str(user_id),
    )
    assert [(a["action"], a["actor_id"], a["reason"]) for a in actions] == [
        ("user_suspended", admin_id, REASON["reason"]),
        ("user_reinstated", admin_id, REASON["reason"]),
    ]

    again = await api.post(f"{url}/reinstate", json=REASON, headers=bearer(token))
    assert (again.status_code, again.json()["error"]["code"]) == (409, "NOT_SUSPENDED")
    missing = await api.post(
        f"/api/v1/admin/users/{uuid4()}/reinstate", json=REASON, headers=bearer(token)
    )
    assert missing.status_code == 404


async def test_role_grant_and_revoke_are_audited(
    api: AsyncClient, make_user: Any, db: asyncpg.Connection
) -> None:
    """FR-75, BR-12, D6: roles change through the admin API with the actor and the reason."""
    admin_id, token = await make_user("admin")
    user_id, _ = await make_user()
    url = f"/api/v1/admin/users/{user_id}/roles/seller"

    response = await api.put(url, json=REASON, headers=bearer(token))
    assert response.json()["roles"] == ["bidder", "seller"]
    response = await api.request("DELETE", url, json=REASON, headers=bearer(token))
    assert response.json()["roles"] == ["bidder"]
    rows = await db.fetch(
        "SELECT action, actor_id, reason, before, after FROM audit_log WHERE entity_id = $1 "
        "AND action LIKE 'role_%' ORDER BY id",
        str(user_id),
    )
    assert [(r["action"], r["actor_id"], r["reason"]) for r in rows] == [
        ("role_granted", admin_id, REASON["reason"]),
        ("role_revoked", admin_id, REASON["reason"]),
    ]
    assert json.loads(rows[0]["after"]) == {"roles": ["bidder", "seller"]}
    bad = await api.put(
        f"/api/v1/admin/users/{user_id}/roles/owner", json=REASON, headers=bearer(token)
    )
    assert bad.status_code == 422


async def test_user_search_filters_and_pages(
    api: AsyncClient, make_user: Any, db: asyncpg.Connection
) -> None:
    """FR-76: free text over email and name (wildcards literal), role and status, paged."""
    _, token = await make_user("admin")
    tag = uuid4().hex[:12]
    users = [(await make_user())[0] for _ in range(3)]
    for i, user_id in enumerate(users):
        await db.execute("UPDATE users SET full_name = $2 WHERE id = $1", user_id, f"Zed {tag} {i}")
    await db.execute("INSERT INTO user_roles (user_id, role) VALUES ($1, 'seller')", users[0])
    await db.execute("UPDATE users SET status = 'suspended' WHERE id = $1", users[1])

    async def search(**params: Any) -> dict:
        response = await api.get("/api/v1/admin/users", params=params, headers=bearer(token))
        assert response.status_code == 200, response.text
        return response.json()

    first = await search(q=tag.upper(), page_size=2)  # case-insensitive
    second = await search(q=tag, page_size=2, page=2)
    assert first["has_more"] and not second["has_more"]
    assert {i["id"] for i in first["items"] + second["items"]} == {str(u) for u in users}
    assert [i["id"] for i in (await search(q=tag, role="seller"))["items"]] == [str(users[0])]
    assert [i["id"] for i in (await search(q=tag, status="suspended"))["items"]] == [str(users[1])]
    assert (await search(q=f"{tag}%"))["items"] == []  # a LIKE wildcard matches literally
    email = await db.fetchval("SELECT email FROM users WHERE id = $1", users[2])
    (found,) = (await search(q=email))["items"]
    assert (found["id"], found["roles"]) == (str(users[2]), ["bidder"])
    bad = await api.get("/api/v1/admin/users?status=banned", headers=bearer(token))
    assert bad.status_code == 422


# ------------------------------------------------------------------ audit (FR-80)


async def test_audit_query_filters_and_pages(
    api: AsyncClient, make_user: Any, db: asyncpg.Connection
) -> None:
    """FR-80: by actor, entity type, entity reference and date range, newest first, paged, with
    the before and after state of each entry."""
    admin_id, token = await make_user("admin")
    users = [(await make_user())[0] for _ in range(3)]
    for user_id in users:
        await api.put(
            f"/api/v1/admin/users/{user_id}/roles/seller", json=REASON, headers=bearer(token)
        )

    async def query(**params: Any) -> dict:
        response = await api.get("/api/v1/admin/audit", params=params, headers=bearer(token))
        assert response.status_code == 200, response.text
        return response.json()

    page = await query(actor_id=str(admin_id), limit=2)
    assert [i["entity_id"] for i in page["items"]] == [str(users[2]), str(users[1])]
    rest = await query(actor_id=str(admin_id), limit=2, before_id=page["next_before_id"])
    assert [i["entity_id"] for i in rest["items"]] == [str(users[0])]
    assert rest["next_before_id"] is None

    (entry,) = (await query(entity_type="user", entity_id=str(users[1]), actor_id=str(admin_id)))[
        "items"
    ]
    assert entry["action"] == "role_granted" and entry["reason"] == REASON["reason"]
    assert entry["before"] == {"roles": ["bidder"]}
    assert entry["after"] == {"roles": ["bidder", "seller"]}

    now = datetime.now(UTC)
    window = {"actor_id": str(admin_id)}
    assert (
        len((await query(**window, **{"from": (now - timedelta(hours=1)).isoformat()}))["items"])
        == 3
    )
    assert (await query(**window, **{"from": (now + timedelta(hours=1)).isoformat()}))[
        "items"
    ] == []
    assert (await query(**window, to=(now - timedelta(hours=1)).isoformat()))["items"] == []
    assert (await query(entity_type="nothing-here"))["items"] == []


# ------------------------------------------------------------------ settlement read (FR-64)


async def test_settlement_visible_to_winner_seller_and_admins_only(
    api: AsyncClient,
    make_user: Any,
    ended: Ended,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """FR-64, BR-10, BR-6: the winner, the seller (who sees the winner's identity once the
    settlement exists) and administrators; anyone else FORBIDDEN; never the reserve."""
    seller_id, seller = await make_user("seller")
    winner_id, winner = await make_user()
    loser_id, loser = await make_user()
    _, admin = await make_user("admin")
    _, stranger = await make_user()
    auction_id = await ended(
        bids=[(loser_id, 10_000), (winner_id, 10_500)], reserve=10_400, seller=seller_id
    )
    url = f"/api/v1/auctions/{auction_id}/settlement"

    before = await api.get(url, headers=bearer(seller))
    assert (before.status_code, before.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert (await api.get(url, headers=bearer(loser))).status_code == 403

    assert await Closer(redis, sessions).close(auction_id) is Outcome.SETTLED
    for token in (winner, seller, admin):
        response = await api.get(url, headers=bearer(token))
        assert response.status_code == 200
        body = response.json()
        assert (body["status"], body["final_price"]) == ("pending_payment", 10_500)
        assert body["winner"] == {"id": str(winner_id), "full_name": "Phase Seven"}
        assert "10400" not in response.text and "reserve" not in response.text
    for token in (loser, stranger):
        response = await api.get(url, headers=bearer(token))
        assert (response.status_code, response.json()["error"]["code"]) == (403, "FORBIDDEN")
    assert (await api.get(url)).status_code == 401
    missing = await api.get(f"/api/v1/auctions/{uuid4()}/settlement", headers=bearer(admin))
    assert missing.status_code == 404


async def test_admin_flip_edge_states(
    api: AsyncClient, make_user: Any, ended: Ended, db: asyncpg.Connection, redis: Redis
) -> None:
    """FR-63, BR-9: lost live state is rebuilt before the flip; an auction Redis already moved
    elsewhere is ALREADY_CLOSED; a rebuild in progress is a retryable SERVICE_UNAVAILABLE."""
    _, token = await make_user("admin")

    async def call(action: str, auction_id: UUID) -> Any:
        return await api.post(
            f"/api/v1/admin/auctions/{auction_id}/{action}", json=REASON, headers=bearer(token)
        )

    lost = await ended(status="live", end_s=3600, live_state=False)
    response = await call("force-close", lost)
    assert (response.status_code, response.json()["status"]) == (200, "closed_no_sale")

    ended_meanwhile = await ended(status="live", end_s=3600)
    await redis.hset(state_key(ended_meanwhile), "status", "closing")
    response = await call("cancel", ended_meanwhile)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "ALREADY_CLOSED")

    being_cancelled = await ended(status="live", end_s=3600)
    await redis.hset(state_key(being_cancelled), "status", "cancelled")
    response = await call("force-close", being_cancelled)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "ALREADY_CLOSED")

    rebuilding = await ended(status="live", end_s=3600)
    await redis.hset(state_key(rebuilding), "status", "rebuilding")
    response = await call("force-close", rebuilding)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "SERVICE_UNAVAILABLE")
    assert await _status(db, rebuilding) == "live"


async def test_admin_cancel_of_an_auction_that_went_live_meanwhile(
    api: AsyncClient,
    make_user: Any,
    ended: Ended,
    db: asyncpg.Connection,
    redis: Redis,
    admin_service: admin_module.AdminService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BR-9: read as scheduled, live by the time of the write: the conditional transition
    misses, and the cancel is decided again as a live one (Redis flipped, state removed)."""
    _, token = await make_user("admin")
    auction_id = await ended(status="live", end_s=3600)
    load = admin_service._load
    calls = 0

    async def stale_first(a: UUID) -> Any:
        nonlocal calls
        calls += 1
        auction = await load(a)
        if calls == 1:
            auction.status = "scheduled"  # as read just before the scheduler activated it
        return auction

    monkeypatch.setattr(admin_service, "_load", stale_first)
    response = await api.post(
        f"/api/v1/admin/auctions/{auction_id}/cancel", json=REASON, headers=bearer(token)
    )
    assert response.status_code == 200, response.text
    assert await _status(db, auction_id) == "cancelled"
    assert not await redis.exists(state_key(auction_id))
    (row,) = await _audits(db, auction_id, "status_changed")
    assert json.loads(row["before"])["status"] == "live"


# ------------------------------------------------------------------ role revocation (FR-6, FR-75)


async def _login(auth_service: Any, db: asyncpg.Connection, user_id: UUID) -> Any:
    """A fresh session: access token plus the refresh token the cookie would carry."""
    email = await db.fetchval("SELECT email FROM users WHERE id = $1", user_id)
    return await auth_service.login(email, PASSWORD, uuid4().hex)


async def _sessions_open(db: asyncpg.Connection, user_id: UUID) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM sessions WHERE user_id = $1 AND revoked_at IS NULL", user_id
    )


async def test_demoted_admin_is_locked_out_at_once(
    api: AsyncClient, make_user: Any, db: asyncpg.Connection, auth_service: Any
) -> None:
    """FR-6, FR-75: revoking a role revokes every session of the user. The old access token
    gets 401 everywhere, so a demoted admin can neither re-grant itself admin nor suspend the
    admin who demoted it; the refresh token is dead too; a fresh login carries no admin role;
    open sockets are told to close (session_revoked)."""
    boss_id, boss = await make_user("admin")
    demoted_id, _ = await make_user("admin")
    tokens = await _login(auth_service, db, demoted_id)
    old = bearer(tokens.access_token)
    assert (await api.get("/api/v1/admin/users", headers=old)).status_code == 200

    async with Room(channel=user_channel(demoted_id)) as sockets:
        response = await api.request(
            "DELETE",
            f"/api/v1/admin/users/{demoted_id}/roles/admin",
            json=REASON,
            headers=bearer(boss),
        )
    assert response.status_code == 200 and response.json()["roles"] == ["bidder"]
    assert [(e["type"], e["sid"]) for e in sockets.events] == [("session_revoked", None)]

    for method, path in (
        ("GET", "/api/v1/admin/users"),
        ("PUT", f"/api/v1/admin/users/{demoted_id}/roles/admin"),
        ("POST", f"/api/v1/admin/users/{boss_id}/suspend"),
    ):
        refused = await api.request(method, path, json=REASON, headers=old)
        assert (refused.status_code, refused.json()["error"]["code"]) == (401, "INVALID_TOKEN")
    roles = await db.fetch("SELECT role FROM user_roles WHERE user_id = $1", demoted_id)
    assert [r["role"] for r in roles] == ["bidder"]
    assert await db.fetchval("SELECT status FROM users WHERE id = $1", boss_id) == "active"

    with pytest.raises(AppError) as info:
        await auth_service.refresh(tokens.refresh_token)
    assert info.value.code == "INVALID_TOKEN"
    fresh = bearer((await _login(auth_service, db, demoted_id)).access_token)
    assert (await api.get("/api/v1/users/me", headers=fresh)).json()["roles"] == ["bidder"]
    again = await api.get("/api/v1/admin/users", headers=fresh)
    assert (again.status_code, again.json()["error"]["code"]) == (403, "FORBIDDEN")

    (row,) = await _audits(db, demoted_id, "role_revoked")
    assert (row["actor_id"], row["reason"]) == (boss_id, REASON["reason"])
    assert json.loads(row["after"]) == {"roles": ["bidder"], "role": "admin", "sessions_revoked": 2}


async def test_revoked_seller_must_log_in_again(
    api: AsyncClient,
    make_user: Any,
    db: asyncpg.Connection,
    auth_service: Any,
    category: Any,
    clock: FakeClock,
) -> None:
    """BR-1, FR-6: a stale seller token cannot keep creating, editing or publishing."""
    _, admin = await make_user("admin")
    seller_id, old = await make_user("seller")
    body = auction_body(clock, await category())
    created = await api.post("/api/v1/auctions", json=body, headers=bearer(old))
    assert created.status_code == 201
    auction_id = created.json()["id"]

    response = await api.request(
        "DELETE",
        f"/api/v1/admin/users/{seller_id}/roles/seller",
        json=REASON,
        headers=bearer(admin),
    )
    assert response.json()["roles"] == ["bidder"]
    assert await _sessions_open(db, seller_id) == 0
    for refused in (
        await api.post("/api/v1/auctions", json=body, headers=bearer(old)),
        await api.patch(f"/api/v1/auctions/{auction_id}", json={"title": "x"}, headers=bearer(old)),
        await api.post(f"/api/v1/auctions/{auction_id}/publish", headers=bearer(old)),
    ):
        assert (refused.status_code, refused.json()["error"]["code"]) == (401, "INVALID_TOKEN")
    fresh = bearer((await _login(auth_service, db, seller_id)).access_token)
    refused = await api.post("/api/v1/auctions", json=body, headers=fresh)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "FORBIDDEN")


async def test_granting_a_role_keeps_sessions(
    api: AsyncClient, make_user: Any, db: asyncpg.Connection
) -> None:
    """A grant revokes nothing: the role arrives with the next login (D6)."""
    _, admin = await make_user("admin")
    user_id, token = await make_user()
    response = await api.put(
        f"/api/v1/admin/users/{user_id}/roles/seller", json=REASON, headers=bearer(admin)
    )
    assert response.json()["roles"] == ["bidder", "seller"]
    me = await api.get("/api/v1/users/me", headers=bearer(token))
    assert me.status_code == 200
    assert await _sessions_open(db, user_id) == 1
    (row,) = await _audits(db, user_id, "role_granted")
    assert json.loads(row["after"]) == {"roles": ["bidder", "seller"]}


# ------------------------------------------------------------------ admin lockout (FR-75)


async def _only_active_admins(db: asyncpg.Connection, *keep: UUID) -> None:
    """Earlier tests leave active admins behind; for a last-admin check only `keep` count."""
    await db.execute(
        "UPDATE users SET status = 'suspended' WHERE status = 'active' AND NOT (id = ANY($1)) "
        "AND id IN (SELECT user_id FROM user_roles WHERE role = 'admin')",
        list(keep),
    )


async def test_admin_cannot_suspend_or_demote_themselves(
    api: AsyncClient, make_user: Any, db: asyncpg.Connection
) -> None:
    """FR-75: CANNOT_TARGET_SELF; nothing changes and the admin keeps working."""
    admin_id, token = await make_user("admin")
    url = f"/api/v1/admin/users/{admin_id}"
    for method, path in (("POST", f"{url}/suspend"), ("DELETE", f"{url}/roles/admin")):
        refused = await api.request(method, path, json=REASON, headers=bearer(token))
        assert (refused.status_code, refused.json()["error"]["code"]) == (
            409,
            "CANNOT_TARGET_SELF",
        )
    assert await db.fetchval("SELECT status FROM users WHERE id = $1", admin_id) == "active"
    roles = await db.fetch("SELECT role FROM user_roles WHERE user_id = $1", admin_id)
    assert "admin" in {r["role"] for r in roles}
    assert (await api.get("/api/v1/admin/users", headers=bearer(token))).status_code == 200
    # removing one's own non-admin role is not a lockout risk
    own_seller = await api.request(
        "DELETE", f"{url}/roles/seller", json=REASON, headers=bearer(token)
    )
    assert own_seller.status_code == 200


async def test_two_admins_suspending_each_other_keep_one(
    make_user: Any, user_service: Any, db: asyncpg.Connection
) -> None:
    """FR-75: the only two active admins suspend each other at the same moment; the admin-set
    lock decides them in turn, so one succeeds and the other is LAST_ADMIN."""
    first, _ = await make_user("admin")
    second, _ = await make_user("admin")
    await _only_active_admins(db, first, second)
    outcomes = await asyncio.gather(
        user_service.suspend_user(first, second, "mutual"),
        user_service.suspend_user(second, first, "mutual"),
        return_exceptions=True,
    )
    errors = [o for o in outcomes if isinstance(o, Exception)]
    assert len(errors) == 1 and getattr(errors[0], "code", None) == "LAST_ADMIN"
    statuses = await db.fetch(
        "SELECT status FROM users WHERE id = ANY($1::uuid[])", [first, second]
    )
    assert sorted(r["status"] for r in statuses) == ["active", "suspended"]


async def test_last_active_admin_cannot_be_demoted(
    api: AsyncClient, make_user: Any, user_service: Any, db: asyncpg.Connection
) -> None:
    """FR-75: with one active admin left, neither the operator nor anyone else can remove
    that admin role or suspend that account."""
    last, token = await make_user("admin")
    await _only_active_admins(db, last)
    for call in (
        user_service.change_role(last, "admin", grant=False, reason="cli"),
        user_service.suspend_user(uuid4(), last, "operator"),
    ):
        with pytest.raises(AppError) as info:
            await call
        assert info.value.code == "LAST_ADMIN"
    assert (await api.get("/api/v1/admin/users", headers=bearer(token))).status_code == 200
    # with a second active admin the first can be demoted again
    other, other_token = await make_user("admin")
    response = await api.request(
        "DELETE",
        f"/api/v1/admin/users/{last}/roles/admin",
        json=REASON,
        headers=bearer(other_token),
    )
    assert response.json()["roles"] == ["bidder"]
