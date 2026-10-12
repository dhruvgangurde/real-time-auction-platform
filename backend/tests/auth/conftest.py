"""Identity tests: the real app over httpx, real Postgres (auction_test) and Redis (db 15),
an injected clock instead of sleeps, and a recording email sender instead of the console one."""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_auth_service, get_user_service, require_roles
from app.core.ratelimit import RateLimiter
from app.main import app as _app
from app.services.auth import AuthService, CurrentUser
from app.services.denylist import Denylist
from app.services.users import UserService

PASSWORD = "correct horse battery staple"
TOKEN_RE = re.compile(r"token=([A-Za-z0-9_-]{43})")


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime.now(UTC).replace(microsecond=0)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class RecordingEmail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    def last_token(self, to: str | None = None) -> str:
        bodies = [b for t, _, b in self.sent if to is None or t == to]
        match = TOKEN_RE.search(bodies[-1])
        assert match, bodies[-1]
        return match.group(1)


class EnqueueRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Any]]] = []

    def __call__(self, name: str, args: list[Any]) -> None:
        self.calls.append((name, args))


@_app.get("/_test/admin-only")
async def _admin_only(user: CurrentUser = Depends(require_roles("admin"))) -> dict[str, str]:
    return {"user": str(user.id)}


@_app.get("/_test/seller-or-bidder")
async def _seller_or_bidder(
    user: CurrentUser = Depends(require_roles("seller", "bidder")),
) -> dict[str, str]:
    return {"user": str(user.id)}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def email() -> RecordingEmail:
    return RecordingEmail()


@pytest.fixture
def enqueued() -> EnqueueRecorder:
    return EnqueueRecorder()


@pytest.fixture
def denylist(redis: Redis, enqueued: EnqueueRecorder) -> Denylist:
    return Denylist(redis, enqueued)


@pytest.fixture
def auth_service(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    email: RecordingEmail,
    denylist: Denylist,
) -> AuthService:
    return AuthService(sessions, redis, clock, email, RateLimiter(redis, clock), denylist)


@pytest.fixture
def user_service(
    sessions: async_sessionmaker[AsyncSession], clock: FakeClock, denylist: Denylist
) -> UserService:
    return UserService(sessions, clock, denylist)


@pytest.fixture
async def client(
    auth_service: AuthService, user_service: UserService
) -> AsyncIterator[AsyncClient]:
    _app.dependency_overrides[get_auth_service] = lambda: auth_service
    _app.dependency_overrides[get_user_service] = lambda: user_service
    transport = ASGITransport(app=_app, raise_app_exceptions=False)
    # https so the Secure refresh cookie is stored and replayed by the client
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c
    _app.dependency_overrides.clear()


def fresh_email() -> str:
    return f"user-{uuid4().hex[:12]}@example.com"


async def register(
    client: AsyncClient, email: str, password: str = PASSWORD, full_name: str = "Test User"
) -> Response:
    return await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )


async def register_verified(
    client: AsyncClient, sender: RecordingEmail, email: str, password: str = PASSWORD
) -> UUID:
    r = await register(client, email, password)
    assert r.status_code == 201, r.text
    v = await client.post("/api/v1/auth/verify-email", json={"token": sender.last_token(email)})
    assert v.status_code == 200, v.text
    return UUID(r.json()["id"])


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
