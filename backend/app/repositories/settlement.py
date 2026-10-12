from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.models import Settlement
from app.repositories.base import BaseRepository


class SettlementRepository(BaseRepository):
    async def for_auction(self, auction_id: UUID) -> Settlement | None:
        stmt = select(Settlement).where(Settlement.auction_id == auction_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, **values: Any) -> Settlement:
        """UNIQUE(auction_id) and UNIQUE(idempotency_key) refuse a second one (FR-61, OR-14)."""
        settlement = Settlement(**values)
        self.session.add(settlement)
        await self.session.flush()
        return settlement
