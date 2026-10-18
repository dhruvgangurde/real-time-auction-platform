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
from app.core.redis import create_redis
from app.services.auth import AuthService, Tokens
from app.services.bidding import BidService
from app.services.denylist import Denylist
from app.services.live_events import publish_session_revoked
from app.services.users import UserService
from tests.auth.conftest import PASSWORD, FakeClock, RecordingEmail, fresh_email
from tests.bidding.conftest import PersistRecorder, auction, new_user  # noqa: F401  fixtures
from tests.conftest import TEST_REDIS_URL

MESSAGE_KINDS = ("place_bid", "set_proxy", "subscribe", "unsubscribe", "authenticate", "ping")
DEAD_REDIS_URL = "redis://redis:6399/15"  # nothing listens on this port
WEB_PROXY_IP = "10.201.0.10"  # docker-compose.yml default: the only trusted X-Forwarded-For peer


@pytest.fixture(autouse=True)
def tuned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short timeouts and generous rate limits; individual tests tighten what they test."""
    for name, value in {
        "ws_auth_timeout_s": 0.5,
        "ws_watch_interval_s": 0.05,
        "ws_presence_interval_s": 0.3,
        "ws_send_queue_size": 16,
        "ws_max_subscriptions": 3,
        "ws_max_sessions_per_user": 3,
        "ws_max_strikes": 3,
        "ws_max_messages_per_s": 100_000,
        **{f"rate_limit_ws_{kind}": "10000/1" for kind in MESSAGE_KINDS},
        "rate_limit_anon_read_ip": "10000/1",
        "rate_limit_auth_live_user": "10000/1",
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


def _provide(value: Any) -> Callable[[], Any]:
    # no parameters: FastAPI would read any as a request parameter
    return lambda: value


@pytest.fixture
def started_instances() -> list["Instance"]:
    """Every instance started in the current test."""
    return []


@pytest.fixture
def clear_snapshot_cache(started_instances: list["Instance"]) -> Callable[[], None]:
    """Forget the cached anonymous /live bodies (brief 12.2 item 1) on every instance of the
    test, so the next anonymous read is built afresh without waiting out the 1 s."""

    def clear() -> None:
        for instance in started_instances:
            instance.app.state.snapshot_cache.clear()

    return clear


@pytest.fixture
async def start_instance(
    started_instances: list["Instance"],
    auth_service: AuthService,
    user_service: UserService,
    bid_service: BidService,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    limiter: RateLimiter,
    clock: FakeClock,
) -> AsyncIterator[StartInstance]:
    started = started_instances

    async def start(
        redis_url: str = TEST_REDIS_URL,
        overrides: dict[Callable[..., Any], Any] | None = None,
        ws_ping_interval: float | None = None,
        forwarded_allow_ips: str = WEB_PROXY_IP,
        instance_name: str | None = None,
    ) -> Instance:
        """One uvicorn server. `overrides` replaces dependency providers (values are the
        instances to serve); `ws_ping_interval` turns on the protocol keepalive (the timeout
        equals the interval), which production runs at 20 s. X-Forwarded-For is trusted only from
        `forwarded_allow_ips`, as in compose; the test clients connect from 127.0.0.1, so by
        default their header is ignored (uvicorn alone would trust 127.0.0.1)."""
        app = app_main.create_app()
        app.state.redis_url = redis_url
        app.state.instance_name = instance_name or f"instance-{len(started) + 1}"
        services: dict[Callable[..., Any], Any] = {
            deps.get_auth_service: auth_service,
            deps.get_user_service: user_service,
            deps.get_bid_service: bid_service,
            deps.get_redis: redis,
            deps.get_sessions: sessions,
            deps.get_limiter: limiter,
            deps.get_clock: clock,
            **(overrides or {}),
        }
        app.dependency_overrides.update(
            {provider: _provide(value) for provider, value in services.items()}
        )
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            lifespan="on",
            log_level="warning",
            ws_max_size=65536,
            ws_ping_interval=ws_ping_interval,
            ws_ping_timeout=ws_ping_interval,
            proxy_headers=True,
            forwarded_allow_ips=forwarded_allow_ips,
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


@pytest.fixture
def new_session(auth_service: AuthService, db: Any) -> Callable[[UUID], Awaitable[Tokens]]:
    """Log an existing user in again: a second session, with its own sid and refresh token."""

    async def login(user_id: UUID) -> Tokens:
        address = await db.fetchval("SELECT email FROM users WHERE id = $1", user_id)
        return await auth_service.login(address, PASSWORD, uuid4().hex)

    return login


def dead_redis_services(sessions: async_sessionmaker[AsyncSession], clock: FakeClock) -> dict:
    """Providers for an instance whose every Redis use (commands, limiter, denylist, bids,
    snapshot) points at a dead Redis while Postgres stays up."""
    dead = create_redis(DEAD_REDIS_URL)
    limiter = RateLimiter(dead, clock)
    auth = AuthService(sessions, dead, clock, RecordingEmail(), limiter, Denylist(dead, None))
    return {
        deps.get_redis: dead,
        deps.get_limiter: limiter,
        deps.get_auth_service: auth,
        deps.get_bid_service: BidService(dead, sessions, PersistRecorder()),
    }
