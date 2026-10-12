"""Login (FR-4, FR-5, NFR-17, NFR-21, NFR-22)."""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimit, RateLimiter
from app.core.redis import create_redis
from app.services import auth as auth_module
from tests.auth.conftest import (
    PASSWORD,
    FakeClock,
    RecordingEmail,
    fresh_email,
    register,
    register_verified,
)


async def _login(client: AsyncClient, email: str, password: str):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_rate_limiter_fails_closed_without_redis(clock: FakeClock) -> None:
    """NFR-22: a limiter that cannot count refuses (SERVICE_UNAVAILABLE), never lets through."""
    dead = create_redis("redis://redis:6399/15")
    try:
        with pytest.raises(AppError) as info:
            await RateLimiter(dead, clock).hit("login_ip", "10.0.0.1", RateLimit(5, 60))
        assert info.value.code == "SERVICE_UNAVAILABLE" and info.value.status == 503
    finally:
        await dead.aclose()


async def test_unknown_email_and_wrong_password_are_indistinguishable(
    client: AsyncClient, email: RecordingEmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-4: identical body and status; the unknown email still pays for a hash check."""
    address = fresh_email()
    await register_verified(client, email, address)
    calls: list[str] = []
    monkeypatch.setattr(auth_module, "dummy_verify", lambda pw: calls.append(pw))

    wrong = await _login(client, address, "definitely-not-the-password")
    unknown = await _login(client, fresh_email(), PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.content == unknown.content
    assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert calls == [PASSWORD]


async def test_unverified_then_suspended_only_after_correct_password(
    client: AsyncClient, email: RecordingEmail, db
) -> None:
    address = fresh_email()
    user_id = (await register(client, address)).json()["id"]
    wrong = await _login(client, address, "wrong-password-here")
    assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"
    unverified = await _login(client, address, PASSWORD)
    assert unverified.status_code == 403
    assert unverified.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"

    await db.execute("UPDATE users SET status = 'suspended' WHERE id = $1", user_id)
    suspended = await _login(client, address, PASSWORD)
    assert suspended.status_code == 403
    assert suspended.json()["error"]["code"] == "ACCOUNT_SUSPENDED"


async def test_success_returns_access_token_and_refresh_cookie_flags(
    client: AsyncClient, email: RecordingEmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "cookie_secure", True)
    address = fresh_email()
    await register_verified(client, email, address)
    r = await _login(client, address, PASSWORD)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == settings.access_token_ttl_s
    assert body["access_token"].count(".") == 2

    cookie = r.headers["set-cookie"]
    assert cookie.startswith("refresh_token=")
    lowered = cookie.lower()
    for flag in ("httponly", "secure", "samesite=strict", "path=/api/v1/auth"):
        assert flag in lowered, cookie
    assert f"max-age={settings.refresh_token_ttl_s}" in lowered
    # the raw refresh token is never in the body (FR-5: cookie only)
    raw = cookie.split(";", 1)[0].split("=", 1)[1]
    assert raw not in r.text


async def test_login_rate_limited_per_email_and_resets_by_clock(
    client: AsyncClient, email: RecordingEmail, clock: FakeClock
) -> None:
    """FR-4, NFR-22: 5 attempts per 300 s per email, then RATE_LIMITED with Retry-After."""
    address = fresh_email()
    await register_verified(client, email, address)
    clock.advance(300 - clock.now().timestamp() % 300)
    for _ in range(5):
        assert (await _login(client, address, "wrong-password-here")).status_code == 401
    limited = await _login(client, address, PASSWORD)
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(limited.headers["Retry-After"]) <= 300
    clock.advance(300)
    assert (await _login(client, address, PASSWORD)).status_code == 200


async def test_login_rate_limited_per_ip(client: AsyncClient, clock: FakeClock) -> None:
    clock.advance(60 - clock.now().timestamp() % 60)
    for _ in range(10):
        assert (await _login(client, fresh_email(), PASSWORD)).status_code == 401
    assert (await _login(client, fresh_email(), PASSWORD)).status_code == 429
    clock.advance(60)
    assert (await _login(client, fresh_email(), PASSWORD)).status_code == 401
