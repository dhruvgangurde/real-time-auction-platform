"""Phase 7 fixtures: the app in process (ASGI) wired to auction_test and Redis db 15, users with
roles, categories, and auctions seeded in any status. Shared by tests/lifecycle,
tests/scheduler and tests/discovery."""

import io
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import main as app_main
from app.api import deps
from app.core.config import settings
from app.core.ratelimit import RateLimiter
from app.core.storage import LocalStorage
from app.services.admin import AdminService
from app.services.auctions import AuctionService
from app.services.auth import AuthService
from app.services.bidding import BidService
from app.services.catalog import CatalogService
from app.services.closing import Closer
from app.services.denylist import Denylist
from app.services.live_events import publish_session_revoked
from app.services.live_state import init_live_state, state_key
from app.services.users import UserService
from tests.auth.conftest import PASSWORD, FakeClock, RecordingEmail, fresh_email
from tests.bidding.conftest import PersistRecorder
from tests.db.conftest import make_auction

User = tuple[UUID, str]  # (user id, access token)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def png(size: tuple[int, int] = (8, 8), color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def email() -> RecordingEmail:
    return RecordingEmail()


@pytest.fixture
def limiter(redis: Redis, clock: FakeClock) -> RateLimiter:
    return RateLimiter(redis, clock)


@pytest.fixture
def auth_service(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    email: RecordingEmail,
    limiter: RateLimiter,
) -> AuthService:
    return AuthService(sessions, redis, clock, email, limiter, Denylist(redis, None))


@pytest.fixture
def storage(tmp_path: Any) -> LocalStorage:
    return LocalStorage(str(tmp_path / "media"))


@pytest.fixture
def auction_service(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    clock: FakeClock,
    limiter: RateLimiter,
    storage: LocalStorage,
) -> AuctionService:
    return AuctionService(sessions, redis, clock, limiter, storage)


@pytest.fixture
def catalog_service(
    sessions: async_sessionmaker[AsyncSession], redis: Redis, storage: LocalStorage
) -> CatalogService:
    return CatalogService(sessions, redis, storage)


@pytest.fixture
def bid_service(redis: Redis, sessions: async_sessionmaker[AsyncSession]) -> BidService:
    return BidService(redis, sessions, PersistRecorder())


@pytest.fixture
def user_service(
    sessions: async_sessionmaker[AsyncSession], clock: FakeClock, redis: Redis
) -> UserService:
    async def notify_revoked(user_id: UUID, sid: UUID | None) -> None:
        await publish_session_revoked(redis, user_id, sid)

    return UserService(sessions, clock, Denylist(redis, None), notify_revoked)


@pytest.fixture
def close_queue() -> list[UUID]:
    """Auctions the admin service handed to the close task."""
    return []


@pytest.fixture
def admin_service(
    sessions: async_sessionmaker[AsyncSession], redis: Redis, close_queue: list[UUID]
) -> AdminService:
    return AdminService(sessions, redis, Closer(redis, sessions), close_queue.append)


def _provide(value: Any) -> Callable[[], Any]:
    return lambda: value  # no parameters: FastAPI would read them as request parameters


@pytest.fixture
async def api(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    storage: LocalStorage,
    auth_service: AuthService,
    auction_service: AuctionService,
    catalog_service: CatalogService,
    bid_service: BidService,
    user_service: UserService,
    admin_service: AdminService,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    limiter: RateLimiter,
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(app_main, "configure_logging", lambda level: None)  # keep caplog
    monkeypatch.setattr(settings, "media_root", str(storage.root))
    app = app_main.create_app()
    for provider, value in {
        deps.get_auth_service: auth_service,
        deps.get_auction_service: auction_service,
        deps.get_catalog_service: catalog_service,
        deps.get_bid_service: bid_service,
        deps.get_user_service: user_service,
        deps.get_admin_service: admin_service,
        deps.get_storage: storage,
        deps.get_redis: redis,
        deps.get_sessions: sessions,
        deps.get_clock: clock,
        deps.get_limiter: limiter,
    }.items():
        app.dependency_overrides[provider] = _provide(value)
    # services only some packages define: overridden when the test uses that fixture
    for provider, name in {
        deps.get_payment_service: "payment_service",
        deps.get_notification_service: "notification_service",
        deps.get_demo_service: "demo_service",
    }.items():
        if name in request.fixturenames:
            app.dependency_overrides[provider] = _provide(request.getfixturevalue(name))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def make_user(
    auth_service: AuthService, email: RecordingEmail, db: asyncpg.Connection
) -> Callable[..., Awaitable[User]]:
    """A verified, logged-in user holding bidder plus the given roles."""

    async def make(*roles: str) -> User:
        address, ip = fresh_email(), uuid4().hex
        profile = await auth_service.register(address, PASSWORD, "Phase Seven", ip)
        await auth_service.verify_email(email.last_token(address), ip)
        for role in roles:
            await db.execute(
                "INSERT INTO user_roles (user_id, role) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                profile.id,
                role,
            )
        return profile.id, (await auth_service.login(address, PASSWORD, ip)).access_token

    return make


@pytest.fixture
def category(db: asyncpg.Connection) -> Callable[..., Awaitable[int]]:
    async def make(parent_id: int | None = None, name: str = "Category") -> int:
        return await db.fetchval(
            "INSERT INTO categories (name, slug, parent_id) VALUES ($1, $2, $3) RETURNING id",
            name,
            f"c-{uuid4().hex[:16]}",
            parent_id,
        )

    return make


@pytest.fixture
def seeded(
    db: asyncpg.Connection,
    clock: FakeClock,
    category: Callable[..., Awaitable[int]],
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> Callable[..., Awaitable[UUID]]:
    """An auction in any status, written directly. Live and closing auctions get their live
    state; `bids` sets the Redis sequence, which is what 'a bid exists' means (D8)."""

    async def make(
        seller_id: UUID,
        status: str = "draft",
        *,
        images: int = 0,
        bids: int = 0,
        start_in_s: int = 3600,
        duration_s: int = 3600,
        **overrides: Any,
    ) -> UUID:
        category_id = overrides.pop("category_id", None) or await category()
        start = clock.now() + timedelta(seconds=start_in_s)
        end = start + timedelta(seconds=duration_s)
        auction_id = await make_auction(
            db,
            seller_id=seller_id,
            category_id=category_id,
            status=status,
            start_time=start,
            end_time=end,
            original_end_time=end,
            **overrides,
        )
        for position in range(images):
            await db.execute(
                "INSERT INTO auction_images (auction_id, object_key, position) VALUES ($1, $2, $3)",
                auction_id,
                f"auctions/{auction_id}/seed{position}.png",
                position,
            )
        if status in ("live", "closing"):
            await init_live_state(auction_id, redis, sessions)
            if bids:
                await redis.hset(state_key(auction_id), "sequence", str(bids))
        return auction_id

    return make


def auction_body(clock: FakeClock, category_id: int, **overrides: Any) -> dict[str, Any]:
    start = clock.now() + timedelta(hours=1)
    return {
        "title": "Brass telescope",
        "description": "A working brass telescope from 1890",
        "category_id": category_id,
        "starting_price": 10_000,
        "min_increment": 500,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(hours=2)).isoformat(),
    } | overrides
