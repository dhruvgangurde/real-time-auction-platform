"""Operator CLI (D6, FR-9, FR-77, FR-78): roles only through it, every change audited."""

import json

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli


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
