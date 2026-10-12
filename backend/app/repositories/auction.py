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
