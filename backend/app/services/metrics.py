"""NFR-8 counters and the figures of the FR-81 overview.

The SRS's list: bids accepted per second, bid rejection rate by reason, WebSocket connections per
instance, background queue depth and reconciliation gap count; plus 5xx responses per minute for
the overview's error rate. They live in Redis as metrics, rebuildable and never data of record
(DC-1): a Redis rebuild starts them again from zero. Buckets are keyed by Redis TIME inside
metrics_bump.lua, so nothing near the bid path reads an app clock (DC-3).

Counts on the request path are fire-and-forget (`spawn`): each task is held in a set until it
finishes, and over MAX_PENDING a new count is dropped and itself counted, so a slow Redis can
never pile up work or change a bid's result. Read only through the admin overview (FR-81).
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app import lua
from app.services.bidding import BidResult, ProxyResult

log = logging.getLogger("metrics")

MAX_PENDING = 1000  # counts in flight per process before new ones are dropped
BIDS = "metrics:bids"  # per second: accepted, rejected:<CODE>
ERRORS = "metrics:5xx"  # per minute: count
INSTANCE = "metrics:instance:"  # per API instance: sockets, dropped (EX INSTANCE_TTL_S)
INSTANCE_TTL_S = 15
GAPS = "metrics:reconcile:gaps"
LAST_RECONCILE = "metrics:reconcile:last"
BID_TTL_S = 120
ERROR_TTL_S = 700
MINUTES = 10


def ledger_rows(result: BidResult | ProxyResult) -> int:
    """The ledger rows a decision created: the bid itself, if accepted, and automatic bids."""
    own = 1 if isinstance(result, BidResult) and result.accepted else 0
    return own + len(result.automatic_bids)


class Metrics:
    def __init__(self, redis: Redis, broker: Redis | None = None) -> None:
        self._redis = redis
        self._broker = broker
        self._bump = redis.register_script(lua.source("metrics_bump"))
        self.pending: set[asyncio.Task[None]] = set()
        self.dropped = 0

    # ------------------------------------------------------------------ counting

    def spawn(self, count: Coroutine[Any, Any, None]) -> None:
        """Run a count in the background, never awaited by the caller."""
        if len(self.pending) >= MAX_PENDING:
            count.close()
            self.dropped += 1
            return
        task = asyncio.create_task(count)
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    def record_bid(self, *, accepted: int, code: str | None) -> None:
        """One bid decision: the ledger rows it created, or the reason it was refused."""
        self.spawn(self._quietly(self.bump_bids(accepted=accepted, rejected=code)))

    async def count_bid(self, accepted: int, code: str | None) -> None:
        """For an HTTP background task, which runs after the response is sent."""
        self.record_bid(accepted=accepted, code=code)

    def record_5xx(self) -> None:
        self.spawn(self._quietly(self.bump_5xx()))

    async def _quietly(self, count: Coroutine[Any, Any, None]) -> None:
        try:
            await count
        except (RedisError, OSError) as exc:
            log.warning("metrics bump failed", extra={"fields": {"error": type(exc).__name__}})

    async def bump_bids(self, *, accepted: int = 0, rejected: str | None = None) -> None:
        fields: list[str | int] = []
        if accepted:
            fields += ["accepted", accepted]
        if rejected:
            fields += [f"rejected:{rejected}", 1]
        if fields:
            await self._bump(keys=[BIDS], args=[1, BID_TTL_S, *fields])

    async def bump_5xx(self) -> None:
        await self._bump(keys=[ERRORS], args=[60, ERROR_TTL_S, "count", 1])

    async def publish_instance(self, name: str, *, sockets: int) -> None:
        key = INSTANCE + name
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping={"sockets": sockets, "dropped": self.dropped})
            pipe.expire(key, INSTANCE_TTL_S)
            await pipe.execute()

    async def record_reconciliation(
        self, *, checked: int, gaps: int, regressions: int, failed: int
    ) -> None:
        seconds, micros = await self._redis.time()
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incrby(GAPS, gaps)
            pipe.hset(
                LAST_RECONCILE,
                mapping={
                    "at_ms": seconds * 1000 + micros // 1000,
                    "checked": checked,
                    "gaps": gaps,
                    "regressions": regressions,
                    "failed": failed,
                },
            )
            await pipe.execute()

    # ------------------------------------------------------------------ reading

    async def redis_time(self) -> tuple[int, int]:
        seconds, micros = await self._redis.time()
        return int(seconds), int(micros)

    async def _now(self) -> int:
        return (await self.redis_time())[0]

    async def bids(self) -> dict[str, Any]:
        """The last 60 whole seconds, oldest first."""
        now = await self._now()
        async with self._redis.pipeline(transaction=False) as pipe:
            for s in range(now - 59, now + 1):
                pipe.hgetall(f"{BIDS}:{s}")
            buckets: list[dict[str, str]] = await pipe.execute()
        per_second = [int(b.get("accepted", 0)) for b in buckets]
        rejected: dict[str, int] = {}
        for b in buckets:
            for field, n in b.items():
                if field.startswith("rejected:"):
                    code = field.removeprefix("rejected:")
                    rejected[code] = rejected.get(code, 0) + int(n)
        accepted = sum(per_second)
        refused = sum(rejected.values())
        decided = accepted + refused
        return {
            "per_second": per_second,
            "accepted_60s": accepted,
            "max_per_second": max(per_second),
            "rejected_60s": rejected,
            "rejection_rate": refused / decided if decided else 0.0,
        }

    async def errors(self) -> dict[str, Any]:
        """5xx responses in each of the last MINUTES minutes, oldest first."""
        now = await self._now()
        current = now - now % 60
        async with self._redis.pipeline(transaction=False) as pipe:
            for m in range(MINUTES - 1, -1, -1):
                pipe.hget(f"{ERRORS}:{current - 60 * m}", "count")
            counts = await pipe.execute()
        return {"per_minute": [int(c or 0) for c in counts]}

    async def instances(self) -> list[dict[str, Any]]:
        out = []
        async for key in self._redis.scan_iter(match=INSTANCE + "*", count=100):
            row = await self._redis.hgetall(key)
            if row:
                out.append(
                    {
                        "instance": key.removeprefix(INSTANCE),
                        "sockets": int(row.get("sockets", 0)),
                        "dropped": int(row.get("dropped", 0)),
                    }
                )
        return sorted(out, key=lambda r: r["instance"])

    async def queues(self) -> dict[str, int]:
        broker = self._broker or self._redis
        waiting = await broker.llen("celery")
        unacked = await broker.hlen("unacked")
        backlog = 0
        # shortcut: SCAN walks the keyspace, as the outbox sweeper does (ledger.persist_all_pending)
        async for key in self._redis.scan_iter(match="auction:*:bids", _type="stream", count=500):
            backlog += await self._redis.xlen(key)
        return {
            "celery_waiting": int(waiting),
            "celery_unacked": int(unacked),
            "outbox_backlog": int(backlog),
        }

    async def health(self) -> dict[str, str]:
        """The dependencies an operator looks at first: Postgres, Redis and the Celery broker."""
        from app.services import health

        async def state(check: Coroutine[Any, Any, Any]) -> str:
            try:
                await asyncio.wait_for(check, health.TIMEOUT_S)
                return "ok"
            except Exception:
                return "unreachable"

        broker = self._broker or self._redis
        return {
            "postgres": await state(health.check_postgres()),
            "redis": await state(self._redis.ping()),
            "broker": await state(broker.ping()),
        }

    async def reconciliation(self) -> dict[str, Any]:
        gaps = await self._redis.get(GAPS)
        last = await self._redis.hgetall(LAST_RECONCILE)
        return {
            "gaps_total": int(gaps or 0),
            "last_run": {k: int(v) for k, v in last.items()} if last else None,
        }
