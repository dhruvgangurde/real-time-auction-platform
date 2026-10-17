"""Demo accounts (brief 12.3; B2 Q5): the demo seller, the watcher and the stress bots are real
users flagged is_demo, created once and reused, that nobody can sign in as. The runner signs them
in the way login does after its password check, a session row and an access token, which the
real authentication accepts; their sessions are removed so they never pile up."""

from uuid import UUID

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.services.auth import AuthService
from app.services.demo_accounts import DemoAccounts
from tests.auth.conftest import FakeClock
from tests.db.conftest import make_user


@pytest.fixture
def accounts(sessions: async_sessionmaker[AsyncSession], clock: FakeClock) -> DemoAccounts:
    return DemoAccounts(sessions, clock)


async def test_the_cast_is_created_once_flagged_and_reused(
    accounts: DemoAccounts, db: asyncpg.Connection
) -> None:
    cast = await accounts.ensure(bots=3)
    again = await accounts.ensure(bots=3)
    assert again == cast
    people = [cast.seller, cast.watcher, *cast.bots]
    rows = await db.fetch(
        "SELECT id, email, status, is_demo, password_hash FROM users WHERE id = ANY($1::uuid[])",
        [p.id for p in people],
    )
    assert len(rows) == 5
    assert {r["status"] for r in rows} == {"active"}
    assert {r["is_demo"] for r in rows} == {True}
    assert all(r["email"].endswith("@demo.invalid") for r in rows)
    assert {r["password_hash"] for r in rows} == {"!demo"}  # not an argon2 hash: never verifies
    assert "seller" in cast.seller.roles and all(b.roles == ("bidder",) for b in cast.bots)


async def test_a_real_account_is_never_adopted(
    accounts: DemoAccounts, db: asyncpg.Connection
) -> None:
    await make_user(db, email="stress-bot-99@demo.invalid")  # a real (is_demo false) row
    with pytest.raises(RuntimeError, match="not a demo account"):
        await accounts.ensure(bots=99)


async def test_nobody_can_sign_in_as_a_demo_account(
    accounts: DemoAccounts, auth_service: AuthService
) -> None:
    await accounts.ensure(bots=1)
    for password in ("!demo", "anything at all", ""):
        with pytest.raises(AppError) as info:
            await auth_service.login("stress-bot-01@demo.invalid", password or "x", "203.0.113.9")
        assert info.value.code == "INVALID_CREDENTIALS"


async def test_minted_sessions_pass_the_real_authentication_and_are_removed_after(
    accounts: DemoAccounts, auth_service: AuthService, db: asyncpg.Connection
) -> None:
    cast = await accounts.ensure(bots=2)
    real = await make_user(db)
    await db.execute(
        "INSERT INTO sessions (user_id, refresh_token_hash, expires_at) "
        "VALUES ($1, 'real-session', now() + interval '1 day')",
        real,
    )
    signed = await accounts.sign_in(cast.bots)
    for bot in cast.bots:
        sid, token = signed[bot.id]
        user = await auth_service.authenticate(token)
        assert (user.id, user.sid, user.roles) == (bot.id, sid, ("bidder",))
        assert await db.fetchval("SELECT user_id FROM sessions WHERE id = $1", sid) == bot.id
    assert await accounts.sign_out_all() >= 2
    left: list[UUID] = [
        r["user_id"]
        for r in await db.fetch(
            "SELECT s.user_id FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.is_demo"
        )
    ]
    assert left == []
    assert await db.fetchval("SELECT count(*) FROM sessions WHERE user_id = $1", real) == 1
