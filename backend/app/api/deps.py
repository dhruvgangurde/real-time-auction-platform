"""Request-scoped dependencies: service wiring, the current user and role checks (FR-6, FR-7)."""

from collections.abc import Awaitable, Callable
from functools import cache

from fastapi import Depends, Header, Request

from app.core.clock import SystemClock
from app.core.errors import AppError
from app.core.ratelimit import RateLimiter
from app.services.auth import AuthService, CurrentUser
from app.services.denylist import Denylist
from app.services.email import ConsoleEmailSender
from app.services.users import UserService


@cache
def _denylist() -> Denylist:
    from app.core.redis import redis
    from app.workers.dispatch import enqueue

    return Denylist(redis, enqueue)


@cache
def get_auth_service() -> AuthService:
    from app.core.db import SessionLocal
    from app.core.redis import redis

    clock = SystemClock()
    return AuthService(
        SessionLocal, redis, clock, ConsoleEmailSender(), RateLimiter(redis, clock), _denylist()
    )


@cache
def get_user_service() -> UserService:
    from app.core.db import SessionLocal

    return UserService(SessionLocal, SystemClock(), _denylist())


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


def require_roles(*roles: str) -> Callable[..., Awaitable[CurrentUser]]:
    """Allow any of the given roles; a user may hold several (FR-7, BR-2)."""

    async def dependency(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not set(roles) & set(user.roles):
            raise AppError("FORBIDDEN", "insufficient role", 403)
        return user

    return dependency
