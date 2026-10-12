from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.orm import aliased

from app.models import Auction, Category
from app.repositories.base import BaseRepository


class CategoryRepository(BaseRepository):
    async def all(self) -> list[Category]:
        stmt = select(Category).order_by(Category.name, Category.id)
        return list((await self.session.execute(stmt)).scalars())

    async def get(self, category_id: int) -> Category | None:
        return await self.session.get(Category, category_id)

    async def get_by_slug(self, slug: str) -> Category | None:
        stmt = select(Category).where(Category.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, name: str, slug: str, parent_id: int | None) -> Category:
        category = Category(name=name, slug=slug, parent_id=parent_id)
        self.session.add(category)
        await self.session.flush()
        return category

    async def update(self, category_id: int, values: dict[str, Any]) -> None:
        await self.session.execute(
            update(Category).where(Category.id == category_id).values(**values)
        )

    async def delete(self, category_id: int) -> None:
        await self.session.execute(delete(Category).where(Category.id == category_id))

    async def subtree_ids(self, category_id: int) -> list[int]:
        """The category and all its descendants, by recursive CTE (FR-21)."""
        tree = select(Category.id).where(Category.id == category_id).cte("tree", recursive=True)
        child = aliased(Category)
        tree = tree.union_all(select(child.id).where(child.parent_id == tree.c.id))
        return list((await self.session.execute(select(tree.c.id))).scalars())

    async def in_use(self, category_id: int) -> bool:
        """Has child categories or auctions."""
        children = exists().where(Category.parent_id == category_id)
        auctions = exists().where(Auction.category_id == category_id)
        return bool((await self.session.execute(select(children | auctions))).scalar())
