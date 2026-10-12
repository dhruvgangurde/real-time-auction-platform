"""Bid submission: validation, arbitration, acknowledgement (UC-01, FR-35 to FR-44).

The decision is taken only by place_bid.lua (DC-2). This service validates input, calls the
script, maps the outcome, schedules persistence and returns. It never reads then writes the
highest bid itself, and never accepts a bid when Redis cannot be reached (FR-42, NFR-9).

Client contract: SERVICE_UNAVAILABLE caused by a timeout means the outcome is unknown, because
the script may have run. Clients must retry with the same request_id to learn the real result.

Auth, account status, room subscription and rate limits (FR-34, FR-44) are checked by the
Phase 5/6 callers before this service is invoked.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import lua
from app.core.config import settings
from app.repositories import AuctionRepository
from app.services import live_state
from app.services.live_state import (
    AuctionNotFound,
    LiveStateUnavailable,
    outbox_key,
    request_key,
    state_key,
)

log = logging.getLogger("bidding")

MAX_AMOUNT = 2**53 - 1  # Lua numbers are doubles; larger integers lose precision
REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NOT_STARTED = {"draft", "scheduled"}


@dataclass(frozen=True)
class BidResult:
    accepted: bool
    code: str | None = None  # rejection code from docs/architecture.md section 9
    sequence_no: int | None = None
    amount: int | None = None
    end_time_ms: int | None = None
    extended: bool = False
    extension_count: int | None = None
    current_highest: int | None = None  # BID_TOO_LOW only, for the submitter alone
    min_next: int | None = None  # BID_TOO_LOW only


def _rejected(code: str) -> dict[str, Any]:
    return {"decision": "rejected", "code": code}


def _to_result(raw: dict[str, Any]) -> BidResult:
    def opt(key: str) -> int | None:
        return int(raw[key]) if key in raw else None

    if raw["decision"] == "accepted":
        return BidResult(
            accepted=True,
            sequence_no=int(raw["sequence_no"]),
            amount=int(raw["amount"]),
            end_time_ms=int(raw["end_time_ms"]),
            extended=bool(raw["extended"]),
            extension_count=int(raw["extension_count"]),
        )
    return BidResult(
        accepted=False,
        code=raw["code"],
        current_highest=opt("current_highest"),
        min_next=opt("min_next"),
    )


def _valid(auction_id: UUID, bidder_id: UUID, amount: int, request_id: str) -> bool:
    return (
        isinstance(auction_id, UUID)
        and isinstance(bidder_id, UUID)
        and isinstance(amount, int)
        and not isinstance(amount, bool)
        and 1 <= amount <= MAX_AMOUNT
        and isinstance(request_id, str)
        and REQUEST_ID.fullmatch(request_id) is not None
    )


class BidService:
    def __init__(
        self,
        redis: Redis,
        sessions: async_sessionmaker[AsyncSession],
        schedule_persist: Callable[[UUID], None],
    ) -> None:
        self._redis = redis
        self._sessions = sessions
        self._schedule_persist = schedule_persist
        self._script = redis.register_script(lua.source("place_bid"))

    async def place_bid(
        self, auction_id: UUID, bidder_id: UUID, amount: int, request_id: str
    ) -> BidResult:
        result = await self._decide(auction_id, bidder_id, amount, request_id)
        if result.accepted:
            # acknowledge first; the durable write happens in the worker (FR-40)
            try:
                self._schedule_persist(auction_id)
            except Exception:
                log.exception("persist enqueue failed; the sweeper will persist the bid")
        # one line per submission (FR-44, NFR-32); the correlation id is added by the formatter
        log.info(
            "bid",
            extra={
                "fields": {
                    "auction_id": str(auction_id),
                    "bidder_id": str(bidder_id),
                    "amount": amount,
                    "bid_request_id": request_id,
                    "decision": "accepted" if result.accepted else "rejected",
                    "code": result.code,
                    "sequence_no": result.sequence_no,
                }
            },
        )
        return result

    async def _decide(
        self, auction_id: UUID, bidder_id: UUID, amount: int, request_id: str
    ) -> BidResult:
        if not _valid(auction_id, bidder_id, amount, request_id):
            return BidResult(accepted=False, code="VALIDATION_ERROR")
        try:
            raw = await self._arbitrate(auction_id, bidder_id, amount, request_id)
            if raw.get("code") == "NOT_INITIALISED":
                raw = await self._rebuild_and_retry(auction_id, bidder_id, amount, request_id)
        except AuctionNotFound:
            # the auction vanished between the existence check and the rebuild
            return BidResult(accepted=False, code="NOT_FOUND")
        except (RedisError, LiveStateUnavailable, SQLAlchemyError, OSError) as exc:
            # fail closed: no bid is accepted whose ordering cannot be established (FR-42)
            log.warning(
                "bid path unavailable",
                extra={"fields": {"auction_id": str(auction_id), "error": repr(exc)}},
            )
            return BidResult(accepted=False, code="SERVICE_UNAVAILABLE")
        return _to_result(raw)

    async def _arbitrate(
        self, auction_id: UUID, bidder_id: UUID, amount: int, request_id: str
    ) -> dict[str, Any]:
        raw = await self._script(
            keys=[
                state_key(auction_id),
                outbox_key(auction_id),
                request_key(auction_id, bidder_id, request_id),
            ],
            args=[str(auction_id), str(bidder_id), str(amount), settings.bid_request_ttl_s * 1000],
        )
        return json.loads(raw)

    async def _rebuild_and_retry(
        self, auction_id: UUID, bidder_id: UUID, amount: int, request_id: str
    ) -> dict[str, Any]:
        """Live state is missing: rebuild once from Postgres if the auction is live (FR-43)."""
        async with self._sessions() as session:
            auction = await AuctionRepository(session).get(auction_id)
        if auction is None:
            return _rejected("NOT_FOUND")
        if auction.status != "live":
            return _rejected(
                "AUCTION_NOT_STARTED" if auction.status in NOT_STARTED else "AUCTION_CLOSED"
            )
        await live_state.init_live_state(auction_id, self._redis, self._sessions)
        raw = await self._arbitrate(auction_id, bidder_id, amount, request_id)
        if raw.get("code") == "NOT_INITIALISED":
            raise LiveStateUnavailable("another rebuild is in progress")
        return raw
