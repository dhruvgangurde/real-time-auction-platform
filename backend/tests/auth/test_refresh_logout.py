"""Refresh rotation and logout (FR-5, FR-6, NFR-17, NFR-21)."""

import asyncio

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
    client.cookies.set("refresh_token", old_raw, path="/api/v1/auth")
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
    raw_refresh = client.cookies.get("refresh_token", path="/api/v1/auth")
    assert (await client.get("/api/v1/users/me", headers=bearer(token))).status_code == 200

    out = await client.post("/api/v1/auth/logout", headers=bearer(token))
    assert out.status_code == 204
    assert (
        "refresh_token=" in out.headers["set-cookie"]
        and "max-age=0" in out.headers["set-cookie"].lower()
    )

    denied = await client.get("/api/v1/users/me", headers=bearer(token))
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "INVALID_TOKEN"
    # and the refresh token of that session is dead too (logout cleared it from the jar, so
    # present it explicitly: without any cookie the answer would be 204)
    client.cookies.set("refresh_token", raw_refresh, path="/api/v1/auth")
    r = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert r.status_code == 401


async def test_refresh_without_a_cookie_is_204_with_nothing_set(client: AsyncClient) -> None:
    """The app calls refresh on every load (brief 13 item 22): an anonymous visitor is no error."""
    r = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert r.status_code == 204
    assert r.content == b"" and "set-cookie" not in r.headers


async def test_refresh_with_a_bad_cookie_is_401_and_clears_it(client: AsyncClient) -> None:
    client.cookies.set("refresh_token", "not-a-real-token", path="/api/v1/auth")
    r = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"
    cleared = r.headers["set-cookie"].lower()
    assert cleared.startswith("refresh_token=") and "max-age=0" in cleared
    assert "path=/api/v1/auth" in cleared and "httponly" in cleared
    # a browser drops the cookie on max-age=0, so the stale cookie costs one 401, not one per
    # load; httpx's jar does not honour the deletion, so the next-load 204 is not asserted here


async def test_refresh_with_a_revoked_cookie_is_401_and_clears_it(
    client: AsyncClient, email: RecordingEmail
) -> None:
    address = fresh_email()
    await register_verified(client, email, address)
    token = await login(client, address)
    raw_refresh = client.cookies.get("refresh_token", path="/api/v1/auth")
    await client.post("/api/v1/auth/logout", headers=bearer(token))  # revokes the session
    client.cookies.set("refresh_token", raw_refresh, path="/api/v1/auth")
    r = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert r.status_code == 401 and "max-age=0" in r.headers["set-cookie"].lower()


async def test_refresh_without_the_header_is_refused_with_or_without_a_cookie(
    client: AsyncClient,
) -> None:
    """The X-Requested-With check runs first, exactly as before."""
    r = await client.post("/api/v1/auth/refresh")  # no cookie either
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    client.cookies.set("refresh_token", "anything", path="/api/v1/auth")
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


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


async def test_concurrent_refreshes_with_one_token_give_exactly_one_success(
    client: AsyncClient, email: RecordingEmail
) -> None:
    """NFR-17 (audit M3): rotation is conditional on the token presented, so two refreshes racing
    with the same token cannot both succeed; the loser gets INVALID_TOKEN."""
    for _ in range(3):  # several sessions: each round is one race on one token
        address = fresh_email()
        await register_verified(client, email, address)
        await login(client, address)
        raw = client.cookies.get("refresh_token", path="/api/v1/auth")
        client.cookies.clear()  # both requests present exactly this token, not the jar's
        headers = {**CSRF, "Cookie": f"refresh_token={raw}"}
        both = await asyncio.gather(
            client.post("/api/v1/auth/refresh", headers=headers),
            client.post("/api/v1/auth/refresh", headers=headers),
        )
        codes = sorted(r.status_code for r in both)
        assert codes == [200, 401], [r.text for r in both]
        loser = next(r for r in both if r.status_code == 401)
        assert loser.json()["error"]["code"] == "INVALID_TOKEN"
        client.cookies.clear()
