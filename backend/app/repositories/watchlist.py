from uuid import UUID

from sqlalchemy import delete, select
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
