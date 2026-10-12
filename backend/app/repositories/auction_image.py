from uuid import UUID

from sqlalchemy import delete, func, select

from app.models import AuctionImage
from app.repositories.base import BaseRepository


class AuctionImageRepository(BaseRepository):
    async def for_auction(self, auction_id: UUID) -> list[AuctionImage]:
        stmt = (
            select(AuctionImage)
            .where(AuctionImage.auction_id == auction_id)
            .order_by(AuctionImage.position, AuctionImage.id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def first_for(self, auction_ids: list[UUID]) -> dict[UUID, str]:
        """object_key of each auction's first image (listing thumbnails; a page holds at most
        50 auctions of at most images_per_auction images each)."""
        stmt = (
            select(AuctionImage.auction_id, AuctionImage.object_key)
            .where(AuctionImage.auction_id.in_(auction_ids))
            .order_by(AuctionImage.position.desc(), AuctionImage.id.desc())
        )
        # last write wins, so after the descending order each auction keeps its first image
        return {r.auction_id: r.object_key for r in (await self.session.execute(stmt)).all()}

    async def count(self, auction_id: UUID) -> int:
        stmt = select(func.count()).where(AuctionImage.auction_id == auction_id)
        return (await self.session.execute(stmt)).scalar_one()

    async def add(self, auction_id: UUID, object_key: str) -> AuctionImage:
        position = (
            await self.session.execute(
                select(func.coalesce(func.max(AuctionImage.position), -1) + 1).where(
                    AuctionImage.auction_id == auction_id
                )
            )
        ).scalar_one()
        image = AuctionImage(auction_id=auction_id, object_key=object_key, position=position)
        self.session.add(image)
        await self.session.flush()
        return image

    async def get(self, auction_id: UUID, image_id: int) -> AuctionImage | None:
        stmt = select(AuctionImage).where(
            AuctionImage.id == image_id, AuctionImage.auction_id == auction_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete(self, image_id: int) -> None:
        await self.session.execute(delete(AuctionImage).where(AuctionImage.id == image_id))
