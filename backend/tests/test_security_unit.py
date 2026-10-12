"""Security primitives (FR-2, FR-5, NFR-17) and the rate-limit window maths (NFR-22)."""

import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.core import security
from app.core.config import settings
from app.core.ratelimit import RateLimit
from app.core.security import (
    TOKEN_PATTERN,
    TokenError,
    decode_access_token,
    hash_password,
    issue_access_token,
    new_token,
    token_hash,
    verify_password,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def test_argon2id_with_configured_parameters() -> None:
    digest = hash_password("correct horse battery staple")
    assert digest.startswith("$argon2id$")
    assert (
        f"m={settings.argon2_memory_kib},t={settings.argon2_time_cost},p={settings.argon2_parallelism}"
        in digest
    )
    assert verify_password(digest, "correct horse battery staple")
    assert not verify_password(digest, "correct horse battery stapl")
    assert not verify_password("not-a-hash", "anything")


def test_opaque_tokens_are_random_and_only_hashes_are_stored() -> None:
    raw, digest = new_token()
    assert re.fullmatch(TOKEN_PATTERN, raw) and len(digest) == 64
    assert digest == token_hash(raw) and raw not in digest
    assert new_token()[0] != raw


def test_access_token_claims_and_clock_based_expiry() -> None:
    user, sid = uuid4(), uuid4()
    token = issue_access_token(user, ("bidder", "seller"), sid, NOW)
    claims = decode_access_token(token, NOW + timedelta(minutes=14))
    assert (claims.user_id, claims.roles, claims.sid) == (user, ("bidder", "seller"), sid)
    assert claims.expires_at - claims.issued_at == settings.access_token_ttl_s and claims.jti
    with pytest.raises(TokenError) as expired:
        decode_access_token(token, NOW + timedelta(minutes=15))
    assert expired.value.code == "TOKEN_EXPIRED"


def _payload() -> dict:
    return {
        "sub": str(uuid4()),
        "roles": ["bidder"],
        "sid": str(uuid4()),
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 900,
    }


@pytest.mark.parametrize(
    ("token", "label"),
    [
        (jwt.encode(_payload(), key=None, algorithm="none"), "alg=none"),
        (
            jwt.encode(_payload(), "another-secret-that-is-long-enough-1234", algorithm="HS256"),
            "wrong secret",
        ),
        (
            jwt.encode(
                {k: v for k, v in _payload().items() if k != "sid"},
                security.jwt_secret(),
                algorithm="HS256",
            ),
            "missing sid",
        ),
        (
            jwt.encode(
                {**_payload(), "sub": "not-a-uuid"}, security.jwt_secret(), algorithm="HS256"
            ),
            "bad sub",
        ),
        ("", "empty"),
    ],
)
def test_forged_or_malformed_tokens_rejected(token: str, label: str) -> None:
    with pytest.raises(TokenError) as info:
        decode_access_token(token, NOW)
    assert info.value.code == "INVALID_TOKEN", label


def test_rate_limit_spec_parsing() -> None:
    assert RateLimit.parse("10/60") == RateLimit(10, 60)
    with pytest.raises(ValueError):
        RateLimit.parse("ten per minute")


def test_startup_refuses_dev_secret_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(settings, "jwt_secret", "short")
    with pytest.raises(RuntimeError):
        security.check_startup_config()
    monkeypatch.setattr(settings, "jwt_secret", "x" * 32)
    monkeypatch.setattr(settings, "alias_secret", "short")  # NFR-27 pseudonym secret
    with pytest.raises(RuntimeError):
        security.check_startup_config()
    monkeypatch.setattr(settings, "alias_secret", "y" * 32)
    monkeypatch.setattr(settings, "email_backend", "console")
    with pytest.raises(RuntimeError):
        security.check_startup_config()
    monkeypatch.setattr(settings, "email_backend", "smtp")
    security.check_startup_config()
