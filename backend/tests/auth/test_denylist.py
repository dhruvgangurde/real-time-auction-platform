"""Denylist durability (FR-6): retry, hand-off to the worker, and the resync safety net."""

import logging
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.redis import create_redis
from app.services.denylist import Denylist, resync
from tests.auth.conftest import (
    EnqueueRecorder,
    FakeClock,
    RecordingEmail,
    bearer,
    fresh_email,
    login,
    register_verified,
)


async def test_failed_write_is_retried_then_enqueued(enqueued: EnqueueRecorder) -> None:
    dead = create_redis("redis://redis:6399/15")
    try:
        sid = uuid4()
        await Denylist(dead, enqueued).revoke([f"sid:{sid}"])
    finally:
        await dead.aclose()
    assert enqueued.calls == [("denylist_revoke", [[f"sid:{sid}"], settings.access_token_ttl_s])]


async def test_failed_write_and_failed_enqueue_raise_alert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(_name: str, _args: list[Any]) -> None:
        raise RedisConnectionError("broker down")

    dead = create_redis("redis://redis:6399/15")
    try:
        with caplog.at_level(logging.CRITICAL, logger="denylist"):
            await Denylist(dead, broken).revoke(["user:x"])
    finally:
        await dead.aclose()
    assert [r.fields["alert"] for r in caplog.records if hasattr(r, "fields")] == [
        "DENYLIST_WRITE_FAILED"
    ]


async def test_resync_rewrites_recent_revocations_from_postgres(
    client: AsyncClient,
    email: RecordingEmail,
    user_service,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    address, victim = fresh_email(), fresh_email()
    await register_verified(client, email, address)
    admin_id = await register_verified(client, email, fresh_email())
    victim_id = await register_verified(client, email, victim)
    token = await login(client, address)
    await login(client, victim)
    await client.post("/api/v1/auth/logout", headers=bearer(token))
    await user_service.suspend_user(admin_id, victim_id, "resync test")

    await redis.flushdb()  # Redis lost the denylist
    assert (await client.get("/api/v1/users/me", headers=bearer(token))).status_code == 200
    written = await resync(redis, sessions, clock)
    assert written >= 2
    assert (await client.get("/api/v1/users/me", headers=bearer(token))).status_code == 401
    assert await redis.exists(f"revoked:user:{victim_id}") == 1
