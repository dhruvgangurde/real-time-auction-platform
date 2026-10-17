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
from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimiter
from app.core.storage import LocalStorage
from app.services.admin import AdminService
from app.services.auctions import AuctionService
from app.services.auth import AuthService, CurrentUser
from app.services.bidding import BidService
from app.services.catalog import CatalogService
from app.services.closing import Closer
from app.services.demo import DemoService
from app.services.denylist import Denylist
from app.services.email import ConsoleEmailSender, EmailSender, QueuedEmailSender
from app.services.notifications import NotificationService
from app.services.payment_gateway import razorpay_from_settings
from app.services.payments import PaymentService
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


def _email_sender() -> EmailSender:
    """Console inline in local dev; otherwise queued to the worker, which sends (SI-4)."""
    if settings.email_backend == "console":
        return ConsoleEmailSender()
    from app.workers.dispatch import enqueue

    return QueuedEmailSender(enqueue)


@cache
def get_auth_service() -> AuthService:
    return AuthService(
        get_sessions(),
        get_redis(),
        get_clock(),
        _email_sender(),
        get_limiter(),
        _denylist(),
        notify_revoked=_notify_revoked,
    )


@cache
def get_user_service() -> UserService:
    return UserService(get_sessions(), get_clock(), _denylist(), notify_revoked=_notify_revoked)


@cache
def get_bid_service() -> BidService:
    from app.workers.dispatch import schedule_outbid, schedule_persist

    return BidService(get_redis(), get_sessions(), schedule_persist, schedule_outbid)


@cache
def get_storage() -> LocalStorage:
    return LocalStorage(settings.media_root)


@cache
def get_auction_service() -> AuctionService:
    return AuctionService(get_sessions(), get_redis(), get_clock(), get_limiter(), get_storage())


@cache
def get_catalog_service() -> CatalogService:
    return CatalogService(get_sessions(), get_redis(), get_storage())


@cache
def get_admin_service() -> AdminService:
    from app.workers.dispatch import enqueue

    return AdminService(
        get_sessions(),
        get_redis(),
        Closer(get_redis(), get_sessions()),
        lambda auction_id: enqueue("close_auction", [str(auction_id)]),
    )


@cache
def get_payment_service() -> PaymentService:
    return PaymentService(
        get_sessions(),
        get_redis(),
        razorpay_from_settings(),
        get_limiter(),
        key_secret=settings.razorpay_key_secret,
        webhook_secret=settings.razorpay_webhook_secret,
    )


@cache
def get_demo_service() -> DemoService:
    return DemoService(get_sessions(), get_redis(), get_limiter())


@cache
def get_notification_service() -> NotificationService:
    # the API only reads and marks; emails are sent by the worker
    return NotificationService(get_sessions(), get_redis(), ConsoleEmailSender())


def client_ip(request: Request) -> str:
    # uvicorn rewrites the peer from X-Forwarded-For only when the request comes from the web
    # proxy (--forwarded-allow-ips, docker-compose.yml); anyone else gets their real peer address
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
