"""Demo data stays out of everything real (B2, brief 12.3): listings and search never show a demo
lot, though its room, live snapshot and history still answer by id (the hero board reads them);
every lot says whether it is a demo; the admin users list leaves demo accounts out unless asked;
and nobody can get a password for a demo account."""

from collections.abc import Awaitable, Callable
from uuid import uuid4

import asyncpg
from httpx import AsyncClient

from app.services.auth import AuthService
from tests.auth.conftest import RecordingEmail
from tests.db.conftest import make_auction
from tests.lifecycle.conftest import User, bearer

STATUSES = ("scheduled", "live", "closing", "closed_sold", "closed_no_sale")


async def test_listings_and_search_never_show_a_demo_lot(
    api: AsyncClient, db: asyncpg.Connection, category: Callable[..., Awaitable[int]]
) -> None:
    cat, word = await category(), uuid4().hex[:12]
    real = {}
    for status in ("scheduled", "closed_sold", "closed_no_sale"):  # no live state needed
        common = {"category_id": cat, "status": status, "title": f"Bidri box {word}"}
        await make_auction(db, is_demo=True, **common)
        real[await make_auction(db, **common)] = status
    for params in ({"category": cat}, {"q": word}):
        response = await api.get(
            "/api/v1/auctions", params={**params, "status": list(STATUSES), "page_size": 50}
        )
        items = response.json()["items"]
        assert {i["id"] for i in items} == {str(a) for a in real}
        assert {i["is_demo"] for i in items} == {False}


async def test_a_demo_lot_still_answers_by_id(api: AsyncClient, db: asyncpg.Connection) -> None:
    lot = await make_auction(db, is_demo=True, status="closed_sold")
    detail = await api.get(f"/api/v1/auctions/{lot}")
    assert detail.status_code == 200 and detail.json()["is_demo"] is True
    live = await api.get(f"/api/v1/auctions/{lot}/live")
    assert live.status_code == 200 and live.json()["is_demo"] is True
    assert (await api.get(f"/api/v1/auctions/{lot}/bids")).status_code == 200
    real = await make_auction(db, status="closed_sold")
    assert (await api.get(f"/api/v1/auctions/{real}")).json()["is_demo"] is False


async def _demo_user(db: asyncpg.Connection, word: str) -> str:
    email = f"bot-{word}@demo.invalid"
    await db.execute(
        "INSERT INTO users (email, password_hash, full_name, status, is_demo) "
        "VALUES ($1, '!demo', $2, 'active', true)",
        email,
        f"Demo bidder {word}",
    )
    return email


async def test_admin_users_leave_demo_accounts_out_unless_asked(
    api: AsyncClient, db: asyncpg.Connection, make_user: Callable[..., Awaitable[User]]
) -> None:
    _, admin = await make_user("admin")
    word = uuid4().hex[:12]
    email = await _demo_user(db, word)
    plain = await api.get("/api/v1/admin/users", params={"q": word}, headers=bearer(admin))
    assert plain.json()["items"] == []
    both = await api.get(
        "/api/v1/admin/users", params={"q": word, "include_demo": True}, headers=bearer(admin)
    )
    [row] = both.json()["items"]
    assert (row["email"], row["is_demo"]) == (email, True)
    _, token = await make_user()
    mine = await api.get("/api/v1/users/me", headers=bearer(token))
    listed = await api.get(
        "/api/v1/admin/users", params={"q": mine.json()["email"]}, headers=bearer(admin)
    )
    assert [r["is_demo"] for r in listed.json()["items"]] == [False]


async def test_nobody_gets_a_password_for_a_demo_account(
    api: AsyncClient, db: asyncpg.Connection, email: RecordingEmail, auth_service: AuthService
) -> None:
    address = await _demo_user(db, uuid4().hex[:12])
    # the API already refuses a reserved .invalid address (422); the service refuses any demo
    # account whatever its address, answering as for an unknown one (no enumeration)
    response = await api.post("/api/v1/auth/forgot-password", json={"email": address})
    assert response.status_code == 422
    assert await auth_service.forgot_password(address, uuid4().hex) is None
    assert [to for to, *_ in email.sent] == []
    tokens = await db.fetchval(
        "SELECT count(*) FROM password_reset_tokens t JOIN users u ON u.id = t.user_id "
        "WHERE u.email = $1",
        address,
    )
    assert tokens == 0
