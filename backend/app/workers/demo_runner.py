"""The demo runner (brief 12.3, 12.4): `python -m app.workers.demo_runner`, run as the compose
service `demo` under the profile `demo`.

One process and one event loop, not a Celery task: a run keeps 50 sockets open for over a minute,
which would block a prefork worker. Only one runner works at a time: it holds a Postgres advisory
lock on a connection of its own for as long as it lives, and a second runner exits. It tells the
API it is alive with a Redis key it renews every 2 s (gone 10 s after it stops), aborts whatever
run a previous runner left unfinished, and creates each run's fresh lot through the real lifecycle
service, as the demo seller.
"""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.core.clock import Clock, SystemClock
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.ratelimit import RateLimiter
from app.core.redis import create_redis
from app.core.storage import LocalStorage
from app.repositories.category import CategoryRepository
from app.repositories.demo import DemoRunRepository
from app.services.auctions import AuctionService
from app.services.auth import CurrentUser
from app.services.catalog import CatalogService
from app.services.demo import RUNNER_ALIVE_KEY
from app.services.demo_accounts import DemoAccount, DemoAccounts, DemoCast
from app.services.denylist import Denylist
from app.workers.dispatch import enqueue

log = logging.getLogger("demo_runner")

IMAGES = Path(__file__).resolve().parents[2] / "demo" / "images"  # CC0 (demo/images/CREDITS.md)
HEARTBEAT_EVERY_S = 2.0
HEARTBEAT_TTL_S = 10

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


def refusal() -> str | None:
    if not settings.demo_mode:
        return "DEMO_MODE is off: the demo runner does not start"
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
    ) -> None:
        self._sessions, self._redis, self._clock = sessions, redis, clock
        self._auctions, self._accounts = auctions, accounts

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

    async def serve(self) -> None:
        while True:
            try:
                await self.beat()
            except Exception:
                log.exception("demo runner heartbeat failed")
            await asyncio.sleep(HEARTBEAT_EVERY_S)

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
        runner = DemoRunner(SessionLocal, redis, clock, auctions, accounts)
        await runner.start(settings.demo_stress_bots)
        log.info("demo runner ready")
        await runner.serve()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
