"""Password hashing, opaque tokens and access tokens (FR-2, FR-5, NFR-17).

Passwords: argon2id with parameters from config. Opaque tokens (verification, reset, refresh):
32 random bytes, urlsafe; only the SHA-256 hex of the raw value is ever stored or compared.
Access tokens: HS256 JWT with claims sub, roles, sid, iat, exp, jti; decoding pins the algorithm
and requires every claim, and checks expiry against the injected clock.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

log = logging.getLogger("security")

TOKEN_PATTERN = r"^[A-Za-z0-9_-]{43}$"  # secrets.token_urlsafe(32)
_DEV_SECRET = "dev-only-jwt-secret-not-for-deployment-0123456789"
_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_kib,
    parallelism=settings.argon2_parallelism,
    type=Type.ID,
)
# a real hash of a throwaway password: verifying against it costs the same as a real check
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def dummy_verify(password: str) -> None:
    """Spend a real verification on an unknown email so timing reveals nothing (FR-4)."""
    verify_password(_DUMMY_HASH, password)


def new_token() -> tuple[str, str]:
    """(raw token for the user, sha256 hex for storage)."""
    raw = secrets.token_urlsafe(32)
    return raw, token_hash(raw)


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def jwt_secret() -> str:
    if len(settings.jwt_secret.encode()) >= 32:
        return settings.jwt_secret
    # check_startup_config() refuses this outside APP_ENV=local
    return _DEV_SECRET


def check_startup_config() -> None:
    """Refuse to start with dev-only security settings outside the local environment (NFR-26)."""
    if settings.app_env == "local":
        if len(settings.jwt_secret.encode()) < 32:
            log.warning("JWT_SECRET missing or short; using the dev-only secret (APP_ENV=local)")
        return
    if len(settings.jwt_secret.encode()) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 bytes outside APP_ENV=local")
    if len(settings.alias_secret.encode()) < 32:
        raise RuntimeError("ALIAS_SECRET must be at least 32 bytes outside APP_ENV=local")
    if settings.email_backend == "console":
        raise RuntimeError("EMAIL_BACKEND=console is dev-only; configure a real sender (TBD-1)")


class TokenError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code  # INVALID_TOKEN | TOKEN_EXPIRED


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    roles: tuple[str, ...]
    sid: UUID
    jti: str
    issued_at: int
    expires_at: int


def issue_access_token(user_id: UUID, roles: tuple[str, ...], sid: UUID, now: datetime) -> str:
    issued = int(now.timestamp())
    payload = {
        "sub": str(user_id),
        "roles": list(roles),
        "sid": str(sid),
        "iat": issued,
        "exp": issued + settings.access_token_ttl_s,
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_access_token(token: str, now: datetime) -> AccessClaims:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            jwt_secret(),
            algorithms=["HS256"],
            # time is judged against the injected clock below, not the library's wall clock
            options={
                "require": ["exp", "iat", "sub", "sid"],
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
        claims = AccessClaims(
            user_id=UUID(payload["sub"]),
            roles=tuple(str(r) for r in payload.get("roles", [])),
            sid=UUID(payload["sid"]),
            jti=str(payload.get("jti", "")),
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
        )
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise TokenError("INVALID_TOKEN") from exc
    if claims.expires_at <= int(now.timestamp()):
        raise TokenError("TOKEN_EXPIRED")
    return claims
