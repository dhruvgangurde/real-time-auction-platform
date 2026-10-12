from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.models import Auction, Watchlist
from app.repositories.base import BaseRepository


class WatchlistRepository(BaseRepository):
    async def add(self, user_id: UUID, auction_id: UUID) -> None:
        """Adding an already-watched auction is a no-op (FR-24)."""
        stmt = (
            insert(Watchlist)
            .values(user_id=user_id, auction_id=auction_id)
            .on_conflict_do_nothing(index_elements=["user_id", "auction_id"])
        )
        await self.session.execute(stmt)

    async def remove(self, user_id: UUID, auction_id: UUID) -> bool:
        stmt = (
            delete(Watchlist)
            .where(Watchlist.user_id == user_id, Watchlist.auction_id == auction_id)
            .returning(Watchlist.auction_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def auctions_for(self, user_id: UUID) -> list[Auction]:
        """The user's watched auctions, ending soonest first (FR-25)."""
        stmt = (
            select(Auction)
            .join(Watchlist, Watchlist.auction_id == Auction.id)
            .where(Watchlist.user_id == user_id)
            .order_by(Auction.end_time, Auction.id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def ending_soon(self, lead_s: int, limit: int) -> list[tuple[UUID, UUID, str, datetime]]:
        """(watcher, auction, title, end_time) for live auctions ending within the lead
        interval (FR-73), on ix_watchlist_auction_id."""
        stmt = (
            select(Watchlist.user_id, Auction.id, Auction.title, Auction.end_time)
            .join(Auction, Auction.id == Watchlist.auction_id)
            .where(
                Auction.status == "live",
                Auction.end_time > func.now(),
                Auction.end_time <= func.now() + timedelta(seconds=lead_s),
            )
            .order_by(Auction.end_time)
            .limit(limit)
        )
        return [tuple(r) for r in (await self.session.execute(stmt)).all()]
