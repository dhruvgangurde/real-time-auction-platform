from uuid import UUID

from sqlalchemy import func, select, update

from app.models.demo import ACTIVE_STATES, DemoRun
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
