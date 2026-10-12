from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update

from app.models import Auction
from app.repositories.base import BaseRepository


class AuctionRepository(BaseRepository):
    async def get(self, auction_id: UUID) -> Auction | None:
        return await self.session.get(Auction, auction_id)

    async def list_live_ids(self) -> list[UUID]:
        stmt = select(Auction.id).where(Auction.status == "live")
        return list((await self.session.execute(stmt)).scalars())

    async def statuses(self, auction_ids: list[UUID]) -> dict[UUID, str]:
        """Status per auction; unknown ids are absent."""
        stmt = select(Auction.id, Auction.status).where(Auction.id.in_(auction_ids))
        return {r.id: r.status for r in (await self.session.execute(stmt)).all()}

    async def extension_state(self, auction_id: UUID) -> tuple[datetime, int]:
        """end_time and extension_count, row locked for the caller's transaction, so the
        audit 'before' of a soft-close extension is exact (FR-77)."""
        stmt = (
            select(Auction.end_time, Auction.extension_count)
            .where(Auction.id == auction_id)
            .with_for_update()
        )
        row = (await self.session.execute(stmt)).one()
        return row.end_time, row.extension_count

    async def apply_extension(
        self, auction_id: UUID, end_time: datetime, extension_count: int
    ) -> None:
        """Persist a soft-close extension (FR-55). Never moves end_time or the count backwards,
        so replays and out-of-order batches are harmless. original_end_time is untouched."""
        stmt = (
            update(Auction)
            .where(Auction.id == auction_id)
            .values(
                end_time=func.greatest(Auction.end_time, end_time),
                extension_count=func.greatest(Auction.extension_count, extension_count),
                updated_at=func.now(),
            )
        )
        await self.session.execute(stmt)
