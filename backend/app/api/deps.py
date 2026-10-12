"""Request-scoped dependencies: service wiring, the current user and role checks (FR-6, FR-7).

Every service comes from a dependency so tests (and the two-instance WebSocket tests) can
override it per app.
"""

from collections.abc import Awaitable, Callable
from functools import cache
from uuid import UUID

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock, SystemClock
from app.core.errors import AppError
from app.core.ratelimit import RateLimiter
from app.services.auth import AuthService, CurrentUser
from app.services.bidding import BidService
from app.services.denylist import Denylist
from app.services.email import ConsoleEmailSender
from app.services.users import UserService


def get_redis() -> Redis:
    from app.core.redis import redis

    return redis


def get_sessions() -> async_sessionmaker[AsyncSession]:
    from app.core.db import SessionLocal

    return SessionLocal


@cache
def get_clock() -> Clock:
    return SystemClock()


@cache
def get_limiter() -> RateLimiter:
    return RateLimiter(get_redis(), get_clock())


@cache
def _denylist() -> Denylist:
    from app.workers.dispatch import enqueue

    return Denylist(get_redis(), enqueue)


async def _notify_revoked(user_id: UUID, sid: UUID | None) -> None:
    from app.services.live_events import publish_session_revoked

    await publish_session_revoked(get_redis(), user_id, sid)


@cache
def get_auth_service() -> AuthService:
    return AuthService(
        get_sessions(),
        get_redis(),
        get_clock(),
        ConsoleEmailSender(),
        get_limiter(),
        _denylist(),
        notify_revoked=_notify_revoked,
    )


@cache
def get_user_service() -> UserService:
    return UserService(get_sessions(), get_clock(), _denylist(), notify_revoked=_notify_revoked)


@cache
def get_bid_service() -> BidService:
    from app.workers.dispatch import schedule_persist

    return BidService(get_redis(), get_sessions(), schedule_persist)


def client_ip(request: Request) -> str:
    # shortcut: direct peer address; a reverse proxy deployment needs forwarded-header handling
    return request.client.host if request.client else "unknown"


async def current_user(
    authorization: str | None = Header(default=None),
    auth: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError("INVALID_TOKEN", "missing bearer token", 401)
    return await auth.authenticate(authorization[7:].strip())


async def optional_user(
    authorization: str | None = Header(default=None),
    auth: AuthService = Depends(get_auth_service),
) -> CurrentUser | None:
    """Public endpoints: anonymous is fine, but a presented token must be valid."""
    if not authorization:
        return None
    return await current_user(authorization, auth)


def require_roles(*roles: str) -> Callable[..., Awaitable[CurrentUser]]:
    """Allow any of the given roles; a user may hold several (FR-7, BR-2)."""

    async def dependency(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not set(roles) & set(user.roles):
            raise AppError("FORBIDDEN", "insufficient role", 403)
        return user

    return dependency
