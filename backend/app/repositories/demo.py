from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.models.demo import ACTIVE_STATES, ACTIVE_STATES_SQL, DemoRun
from app.repositories.base import BaseRepository

RUNNER_LOCK_KEY = 0x44454D4F  # "DEMO": the one demo runner's advisory lock


class DemoRunRepository(BaseRepository):
    async def latest_verified_auction(self, kind: str) -> UUID | None:
        """The lot of the most recent run whose checks all passed: what the hero replays."""
        return await self.session.scalar(
            select(DemoRun.auction_id)
            .where(DemoRun.kind == kind, DemoRun.state == "verified")
            .order_by(DemoRun.finished_at.desc())
            .limit(1)
        )

    async def active(self, kind: str) -> DemoRun | None:
        stmt = select(DemoRun).where(DemoRun.kind == kind, DemoRun.state.in_(ACTIVE_STATES))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def requested_today(self, kind: str) -> int:
        """Runs requested in the last 24 hours, whatever became of them (the daily cap)."""
        since = func.now() - timedelta(days=1)
        stmt = select(func.count()).where(DemoRun.kind == kind, DemoRun.requested_at > since)
        return int(await self.session.scalar(stmt) or 0)

    async def last_finished(self, kind: str) -> tuple[datetime | None, datetime]:
        """(when the latest run of this kind finished, the database's now), one clock for every
        API instance."""
        stmt = select(func.max(DemoRun.finished_at), func.now()).where(DemoRun.kind == kind)
        last, now = (await self.session.execute(stmt)).one()
        return last, now

    async def request(self, kind: str) -> UUID | None:
        """A new run, or None if one is already active: the partial unique index decides, so
        two starts racing on two instances create one run."""
        stmt = (
            insert(DemoRun)
            .values(kind=kind, state="requested")
            .on_conflict_do_nothing(index_elements=["kind"], index_where=text(ACTIVE_STATES_SQL))
            .returning(DemoRun.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def try_runner_lock(self) -> bool:
        """A session-level advisory lock: held while this connection stays open."""
        return bool(await self.session.scalar(select(func.pg_try_advisory_lock(RUNNER_LOCK_KEY))))

    async def abort_unfinished(self, reason: str) -> list[UUID]:
        """Every run still active: a new runner cannot finish what another one started."""
        result = await self.session.execute(
            update(DemoRun)
            .where(DemoRun.state.in_(ACTIVE_STATES))
            .values(
                state="aborted",
                reason=reason,
                finished_at=func.now(),
                version=DemoRun.version + 1,
            )
            .returning(DemoRun.id)
        )
        return list(result.scalars())
