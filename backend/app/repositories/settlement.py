from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update

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

    async def get(self, settlement_id: UUID) -> Settlement | None:
        return await self.session.get(Settlement, settlement_id)

    async def mark_paid(self, settlement_id: UUID) -> bool:
        """pending_payment -> paid, the only transition out of pending_payment (FR-68, B.5)."""
        stmt = (
            update(Settlement)
            .where(Settlement.id == settlement_id, Settlement.status == "pending_payment")
            .values(status="paid", paid_at=func.now())
            .returning(Settlement.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None
