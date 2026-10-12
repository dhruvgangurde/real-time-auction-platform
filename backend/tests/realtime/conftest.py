"""Real-time tests: two real uvicorn servers inside pytest on the same Redis (db 15) and the
auction_test database, each with its own fan-out hub, driven by real WebSocket clients."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import main as app_main
from app.api import deps
from app.core.config import settings
from app.core.ratelimit import RateLimiter
from app.services.auth import AuthService
from app.services.bidding import BidService
from app.services.denylist import Denylist
from app.services.live_events import publish_session_revoked
from app.services.users import UserService
from tests.auth.conftest import PASSWORD, FakeClock, RecordingEmail, fresh_email
from tests.bidding.conftest import PersistRecorder, auction, new_user  # noqa: F401  fixtures
from tests.conftest import TEST_REDIS_URL


@pytest.fixture(autouse=True)
def tuned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short timeouts and generous rate limits; individual tests tighten what they test."""
    for name, value in {
        "ws_auth_timeout_s": 0.5,
        "ws_idle_timeout_s": 30.0,
        "ws_watch_interval_s": 0.05,
        "ws_presence_interval_s": 0.3,
        "ws_send_queue_size": 16,
        "ws_max_subscriptions": 3,
        "ws_max_connections_per_user": 3,
        "ws_max_strikes": 3,
        "rate_limit_ws_place_bid": "10000/1",
        "rate_limit_ws_set_proxy": "10000/1",
        "rate_limit_ws_subscribe": "10000/10",
        "allowed_origins": "http://localhost:5173",
    }.items():
        monkeypatch.setattr(settings, name, value)
    # create_app() would reset the root logger and detach pytest's log capture
    monkeypatch.setattr(app_main, "configure_logging", lambda level: None)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def email() -> RecordingEmail:
    return RecordingEmail()


@pytest.fixture
def notify(redis: Redis) -> Callable[[UUID, UUID | None], Awaitable[None]]:
    async def publish(user_id: UUID, sid: UUID | None) -> None:
        await publish_session_revoked(redis, user_id, sid)

    return publish


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
    notify: Any,
) -> AuthService:
    return AuthService(
        sessions, redis, clock, email, limiter, Denylist(redis, None), notify_revoked=notify
    )


@pytest.fixture
def user_service(
    redis: Redis, sessions: async_sessionmaker[AsyncSession], clock: FakeClock, notify: Any
) -> UserService:
    return UserService(sessions, clock, Denylist(redis, None), notify_revoked=notify)


@pytest.fixture
def bid_service(redis: Redis, sessions: async_sessionmaker[AsyncSession]) -> BidService:
    return BidService(redis, sessions, PersistRecorder())


@dataclass
class Instance:
    app: FastAPI
    server: uvicorn.Server
    task: asyncio.Task[None]
    port: int

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.port}/api/v1/ws"

    @property
    def http(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def hub(self) -> Any:
        return self.app.state.hub


StartInstance = Callable[..., Awaitable[Instance]]


@pytest.fixture
async def start_instance(
    auth_service: AuthService,
    user_service: UserService,
    bid_service: BidService,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    limiter: RateLimiter,
    clock: FakeClock,
) -> AsyncIterator[StartInstance]:
    started: list[Instance] = []

    async def start(redis_url: str = TEST_REDIS_URL) -> Instance:
        app = app_main.create_app()
        app.state.redis_url = redis_url
        app.dependency_overrides.update(
            {
                deps.get_auth_service: lambda: auth_service,
                deps.get_user_service: lambda: user_service,
                deps.get_bid_service: lambda: bid_service,
                deps.get_redis: lambda: redis,
                deps.get_sessions: lambda: sessions,
                deps.get_limiter: lambda: limiter,
                deps.get_clock: lambda: clock,
            }
        )
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            lifespan="on",
            log_level="warning",
            ws_max_size=65536,
            ws_ping_interval=None,
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        instance = Instance(app, server, task, port)
        started.append(instance)
        return instance

    yield start
    for instance in started:
        instance.server.should_exit = True
    for instance in started:
        await asyncio.wait_for(instance.task, 10)


@pytest.fixture
async def instances(start_instance: StartInstance) -> tuple[Instance, Instance]:
    return await start_instance(), await start_instance()


Bidder = tuple[UUID, str]  # (user id, access token)


@pytest.fixture
def make_bidder(
    auth_service: AuthService, email: RecordingEmail
) -> Callable[[], Awaitable[Bidder]]:
    async def make() -> Bidder:
        address, ip = fresh_email(), uuid4().hex
        profile = await auth_service.register(address, PASSWORD, "Live Bidder", ip)
        await auth_service.verify_email(email.last_token(address), ip)
        tokens = await auth_service.login(address, PASSWORD, ip)
        return profile.id, tokens.access_token

    return make
