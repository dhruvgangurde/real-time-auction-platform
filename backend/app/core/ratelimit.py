"""Fixed-window rate limiting in shared Redis state (FR-4, NFR-22).

Windows are keyed by the clock bucket (`rl:{name}:{subject}:{window_start}`), so the limit
resets when the clock enters the next window; the key's TTL is only housekeeping. The counter
increments atomically in a Lua script. When Redis is unreachable the caller fails closed with
SERVICE_UNAVAILABLE: a limiter that silently stops counting is no brute-force protection. The one
exception is a read with its own Redis-down fallback (/live and bid history), which passes
fail_open=True: refusing it would turn the read-only Postgres view of NFR-13 into a 503.
"""

import logging
from typing import NamedTuple

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app import lua
from app.core.clock import Clock
from app.core.errors import AppError

log = logging.getLogger("ratelimit")


class RateLimit(NamedTuple):
    count: int
    window_s: int

    @classmethod
    def parse(cls, spec: str) -> "RateLimit":
        count, window = spec.split("/")
        return cls(int(count), int(window))


class RateLimiter:
    def __init__(self, redis: Redis, clock: Clock) -> None:
        self._clock = clock
        self._script = redis.register_script(lua.source("rate_limit"))

    async def hit(
        self, name: str, subject: str, limit: RateLimit, *, fail_open: bool = False
    ) -> None:
        """Count one attempt; raise RATE_LIMITED (429, Retry-After) when over the limit."""
        now = int(self._clock.now().timestamp())
        window_start = now - now % limit.window_s
        key = f"rl:{name}:{subject}:{window_start}"
        try:
            count = int(await self._script(keys=[key], args=[limit.window_s + 1]))
        except RedisError as exc:
            if fail_open:
                log.warning("rate limiter unavailable; allowed", extra={"fields": {"limit": name}})
                return
            raise AppError("SERVICE_UNAVAILABLE", "rate limiter unavailable", 503) from exc
        if count > limit.count:
            retry_after = window_start + limit.window_s - now
            raise AppError(
                "RATE_LIMITED",
                "too many attempts, try again later",
                429,
                headers={"Retry-After": str(max(retry_after, 1))},
            )
