"""Demo mode (brief 12.3, 12.4): what the landing page reads about the demo, and starting
"Try to break it".

The hero loop is deferred (B2 Q9), so there is never a hero lot; the hero replays the most
recent verified "Try to break it" run instead (brief 8.1 L324)."""

import math
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimit, RateLimiter
from app.repositories.demo import DemoRunRepository
from app.services.live_state import to_ms

RUNNER_ALIVE_KEY = "demo:runner:alive"  # set by the runner every 2 s, for 10 s


class DemoService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], redis: Redis, limiter: RateLimiter
    ) -> None:
        self._sessions, self._redis, self._limiter = sessions, redis, limiter

    async def overview(self) -> dict[str, Any]:
        async with self._sessions() as session:
            replay = await DemoRunRepository(session).latest_verified_auction("stress")
        return {
            "enabled": True,  # this is only ever answered while demo mode is on
            "auction_id": None,
            "next_start_ms": None,
            "last_closed_auction_id": str(replay) if replay else None,
        }

    # ---------------------------------------------------------------- "Try to break it"

    async def start_stress(self, ip: str) -> dict[str, Any]:
        """Starts a run, or joins the one in progress (brief 12.4). Free outcomes come first, so
        the per-address limit counts only runs actually created: joining an active run, then
        the kill switch, a live runner and the daily cap, then the cooldown."""
        async with self._sessions() as session:
            active = await DemoRunRepository(session).active("stress")
        if active is not None:
            return _started(active.id, active.state, joined=True)
        if not settings.demo_stress_enabled:
            raise _unavailable("disabled")
        try:
            alive = await self._redis.exists(RUNNER_ALIVE_KEY)
        except RedisError as exc:  # bidding is down too: fail closed
            raise AppError("SERVICE_UNAVAILABLE", "the demo cannot start now", 503) from exc
        if not alive:
            raise _unavailable("runner_offline")
        async with self._sessions() as session:
            runs = DemoRunRepository(session)
            if await runs.requested_today("stress") >= settings.demo_stress_daily_cap:
                raise _unavailable("daily_cap")
            last, now = await runs.last_finished("stress")
        if last is not None:
            until = last + timedelta(seconds=settings.demo_stress_cooldown_s)
            if now < until:
                wait_s = math.ceil((until - now).total_seconds())
                raise AppError(
                    "DEMO_COOLING_DOWN",
                    "the previous run finished moments ago",
                    429,
                    headers={"Retry-After": str(wait_s)},
                    fields={"retry_at_ms": str(to_ms(until))},
                )
        await self._limiter.hit(
            "demo_stress_ip", ip, RateLimit.parse(settings.rate_limit_demo_stress_ip)
        )
        async with self._sessions() as session, session.begin():
            runs = DemoRunRepository(session)
            run_id = await runs.request("stress")
            if run_id is None:  # another start won the race, on this instance or the other
                raced = await runs.active("stress")
                if raced is not None:
                    return _started(raced.id, raced.state, joined=True)
                raise AppError("SERVICE_UNAVAILABLE", "the demo cannot start now", 503)
        return _started(run_id, "requested", joined=False)


def _started(run_id: Any, state: str, *, joined: bool) -> dict[str, Any]:
    return {"run_id": str(run_id), "state": state, "joined": joined}


def _unavailable(reason: str) -> AppError:
    return AppError(
        "DEMO_UNAVAILABLE", "the demo is not available now", 503, fields={"reason": reason}
    )
