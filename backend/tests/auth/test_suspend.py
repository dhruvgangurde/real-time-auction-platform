"""Suspension (FR-6, FR-78, BR-12): status, session revocation and audit in one transaction."""

import json

import asyncpg
import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from app.core.errors import AppError
from app.services.users import UserService
from tests.auth.conftest import (
    PASSWORD,
    RecordingEmail,
    bearer,
    fresh_email,
    login,
    register_verified,
)


async def test_suspend_revokes_sessions_audits_and_denies_next_request(
    client: AsyncClient,
    email: RecordingEmail,
    user_service: UserService,
    db: asyncpg.Connection,
    redis: Redis,
) -> None:
    victim, admin = fresh_email(), fresh_email()
    victim_id = await register_verified(client, email, victim)
    admin_id = await register_verified(client, email, admin)
    token_a = await login(client, victim)
    token_b = await login(client, victim)
    assert (await client.get("/api/v1/users/me", headers=bearer(token_a))).status_code == 200

    await user_service.suspend_user(admin_id, victim_id, "chargeback fraud, ticket 4411")

    assert await db.fetchval("SELECT status FROM users WHERE id = $1", victim_id) == "suspended"
    assert (
        await db.fetchval(
            "SELECT count(*) FROM sessions WHERE user_id = $1 AND revoked_at IS NULL", victim_id
        )
        == 0
    )
    audit = await db.fetchrow(
        "SELECT actor_id, before, after, reason FROM audit_log "
        "WHERE entity_type = 'user' AND entity_id = $1 AND action = 'user_suspended'",
        str(victim_id),
    )
    assert audit["actor_id"] == admin_id and audit["reason"] == "chargeback fraud, ticket 4411"
    assert json.loads(audit["before"]) == {"status": "active"}
    assert json.loads(audit["after"]) == {"status": "suspended", "sessions_revoked": 2}
    assert await redis.exists(f"revoked:user:{victim_id}") == 1

    for token in (token_a, token_b):
        r = await client.get("/api/v1/users/me", headers=bearer(token))
        assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_SUSPENDED"
    again = await client.post("/api/v1/auth/login", json={"email": victim, "password": PASSWORD})
    assert again.status_code == 403 and again.json()["error"]["code"] == "ACCOUNT_SUSPENDED"


async def test_suspend_requires_a_reason_and_an_existing_user(
    user_service: UserService, client: AsyncClient, email: RecordingEmail, db: asyncpg.Connection
) -> None:
    admin_id = await register_verified(client, email, fresh_email())
    victim_id = await register_verified(client, email, fresh_email())
    with pytest.raises(AppError) as info:
        await user_service.suspend_user(admin_id, victim_id, "   ")
    assert info.value.code == "VALIDATION_ERROR"
    assert await db.fetchval("SELECT status FROM users WHERE id = $1", victim_id) == "active"
