"""Password reset (FR-8): no enumeration, single use, 1 h, revokes every session."""

import asyncpg
from httpx import AsyncClient
from redis.asyncio import Redis

from tests.auth.conftest import (
    PASSWORD,
    FakeClock,
    RecordingEmail,
    bearer,
    fresh_email,
    login,
    register_verified,
)

NEW_PASSWORD = "a brand new passphrase 42"


async def _forgot(client: AsyncClient, address: str):
    return await client.post("/api/v1/auth/forgot-password", json={"email": address})


async def _reset(client: AsyncClient, token: str, password: str = NEW_PASSWORD):
    return await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "password": password}
    )


async def test_forgot_is_identical_for_known_and_unknown(
    client: AsyncClient, email: RecordingEmail
) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    before = len(email.sent)
    unknown = await _forgot(client, fresh_email())
    known = await _forgot(client, address)
    assert (
        (unknown.status_code, unknown.json())
        == (known.status_code, known.json())
        == (
            202,
            {"status": "accepted"},
        )
    )
    assert len(email.sent) == before + 1 and "reset-password?token=" in email.sent[-1][2]


async def test_reset_revokes_old_sessions_and_new_login_works_immediately(
    client: AsyncClient, email: RecordingEmail, redis: Redis, db: asyncpg.Connection
) -> None:
    address = fresh_email()
    user_id = await register_verified(client, email, address)
    old_a = await login(client, address)
    old_b = await login(client, address)
    await _forgot(client, address)
    token = email.last_token(address)

    assert (await _reset(client, token)).status_code == 200
    for old in (old_a, old_b):
        r = await client.get("/api/v1/users/me", headers=bearer(old))
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"
    # per-session denylist keys, never a user-level key: the next login must work at once
    assert await redis.exists(f"revoked:user:{user_id}") == 0
    sids = await db.fetch("SELECT id FROM sessions WHERE user_id = $1", user_id)
    assert len(sids) == 2
    for sid in sids:
        assert await redis.exists(f"revoked:sid:{sid['id']}") == 1

    old_login = await client.post(
        "/api/v1/auth/login", json={"email": address, "password": PASSWORD}
    )
    assert old_login.status_code == 401
    fresh = await login(client, address, NEW_PASSWORD)
    assert (await client.get("/api/v1/users/me", headers=bearer(fresh))).status_code == 200

    reused = await _reset(client, token, "yet another passphrase 7")
    assert reused.status_code == 400 and reused.json()["error"]["code"] == "INVALID_TOKEN"


async def test_reset_token_expires_after_one_hour(
    client: AsyncClient, email: RecordingEmail, clock: FakeClock
) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    await _forgot(client, address)
    clock.advance(3600 + 1)
    r = await _reset(client, email.last_token(address))
    assert r.status_code == 400 and r.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_forgot_rate_limited_per_email(
    client: AsyncClient, email: RecordingEmail, clock: FakeClock
) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    clock.advance(3600 - clock.now().timestamp() % 3600)
    for _ in range(3):
        assert (await _forgot(client, address)).status_code == 202
    assert (await _forgot(client, address)).status_code == 429
    clock.advance(3600)
    assert (await _forgot(client, address)).status_code == 202
