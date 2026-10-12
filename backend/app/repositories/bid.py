from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.models import Bid
from app.repositories.base import BaseRepository


class BidRepository(BaseRepository):
    async def last_bid(self, auction_id: UUID) -> Bid | None:
        """The highest-sequence bid, which is also the highest amount (FR-43 rebuild)."""
        stmt = (
            select(Bid)
            .where(Bid.auction_id == auction_id)
            .order_by(Bid.sequence_no.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def max_sequence(self, auction_id: UUID) -> int:
        stmt = select(func.coalesce(func.max(Bid.sequence_no), 0)).where(
            Bid.auction_id == auction_id
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def insert_ignore_conflicts(self, rows: list[dict[str, Any]]) -> set[int]:
        """Insert ledger rows; existing (auction_id, sequence_no) pairs are skipped (FR-41).

        Returns the sequence numbers actually inserted.
        """
        if not rows:
            return set()
        stmt = (
            insert(Bid)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_bids_auction_id_sequence_no")
            .returning(Bid.sequence_no)
        )
        return set((await self.session.execute(stmt)).scalars())

    async def get_by_sequences(
        self, auction_id: UUID, sequence_nos: list[int]
    ) -> dict[int, tuple[UUID, int, str]]:
        stmt = select(Bid.sequence_no, Bid.bidder_id, Bid.amount, Bid.bid_type).where(
            Bid.auction_id == auction_id, Bid.sequence_no.in_(sequence_nos)
        )
        rows = (await self.session.execute(stmt)).all()
        return {r.sequence_no: (r.bidder_id, r.amount, r.bid_type) for r in rows}

    async def missing_sequences(self, auction_id: UUID, upto: int) -> list[int]:
        """Sequence numbers in 1..upto with no ledger row (reconciliation, FR-43)."""
        stmt = text(
            "SELECT s FROM generate_series(1, :upto) AS s "
            "WHERE NOT EXISTS (SELECT 1 FROM bids b WHERE b.auction_id = :a AND b.sequence_no = s) "
            "ORDER BY s"
        )
        return list((await self.session.execute(stmt, {"upto": upto, "a": auction_id})).scalars())
