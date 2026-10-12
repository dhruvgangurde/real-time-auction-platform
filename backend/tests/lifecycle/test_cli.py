"""Operator CLI (D6, FR-9, FR-77, FR-78): roles only through it, every change audited."""

import json
from uuid import uuid4

import asyncpg
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.core.config import settings
from app.core.errors import AppError
from app.services.auth import AuthService
from app.services.live_events import user_channel
from tests.auth.conftest import PASSWORD
from tests.closing.conftest import Room
from tests.conftest import TEST_REDIS_URL
from tests.lifecycle.conftest import bearer


@pytest.fixture(autouse=True)
def cli_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI builds its own Redis client: point it at the test database, not the dev one."""
    monkeypatch.setattr(settings, "redis_url", TEST_REDIS_URL)


async def _role_audits(db: asyncpg.Connection, user_id) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT action, actor_id, before, after, reason FROM audit_log "
        "WHERE entity_type = 'user' AND entity_id = $1 ORDER BY id",
        str(user_id),
    )


async def test_grant_and_revoke_are_audited(
    make_user,
    db: asyncpg.Connection,
    sessions: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_id, _ = await make_user()
    email = await db.fetchval("SELECT email FROM users WHERE id = $1", user_id)

    assert await cli.run(["grant-role", email.upper(), "seller"], sessions) == 0
    assert "log in again" in capsys.readouterr().out
    assert await cli.run(["grant-role", email, "seller"], sessions) == 0  # no change
    assert await cli.run(["revoke-role", email, "seller"], sessions) == 0

    audits = await _role_audits(db, user_id)
    assert [a["action"] for a in audits] == ["role_granted", "role_revoked"]
    granted = audits[0]
    assert granted["actor_id"] is None and granted["reason"] == "cli"
    assert json.loads(granted["before"]) == {"roles": ["bidder"]}
    assert json.loads(granted["after"]) == {"roles": ["bidder", "seller"]}
    roles = await db.fetch("SELECT role FROM user_roles WHERE user_id = $1", user_id)
    assert [r["role"] for r in roles] == ["bidder"]


async def test_unknown_user_fails(
    sessions: async_sessionmaker[AsyncSession], capsys: pytest.CaptureFixture[str]
) -> None:
    assert await cli.run(["grant-role", "nobody@example.com", "admin"], sessions) == 1
    assert "NOT_FOUND" in capsys.readouterr().err


async def test_seed_categories_is_idempotent_and_audited(
    db: asyncpg.Connection, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await cli.run(["seed-categories"], sessions)
    await cli.run(["seed-categories"], sessions)
    phones = await db.fetchrow("SELECT id, parent_id FROM categories WHERE slug = 'phones'")
    parent = await db.fetchval("SELECT slug FROM categories WHERE id = $1", phones["parent_id"])
    assert parent == "electronics"
    audits = await db.fetch(
        "SELECT actor_id, reason FROM audit_log WHERE entity_type = 'category' AND entity_id = $1",
        str(phones["id"]),
    )
    assert [(a["actor_id"], a["reason"]) for a in audits] == [(None, "cli")]


async def test_cli_revoke_role_revokes_every_session(
    api: AsyncClient,
    make_user,
    auth_service: AuthService,
    db: asyncpg.Connection,
    sessions: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FR-6, D6: the CLI revoke takes the same path as the admin API. The old access token and
    the refresh token are dead at once, sockets are told to close, and a fresh login carries
    no seller role. A CLI grant revokes nothing."""
    user_id, old = await make_user("seller")
    email = await db.fetchval("SELECT email FROM users WHERE id = $1", user_id)
    tokens = await auth_service.login(email, PASSWORD, uuid4().hex)

    assert await cli.run(["grant-role", email, "admin"], sessions) == 0
    assert (await api.get("/api/v1/users/me", headers=bearer(old))).status_code == 200

    async with Room(channel=user_channel(user_id)) as sockets:
        assert await cli.run(["revoke-role", email, "seller"], sessions) == 0
    assert "every session was revoked" in capsys.readouterr().out
    assert [e["type"] for e in sockets.events] == ["session_revoked"]
    for token in (old, tokens.access_token):
        refused = await api.get("/api/v1/users/me", headers=bearer(token))
        assert (refused.status_code, refused.json()["error"]["code"]) == (401, "INVALID_TOKEN")
    with pytest.raises(AppError) as info:
        await auth_service.refresh(tokens.refresh_token)
    assert info.value.code == "INVALID_TOKEN"

    fresh = (await auth_service.login(email, PASSWORD, uuid4().hex)).access_token
    me = await api.get("/api/v1/users/me", headers=bearer(fresh))
    assert me.json()["roles"] == ["admin", "bidder"]
    (row,) = [a for a in await _role_audits(db, user_id) if a["action"] == "role_revoked"]
    assert (row["actor_id"], row["reason"]) == (None, "cli")
    assert json.loads(row["after"]) == {
        "roles": ["admin", "bidder"],
        "role": "seller",
        "sessions_revoked": 2,
    }


async def test_cli_reinstate_recovers_a_suspended_account(
    api: AsyncClient,
    make_user,
    user_service,
    auth_service: AuthService,
    db: asyncpg.Connection,
    sessions: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FR-76, BR-12: the operator's recovery path when no active admin is left. Audited with
    actor NULL and the stated reason; the user can log in at once; NOT_SUSPENDED otherwise."""
    user_id, _ = await make_user("admin")
    email = await db.fetchval("SELECT email FROM users WHERE id = $1", user_id)
    actor_id, _ = await make_user("admin")
    await user_service.suspend_user(actor_id, user_id, "locked out during a test")

    with pytest.raises(SystemExit):  # the reason is required
        await cli.run(["reinstate", email], sessions)
    with pytest.raises(SystemExit):
        await cli.run(["reinstate", email, "--reason", "   "], sessions)
    assert await cli.run(["reinstate", email, "--reason", "admin locked out"], sessions) == 0
    assert "reinstated" in capsys.readouterr().out
    assert await db.fetchval("SELECT status FROM users WHERE id = $1", user_id) == "active"
    row = await db.fetchrow(
        "SELECT actor_id, before, after, reason FROM audit_log WHERE entity_id = $1 "
        "AND action = 'user_reinstated'",
        str(user_id),
    )
    assert (row["actor_id"], row["reason"]) == (None, "cli: admin locked out")
    assert json.loads(row["before"]) == {"status": "suspended"}
    fresh = (await auth_service.login(email, PASSWORD, uuid4().hex)).access_token
    me = await api.get("/api/v1/users/me", headers=bearer(fresh))
    assert me.status_code == 200
    assert await cli.run(["reinstate", email, "--reason", "again"], sessions) == 1
    assert "NOT_SUSPENDED" in capsys.readouterr().err


async def test_cli_cannot_demote_the_last_active_admin(
    make_user,
    db: asyncpg.Connection,
    sessions: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FR-75: the operator CLI obeys the last-admin rule too."""
    user_id, _ = await make_user("admin")
    await db.execute(
        "UPDATE users SET status = 'suspended' WHERE status = 'active' AND id <> $1 "
        "AND id IN (SELECT user_id FROM user_roles WHERE role = 'admin')",
        user_id,
    )
    email = await db.fetchval("SELECT email FROM users WHERE id = $1", user_id)
    assert await cli.run(["revoke-role", email, "admin"], sessions) == 1
    assert "LAST_ADMIN" in capsys.readouterr().err
    roles = await db.fetch("SELECT role FROM user_roles WHERE user_id = $1", user_id)
    assert "admin" in {r["role"] for r in roles}
