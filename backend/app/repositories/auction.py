from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import REGCONFIG

from app.models import Auction
from app.repositories.base import BaseRepository


class AuctionRepository(BaseRepository):
    async def get(self, auction_id: UUID) -> Auction | None:
        return await self.session.get(Auction, auction_id)

    async def get_for_update(self, auction_id: UUID) -> Auction | None:
        """Row-locked for the caller's transaction; the scheduler's SKIP LOCKED passes it by."""
        stmt = select(Auction).where(Auction.id == auction_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, **values: Any) -> Auction:
        auction = Auction(**values)
        self.session.add(auction)
        await self.session.flush()
        await self.session.refresh(auction)
        return auction

    async def update_fields(self, auction_id: UUID, values: dict[str, Any]) -> None:
        await self.session.execute(
            update(Auction).where(Auction.id == auction_id).values(**values, updated_at=func.now())
        )

    async def transition(self, auction_id: UUID, from_status: str, to_status: str) -> bool:
        """Conditional status change; False when the auction is no longer in from_status (another
        run or request got there first), so each transition happens and is audited once."""
        stmt = (
            update(Auction)
            .where(Auction.id == auction_id, Auction.status == from_status)
            .values(status=to_status, updated_at=func.now())
            .returning(Auction.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def list_live_ids(self) -> list[UUID]:
        stmt = select(Auction.id).where(Auction.status == "live")
        return list((await self.session.execute(stmt)).scalars())

    async def statuses(self, auction_ids: list[UUID]) -> dict[UUID, str]:
        """Status per auction; unknown ids are absent."""
        stmt = select(Auction.id, Auction.status).where(Auction.id.in_(auction_ids))
        return {r.id: r.status for r in (await self.session.execute(stmt)).all()}

    # ---------------------------------------------------------------- scheduler (FR-16)

    async def db_now(self) -> datetime:
        return (await self.session.execute(select(func.now()))).scalar_one()

    async def lock_due_to_start(self, limit: int) -> list[tuple[UUID, datetime]]:
        """Scheduled auctions whose start time has passed, locked; rows another run holds are
        skipped (ix_auctions_status_start_time, OR-5)."""
        stmt = (
            select(Auction.id, Auction.start_time)
            .where(Auction.status == "scheduled", Auction.start_time <= func.now())
            .order_by(Auction.start_time)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [(r.id, r.start_time) for r in (await self.session.execute(stmt)).all()]

    async def live_ended_ids(self, limit: int) -> list[UUID]:
        """Live auctions whose Postgres end_time has passed. Postgres lags Redis and never leads
        it, so this is a superset of the auctions that have really ended."""
        stmt = (
            select(Auction.id)
            .where(Auction.status == "live", Auction.end_time <= func.now())
            .order_by(Auction.end_time)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    # ---------------------------------------------------------------- persister (D7)

    async def apply_bids(self, auction_id: UUID, amount: int, sequence_no: int) -> None:
        """Current price and bid count from the highest newly persisted bid. Never decreases:
        a replayed or out-of-order batch leaves a newer value alone. Both SET expressions see
        the old row, so the CASE compares against the old bid_count."""
        await self.session.execute(
            update(Auction)
            .where(Auction.id == auction_id)
            .values(
                current_price=case(
                    (Auction.bid_count < sequence_no, amount), else_=Auction.current_price
                ),
                bid_count=func.greatest(Auction.bid_count, sequence_no),
            )
        )

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

    # ---------------------------------------------------------------- discovery (FR-19 to FR-23)

    async def search(
        self,
        *,
        query: str | None,
        statuses: list[str],
        category_ids: list[int] | None,
        min_price: int | None,
        max_price: int | None,
        closing_before: datetime | None,
        limit: int,
        offset: int,
    ) -> list[Auction]:
        """Filtered listing (FR-23). With a query: full-text match ranked by ts_rank over the
        weighted vector, so a title match outranks a description-only one (FR-22); without:
        newest first. Ties break on created_at then id, so pages are stable."""
        price = func.coalesce(Auction.current_price, Auction.starting_price)
        stmt = select(Auction).where(Auction.status.in_(statuses))
        if category_ids is not None:
            stmt = stmt.where(Auction.category_id.in_(category_ids))
        if min_price is not None:
            stmt = stmt.where(price >= min_price)
        if max_price is not None:
            stmt = stmt.where(price <= max_price)
        if closing_before is not None:
            stmt = stmt.where(Auction.end_time < closing_before)
        order: list[Any] = [Auction.created_at.desc(), Auction.id.desc()]
        if query:
            tsquery = func.websearch_to_tsquery(cast(literal("simple"), REGCONFIG), query)
            stmt = stmt.where(Auction.search_vector.op("@@")(tsquery))
            order.insert(0, func.ts_rank(Auction.search_vector, tsquery).desc())
        stmt = stmt.order_by(*order).limit(limit).offset(offset)
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_seller(self, seller_id: UUID, limit: int, offset: int) -> list[Auction]:
        """Every auction the seller owns, drafts included, newest first (FR-19)."""
        stmt = (
            select(Auction)
            .where(Auction.seller_id == seller_id)
            .order_by(Auction.created_at.desc(), Auction.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get_many(self, auction_ids: list[UUID]) -> dict[UUID, Auction]:
        stmt = select(Auction).where(Auction.id.in_(auction_ids))
        return {a.id: a for a in (await self.session.execute(stmt)).scalars()}
