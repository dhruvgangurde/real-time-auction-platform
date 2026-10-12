"""Async runtime for Celery worker processes.

Each worker process owns exactly one event loop, one SQLAlchemy engine (app role) and one async
Redis client. They are created when the process starts and disposed when it stops, so asyncpg
connections and Redis connections never cross a fork or an event loop.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

from celery.signals import worker_process_init, worker_process_shutdown
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.redis import create_redis

_loop: asyncio.AbstractEventLoop | None = None
_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None
_redis: Redis | None = None


def _ensure() -> asyncio.AbstractEventLoop:
    global _loop, _engine, _sessions, _redis
    if _loop is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _engine = create_async_engine(settings.database_url)
        _sessions = async_sessionmaker(_engine, expire_on_commit=False)
        _redis = create_redis(settings.redis_url)
    return _loop


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return _ensure().run_until_complete(coro)


def sessions() -> async_sessionmaker[AsyncSession]:
    _ensure()
    assert _sessions is not None
    return _sessions


def redis() -> Redis:
    _ensure()
    assert _redis is not None
    return _redis


@worker_process_init.connect
def _on_process_init(**_: Any) -> None:
    _ensure()


@worker_process_shutdown.connect
def _on_process_shutdown(**_: Any) -> None:
    global _loop, _engine, _sessions, _redis
    if _loop is None:
        return
    if _redis is not None:
        _loop.run_until_complete(_redis.aclose())
    if _engine is not None:
        _loop.run_until_complete(_engine.dispose())
    _loop.close()
    _loop = _engine = _sessions = _redis = None
