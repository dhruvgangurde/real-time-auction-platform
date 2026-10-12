"""Revocation denylist: immediate rejection of revoked sessions and suspended users (FR-5, FR-6).

Postgres is the source of truth (sessions.revoked_at, users.status). Redis holds
`revoked:sid:{sid}` and `revoked:user:{user_id}` keys for the access-token lifetime so the
per-request check is one round trip. If a key cannot be written, the write is retried once,
then handed to a Celery task; the `sync_denylist` beat task rewrites every recent revocation
from Postgres as a safety net. If Redis is unreachable at request time the caller falls back
to Postgres instead of skipping the check.
"""

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.config import settings
from app.repositories import AuditLogRepository, SessionRepository, UserRepository

log = logging.getLogger("denylist")

SUSPENDED_ACTION = "user_suspended"


def sid_key(sid: UUID) -> str:
    return f"sid:{sid}"


def user_key(user_id: UUID) -> str:
    return f"user:{user_id}"


async def write_keys(redis: Redis, keys: list[str], ttl_s: int) -> None:
    async with redis.pipeline(transaction=False) as pipe:
        for key in keys:
            pipe.set(f"revoked:{key}", "1", ex=ttl_s)
        await pipe.execute()


class Denylist:
    def __init__(self, redis: Redis, enqueue: Callable[[str, list[Any]], None] | None) -> None:
        self._redis = redis
        self._enqueue = enqueue

    async def revoke(self, keys: list[str]) -> None:
        """Deny these sids/users for the access-token lifetime. Never silently gives up."""
        ttl = settings.access_token_ttl_s
        error: Exception | None = None
        for _ in range(2):  # the write itself, then one retry
            try:
                await write_keys(self._redis, keys, ttl)
                return
            except RedisError as exc:
                error = exc
        log.error(
            "denylist write failed, handing to worker",
            extra={"fields": {"keys": keys, "error": repr(error)}},
        )
        try:
            if self._enqueue is None:
                raise RuntimeError("no task queue configured")
            self._enqueue("denylist_revoke", [keys, ttl])
        except Exception as exc:
            # Postgres already holds the revocation; sync_denylist repairs Redis within 30 s
            log.critical(
                "denylist write and enqueue both failed",
                extra={
                    "fields": {"alert": "DENYLIST_WRITE_FAILED", "keys": keys, "error": repr(exc)}
                },
            )

    async def clear_user(self, user_id: UUID) -> None:
        """A reinstated user (FR-76). If Redis cannot be reached now, sync_denylist removes the
        key within 30 s; until then the user is still refused (fail closed)."""
        try:
            await self._redis.delete(f"revoked:{user_key(user_id)}")
        except RedisError as exc:
            log.warning(
                "denylist key not removed; sync_denylist will",
                extra={"fields": {"user_id": str(user_id), "error": repr(exc)}},
            )

    async def check(self, sid: UUID, user_id: UUID) -> str | None:
        """'user' if the account is denied, 'sid' if the session is, else None.

        Raises RedisError when Redis is unreachable; the caller falls back to Postgres.
        """
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.exists(f"revoked:{user_key(user_id)}")
            pipe.exists(f"revoked:{sid_key(sid)}")
            user_hit, sid_hit = await pipe.execute()
        if user_hit:
            return "user"
        if sid_hit:
            return "sid"
        return None


async def resync(redis: Redis, sessions: async_sessionmaker[AsyncSession], clock: Clock) -> int:
    """Rewrite denylist keys for everything revoked within the access-token lifetime. A user
    suspended in that window but reinstated since gets the key removed instead (FR-76)."""
    ttl = settings.access_token_ttl_s
    since = clock.now() - timedelta(seconds=ttl)
    async with sessions() as session:
        sids = await SessionRepository(session).revoked_since(since)
        audited = await AuditLogRepository(session).entity_ids_for_action(SUSPENDED_ACTION, since)
        users = await UserRepository(session).suspended_ids(audited)
    keys = [sid_key(s) for s in sids] + [user_key(u) for u in users]
    if keys:
        await write_keys(redis, keys, ttl)
    if reinstated := set(audited) - set(users):
        await redis.delete(*[f"revoked:{user_key(u)}" for u in reinstated])
    return len(keys)
