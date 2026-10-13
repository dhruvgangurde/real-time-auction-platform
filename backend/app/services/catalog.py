"""Discovery: categories, listing and search, auction detail, bid history, watchlist and the
seller dashboard (FR-19 to FR-27, FR-30, FR-75, BR-6, BR-10).

Live and closing auctions show price, bid count and end time from Redis, which may be ahead of
Postgres (D7); everything else comes from Postgres. Drafts are never listed and are visible
only to their seller and admins; cancelled auctions are never listed (FR-30).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.core.storage import LocalStorage
from app.models import Auction, Category
from app.repositories import (
    AuctionImageRepository,
    AuctionRepository,
    AuditLogRepository,
    BidRepository,
    CategoryRepository,
    UserRepository,
    WatchlistRepository,
)
from app.services.auth import CurrentUser
from app.services.live_events import alias, server_time_ms
from app.services.live_state import LIVE_STATES, state_key, to_ms
from app.services.snapshot import live_snapshot, load_visible_auction, read_live, visible

LISTED_BY_DEFAULT = ("scheduled", "live", "closing")  # closed ones only when asked for
CLOSED = ("closed_sold", "closed_no_sale")

DEFAULT_CATEGORIES = (  # (name, slug, parent slug)
    ("Electronics", "electronics", None),
    ("Phones", "phones", "electronics"),
    ("Laptops", "laptops", "electronics"),
    ("Collectibles", "collectibles", None),
    ("Coins", "coins", "collectibles"),
    ("Stamps", "stamps", "collectibles"),
    ("Fashion", "fashion", None),
    ("Home and garden", "home-and-garden", None),
)

LiveValues = dict[UUID, tuple[int | None, int, int]]  # price, bid count, end_time_ms


def _category_view(category: Category) -> dict[str, Any]:
    return {
        "id": category.id,
        "name": category.name,
        "slug": category.slug,
        "parent_id": category.parent_id,
    }


def _not_found() -> AppError:
    return AppError("CATEGORY_NOT_FOUND", "unknown category", 422)


class CatalogService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], redis: Redis, storage: LocalStorage
    ) -> None:
        self._sessions, self._redis, self._storage = sessions, redis, storage

    # ---------------------------------------------------------------- categories (FR-20)

    async def category_tree(self) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            categories = await CategoryRepository(session).all()
        nodes = {c.id: {**_category_view(c), "children": []} for c in categories}
        roots: list[dict[str, Any]] = []
        for c in categories:
            parent = nodes.get(c.parent_id) if c.parent_id is not None else None
            (parent["children"] if parent else roots).append(nodes[c.id])
        return roots

    async def create_category(
        self, admin: CurrentUser | None, name: str, slug: str, parent_id: int | None, reason: str
    ) -> dict[str, Any]:
        """admin None means the CLI (actor NULL, FR-77)."""
        try:
            async with self._sessions() as session, session.begin():
                categories = CategoryRepository(session)
                if parent_id is not None and await categories.get(parent_id) is None:
                    raise _not_found()
                category = await categories.create(name, slug, parent_id)
                await AuditLogRepository(session).add(
                    entity_type="category",
                    entity_id=str(category.id),
                    action="category_created",
                    actor_id=admin.id if admin else None,
                    after=_category_view(category),
                    reason=reason,
                )
        except IntegrityError as exc:
            raise AppError("SLUG_TAKEN", "a category with this slug exists", 409) from exc
        return _category_view(category)

    async def update_category(
        self, admin: CurrentUser, category_id: int, changes: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        """Rename and/or re-parent; a parent inside the category's own subtree is a cycle."""
        try:
            async with self._sessions() as session, session.begin():
                categories = CategoryRepository(session)
                category = await categories.get(category_id)
                if category is None:
                    raise AppError("NOT_FOUND", "category not found", 404)
                parent_id = changes.get("parent_id")
                if parent_id is not None:
                    if await categories.get(parent_id) is None:
                        raise _not_found()
                    if parent_id in await categories.subtree_ids(category_id):
                        raise AppError("CATEGORY_CYCLE", "a category cannot move under itself", 422)
                before = _category_view(category)
                await categories.update(category_id, changes)
                await session.refresh(category)
                await AuditLogRepository(session).add(
                    entity_type="category",
                    entity_id=str(category_id),
                    action="category_updated",
                    actor_id=admin.id,
                    before=before,
                    after=_category_view(category),
                    reason=reason,
                )
        except IntegrityError as exc:
            raise AppError("SLUG_TAKEN", "a category with this slug exists", 409) from exc
        return _category_view(category)

    async def delete_category(self, admin: CurrentUser, category_id: int, reason: str) -> None:
        async with self._sessions() as session, session.begin():
            categories = CategoryRepository(session)
            category = await categories.get(category_id)
            if category is None:
                raise AppError("NOT_FOUND", "category not found", 404)
            if await categories.in_use(category_id):
                raise AppError("CATEGORY_IN_USE", "the category has subcategories or auctions", 409)
            before = _category_view(category)
            await categories.delete(category_id)
            await AuditLogRepository(session).add(
                entity_type="category",
                entity_id=str(category_id),
                action="category_deleted",
                actor_id=admin.id,
                before=before,
                reason=reason,
            )

    async def seed_defaults(self) -> int:
        """Idempotent default tree for a fresh install (CLI). Returns how many were created."""
        created = 0
        for name, slug, parent_slug in DEFAULT_CATEGORIES:
            async with self._sessions() as session:
                categories = CategoryRepository(session)
                if await categories.get_by_slug(slug) is not None:
                    continue
                parent = await categories.get_by_slug(parent_slug) if parent_slug else None
            await self.create_category(None, name, slug, parent.id if parent else None, "cli")
            created += 1
        return created

    # ---------------------------------------------------------------- live values

    async def _live_values(self, auctions: list[Auction]) -> LiveValues:
        live = [a for a in auctions if a.status in LIVE_STATES]
        if not live:
            return {}
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for a in live:
                    pipe.hmget(
                        state_key(a.id), "status", "highest_amount", "sequence", "end_time_ms"
                    )
                rows = await pipe.execute()
        except RedisError:
            return {}  # Postgres values, which may lag by a few seconds
        values: LiveValues = {}
        for a, (status, highest, sequence, end_ms) in zip(live, rows, strict=True):
            if status is None or status == "rebuilding":
                continue
            values[a.id] = (int(highest) if int(sequence) else None, int(sequence), int(end_ms))
        return values

    def _card(self, a: Auction, live: LiveValues, thumbnails: dict[UUID, str]) -> dict[str, Any]:
        price, bids, end_ms = live.get(a.id, (a.current_price, a.bid_count, to_ms(a.end_time)))
        key = thumbnails.get(a.id)
        return {
            "id": str(a.id),
            "title": a.title,
            "status": a.status,
            "category_id": a.category_id,
            "starting_price": a.starting_price,
            "current_price": price,
            "bid_count": bids,
            "start_time_ms": to_ms(a.start_time),
            "end_time_ms": end_ms,
            "thumbnail_url": self._storage.url(key) if key else None,
        }

    async def _cards(self, auctions: list[Auction]) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            thumbnails = await AuctionImageRepository(session).first_for([a.id for a in auctions])
        live = await self._live_values(auctions)
        return [self._card(a, live, thumbnails) for a in auctions]

    # ---------------------------------------------------------------- listing (FR-21 to FR-23)

    async def list_auctions(
        self,
        *,
        query: str | None,
        category_id: int | None,
        statuses: list[str] | None,
        min_price: int | None,
        max_price: int | None,
        closing_before: datetime | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        if min_price is not None and max_price is not None and min_price > max_price:
            raise AppError(
                "VALIDATION_ERROR",
                "min_price exceeds max_price",
                422,
                fields={"min_price": "must not exceed max_price"},
            )
        async with self._sessions() as session:
            category_ids = None
            if category_id is not None:
                categories = CategoryRepository(session)
                if await categories.get(category_id) is None:
                    raise _not_found()
                category_ids = await categories.subtree_ids(category_id)
            rows = await AuctionRepository(session).search(
                query=(query or "").strip() or None,
                statuses=list(statuses or LISTED_BY_DEFAULT),
                category_ids=category_ids,
                min_price=min_price,
                max_price=max_price,
                closing_before=closing_before,
                limit=page_size + 1,
                offset=(page - 1) * page_size,
            )
        return {
            "items": await self._cards(rows[:page_size]),
            "page": page,
            "page_size": page_size,
            "has_more": len(rows) > page_size,
            "server_time_ms": server_time_ms(),
        }

    # ---------------------------------------------------------------- detail (FR-26, BR-6)

    async def auction_detail(self, viewer: CurrentUser | None, auction_id: UUID) -> dict[str, Any]:
        snapshot = await live_snapshot(auction_id, viewer, self._redis, self._sessions)
        async with self._sessions() as session:
            auction = await AuctionRepository(session).get(auction_id)
            assert auction is not None  # live_snapshot checked existence and visibility
            category = await CategoryRepository(session).get(auction.category_id)
            images = await AuctionImageRepository(session).for_auction(auction_id)
            seller = await UserRepository(session).get(auction.seller_id)
        price, reserve = snapshot["current_price"], auction.reserve_price
        detail = {
            **snapshot,
            "title": auction.title,
            "description": auction.description,
            "category": _category_view(category) if category else None,
            "images": [
                {"id": i.id, "url": self._storage.url(i.object_key), "position": i.position}
                for i in images
            ],
            "seller": {"id": str(auction.seller_id), "name": seller.full_name if seller else None},
            "start_time_ms": to_ms(auction.start_time),
            "original_end_time_ms": to_ms(auction.original_end_time),
            "antisnipe_threshold_s": auction.antisnipe_threshold_s,
            "antisnipe_extension_s": auction.antisnipe_extension_s,
            "has_reserve": reserve is not None,
            "reserve_met": None if reserve is None else (price is not None and price >= reserve),
        }
        if viewer is not None and viewer.id == auction.seller_id:
            detail["reserve_price"] = reserve  # the seller's own; nobody else (BR-6)
        return detail

    # ---------------------------------------------------------------- bid history (FR-27)

    async def bid_history(
        self, viewer: CurrentUser | None, auction_id: UUID, before: int | None, limit: int
    ) -> dict[str, Any]:
        """Newest first, keyset on sequence_no. For a live or closing auction, bids accepted but
        not yet persisted are merged from the outbox, never above the state's sequence.
        Aliases for everyone; admins also see bidder ids (BR-10)."""
        auction = await load_visible_auction(auction_id, viewer, self._sessions)
        merged: dict[int, tuple[str, int, str, int]] = {}
        upto, degraded = None, False
        if auction.status in LIVE_STATES:
            try:
                state, outbox, _, _ = await read_live(auction_id, self._redis)
                if state and state.get("status") != "rebuilding":
                    upto = int(state["sequence"])
                    for _, f in outbox:
                        if f.get("kind", "bid") == "bid":
                            merged[int(f["sequence_no"])] = (
                                f["bidder_id"],
                                int(f["amount"]),
                                f["bid_type"],
                                int(f["accepted_at_ms"]),
                            )
            except RedisError:
                degraded = True  # the ledger alone, which may lag
        async with self._sessions() as session:
            rows = await BidRepository(session).history(auction_id, before, limit + 1)
        for r in rows:
            merged.setdefault(
                r.sequence_no, (str(r.bidder_id), r.amount, r.bid_type, to_ms(r.created_at))
            )
        sequences = sorted(
            (s for s in merged if (before is None or s < before) and (upto is None or s <= upto)),
            reverse=True,
        )
        admin = viewer is not None and "admin" in viewer.roles
        items = []
        for seq in sequences[:limit]:
            bidder, amount, kind, at_ms = merged[seq]
            item: dict[str, Any] = {
                "sequence_no": seq,
                "amount": amount,
                "bid_type": kind,
                "bidder_alias": alias(auction_id, bidder),
                "placed_at_ms": at_ms,
            }
            if admin:
                item["bidder_id"] = bidder
            items.append(item)
        more = len(sequences) > limit
        return {
            "items": items,
            "next_before": items[-1]["sequence_no"] if more else None,
            "degraded": degraded,
        }

    # ---------------------------------------------------------------- watchlist (FR-24, FR-25)

    async def watch(self, user: CurrentUser, auction_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            auction = await AuctionRepository(session).get(auction_id)
            if auction is None or not visible(auction, user):
                raise AppError("NOT_FOUND", "auction not found", 404)
            if auction.status == "cancelled":
                raise AppError("ALREADY_CLOSED", "a cancelled auction cannot be watched", 409)
            await WatchlistRepository(session).add(user.id, auction_id)

    async def unwatch(self, user: CurrentUser, auction_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            if not await WatchlistRepository(session).remove(user.id, auction_id):
                raise AppError("NOT_WATCHING", "this auction is not on your watchlist", 404)

    async def watchlist(self, user: CurrentUser) -> dict[str, Any]:
        async with self._sessions() as session:
            auctions = await WatchlistRepository(session).auctions_for(user.id)
        return {"items": await self._cards(auctions), "server_time_ms": server_time_ms()}

    # ---------------------------------------------------------------- seller dashboard (FR-19)

    async def seller_auctions(self, user: CurrentUser, page: int, page_size: int) -> dict[str, Any]:
        async with self._sessions() as session:
            rows = await AuctionRepository(session).list_for_seller(
                user.id, page_size + 1, (page - 1) * page_size
            )
        items = await self._cards(rows[:page_size])
        for item in items:
            item["outcome"] = item["status"] if item["status"] in CLOSED else None
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "has_more": len(rows) > page_size,
            "server_time_ms": server_time_ms(),
        }
