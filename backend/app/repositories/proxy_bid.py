from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, exists, func, literal, select, text, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from app.models import ProxyBid
from app.repositories.base import BaseRepository


class ProxyBidRepository(BaseRepository):
    async def list_active(self, auction_id: UUID) -> list[tuple[UUID, int, int]]:
        """(bidder_id, max_amount, arm_seq) for every active maximum (rebuild, FR-43)."""
        stmt = select(ProxyBid.bidder_id, ProxyBid.max_amount, ProxyBid.arm_seq).where(
            ProxyBid.auction_id == auction_id, ProxyBid.is_active.is_(True)
        )
        return [(r.bidder_id, r.max_amount, r.arm_seq) for r in await self.session.execute(stmt)]

    async def max_arm_seq(self, auction_id: UUID) -> int:
        """Highest arm_seq ever persisted for the auction, active or not."""
        stmt = select(func.coalesce(func.max(ProxyBid.arm_seq), 0)).where(
            ProxyBid.auction_id == auction_id
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def upsert_active(
        self, auction_id: UUID, bidder_id: UUID, max_amount: int, arm_seq: int, armed_at: datetime
    ) -> None:
        """Apply a proxy_set outbox entry idempotently (FR-45, FR-46).

        The entry is applied only when its arm_seq is above every row ever persisted for this
        (auction, bidder), active or not; otherwise it is a stale replay and a no-op. A stale
        replay must never resurrect a maximum that was already deactivated. The check and the
        write happen under a transaction-scoped advisory lock on the pair, so two persisters
        holding the same batch cannot both pass the check.
        """
        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtext(str(auction_id)), func.hashtext(str(bidder_id))
                )
            )
        )
        other = aliased(ProxyBid)  # alias: never correlated to the row being written
        newer_than_all = ~exists().where(
            other.auction_id == auction_id,
            other.bidder_id == bidder_id,
            other.arm_seq >= arm_seq,
        )
        raised = await self.session.execute(
            update(ProxyBid)
            .where(
                ProxyBid.auction_id == auction_id,
                ProxyBid.bidder_id == bidder_id,
                ProxyBid.is_active.is_(True),
                newer_than_all,
            )
            .values(
                max_amount=func.greatest(ProxyBid.max_amount, max_amount),
                arm_seq=arm_seq,
                armed_at=armed_at,
                updated_at=func.now(),
            )
        )
        if raised.rowcount:
            return
        values = select(
            literal(auction_id, PG_UUID(as_uuid=True)),
            literal(bidder_id, PG_UUID(as_uuid=True)),
            literal(max_amount, BigInteger),
            literal(arm_seq, BigInteger),
            literal(armed_at, DateTime(timezone=True)),
            literal(armed_at, DateTime(timezone=True)),
        ).where(newer_than_all)
        await self.session.execute(
            insert(ProxyBid)
            .from_select(
                ["auction_id", "bidder_id", "max_amount", "arm_seq", "armed_at", "updated_at"],
                values,
            )
            .on_conflict_do_nothing(
                index_elements=["auction_id", "bidder_id"], index_where=text("is_active")
            )
        )

    async def deactivate(self, auction_id: UUID, bidder_id: UUID, arm_seq: int) -> None:
        """Deactivate exactly the maximum with this arm_seq; a replay never touches a newer one."""
        await self.session.execute(
            update(ProxyBid)
            .where(
                ProxyBid.auction_id == auction_id,
                ProxyBid.bidder_id == bidder_id,
                ProxyBid.is_active.is_(True),
                ProxyBid.arm_seq == arm_seq,
            )
            .values(is_active=False, updated_at=func.now())
        )

    async def deactivate_all(self, auction_id: UUID) -> int:
        """Every maximum of an auction that reached a terminal status (FR-51)."""
        result = await self.session.execute(
            update(ProxyBid)
            .where(ProxyBid.auction_id == auction_id, ProxyBid.is_active.is_(True))
            .values(is_active=False, updated_at=func.now())
        )
        return result.rowcount
