"""The demo runner (brief 12.3, 12.4): `python -m app.workers.demo_runner`, run as the compose
service `demo` under the profile `demo`.

One process and one event loop, not a Celery task: a run keeps 50 sockets open for over a minute,
which would block a prefork worker. Only one runner works at a time: it holds a Postgres advisory
lock on a connection of its own for as long as it lives, and a second runner exits. It tells the
API it is alive with a Redis key it renews every 2 s (gone 10 s after it stops), aborts whatever
run a previous runner left unfinished, and creates each run's fresh lot through the real lifecycle
service, as the demo seller.

A "Try to break it" run: the requested run is claimed; its lot is created; the bots and a watcher
are signed in and connect to both API instances; once the scheduler has opened the lot, each bot
sends at most 20 bids, the last one well before the soft-close window; then the runner waits for
the real close task to settle the lot, runs the six ledger checks in Postgres and stores the
result. The live tally (bots connected, bids sent, accepted, rejected by code) is a Redis hash the
status endpoint reads while the run lasts, copied into demo_runs when it ends.
"""

import asyncio
import contextlib
import logging
import random
import signal
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.core.clock import Clock, SystemClock
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.ratelimit import RateLimiter
from app.core.redis import create_redis
from app.core.storage import LocalStorage
from app.repositories.auction import AuctionRepository
from app.repositories.category import CategoryRepository
from app.repositories.demo import DemoRunRepository
from app.repositories.settlement import SettlementRepository
from app.services.auctions import AuctionService
from app.services.auth import CurrentUser
from app.services.catalog import CatalogService
from app.services.demo import COUNTERS_TTL_S, RUNNER_ALIVE_KEY, counters_view, stress_key
from app.services.demo_accounts import DemoAccount, DemoAccounts, DemoCast
from app.services.demo_checks import verify
from app.services.denylist import Denylist
from app.workers.demo_bots import BotSocket, bid_times
from app.workers.dispatch import enqueue

log = logging.getLogger("demo_runner")

IMAGES = Path(__file__).resolve().parents[2] / "demo" / "images"  # CC0 (demo/images/CREDITS.md)
HEARTBEAT_EVERY_S = 2.0
HEARTBEAT_TTL_S = 10
POLL_EVERY_S = 1.0  # requested runs
PICKUP_DEADLINE_S = 30  # a requested run not picked up by then is aborted
LIVE_DEADLINE_S = 30  # after the scheduled start
SETTLE_DEADLINE_S = 120  # after the end; past it the checks run anyway and say what is missing
WATCHER_GRACE_S = 5.0  # the auction_closed frame follows the settlement's commit
MIN_GAP_S = 2.0  # between two bids of one bot: far below the per-user limit of 10 a second
SAMPLE_EVERY_S = 0.2  # the Celery backlog
# the worker prefetches eagerly, so a backlog sits less in the queue than in the messages it has
# taken and not yet acknowledged (persist_bids acks late): both are measured
CELERY_QUEUE, CELERY_UNACKED = "celery", "unacked"

STRESS_LOT = {
    "title": "Bidri betel box (Try to break it)",
    "description": (
        "A demo listing: the bidders are simulated and nothing is sold. Fifty demo bidders, "
        "each with its own account and session, bid on it at once through the real bid path; "
        "then the ledger is checked in Postgres.\n\n"
        "Photo of a comparable piece: Cleveland Museum of Art, 2020.206, CC0."
    ),
    "starting_price": 500_000,  # INR 5,000 in paise: room for 1,000 bids under the 100x cap
    "min_increment": 5_000,
    "reserve_price": None,
}
STRESS_CATEGORY = "home-and-garden"
STRESS_PHOTO = "bidri-pandan.jpg"


class RunAborted(Exception):
    """The run could not go ahead (no bot connected, the lot never opened): nothing to check."""


def refusal() -> str | None:
    if not settings.demo_mode:
        return "DEMO_MODE is off: the demo runner does not start"
    return None


def rss_peak_kb() -> int | None:
    """This process's peak resident memory (Linux), for the run's record."""
    with contextlib.suppress(OSError, ValueError):
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    return None


async def hold_runner_lock(connection: AsyncConnection) -> bool:
    """True if this connection now holds the one runner lock; it is held until it closes."""
    session = AsyncSession(bind=connection)
    held = await DemoRunRepository(session).try_runner_lock()
    await session.commit()
    return held


async def _file(path: Path) -> AsyncIterator[bytes]:
    yield path.read_bytes()


class DemoRunner:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        clock: Clock,
        auctions: AuctionService,
        accounts: DemoAccounts,
        broker: Redis | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._sessions, self._redis, self._clock = sessions, redis, clock
        self._auctions, self._accounts = auctions, accounts
        self._broker = broker  # Celery's broker, to measure the queue during a run
        self._rng = rng or random.Random()

    async def start(self, bots: int) -> DemoCast:
        """The accounts (created once), no session left from a previous runner, no run left
        half done, and the default categories the lot needs."""
        cast = await self._accounts.ensure(bots)
        await self._accounts.sign_out_all()
        async with self._sessions() as session, session.begin():
            aborted = await DemoRunRepository(session).abort_unfinished("runner restarted")
        if aborted:
            log.warning("unfinished demo runs aborted", extra={"fields": {"runs": len(aborted)}})
        await CatalogService(self._sessions, self._redis, LocalStorage("/")).seed_defaults()
        return cast

    async def beat(self) -> None:
        await self._redis.set(RUNNER_ALIVE_KEY, "1", ex=HEARTBEAT_TTL_S)

    async def serve(self, cast: DemoCast) -> None:
        beating = asyncio.create_task(self._heartbeat())
        try:
            while True:
                run_id = await self.next_run()
                if run_id is None:
                    await asyncio.sleep(POLL_EVERY_S)
                    continue
                await self.run_stress(run_id, cast)
        finally:
            beating.cancel()

    async def next_run(self) -> UUID | None:
        """The requested run, now starting; a request older than the pickup deadline is aborted
        instead (its visitor has long moved on)."""
        async with self._sessions() as session, session.begin():
            runs = DemoRunRepository(session)
            await runs.abort_stale_requests("stress", PICKUP_DEADLINE_S)
            return await runs.claim("stress")

    async def new_lot(self, seller: DemoAccount) -> UUID:
        """A fresh demo lot for one run, never reused: scheduled to open after the start delay
        and to stay live for the bidding window plus the stop before the end."""
        async with self._sessions() as session:
            category = await CategoryRepository(session).get_by_slug(STRESS_CATEGORY)
        assert category is not None, "the runner seeds the default categories at start"
        start = self._clock.now() + timedelta(seconds=settings.demo_stress_start_delay_s)
        live = settings.demo_stress_seconds + settings.demo_stress_stop_before_end_s
        threshold = settings.antisnipe_threshold_min_s  # 10 s: shorter than the stop before end
        user = CurrentUser(seller.id, seller.roles, uuid4())
        view = await self._auctions.create(
            user,
            {
                **STRESS_LOT,
                "category_id": category.id,
                "start_time": start,
                "end_time": start + timedelta(seconds=live),
                "antisnipe_threshold_s": threshold,
                "antisnipe_extension_s": settings.antisnipe_extension_min_s,
            },
            is_demo=True,
        )
        lot = UUID(view["id"])
        await self._auctions.add_image(user, lot, _file(IMAGES / STRESS_PHOTO))
        await self._auctions.publish(user, lot)
        return lot

    # ---------------------------------------------------------------- one run

    async def run_stress(self, run_id: UUID, cast: DemoCast) -> str:
        """Runs one claimed run to its end; returns its final state."""
        key = stress_key(run_id)
        sockets: list[BotSocket] = []
        peak: dict[str, int] = {}
        sampler: asyncio.Task[None] | None = None
        try:
            await self._tally(key, {"planned": len(cast.bots)})
            lot = await self.new_lot(cast.seller)
            await self._state(run_id, "starting", auction_id=lot)
            signed = await self._accounts.sign_in((*cast.bots, cast.watcher))
            urls = [u.strip() for u in settings.demo_ws_urls.split(",") if u.strip()]
            watcher = BotSocket(urls[0], signed[cast.watcher.id][1], cast.watcher.id, lot)
            sockets.append(watcher)
            await watcher.open()
            bots = [
                BotSocket(urls[i % len(urls)], signed[bot.id][1], bot.id, lot)
                for i, bot in enumerate(cast.bots)
            ]
            sockets += bots
            opened = await asyncio.gather(*(b.open() for b in bots), return_exceptions=True)
            for problem in {type(r).__name__ for r in opened if r is not None}:
                log.warning("demo bot did not connect", extra={"fields": {"error": problem}})
            bidders = [b for b, r in zip(bots, opened, strict=True) if r is None]
            await self._tally(key, {"connected": len(bidders)})
            if not bidders:
                raise RunAborted("no bidder could connect")
            end = await self._wait_live(lot)
            await self._state(run_id, "running")
            if self._broker is not None:
                sampler = asyncio.create_task(self._sample(peak))
            await self._bid(key, bidders, end)
            for bot in bidders:
                await bot.close()
            await self._state(run_id, "settling")
            settled = await self._wait_settled(lot, end)
            await self._wait_for_watcher(watcher)
            await self._redis.hset(key, "watcher_closed", watcher.closed_events)
            acks = sorted(ack for bot in bots for ack in bot.acks)
            checks = await verify(self._sessions, lot, acks)
            state = "verified" if all(c["passed"] for c in checks) else "failed"
            reason = None if settled else f"not settled within {SETTLE_DEADLINE_S} s of the end"
            counters = counters_view(await self._redis.hgetall(key)) | {
                "celery_queue_peak": peak.get("waiting"),
                "celery_unacked_peak": peak.get("unacked"),
                "runner_rss_peak_kb": rss_peak_kb(),
            }
            await self._finish(
                run_id, state, reason=reason, counters=counters, checks=checks, acks=acks
            )
            log.info(
                "demo stress run finished",
                extra={"fields": {"run_id": str(run_id), "state": state, **counters}},
            )
            return state
        except RunAborted as exc:
            await self._finish(run_id, "aborted", reason=str(exc))
            return "aborted"
        except asyncio.CancelledError:  # stopped (SIGTERM): say so now, not at the next start
            with contextlib.suppress(Exception):
                await self._finish(run_id, "aborted", reason="the runner was stopped")
            raise
        except Exception:
            log.exception("demo stress run stopped", extra={"fields": {"run_id": str(run_id)}})
            await self._finish(run_id, "aborted", reason="the runner hit an error")
            return "aborted"
        finally:
            if sampler is not None:
                sampler.cancel()
            for socket in sockets:
                with contextlib.suppress(Exception):
                    await socket.close()
            with contextlib.suppress(Exception):
                await self._accounts.sign_out_all()

    async def _bid(self, key: str, bots: list[BotSocket], end: datetime) -> None:
        """Every bot at once, each on its own jittered schedule inside the window, which ends
        the stop-before-end ahead of the lot's end (no extension)."""
        stop_at = end - timedelta(seconds=settings.demo_stress_stop_before_end_s)
        window = min(
            float(settings.demo_stress_seconds), (stop_at - self._clock.now()).total_seconds()
        )
        if window < MIN_GAP_S:
            raise RunAborted("the lot opened too late to bid")
        loop = asyncio.get_running_loop()
        t0 = loop.time()

        async def bidder(bot: BotSocket, times: list[float]) -> None:
            for at in times:
                await asyncio.sleep(max(0.0, t0 + at - loop.time()))
                if loop.time() > t0 + window:  # a slow reply never pushes a bid past the window
                    return
                outcome = await bot.bid()
                if outcome is None:  # leading: held back, nothing sent
                    continue
                counted = "accepted" if outcome == "accepted" else f"rejected:{outcome}"
                await self._tally(key, {"sent": 1, counted: 1})

        bids = settings.demo_stress_bids_per_bot
        await asyncio.gather(
            *(bidder(b, bid_times(self._rng, window, bids, MIN_GAP_S)) for b in bots)
        )

    async def _wait_live(self, lot: UUID) -> datetime:
        """The lot's end time, once the scheduler has opened it."""
        while True:
            async with self._sessions() as session:
                auction = await AuctionRepository(session).get(lot)
            assert auction is not None
            if auction.status == "live":
                return auction.end_time
            if self._clock.now() > auction.start_time + timedelta(seconds=LIVE_DEADLINE_S):
                raise RunAborted("the lot did not open (is the scheduler running?)")
            await asyncio.sleep(0.25)

    async def _wait_settled(self, lot: UUID, end: datetime) -> bool:
        """True once the close task has settled the lot; False past the deadline."""
        while True:
            async with self._sessions() as session:
                auction = await AuctionRepository(session).get(lot)
                settlement = await SettlementRepository(session).for_auction(lot)
            if auction is not None and auction.status.startswith("closed_") and settlement:
                return True
            if self._clock.now() > end + timedelta(seconds=SETTLE_DEADLINE_S):
                return False
            await asyncio.sleep(0.5)

    @staticmethod
    async def _wait_for_watcher(watcher: BotSocket) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WATCHER_GRACE_S
        while watcher.closed_events == 0 and loop.time() < deadline:
            await asyncio.sleep(0.1)

    async def _sample(self, peak: dict[str, int]) -> None:
        """The Celery backlog's peaks while the bots bid and the lot settles: messages waiting
        in the queue, and messages the worker has taken but not yet acknowledged."""
        assert self._broker is not None
        while True:
            with contextlib.suppress(RedisError):
                async with self._broker.pipeline(transaction=False) as pipe:
                    pipe.llen(CELERY_QUEUE)
                    pipe.hlen(CELERY_UNACKED)
                    waiting, unacked = await pipe.execute()
                for name, value in (("waiting", waiting), ("unacked", unacked)):
                    peak[name] = max(peak.get(name, 0), int(value))
            await asyncio.sleep(SAMPLE_EVERY_S)

    async def _tally(self, key: str, counts: dict[str, int]) -> None:
        async with self._redis.pipeline(transaction=False) as pipe:
            for name, n in counts.items():
                pipe.hincrby(key, name, n)
            pipe.hincrby(key, "version", 1)
            pipe.expire(key, COUNTERS_TTL_S)
            await pipe.execute()

    async def _version(self, run_id: UUID) -> int | None:
        """The run's next version: the tally's, so a poll can tell newer from older."""
        try:
            return int(await self._redis.hincrby(stress_key(run_id), "version", 1))
        except RedisError:
            return None  # the row's own version is raised instead

    async def _state(self, run_id: UUID, state: str, **values: Any) -> None:
        version = await self._version(run_id)
        async with self._sessions() as session, session.begin():
            await DemoRunRepository(session).set_state(run_id, state, version, **values)

    async def _finish(self, run_id: UUID, state: str, **values: Any) -> None:
        version = await self._version(run_id)
        async with self._sessions() as session, session.begin():
            await DemoRunRepository(session).finish(run_id, state, version, **values)

    async def _heartbeat(self) -> None:
        while True:
            try:
                await self.beat()
            except Exception:
                log.exception("demo runner heartbeat failed")
            await asyncio.sleep(HEARTBEAT_EVERY_S)


async def main() -> int:
    configure_logging(settings.log_level)
    if (refused := refusal()) is not None:
        log.warning(refused)
        return 0  # nothing to do is not a failure: compose does not restart it
    from app.core.db import SessionLocal, engine

    redis = create_redis(settings.redis_url)
    clock = SystemClock()
    async with engine.connect() as lock:
        if not await hold_runner_lock(lock):
            log.error("another demo runner holds the lock; this one exits")
            return 1
        auctions = AuctionService(
            SessionLocal, redis, clock, RateLimiter(redis, clock), LocalStorage(settings.media_root)
        )
        accounts = DemoAccounts(SessionLocal, clock, Denylist(redis, enqueue))
        broker = create_redis(settings.celery_broker_url)
        runner = DemoRunner(SessionLocal, redis, clock, auctions, accounts, broker=broker)
        cast = await runner.start(settings.demo_stress_bots)
        # PID 1 in its container ignores SIGTERM without a handler: stop cleanly instead of
        # being killed 10 s later, so a run in progress ends with its reason
        serving = asyncio.current_task()
        assert serving is not None
        loop = asyncio.get_running_loop()
        for stop in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(stop, serving.cancel)
        log.info("demo runner ready")
        try:
            await runner.serve(cast)
        except asyncio.CancelledError:
            log.info("demo runner stopped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
