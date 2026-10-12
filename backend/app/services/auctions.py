"""Auction lifecycle for sellers: create, edit, publish, cancel, images
(FR-11 to FR-14, FR-17, FR-18, BR-1, BR-4, BR-9, NFR-22, NFR-24, D1, D8).

Every change and its audit row (before and after) commit in one transaction (FR-77, FR-78).
Editing rules by status (FR-17):
- draft, scheduled: any field, re-validated (FR-12); original_end_time follows end_time (D1)
- live: title, description and images only, and only while no bid exists: Redis sequence == 0
  (D8); if Redis cannot answer, the edit is refused (fail closed)
- closing, closed, cancelled: nothing (ALREADY_CLOSED)
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimit, RateLimiter
from app.core.storage import LocalStorage
from app.models import Auction
from app.repositories import (
    AuctionImageRepository,
    AuctionRepository,
    AuditLogRepository,
    CategoryRepository,
)
from app.services.auth import CurrentUser
from app.services.images import read_capped, reencode
from app.services.live_state import (
    AuctionNotLive,
    LiveStateUnavailable,
    init_live_state,
    state_key,
    to_ms,
)
from app.services.snapshot import visible

EDITABLE = (
    "title",
    "description",
    "category_id",
    "starting_price",
    "reserve_price",
    "min_increment",
    "start_time",
    "end_time",
    "antisnipe_threshold_s",
    "antisnipe_extension_s",
)
DESCRIPTIVE = frozenset({"title", "description"})  # editable on a live auction without bids
OPEN = frozenset({"draft", "scheduled"})


def auction_view(auction: Auction, *, owner: bool) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": str(auction.id),
        "seller_id": str(auction.seller_id),
        "category_id": auction.category_id,
        "title": auction.title,
        "description": auction.description,
        "status": auction.status,
        "starting_price": auction.starting_price,
        "min_increment": auction.min_increment,
        "has_reserve": auction.reserve_price is not None,
        "start_time_ms": to_ms(auction.start_time),
        "end_time_ms": to_ms(auction.end_time),
        "original_end_time_ms": to_ms(auction.original_end_time),
        "antisnipe_threshold_s": auction.antisnipe_threshold_s,
        "antisnipe_extension_s": auction.antisnipe_extension_s,
        "extension_count": auction.extension_count,
        "current_price": auction.current_price,
        "bid_count": auction.bid_count,
        "created_at_ms": to_ms(auction.created_at),
    }
    if owner:
        view["reserve_price"] = auction.reserve_price  # never to anyone else (BR-6)
    return view


def _audit_values(auction: Auction, names: Any) -> dict[str, Any]:
    values = {}
    for name in names:
        value = getattr(auction, name)
        values[name] = to_ms(value) if isinstance(value, datetime) else value
    return values


def _status_error(status: str, action: str) -> AppError:
    if status in ("live", "closing"):
        return AppError("AUCTION_ALREADY_LIVE", f"a {status} auction cannot {action}", 409)
    if status == "scheduled":
        return AppError("NOT_DRAFT", f"only a draft can {action}", 409)
    return AppError("ALREADY_CLOSED", f"a {status} auction cannot {action}", 409)


class AuctionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        clock: Clock,
        limiter: RateLimiter,
        storage: LocalStorage,
    ) -> None:
        self._sessions, self._redis, self._clock = sessions, redis, clock
        self._limiter, self._storage = limiter, storage

    # ---------------------------------------------------------------- checks

    def _validate(self, values: dict[str, Any], *, check_start: bool) -> None:
        """FR-12, per field. Types and ranges come from the request schema."""
        errors: dict[str, str] = {}
        if values["end_time"] <= values["start_time"]:
            errors["end_time"] = "must be later than start_time"
        if check_start and values["start_time"] < self._clock.now():
            errors["start_time"] = "must not be in the past"
        reserve = values.get("reserve_price")
        if reserve is not None and reserve < values["starting_price"]:
            errors["reserve_price"] = "must not be less than starting_price"
        for name, low, high in (
            (
                "antisnipe_threshold_s",
                settings.antisnipe_threshold_min_s,
                settings.antisnipe_threshold_max_s,
            ),
            (
                "antisnipe_extension_s",
                settings.antisnipe_extension_min_s,
                settings.antisnipe_extension_max_s,
            ),
        ):
            if not low <= values[name] <= high:
                errors[name] = f"must be between {low} and {high} seconds"
        if errors:
            raise AppError("VALIDATION_ERROR", "invalid auction", 422, fields=errors)

    @staticmethod
    async def _require_category(session: AsyncSession, category_id: int) -> None:
        if await CategoryRepository(session).get(category_id) is None:
            raise AppError(
                "CATEGORY_NOT_FOUND",
                "unknown category",
                422,
                fields={"category_id": "unknown category"},
            )

    @staticmethod
    def _owned(auction: Auction | None, user: CurrentUser) -> Auction:
        """A hidden draft is NOT_FOUND; anyone but the seller gets FORBIDDEN (FR-18)."""
        if auction is None or not visible(auction, user):
            raise AppError("NOT_FOUND", "auction not found", 404)
        if auction.seller_id != user.id:
            raise AppError("FORBIDDEN", "only the seller can change this auction", 403)
        return auction

    async def _require_no_bids(self, auction_id: UUID) -> None:
        """D8: 'a bid exists' means Redis sequence > 0; unknown means refuse (fail closed)."""
        unavailable = AppError(
            "SERVICE_UNAVAILABLE", "cannot confirm the auction has no bids; retry", 503
        )
        try:
            sequence = await self._redis.hget(state_key(auction_id), "sequence")
            if sequence is None:  # live state lost: rebuild it from Postgres first
                await init_live_state(auction_id, self._redis, self._sessions)
                sequence = await self._redis.hget(state_key(auction_id), "sequence")
        except (RedisError, LiveStateUnavailable, AuctionNotLive) as exc:
            raise unavailable from exc
        if sequence is None:
            raise unavailable
        if int(sequence) > 0:
            raise AppError(
                "AUCTION_ALREADY_LIVE", "the auction has bids and can no longer change (BR-4)", 409
            )

    async def _require_editable(self, auction: Auction, *, descriptive: bool) -> None:
        """descriptive: the change touches only title, description or images."""
        if auction.status in OPEN:
            return
        if auction.status == "live":
            if not descriptive:
                raise AppError(
                    "AUCTION_ALREADY_LIVE",
                    "once live, only title, description and images can change",
                    409,
                )
            await self._require_no_bids(auction.id)
            return
        raise AppError("ALREADY_CLOSED", f"a {auction.status} auction cannot change", 409)

    # ---------------------------------------------------------------- create and edit

    async def create(self, user: CurrentUser, data: dict[str, Any]) -> dict[str, Any]:
        await self._limiter.hit(
            "auction_create", str(user.id), RateLimit.parse(settings.rate_limit_auction_create)
        )
        self._validate(data, check_start=True)
        async with self._sessions() as session, session.begin():
            await self._require_category(session, data["category_id"])
            auction = await AuctionRepository(session).create(
                **data, seller_id=user.id, status="draft", original_end_time=data["end_time"]
            )
            await AuditLogRepository(session).add(
                entity_type="auction",
                entity_id=str(auction.id),
                action="auction_created",
                actor_id=user.id,
                after=_audit_values(auction, (*EDITABLE, "status")),
            )
        return auction_view(auction, owner=True)

    async def update(
        self, user: CurrentUser, auction_id: UUID, changes: dict[str, Any]
    ) -> dict[str, Any]:
        if not changes:
            raise AppError("VALIDATION_ERROR", "no fields to change", 422)
        async with self._sessions() as session, session.begin():
            auctions = AuctionRepository(session)
            auction = self._owned(await auctions.get_for_update(auction_id), user)
            await self._require_editable(auction, descriptive=set(changes) <= DESCRIPTIVE)
            if auction.status in OPEN:
                merged = {name: getattr(auction, name) for name in EDITABLE} | changes
                self._validate(merged, check_start="start_time" in changes)
                if "category_id" in changes:
                    await self._require_category(session, changes["category_id"])
                if "end_time" in changes:  # D1: the scheduled end until go-live
                    changes = {**changes, "original_end_time": changes["end_time"]}
            before = _audit_values(auction, changes)
            await auctions.update_fields(auction_id, changes)
            await session.refresh(auction)
            await AuditLogRepository(session).add(
                entity_type="auction",
                entity_id=str(auction_id),
                action="auction_updated",
                actor_id=user.id,
                before=before,
                after=_audit_values(auction, changes),
            )
        return auction_view(auction, owner=True)

    # ---------------------------------------------------------------- transitions

    async def _transition(
        self, session: AsyncSession, auction: Auction, to_status: str, user: CurrentUser
    ) -> None:
        from_status = auction.status
        moved = await AuctionRepository(session).transition(auction.id, from_status, to_status)
        assert moved, "row is locked by this transaction"
        await AuditLogRepository(session).add(
            entity_type="auction",
            entity_id=str(auction.id),
            action="status_changed",
            actor_id=user.id,
            before={"status": from_status},
            after={"status": to_status},
        )
        await session.refresh(auction)

    async def publish(self, user: CurrentUser, auction_id: UUID) -> dict[str, Any]:
        """draft -> scheduled, with at least one image (FR-13) and FR-12 re-checked."""
        async with self._sessions() as session, session.begin():
            auction = self._owned(await AuctionRepository(session).get_for_update(auction_id), user)
            if auction.status != "draft":
                raise _status_error(auction.status, "be published")
            if await AuctionImageRepository(session).count(auction_id) < 1:
                raise AppError(
                    "VALIDATION_ERROR",
                    "an auction needs at least one image before it leaves draft",
                    422,
                    fields={"images": "at least one image is required (FR-13)"},
                )
            values = {name: getattr(auction, name) for name in EDITABLE}
            self._validate(values, check_start=True)
            await self._transition(session, auction, "scheduled", user)
        return auction_view(auction, owner=True)

    async def cancel(self, user: CurrentUser, auction_id: UUID) -> dict[str, Any]:
        """draft or scheduled -> cancelled; a seller cannot cancel once live (FR-18, BR-9)."""
        async with self._sessions() as session, session.begin():
            auction = self._owned(await AuctionRepository(session).get_for_update(auction_id), user)
            if auction.status not in OPEN:
                raise _status_error(auction.status, "be cancelled")
            await self._transition(session, auction, "cancelled", user)
        return auction_view(auction, owner=True)

    # ---------------------------------------------------------------- images (FR-13, NFR-24)

    async def add_image(
        self, user: CurrentUser, auction_id: UUID, body: AsyncIterator[bytes]
    ) -> dict[str, Any]:
        async with self._sessions() as session:  # cheap ownership check before the upload
            self._owned(await AuctionRepository(session).get(auction_id), user)
        data = await read_capped(body, settings.image_max_bytes)
        encoded, extension = await asyncio.to_thread(reencode, data)
        key = f"auctions/{auction_id}/{uuid4().hex}.{extension}"

        async with self._sessions() as session:
            auction = self._owned(await AuctionRepository(session).get_for_update(auction_id), user)
            await self._require_editable(auction, descriptive=True)
            images = AuctionImageRepository(session)
            if await images.count(auction_id) >= settings.images_per_auction:
                raise AppError(
                    "TOO_MANY_IMAGES",
                    f"an auction has at most {settings.images_per_auction} images",
                    409,
                )
            # save the file, then commit; a failed commit deletes the file, so no orphan
            await self._storage.save(key, encoded)
            try:
                image = await images.add(auction_id, key)
                await AuditLogRepository(session).add(
                    entity_type="auction",
                    entity_id=str(auction_id),
                    action="image_added",
                    actor_id=user.id,
                    after={"image_id": image.id, "object_key": key},
                )
                await session.commit()
            except BaseException:
                await self._storage.delete(key)
                raise
        return {"id": image.id, "url": self._storage.url(key), "position": image.position}

    async def remove_image(self, user: CurrentUser, auction_id: UUID, image_id: int) -> None:
        async with self._sessions() as session, session.begin():
            auction = self._owned(await AuctionRepository(session).get_for_update(auction_id), user)
            await self._require_editable(auction, descriptive=True)
            images = AuctionImageRepository(session)
            image = await images.get(auction_id, image_id)
            if image is None:
                raise AppError("NOT_FOUND", "image not found", 404)
            if auction.status != "draft" and await images.count(auction_id) <= 1:
                raise AppError(
                    "VALIDATION_ERROR",
                    "an auction that has left draft keeps at least one image",
                    422,
                    fields={"images": "at least one image is required (FR-13)"},
                )
            await images.delete(image_id)
            await AuditLogRepository(session).add(
                entity_type="auction",
                entity_id=str(auction_id),
                action="image_removed",
                actor_id=user.id,
                before={"image_id": image_id, "object_key": image.object_key},
            )
        await self._storage.delete(image.object_key)  # after the commit
