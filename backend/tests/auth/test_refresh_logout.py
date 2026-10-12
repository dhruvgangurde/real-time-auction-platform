"""Refresh rotation and logout (FR-5, FR-6, NFR-17, NFR-21)."""

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.core.ratelimit import RateLimiter
from app.core.redis import create_redis
from app.services.auth import AuthService
from app.services.denylist import Denylist
from tests.auth.conftest import (
    FakeClock,
    RecordingEmail,
    bearer,
    fresh_email,
    login,
    register_verified,
)

CSRF = {"X-Requested-With": "XMLHttpRequest"}


async def test_refresh_rotates_and_old_token_is_rejected(
    client: AsyncClient, email: RecordingEmail
) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    await login(client, address)
    old_raw = client.cookies.get("refresh_token", path="/api/v1/auth")

    r = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert r.status_code == 200, r.text
    new_raw = client.cookies.get("refresh_token", path="/api/v1/auth")
    assert new_raw and new_raw != old_raw
    assert (
        await client.get("/api/v1/users/me", headers=bearer(r.json()["access_token"]))
    ).status_code == 200

    # replaying the pre-rotation token fails (NFR-17: invalidated when used)
    client.cookies.clear()  # the jar holds the rotated token; present only the old one
    client.cookies.set("refresh_token", old_raw, domain="test", path="/api/v1/auth")
    replay = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "INVALID_TOKEN"


async def test_refresh_requires_custom_header(client: AsyncClient, email: RecordingEmail) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    await login(client, address)
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


async def test_refresh_after_session_expiry_is_rejected(
    client: AsyncClient, email: RecordingEmail, clock: FakeClock
) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    await login(client, address)
    clock.advance(30 * 24 * 3600 + 1)
    r = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"


async def test_logout_rejects_the_access_token_on_the_very_next_request(
    client: AsyncClient, email: RecordingEmail
) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    token = await login(client, address)
    assert (await client.get("/api/v1/users/me", headers=bearer(token))).status_code == 200

    out = await client.post("/api/v1/auth/logout", headers=bearer(token))
    assert out.status_code == 204
    assert (
        "refresh_token=" in out.headers["set-cookie"]
        and "max-age=0" in out.headers["set-cookie"].lower()
    )

    denied = await client.get("/api/v1/users/me", headers=bearer(token))
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "INVALID_TOKEN"
    # and the refresh token of that session is dead too
    r = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert r.status_code == 401


async def test_denylist_unavailable_falls_back_to_postgres(
    client: AsyncClient,
    email: RecordingEmail,
    clock: FakeClock,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """FR-6: with Redis unreachable the check runs against Postgres, never skipped."""
    address = fresh_email()
    await register_verified(client, email, address)
    live_token = await login(client, address)
    dead_token = await login(client, address)
    await client.post("/api/v1/auth/logout", headers=bearer(dead_token))

    dead_redis = create_redis("redis://redis:6399/15")
    try:
        offline = AuthService(
            sessions,
            dead_redis,
            clock,
            email,
            RateLimiter(redis, clock),
            Denylist(dead_redis, None),
        )
        user = await offline.authenticate(live_token)
        assert user.roles == ("bidder",)
        try:
            await offline.authenticate(dead_token)
        except Exception as exc:  # AppError
            assert getattr(exc, "code", None) == "INVALID_TOKEN"
        else:
            raise AssertionError("revoked session accepted while Redis was down")
    finally:
        await dead_redis.aclose()


async def test_denylist_fallback_refuses_a_suspended_user(
    client: AsyncClient,
    email: RecordingEmail,
    clock: FakeClock,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    user_service,
) -> None:
    """FR-6, FR-76: with Redis unreachable, a suspension is found in Postgres."""
    address = fresh_email()
    user_id = await register_verified(client, email, address)
    token = await login(client, address)
    admin_id = await register_verified(client, email, fresh_email())
    await user_service.suspend_user(admin_id, user_id, "fallback test")

    dead_redis = create_redis("redis://redis:6399/15")
    try:
        offline = AuthService(
            sessions,
            dead_redis,
            clock,
            email,
            RateLimiter(redis, clock),
            Denylist(dead_redis, None),
        )
        with pytest.raises(AppError) as info:
            await offline.authenticate(token)
        assert info.value.code == "ACCOUNT_SUSPENDED"
    finally:
        await dead_redis.aclose()


async def test_access_token_expires_by_clock(
    client: AsyncClient, email: RecordingEmail, clock: FakeClock
) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    token = await login(client, address)
    clock.advance(15 * 60 + 1)
    r = await client.get("/api/v1/users/me", headers=bearer(token))
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_EXPIRED"
