"""Demo mode (brief 12.3, 12.4): what the landing page reads about the demo, and starting
"Try to break it".

The hero loop is deferred (B2 Q9), so there is never a hero lot; the hero replays the most
recent verified "Try to break it" run instead (brief 8.1 L324)."""

import math
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimit, RateLimiter
from app.models.demo import FINAL_STATES
from app.repositories.demo import DemoRunRepository
from app.services.live_state import to_ms
from app.services.snapshot import live_snapshot

RUNNER_ALIVE_KEY = "demo:runner:alive"  # set by the runner every 2 s, for 10 s
COUNTERS_TTL_S = 3600  # the live tally; the runner copies it into demo_runs when a run ends


def stress_key(run_id: object) -> str:
    """A run's live tally, as the runner counts it: a Redis hash of planned and connected bots,
    submissions sent and accepted, rejections by code, the watcher's auction_closed frames, and
    a version raised on every write."""
    return f"demo:stress:{run_id}"


def counters_view(fields: dict[str, str]) -> dict[str, Any]:
    """The public shape of a tally (brief 8.3: bidders connected, bids sent, accepted, rejected
    by reason, the watcher's count)."""

    def count(name: str) -> int:
        return int(fields.get(name, 0))

    closed = fields.get("watcher_closed")
    return {
        "bots": {"planned": count("planned"), "connected": count("connected")},
        "submissions": {
            "sent": count("sent"),
            "accepted": count("accepted"),
            "rejected": {
                name.split(":", 1)[1]: int(value)
                for name, value in sorted(fields.items())
                if name.startswith("rejected:")
            },
        },
        "watcher": {
            "auction_closed_received": int(closed) if closed is not None else None,
            "expected": 1,
        },
    }


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
            "stress": await self._stress_block(),
        }

    async def _stress_block(self) -> dict[str, Any]:
        """Before any click (brief 8.3): a run in progress to watch, the cooldown's end, or why
        a start would be refused, in the order the start itself checks them."""
        async with self._sessions() as session:
            runs = DemoRunRepository(session)
            active = await runs.active("stress")
            today = await runs.requested_today("stress")
            last, now = await runs.last_finished("stress")
        try:
            alive = bool(await self._redis.exists(RUNNER_ALIVE_KEY))
        except RedisError:
            alive = False
        reason = None
        if not settings.demo_stress_enabled:
            reason = "disabled"
        elif not alive:
            reason = "runner_offline"
        elif today >= settings.demo_stress_daily_cap:
            reason = "daily_cap"
        return {
            "enabled": settings.demo_stress_enabled,
            "active_run_id": str(active.id) if active else None,
            "cooldown_until_ms": _cooldown_until_ms(last, now),
            "unavailable_reason": reason,
        }

    async def stress_status(self, run_id: UUID) -> dict[str, Any]:
        """What the page polls every second (brief 8.3). While the run lasts the counts are the
        runner's live tally; once it ends they are the stored record, with the checks. The
        version is the newer of the two, so a client can drop an older poll. The runner's own
        measurements (queue, memory) are not part of it."""
        async with self._sessions() as session:
            runs = DemoRunRepository(session)
            run = await runs.get(run_id)
            if run is None or run.kind != "stress":
                raise AppError("NOT_FOUND", "no such run", 404)
            last, now = await runs.last_finished("stress")
        try:
            tally = await self._redis.hgetall(stress_key(run_id))
        except RedisError:
            tally = {}
        stored = run.state in FINAL_STATES and run.counters
        counts = run.counters if stored else counters_view(tally)
        lot: dict[str, Any] | None = None
        if run.auction_id is not None:
            try:
                lot = await live_snapshot(run.auction_id, None, self._redis, self._sessions)
            except AppError:  # live state being rebuilt: the counts still answer
                lot = None
        return {
            "run_id": str(run.id),
            "auction_id": str(run.auction_id) if run.auction_id else None,
            "state": run.state,
            "reason": run.reason,
            "version": max(run.version, int(tally.get("version", 0))),
            "bots": counts["bots"],
            "submissions": counts["submissions"],
            "watcher": counts["watcher"],
            "current_price": lot["current_price"] if lot else None,
            "sequence_no": lot["sequence_no"] if lot else 0,
            "leader_alias": lot["leader_alias"] if lot else None,
            "end_time_ms": lot["end_time_ms"] if lot else None,
            "recent_bids": lot["recent_bids"] if lot else [],  # the newest ledger rows
            "checks": run.checks,
            "cooldown_until_ms": _cooldown_until_ms(last, now),
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


def _cooldown_until_ms(last: datetime | None, now: datetime) -> int | None:
    """When the next run may start, while the cooldown after the last one is on."""
    if last is None:
        return None
    until = last + timedelta(seconds=settings.demo_stress_cooldown_s)
    return to_ms(until) if now < until else None


def _started(run_id: Any, state: str, *, joined: bool) -> dict[str, Any]:
    return {"run_id": str(run_id), "state": state, "joined": joined}


def _unavailable(reason: str) -> AppError:
    return AppError(
        "DEMO_UNAVAILABLE", "the demo is not available now", 503, fields={"reason": reason}
    )
