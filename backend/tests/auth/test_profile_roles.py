"""Profile, roles and own bid history (FR-7, FR-9, FR-10, BR-2)."""

from datetime import timedelta

import asyncpg
from httpx import AsyncClient

from tests.auth.conftest import RecordingEmail, bearer, fresh_email, login, register_verified
from tests.db.conftest import NOW, make_auction


async def test_get_and_patch_profile(client: AsyncClient, email: RecordingEmail) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    token = await login(client, address)
    me = await client.get("/api/v1/users/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["email"] == address and me.json()["status"] == "active"
    assert me.json()["roles"] == ["bidder"] and me.json()["avatar_url"] is None

    patched = await client.patch(
        "/api/v1/users/me",
        headers=bearer(token),
        json={"full_name": "Grace Hopper", "avatar_url": "https://img.example.com/g.png"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["full_name"] == "Grace Hopper"
    assert patched.json()["avatar_url"] == "https://img.example.com/g.png"

    cleared = await client.patch(
        "/api/v1/users/me", headers=bearer(token), json={"clear_avatar": True}
    )
    assert cleared.json()["avatar_url"] is None and cleared.json()["full_name"] == "Grace Hopper"


async def test_email_status_and_roles_are_not_self_editable(
    client: AsyncClient, email: RecordingEmail
) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    token = await login(client, address)
    for field in ({"email": "x@example.com"}, {"status": "suspended"}, {"roles": ["admin"]}):
        r = await client.patch("/api/v1/users/me", headers=bearer(token), json=field)
        assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR", field
    bad_url = await client.patch(
        "/api/v1/users/me", headers=bearer(token), json={"avatar_url": "javascript:alert(1)"}
    )
    assert bad_url.status_code == 422


async def test_missing_or_garbage_bearer_rejected(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/users/me")).status_code == 401
    r = await client.get("/api/v1/users/me", headers=bearer("not.a.jwt"))
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"


async def test_roles_enforced_and_multiple_roles_allowed(
    client: AsyncClient, email: RecordingEmail, db: asyncpg.Connection
) -> None:
    address = fresh_email()
    user_id = await register_verified(client, email, address)
    token = await login(client, address)
    forbidden = await client.get("/_test/admin-only", headers=bearer(token))
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "FORBIDDEN"
    assert (await client.get("/_test/seller-or-bidder", headers=bearer(token))).status_code == 200

    # BR-2: a seller is still a bidder; roles are read at login
    await db.execute("INSERT INTO user_roles (user_id, role) VALUES ($1, 'seller')", user_id)
    token = await login(client, address)
    me = await client.get("/api/v1/users/me", headers=bearer(token))
    assert me.json()["roles"] == ["bidder", "seller"]
    assert (await client.get("/_test/seller-or-bidder", headers=bearer(token))).status_code == 200
    assert (await client.get("/_test/admin-only", headers=bearer(token))).status_code == 403


async def test_bid_history_most_recent_first_with_cursor(
    client: AsyncClient, email: RecordingEmail, db: asyncpg.Connection
) -> None:
    """FR-10: own bids across auctions, newest first, paginated."""
    address = fresh_email()
    user_id = await register_verified(client, email, address)
    token = await login(client, address)
    a1, a2 = await make_auction(db), await make_auction(db)
    for i, (auction, seq) in enumerate([(a1, 1), (a2, 1), (a1, 2), (a2, 2), (a1, 3)]):
        await db.execute(
            "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type, created_at) "
            "VALUES ($1, $2, $3, $4, 'manual', $5)",
            auction,
            user_id,
            10_000 + i * 500,
            seq,
            NOW + timedelta(seconds=i),
        )
    first = await client.get("/api/v1/users/me/bids?limit=2", headers=bearer(token))
    assert first.status_code == 200, first.text
    page = first.json()
    assert [b["amount"] for b in page["items"]] == [12_000, 11_500]
    assert page["items"][0]["auction_id"] == str(a1) and page["next_cursor"]

    second = await client.get(
        f"/api/v1/users/me/bids?limit=2&cursor={page['next_cursor']}", headers=bearer(token)
    )
    assert [b["amount"] for b in second.json()["items"]] == [11_000, 10_500]
    third = await client.get(
        f"/api/v1/users/me/bids?limit=2&cursor={second.json()['next_cursor']}",
        headers=bearer(token),
    )
    assert [b["amount"] for b in third.json()["items"]] == [10_000]
    assert third.json()["next_cursor"] is None
    assert (
        await client.get("/api/v1/users/me/bids?limit=0", headers=bearer(token))
    ).status_code == 422
