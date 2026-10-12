"""Registration, verification, login, tokens, logout and password reset (FR-1 to FR-8).

Routers call this service with validated input; the service owns the rules. Authorisation
decisions (roles) live in the API dependencies, not here (NFR-18 is enforced at the boundary).
Passwords and raw tokens are never logged.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimit, RateLimiter
from app.core.security import (
    TokenError,
    decode_access_token,
    dummy_verify,
    hash_password,
    issue_access_token,
    new_token,
    token_hash,
    verify_password,
)
from app.models import EmailVerificationToken, PasswordResetToken, User
from app.repositories import SessionRepository, TokenRepository, UserRepository
from app.services.denylist import Denylist, sid_key
from app.services.email import EmailSender

log = logging.getLogger("auth")

# closes the user's live sockets on every instance (session id, or None for all)
RevokeNotifier = Callable[[UUID, UUID | None], Awaitable[None]]

DEFAULT_ROLES = ("bidder",)  # the SRS is silent; sellers and admins are granted later


@dataclass(frozen=True)
class Profile:
    id: UUID
    email: str
    full_name: str
    avatar_url: str | None
    status: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str  # raw, goes into the cookie only
    expires_in: int


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    roles: tuple[str, ...]
    sid: UUID
    expires_at: int = 0  # access token exp (epoch seconds), for the WebSocket expiry watch


def normalise_email(email: str) -> str:
    return email.strip().lower()


def profile_of(user: User, roles: tuple[str, ...]) -> Profile:
    return Profile(user.id, user.email, user.full_name, user.avatar_url, user.status, roles)


class AuthService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        clock: Clock,
        email: EmailSender,
        limiter: RateLimiter,
        denylist: Denylist,
        notify_revoked: RevokeNotifier | None = None,
    ) -> None:
        self._notify_revoked = notify_revoked
        self._sessions = sessions
        self._redis = redis
        self._clock = clock
        self._email = email
        self._limiter = limiter
        self._denylist = denylist
        self._limits = {
            name: RateLimit.parse(getattr(settings, f"rate_limit_{name}"))
            for name in (
                "login_ip",
                "login_email",
                "register_ip",
                "resend_ip",
                "resend_email",
                "forgot_ip",
                "forgot_email",
                "reset_ip",
                "verify_ip",
            )
        }

    async def _limit(self, name: str, subject: str) -> None:
        await self._limiter.hit(name, subject, self._limits[name])

    # ------------------------------------------------------- registration (FR-1 to FR-3)

    async def register(self, email: str, password: str, full_name: str, ip: str) -> Profile:
        await self._limit("register_ip", ip)
        email = normalise_email(email)
        if password.strip().lower() == email:
            raise AppError("VALIDATION_ERROR", "password must not be the email address", 422)
        password_hash = await asyncio.to_thread(hash_password, password)
        raw, digest = new_token()
        now = self._clock.now()
        try:
            async with self._sessions() as session, session.begin():
                users = UserRepository(session)
                if await users.get_by_email(email) is not None:
                    raise AppError("EMAIL_TAKEN", "email address already registered", 409)
                user = await users.create(email, password_hash, full_name, DEFAULT_ROLES)
                await TokenRepository(session, EmailVerificationToken).create(
                    user.id, digest, now + timedelta(seconds=settings.verification_token_ttl_s)
                )
                profile = profile_of(user, DEFAULT_ROLES)
        except IntegrityError as exc:  # two registrations raced; the unique index decided
            raise AppError("EMAIL_TAKEN", "email address already registered", 409) from exc
        await self._send_verification(email, raw)
        log.info("user registered", extra={"fields": {"user_id": str(profile.id)}})
        return profile

    async def verify_email(self, raw_token: str, ip: str) -> None:
        await self._limit("verify_ip", ip)
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            consumed = await TokenRepository(session, EmailVerificationToken).consume(
                token_hash(raw_token), now
            )
            if consumed is None:
                raise AppError("INVALID_TOKEN", "verification token is invalid or used", 400)
            user_id, expires_at = consumed
            if expires_at <= now:
                raise AppError("TOKEN_EXPIRED", "verification token has expired", 400)
            users = UserRepository(session)
            user = await users.get(user_id)
            if user is not None and user.status == "pending_verification":
                await users.set_status(user_id, "active")
        log.info("email verified", extra={"fields": {"user_id": str(user_id)}})

    async def resend_verification(self, email: str, ip: str) -> None:
        """Same outcome whether or not the address exists (no enumeration)."""
        email = normalise_email(email)
        await self._limit("resend_ip", ip)
        await self._limit("resend_email", email)
        raw, digest = new_token()
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            user = await UserRepository(session).get_by_email(email)
            if user is None or user.status != "pending_verification":
                return
            await TokenRepository(session, EmailVerificationToken).create(
                user.id, digest, now + timedelta(seconds=settings.verification_token_ttl_s)
            )
        await self._send_verification(email, raw)

    async def _send_verification(self, email: str, raw: str) -> None:
        link = f"{settings.app_base_url}/verify-email?token={raw}"
        await self._email.send(
            email,
            "Verify your email address",
            f"Confirm your account by opening this link within 24 hours:\n{link}\n",
        )

    # ------------------------------------------------------- login and tokens (FR-4 to FR-6)

    async def login(self, email: str, password: str, ip: str) -> Tokens:
        email = normalise_email(email)
        await self._limit("login_ip", ip)
        await self._limit("login_email", email)
        invalid = AppError("INVALID_CREDENTIALS", "invalid email or password", 401)
        async with self._sessions() as session:
            users = UserRepository(session)
            user = await users.get_by_email(email)
            if user is None:
                await asyncio.to_thread(dummy_verify, password)  # same cost as a real check
                raise invalid
            if not await asyncio.to_thread(verify_password, user.password_hash, password):
                raise invalid
            self._require_active(user.status)
            roles = await users.roles(user.id)
            raw, digest = new_token()
            now = self._clock.now()
            # the lookup above already began the transaction; commit the new session row
            sid = await SessionRepository(session).create(
                user.id, digest, now + timedelta(seconds=settings.refresh_token_ttl_s)
            )
            await session.commit()
        log.info("login", extra={"fields": {"user_id": str(user.id), "sid": str(sid)}})
        return Tokens(
            issue_access_token(user.id, roles, sid, now), raw, settings.access_token_ttl_s
        )

    async def _notify(self, user_id: UUID, sid: UUID | None) -> None:
        if self._notify_revoked is not None:
            await self._notify_revoked(user_id, sid)

    @staticmethod
    def _require_active(status: str) -> None:
        if status == "pending_verification":
            raise AppError("EMAIL_NOT_VERIFIED", "verify your email address first", 403)
        if status == "suspended":
            raise AppError("ACCOUNT_SUSPENDED", "this account is suspended", 403)

    async def refresh(self, raw_refresh: str | None) -> Tokens:
        """Rotate the refresh token (NFR-17) and issue a new access token."""
        invalid = AppError("INVALID_TOKEN", "refresh token is invalid", 401)
        if not raw_refresh:
            raise invalid
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            repo = SessionRepository(session)
            row = await repo.get_by_token_hash(token_hash(raw_refresh))
            if row is None or row.revoked_at is not None or row.expires_at <= now:
                raise invalid
            users = UserRepository(session)
            user = await users.get(row.user_id)
            if user is None:
                raise invalid
            self._require_active(user.status)
            roles = await users.roles(user.id)
            new_raw, new_digest = new_token()
            await repo.rotate(
                row.id, new_digest, now + timedelta(seconds=settings.refresh_token_ttl_s)
            )
            sid = row.id
        return Tokens(
            issue_access_token(user.id, roles, sid, now), new_raw, settings.access_token_ttl_s
        )

    async def logout(self, user: CurrentUser) -> None:
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            await SessionRepository(session).revoke(user.sid, now)
        await self._denylist.revoke([sid_key(user.sid)])
        await self._notify(user.id, user.sid)
        log.info("logout", extra={"fields": {"user_id": str(user.id), "sid": str(user.sid)}})

    async def authenticate(self, access_token: str) -> CurrentUser:
        """Every authenticated request: valid token, session not revoked, account active (FR-6)."""
        try:
            claims = decode_access_token(access_token, self._clock.now())
        except TokenError as exc:
            raise AppError(exc.code, "access token rejected", 401) from exc
        try:
            denied = await self._denylist.check(claims.sid, claims.user_id)
        except RedisError:
            denied = await self._denied_in_db(claims.sid, claims.user_id)  # never skip the check
        if denied == "user":
            raise AppError("ACCOUNT_SUSPENDED", "this account is suspended", 403)
        if denied == "sid":
            raise AppError("INVALID_TOKEN", "session revoked", 401)
        return CurrentUser(claims.user_id, claims.roles, claims.sid, claims.expires_at)

    async def _denied_in_db(self, sid: UUID, user_id: UUID) -> str | None:
        now = self._clock.now()
        async with self._sessions() as session:
            user = await UserRepository(session).get(user_id)
            if user is None or user.status == "suspended":
                return "user"
            row = await SessionRepository(session).get(sid)
            if row is None or row.revoked_at is not None or row.expires_at <= now:
                return "sid"
        return None

    # ------------------------------------------------------- password reset (FR-8)

    async def forgot_password(self, email: str, ip: str) -> None:
        email = normalise_email(email)
        await self._limit("forgot_ip", ip)
        await self._limit("forgot_email", email)
        raw, digest = new_token()
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            user = await UserRepository(session).get_by_email(email)
            if user is None:
                return  # identical response either way
            await TokenRepository(session, PasswordResetToken).create(
                user.id, digest, now + timedelta(seconds=settings.reset_token_ttl_s)
            )
        link = f"{settings.app_base_url}/reset-password?token={raw}"
        await self._email.send(
            email, "Reset your password", f"Set a new password within 1 hour:\n{link}\n"
        )

    async def reset_password(self, raw_token: str, password: str, ip: str) -> None:
        await self._limit("reset_ip", ip)
        now = self._clock.now()
        digest = token_hash(raw_token)
        # cheap SELECT first: an invalid token must never cost a 64 MiB argon2 hash
        async with self._sessions() as session:
            found = await TokenRepository(session, PasswordResetToken).find(digest)
        if found is None or found[2] is not None:
            raise AppError("INVALID_TOKEN", "reset token is invalid or used", 400)
        if found[1] <= now:
            raise AppError("TOKEN_EXPIRED", "reset token has expired", 400)
        password_hash = await asyncio.to_thread(hash_password, password)
        # the atomic consume still decides: two concurrent resets cannot both succeed
        async with self._sessions() as session, session.begin():
            consumed = await TokenRepository(session, PasswordResetToken).consume(digest, now)
            if consumed is None:
                raise AppError("INVALID_TOKEN", "reset token is invalid or used", 400)
            user_id, expires_at = consumed
            if expires_at <= now:
                raise AppError("TOKEN_EXPIRED", "reset token has expired", 400)
            await UserRepository(session).set_password_hash(user_id, password_hash)
            sids = await SessionRepository(session).revoke_all_for_user(user_id, now)
        # per session, never per user: the next login must work immediately
        if sids:
            await self._denylist.revoke([sid_key(s) for s in sids])
            await self._notify(user_id, None)
        log.info(
            "password reset", extra={"fields": {"user_id": str(user_id), "sessions": len(sids)}}
        )
