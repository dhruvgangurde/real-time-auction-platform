"""Bid submission and proxy maxima: validation, arbitration, acknowledgement
(UC-01, UC-02, FR-35 to FR-51).

Every decision is taken by a Lua script at the auction's serialisation point (DC-2):
place_bid.lua for manual bids and set_proxy.lua for maxima; both resolve armed maxima in the
same step (FR-47). This service validates input, calls the script, maps the outcome, schedules
persistence and returns. It never reads then writes the highest bid itself, and never accepts
anything when Redis cannot be reached (FR-42, NFR-9).

Client contract: SERVICE_UNAVAILABLE caused by a timeout means the outcome is unknown, because
the script may have run. Clients must retry with the same request_id to learn the real result.

Privacy (FR-51, BR-7): a maximum is returned only to its owner, and never logged.

Auth, account status, room subscription and rate limits (FR-34, FR-44) are checked by the
Phase 5/6 callers before this service is invoked.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
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
    proxies_key,
    proxy_request_key,
    request_key,
    state_key,
)

log = logging.getLogger("bidding")

MAX_AMOUNT = 2**53 - 1  # Lua numbers are doubles; larger integers lose precision
REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NOT_STARTED = {"draft", "scheduled"}


@dataclass(frozen=True)
class AutomaticBid:
    """A bid placed on a proxy holder's behalf during resolution (FR-48 to FR-50)."""

    bidder_id: UUID
    amount: int
    sequence_no: int
    extended: bool
    end_time_ms: int
    extension_count: int


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
    automatic_bids: tuple[AutomaticBid, ...] = ()  # proxy responses, in sequence order


@dataclass(frozen=True)
class ProxyResult:
    armed: bool
    code: str | None = None
    max_amount: int | None = None  # the owner's own maximum, nobody else's (FR-51)
    arm_seq: int | None = None
    active: bool = False  # False when the maximum was exhausted by its own resolution
    min_next: int | None = None  # BELOW_CURRENT_PRICE only
    automatic_bids: tuple[AutomaticBid, ...] = ()


def _rejected(code: str) -> dict[str, Any]:
    return {"decision": "rejected", "code": code}


def _autos(raw: dict[str, Any]) -> tuple[AutomaticBid, ...]:
    return tuple(
        AutomaticBid(
            bidder_id=UUID(a["bidder_id"]),
            amount=int(a["amount"]),
            sequence_no=int(a["sequence_no"]),
            extended=bool(a["extended"]),
            end_time_ms=int(a["end_time_ms"]),
            extension_count=int(a["extension_count"]),
        )
        for a in raw.get("automatic_bids", ())
    )


def _opt(raw: dict[str, Any], key: str) -> int | None:
    return int(raw[key]) if key in raw else None


def _to_bid_result(raw: dict[str, Any]) -> BidResult:
    if raw["decision"] == "accepted":
        return BidResult(
            accepted=True,
            sequence_no=int(raw["sequence_no"]),
            amount=int(raw["amount"]),
            end_time_ms=int(raw["end_time_ms"]),
            extended=bool(raw["extended"]),
            extension_count=int(raw["extension_count"]),
            automatic_bids=_autos(raw),
        )
    return BidResult(
        accepted=False,
        code=raw["code"],
        current_highest=_opt(raw, "current_highest"),
        min_next=_opt(raw, "min_next"),
    )


def _to_proxy_result(raw: dict[str, Any]) -> ProxyResult:
    if raw["decision"] == "armed":
        return ProxyResult(
            armed=True,
            max_amount=int(raw["max_amount"]),
            arm_seq=int(raw["arm_seq"]),
            active=bool(raw["active"]),
            automatic_bids=_autos(raw),
        )
    return ProxyResult(armed=False, code=raw["code"], min_next=_opt(raw, "min_next"))


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
        self._place_bid = redis.register_script(lua.source("place_bid"))
        self._set_proxy = redis.register_script(lua.source("set_proxy"))

    async def place_bid(
        self, auction_id: UUID, bidder_id: UUID, amount: int, request_id: str
    ) -> BidResult:
        """Manual bid (UC-01). Returns as soon as the script has decided (FR-40)."""

        async def arbitrate() -> dict[str, Any]:
            return await self._run(
                self._place_bid,
                request_key(auction_id, bidder_id, request_id),
                auction_id,
                bidder_id,
                amount,
            )

        raw = await self._decide(auction_id, bidder_id, amount, request_id, arbitrate)
        result = _to_bid_result(raw)
        self._after(auction_id, result.accepted or bool(result.automatic_bids))
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
                    "automatic_bids": len(result.automatic_bids),
                }
            },
        )
        return result

    async def set_proxy(
        self, auction_id: UUID, bidder_id: UUID, max_amount: int, request_id: str
    ) -> ProxyResult:
        """Set or raise a proxy maximum (UC-02). The amount is never logged (FR-51)."""

        async def arbitrate() -> dict[str, Any]:
            return await self._run(
                self._set_proxy,
                proxy_request_key(auction_id, bidder_id, request_id),
                auction_id,
                bidder_id,
                max_amount,
            )

        raw = await self._decide(auction_id, bidder_id, max_amount, request_id, arbitrate)
        result = _to_proxy_result(raw)
        self._after(auction_id, result.armed)
        log.info(
            "proxy",
            extra={
                "fields": {
                    "auction_id": str(auction_id),
                    "bidder_id": str(bidder_id),
                    "bid_request_id": request_id,
                    "decision": "armed" if result.armed else "rejected",
                    "code": result.code,
                    "active": result.active,
                    "automatic_bids": len(result.automatic_bids),
                }
            },
        )
        return result

    def _after(self, auction_id: UUID, wrote_outbox: bool) -> None:
        if not wrote_outbox:
            return
        # acknowledge first; the durable write happens in the worker (FR-40)
        try:
            self._schedule_persist(auction_id)
        except Exception:
            log.exception("persist enqueue failed; the sweeper will persist the outbox")

    async def _run(
        self, script: Any, req_key: str, auction_id: UUID, bidder_id: UUID, amount: int
    ) -> dict[str, Any]:
        raw = await script(
            keys=[state_key(auction_id), outbox_key(auction_id), req_key, proxies_key(auction_id)],
            args=[str(auction_id), str(bidder_id), str(amount), settings.bid_request_ttl_s * 1000],
        )
        return json.loads(raw)

    async def _decide(
        self,
        auction_id: UUID,
        bidder_id: UUID,
        amount: int,
        request_id: str,
        arbitrate: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        if not _valid(auction_id, bidder_id, amount, request_id):
            return _rejected("VALIDATION_ERROR")
        try:
            raw = await arbitrate()
            if raw.get("code") == "NOT_INITIALISED":
                raw = await self._rebuild_and_retry(auction_id, arbitrate)
        except AuctionNotFound:
            # the auction vanished between the existence check and the rebuild
            return _rejected("NOT_FOUND")
        except (RedisError, LiveStateUnavailable, SQLAlchemyError, OSError) as exc:
            # fail closed: nothing is accepted whose ordering cannot be established (FR-42)
            log.warning(
                "bid path unavailable",
                extra={"fields": {"auction_id": str(auction_id), "error": repr(exc)}},
            )
            return _rejected("SERVICE_UNAVAILABLE")
        return raw

    async def _rebuild_and_retry(
        self, auction_id: UUID, arbitrate: Callable[[], Awaitable[dict[str, Any]]]
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
        raw = await arbitrate()
        if raw.get("code") == "NOT_INITIALISED":
            raise LiveStateUnavailable("another rebuild is in progress")
        return raw
