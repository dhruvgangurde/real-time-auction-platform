from uuid import UUID

from sqlalchemy import select

from app.models.demo import DemoRun
from app.repositories.base import BaseRepository


class DemoRunRepository(BaseRepository):
    async def latest_verified_auction(self, kind: str) -> UUID | None:
        """The lot of the most recent run whose checks all passed: what the hero replays."""
        return await self.session.scalar(
            select(DemoRun.auction_id)
            .where(DemoRun.kind == kind, DemoRun.state == "verified")
            .order_by(DemoRun.finished_at.desc())
            .limit(1)
        )
