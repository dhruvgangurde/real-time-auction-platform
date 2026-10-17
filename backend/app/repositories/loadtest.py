from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from app.models import Bid
from app.repositories.base import BaseRepository


class LoadTestRepository(BaseRepository):
    """The load test's reads of the ledger (Phase 11, loadtest/README.md)."""

    async def bids_after(
        self, auction_id: UUID, after_seq: int, limit: int = 5_000
    ) -> list[tuple[int, datetime]]:
        """(sequence_no, acceptance time) of the auction's persisted bids above a number."""
        stmt = (
            select(Bid.sequence_no, Bid.created_at)
            .where(Bid.auction_id == auction_id, Bid.sequence_no > after_seq)
            .order_by(Bid.sequence_no)
            .limit(limit)
        )
        return [(int(s), at) for s, at in (await self.session.execute(stmt)).all()]

    async def acceptances(self, auction_id: UUID) -> list[tuple[int, int, datetime]]:
        """(sequence_no, amount, acceptance time) of every bid in the ledger, in order (NFR-4)."""
        stmt = (
            select(Bid.sequence_no, Bid.amount, Bid.created_at)
            .where(Bid.auction_id == auction_id)
            .order_by(Bid.sequence_no)
        )
        return [(int(s), int(a), at) for s, a, at in (await self.session.execute(stmt)).all()]

    async def accepted_per_second(self, auction_id: UUID) -> list[tuple[datetime, int]]:
        """Accepted bids per second of their acceptance time (NFR-4)."""
        second = func.date_trunc("second", Bid.created_at)
        stmt = (
            select(second, func.count())
            .where(Bid.auction_id == auction_id)
            .group_by(second)
            .order_by(second)
        )
        return [(at, int(n)) for at, n in (await self.session.execute(stmt)).all()]
