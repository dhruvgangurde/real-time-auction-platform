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
    LIVE_STATES,
    AuctionNotFound,
    AuctionNotLive,
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
    placed_at_ms: int | None = None  # Redis TIME of acceptance (DC-3); None on an old replay
    reserve_met: bool | None = None  # None without a reserve; never the reserve itself (BR-6)


@dataclass(frozen=True)
class BidResult:
    accepted: bool
    code: str | None = None  # rejection code from docs/architecture.md section 9
    sequence_no: int | None = None
    amount: int | None = None
    end_time_ms: int | None = None
    extended: bool = False
    extension_count: int | None = None
    placed_at_ms: int | None = None  # Redis TIME of acceptance (DC-3); None on an old replay
    reserve_met: bool | None = None  # None without a reserve; never the reserve itself (BR-6)
    current_highest: int | None = None  # BID_TOO_LOW only, for the submitter alone
    min_next: int | None = None  # BID_TOO_LOW only
    automatic_bids: tuple[AutomaticBid, ...] = ()  # proxy responses, in sequence order
    # internal only, for outbid notices; never serialised to a client (FR-51)
    previous_leader: UUID | None = None
    exhausted: tuple[UUID, ...] = ()
    demo: bool = False  # internal: a demo lot records no outbid notifications (B2 Q13)


@dataclass(frozen=True)
class ProxyResult:
    armed: bool
    code: str | None = None
    max_amount: int | None = None  # the owner's own maximum, nobody else's (FR-51)
    arm_seq: int | None = None
    active: bool = False  # False when the maximum was exhausted by its own resolution
    min_next: int | None = None  # BELOW_CURRENT_PRICE only
    automatic_bids: tuple[AutomaticBid, ...] = ()
    # internal only, for outbid notices; never serialised to a client (FR-51)
    previous_leader: UUID | None = None
    exhausted: tuple[UUID, ...] = ()
    demo: bool = False  # internal: a demo lot records no outbid notifications (B2 Q13)


def _rejected(code: str) -> dict[str, Any]:
    return {"decision": "rejected", "code": code}


def _met(reserve: int | None, amount: int) -> bool | None:
    return None if reserve is None else amount >= reserve


def _autos(raw: dict[str, Any], reserve: int | None = None) -> tuple[AutomaticBid, ...]:
    return tuple(
        AutomaticBid(
            bidder_id=UUID(a["bidder_id"]),
            amount=int(a["amount"]),
            sequence_no=int(a["sequence_no"]),
            extended=bool(a["extended"]),
            end_time_ms=int(a["end_time_ms"]),
            extension_count=int(a["extension_count"]),
            placed_at_ms=_opt(a, "accepted_at_ms"),
            reserve_met=_met(reserve, int(a["amount"])),
        )
        for a in raw.get("automatic_bids", ())
    )


def _internal(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("_internal") or {}
    previous = data.get("previous_leader")
    return {
        "previous_leader": UUID(previous) if previous else None,
        "exhausted": tuple(UUID(b) for b in data.get("exhausted", ())),
    }


def _opt(raw: dict[str, Any], key: str) -> int | None:
    return int(raw[key]) if key in raw else None


def _to_bid_result(
    raw: dict[str, Any], reserve: int | None = None, demo: bool = False
) -> BidResult:
    if raw["decision"] == "accepted":
        return BidResult(
            demo=demo,
            accepted=True,
            sequence_no=int(raw["sequence_no"]),
            amount=int(raw["amount"]),
            end_time_ms=int(raw["end_time_ms"]),
            extended=bool(raw["extended"]),
            extension_count=int(raw["extension_count"]),
            # results stored by the previous script (up to bid_request_ttl_s) lack it
            placed_at_ms=_opt(raw, "accepted_at_ms"),
            reserve_met=_met(reserve, int(raw["amount"])),
            automatic_bids=_autos(raw, reserve),
            **_internal(raw),
        )
    return BidResult(
        accepted=False,
        code=raw["code"],
        current_highest=_opt(raw, "current_highest"),
        min_next=_opt(raw, "min_next"),
    )


def _to_proxy_result(
    raw: dict[str, Any], reserve: int | None = None, demo: bool = False
) -> ProxyResult:
    if raw["decision"] == "armed":
        return ProxyResult(
            demo=demo,
            armed=True,
            max_amount=int(raw["max_amount"]),
            arm_seq=int(raw["arm_seq"]),
            active=bool(raw["active"]),
            automatic_bids=_autos(raw, reserve),
            **_internal(raw),
        )
    return ProxyResult(armed=False, code=raw["code"], min_next=_opt(raw, "min_next"))


def log_bid(
    auction_id: UUID, bidder_id: UUID, amount: int, request_id: str, result: BidResult
) -> None:
    """One line per bid submission, whoever decided it: arbitration here, or a check before it
    in the WebSocket gate or the REST limiter (FR-44, NFR-32). The correlation id is added by
    the formatter; automatic bids are traceable by their sequence numbers."""
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
                "automatic_sequence_nos": [a.sequence_no for a in result.automatic_bids],
            }
        },
    )


def log_proxy(auction_id: UUID, bidder_id: UUID, request_id: str, result: ProxyResult) -> None:
    """One line per maximum submission. The maximum itself is never logged (FR-51)."""
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
                "automatic_sequence_nos": [a.sequence_no for a in result.automatic_bids],
            }
        },
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


OutbidRecorder = Callable[[UUID, list[UUID], int, int], None]


class BidService:
    def __init__(
        self,
        redis: Redis,
        sessions: async_sessionmaker[AsyncSession],
        schedule_persist: Callable[[UUID], None],
        record_outbid: OutbidRecorder | None = None,
    ) -> None:
        self._redis = redis
        self._sessions = sessions
        self._schedule_persist = schedule_persist
        # called after the acknowledgement with the displaced bidders (FR-70); never on the
        # arbitration path
        self.record_outbid = record_outbid
        self._place_bid = redis.register_script(lua.source("place_bid"))
        self._set_proxy = redis.register_script(lua.source("set_proxy"))
        # auction -> seller and auction -> reserve, for live and closing auctions only: neither
        # changes once an auction is live (the reserve is editable only while draft or scheduled)
        self._sellers: dict[UUID, UUID] = {}
        self._reserves: dict[UUID, int | None] = {}
        self._demo: dict[UUID, bool] = {}  # a lot's demo flag never changes

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

        raw, reserve, demo = await self._decide(
            auction_id, bidder_id, amount, request_id, arbitrate
        )
        result = _to_bid_result(raw, reserve, demo)
        self._after(auction_id, result.accepted or bool(result.automatic_bids))
        log_bid(auction_id, bidder_id, amount, request_id, result)
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

        raw, reserve, demo = await self._decide(
            auction_id, bidder_id, max_amount, request_id, arbitrate
        )
        result = _to_proxy_result(raw, reserve, demo)
        self._after(auction_id, result.armed)
        log_proxy(auction_id, bidder_id, request_id, result)
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
    ) -> tuple[dict[str, Any], int | None, bool]:
        """(script result, reserve, demo). The reserve only feeds reserve_met in room events;
        demo only spares the outbid recorder (B2 Q13)."""
        if not _valid(auction_id, bidder_id, amount, request_id):
            return _rejected("VALIDATION_ERROR"), None, False
        reserve, demo = None, False
        try:
            facts = await self._facts_of(auction_id)
            if facts is not None:
                seller, reserve, demo = facts
                if seller == bidder_id:  # no shill bidding, before arbitration
                    return _rejected("SELLER_CANNOT_BID"), None, False
            raw = await arbitrate()
            if raw.get("code") == "NOT_INITIALISED":
                raw = await self._rebuild_and_retry(auction_id, arbitrate)
        except AuctionNotFound:
            # the auction vanished between the existence check and the rebuild
            return _rejected("NOT_FOUND"), None, False
        except AuctionNotLive:
            # the auction left live status between the status check and the rebuild
            return _rejected("AUCTION_CLOSED"), None, False
        except (RedisError, LiveStateUnavailable, SQLAlchemyError, OSError) as exc:
            # fail closed: nothing is accepted whose ordering cannot be established (FR-42)
            log.warning(
                "bid path unavailable",
                extra={"fields": {"auction_id": str(auction_id), "error": repr(exc)}},
            )
            return _rejected("SERVICE_UNAVAILABLE"), None, False
        return raw, reserve, demo

    async def _facts_of(self, auction_id: UUID) -> tuple[UUID, int | None, bool] | None:
        """(seller, reserve, demo), cached per process once the auction is live or closing: only the
        first bid on a live auction reads Postgres, so the bid path keeps working on Redis alone
        afterwards. A draft or scheduled row is not cached, since its reserve may still change.
        If the read fails the bid fails closed (SERVICE_UNAVAILABLE). None for an unknown
        auction, which the arbitration path then reports."""
        if auction_id in self._sellers:
            return self._sellers[auction_id], self._reserves[auction_id], self._demo[auction_id]
        async with self._sessions() as session:
            auction = await AuctionRepository(session).get(auction_id)
        if auction is None:
            return None
        if auction.status in LIVE_STATES:
            if len(self._sellers) >= 10_000:  # shortcut: crude bound; an LRU if it ever matters
                self._sellers.clear()
                self._reserves.clear()
                self._demo.clear()
            self._reserves[auction_id] = auction.reserve_price
            self._demo[auction_id] = auction.is_demo
            self._sellers[auction_id] = auction.seller_id
        return auction.seller_id, auction.reserve_price, auction.is_demo

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
