"""Closing and settlement (FR-57 to FR-62, FR-64, FR-51, NFR-11, NFR-12, BR-5, BR-6, BR-10, UC-03).

Closer.close(auction_id) runs for an auction the scheduler or an administrator has moved to
closing. Safe to run any number of times, concurrently, and after a crash at any point:

1. Drain: persist the auction's outbox with the ledger persister, so Postgres holds every
   accepted bid, end_time and extension_count (FR-55, NFR-15). If it cannot: DEFERRED.
2. One transaction:
   a. pg_try_advisory_xact_lock on the auction's settlement key (FR-57); not acquired: LOCKED,
      exit cleanly. Released at commit or rollback.
   b. closed_sold / closed_no_sale or an existing settlement: ALREADY_CLOSED; anything but
      closing: NOT_CLOSING.
   c. Execution-time re-check (FR-58): Redis status closing and Redis TIME >= end_time_ms, or
      no Redis state and Postgres end_time passed; the outbox still empty and the ledger holding
      every sequence number Redis assigned. Otherwise NOT_ENDED or DEFERRED, nothing written.
   d. Winner: highest amount, ties by lowest sequence_no (FR-59). Sold when there is a winner
      and no reserve or the reserve is met (FR-60, BR-5).
   e. The settlement first (pending_payment with winner and price, or no_sale with neither,
      idempotency key settle:{auction_id}): with the lock bypassed, UNIQUE(auction_id) stops a
      second closer right here (OR-14). Then closing -> closed_sold / closed_no_sale, the audit
      rows of both (FR-64), every proxy deactivated (FR-51) and the notification rows (FR-62);
      all commit together (FR-78) or not at all.
3. After the commit: Redis follows (closing -> terminal through set_status.lua), auction_closed
   is broadcast with the outcome, final price and reserve_met, never the reserve (BR-6), and the
   live state is removed. Exactly one caller broadcasts: the one whose closing -> terminal flip
   returned OK, or, when there is no live state at all, the one that settled. A retry after a
   crash between the commit and these steps finds the auction ALREADY_CLOSED with Redis still
   closing and finishes them; a retry after everything succeeded does nothing.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import lua
from app.core.errors import AppError
from app.models import Auction, Settlement, User
from app.repositories import (
    AuctionRepository,
    AuditLogRepository,
    BidRepository,
    NotificationRepository,
    PaymentRepository,
    ProxyBidRepository,
    SettlementRepository,
    UserRepository,
)
from app.services import live_events
from app.services.auth import CurrentUser
from app.services.ledger import persist_outbox
from app.services.live_state import (
    auction_id_from_key,
    outbox_key,
    proxies_key,
    state_key,
    to_ms,
)
from app.services.payments import payment_view
from app.services.snapshot import visible

log = logging.getLogger("closing")

SETTLED = frozenset({"closed_sold", "closed_no_sale"})
TERMINAL = SETTLED | {"cancelled"}
BATCH = 200  # auctions per sweep step; the rest wait for the next run


class Outcome(StrEnum):
    SETTLED = "settled"
    ALREADY_CLOSED = "already_closed"  # settled before; unfinished after-commit steps re-run
    LOCKED = "locked"  # another closer holds the lock: exit cleanly (FR-57)
    DEFERRED = "deferred"  # no complete ledger yet: retry later
    NOT_ENDED = "not_ended"  # FR-58 re-check failed: nothing written
    NOT_CLOSING = "not_closing"  # unknown, live, scheduled, cancelled...


@dataclass(frozen=True)
class Decision:
    status: str  # closed_sold | closed_no_sale
    winner_id: UUID | None
    final_price: int | None
    reserve_met: bool | None  # None when the auction has no reserve


class _Conflict(Exception):
    """The closing -> terminal transition found the auction already moved: roll back."""


def reserve_met(auction: Auction) -> bool | None:
    """For a settled auction: a reserve is met exactly when it sold (BR-5, BR-6)."""
    return None if auction.reserve_price is None else auction.status == "closed_sold"


class Closer:
    def __init__(
        self, redis: Redis, sessions: async_sessionmaker[AsyncSession], *, use_lock: bool = True
    ) -> None:
        """use_lock=False is a test seam only: it shows UNIQUE(settlements.auction_id) alone
        prevents a second settlement (OR-14)."""
        self._redis, self._sessions, self._use_lock = redis, sessions, use_lock
        self._set_status = redis.register_script(lua.source("set_status"))
        self._cleanup = redis.register_script(lua.source("cleanup_state"))

    async def close(self, auction_id: UUID) -> Outcome:
        fields = {"auction_id": str(auction_id)}
        try:
            await persist_outbox(auction_id, self._redis, self._sessions)  # 1. drain
            outcome = await self._settle(auction_id)  # 2. decide and write
        except IntegrityError as exc:
            if "uq_settlements_" not in str(exc):
                raise
            # the lock was bypassed and the constraint stopped a second settlement (OR-14)
            log.info("settlement already exists; rolled back", extra={"fields": fields})
            outcome = Outcome.ALREADY_CLOSED
        except _Conflict:
            outcome = Outcome.NOT_CLOSING
        except (RedisError, SQLAlchemyError, OSError) as exc:
            log.warning("close deferred", extra={"fields": fields | {"error": repr(exc)}})
            return Outcome.DEFERRED
        log.info("close", extra={"fields": fields | {"outcome": outcome.value}})
        if outcome in (Outcome.SETTLED, Outcome.ALREADY_CLOSED):
            await self._finish(auction_id, settled=outcome is Outcome.SETTLED)  # 3.
        return outcome

    # ---------------------------------------------------------------- 2. the transaction

    async def _settle(self, auction_id: UUID) -> Outcome:
        async with self._sessions() as session, session.begin():
            auctions = AuctionRepository(session)
            if self._use_lock and not await auctions.try_settlement_lock(auction_id):
                return Outcome.LOCKED
            auction = await auctions.get(auction_id)
            settlements = SettlementRepository(session)
            if auction is None:
                return Outcome.NOT_CLOSING
            if auction.status in SETTLED or await settlements.for_auction(auction_id):
                return Outcome.ALREADY_CLOSED
            if auction.status != "closing":
                return Outcome.NOT_CLOSING
            if (refused := await self._recheck(session, auction)) is not None:
                return refused
            decision = await self._decide(session, auction)
            settlement = await settlements.create(  # first: the UNIQUE backstop fires here
                auction_id=auction_id,
                winner_id=decision.winner_id,
                final_price=decision.final_price,
                status="pending_payment" if decision.winner_id else "no_sale",
                idempotency_key=f"settle:{auction_id}",
            )
            if not await auctions.transition(auction_id, "closing", decision.status):
                raise _Conflict
            outcome = {
                "status": decision.status,
                "winner_id": str(decision.winner_id) if decision.winner_id else None,
                "final_price": decision.final_price,
            }
            audit = AuditLogRepository(session)
            await audit.add(
                entity_type="auction",
                entity_id=str(auction_id),
                action="status_changed",
                before={"status": "closing"},
                after=outcome,
            )
            await audit.add(  # FR-64: the settlement's own first status
                entity_type="settlement",
                entity_id=str(settlement.id),
                action="settlement_created",
                after={"auction_id": str(auction_id), **outcome, "status": settlement.status},
            )
            await ProxyBidRepository(session).deactivate_all(auction_id)  # FR-51
            created = await NotificationRepository(session).add_many(
                await self._notifications(session, auction, decision)
            )
        await live_events.publish_notifications(self._redis, created)  # FR-71, after commit
        log.info(
            "auction settled",
            extra={"fields": {"auction_id": str(auction_id), **outcome}},
        )
        return Outcome.SETTLED

    async def _recheck(self, session: AsyncSession, auction: Auction) -> Outcome | None:
        """FR-58 at execution time, plus a complete ledger (NFR-15)."""
        status, end_ms, sequence = await self._redis.hmget(
            state_key(auction.id), "status", "end_time_ms", "sequence"
        )
        if await self._redis.xlen(outbox_key(auction.id)):
            return Outcome.DEFERRED  # accepted bids still on their way to the ledger
        if status is None:  # no live state: Postgres alone, complete after the drain
            ended = auction.end_time <= await AuctionRepository(session).db_now()
            return None if ended else Outcome.NOT_ENDED
        if status == "rebuilding":
            return Outcome.DEFERRED
        if status != "closing":
            return Outcome.NOT_ENDED
        if int(sequence) > await BidRepository(session).max_sequence(auction.id):
            return Outcome.DEFERRED  # Redis accepted a bid the ledger does not have
        seconds, micros = await self._redis.time()
        return None if seconds * 1000 + micros // 1000 >= int(end_ms) else Outcome.NOT_ENDED

    @staticmethod
    async def _decide(session: AsyncSession, auction: Auction) -> Decision:
        top = await BidRepository(session).winning_bid(auction.id)
        reserve = auction.reserve_price
        met = None if reserve is None else (top is not None and top.amount >= reserve)
        if top is None or met is False:
            return Decision("closed_no_sale", None, None, met)
        return Decision("closed_sold", top.bidder_id, top.amount, met)

    @staticmethod
    async def _notifications(
        session: AsyncSession, auction: Auction, decision: Decision
    ) -> list[dict[str, Any]]:
        """FR-62: one set per auction; dedupe_key makes a repeat a no-op (DC-5)."""
        payload = {
            "auction_id": str(auction.id),
            "title": auction.title,
            "outcome": decision.status,
            "final_price": decision.final_price,
            "reserve_met": decision.reserve_met,  # never the reserve itself (BR-6)
        }
        recipients: list[tuple[UUID, str]] = []
        if decision.winner_id:  # the outcome and that payment is due (FR-62, FR-70, 4.10.2 S3)
            recipients += [
                (decision.winner_id, "auction_won"),
                (decision.winner_id, "payment_due"),
                (auction.seller_id, "auction_sold"),
            ]
        else:
            recipients.append((auction.seller_id, "auction_no_sale"))
        losers = await BidRepository(session).participant_ids(auction.id) - {decision.winner_id}
        recipients += [(user, "auction_lost") for user in sorted(losers)]
        return [
            {
                "user_id": user,
                "auction_id": auction.id,
                "type": kind,
                "payload": payload,
                "dedupe_key": f"close:{auction.id}:{kind}:{user}",
            }
            for user, kind in recipients
        ]

    # ---------------------------------------------------------------- 3. after the commit

    async def _finish(self, auction_id: UUID, *, settled: bool) -> None:
        """Redis follows Postgres, one auction_closed broadcast, live state removed. Failures
        are left to the next retry or the repair sweep."""
        async with self._sessions() as session:
            auction = await AuctionRepository(session).get(auction_id)
            settlement = await SettlementRepository(session).for_auction(auction_id)
        if auction is None or settlement is None or auction.status not in SETTLED:
            return
        keys = [state_key(auction_id), outbox_key(auction_id), proxies_key(auction_id)]
        try:
            flipped = await self._set_status(keys=keys[:1], args=["closing", auction.status, "0"])
            if flipped == "OK" or (flipped == "MISSING" and settled):
                await live_events.publish_auction_closed(
                    self._redis,
                    auction_id,
                    outcome=auction.status,
                    final_price=settlement.final_price,
                    reserve_met=reserve_met(auction),
                    final_sequence_no=auction.bid_count,
                )
            await self._cleanup(keys=keys)  # keeps anything still in use (cleanup_state.lua)
        except RedisError as exc:
            log.warning(
                "close committed; Redis steps left to the repair sweep",
                extra={"fields": {"auction_id": str(auction_id), "error": repr(exc)}},
            )


# -------------------------------------------------------------------- repair sweep (NFR-12)


async def _settled_but_closing(
    redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> list[UUID]:
    """Settled in Postgres while Redis still says closing: a crash after the commit."""
    # shortcut: SCAN walks the keyspace; fine at this scale, keep an index set if it grows
    keys = [k async for k in redis.scan_iter(match="auction:*:state", _type="hash", count=500)]
    if not keys:
        return []
    async with redis.pipeline(transaction=False) as pipe:
        for key in keys:
            pipe.hget(key, "status")
        statuses = await pipe.execute()
    closing = [
        auction_id_from_key(k) for k, s in zip(keys, statuses, strict=True) if s == "closing"
    ]
    if not closing:
        return []
    async with sessions() as session:
        postgres = await AuctionRepository(session).statuses(closing)
    return [a for a in closing if postgres.get(a) in SETTLED]


async def sweep(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    enqueue: Callable[[UUID], None],
    grace_s: int,
) -> dict[str, int]:
    """Closing auctions without a settlement, and settled auctions whose Redis state still says
    closing, are (re-)enqueued. Any auction past end_time + grace that is not terminal raises
    UNRESOLVED_CLOSE (NFR-12)."""
    async with sessions() as session:
        auctions = AuctionRepository(session)
        pending = await auctions.closing_without_settlement(BATCH)
        unresolved = await auctions.unresolved(grace_s, BATCH)
    for auction_id, status, end_time in unresolved:
        log.critical(
            "auction past its end time without an outcome",
            extra={
                "fields": {
                    "alert": "UNRESOLVED_CLOSE",
                    "auction_id": str(auction_id),
                    "status": status,
                    "end_time": end_time.isoformat(),
                }
            },
        )
    try:
        unfinished = await _settled_but_closing(redis, sessions)
    except RedisError as exc:
        log.warning("sweep could not read live state", extra={"fields": {"error": repr(exc)}})
        unfinished = []
    for auction_id in [*pending, *unfinished]:
        enqueue(auction_id)
    return {"enqueued": len(pending) + len(unfinished), "unresolved": len(unresolved)}


# -------------------------------------------------------------------- settlement read (FR-64)


def settlement_dict(settlement: Settlement, winner: User | None) -> dict[str, Any]:
    return {
        "id": str(settlement.id),
        "auction_id": str(settlement.auction_id),
        "status": settlement.status,
        "final_price": settlement.final_price,
        # BR-10: full identity of the winner, for the winner, the seller and administrators
        "winner": {"id": str(winner.id), "full_name": winner.full_name} if winner else None,
        "created_at_ms": to_ms(settlement.created_at),
        "paid_at_ms": to_ms(settlement.paid_at) if settlement.paid_at else None,
    }


async def settlement_view(
    sessions: async_sessionmaker[AsyncSession], viewer: CurrentUser, auction_id: UUID
) -> dict[str, Any]:
    """Visible only to the winner, the seller and administrators (FR-64, BR-10)."""
    async with sessions() as session:
        auction = await AuctionRepository(session).get(auction_id)
        if auction is None or not visible(auction, viewer):
            raise AppError("NOT_FOUND", "auction not found", 404)
        settlement = await SettlementRepository(session).for_auction(auction_id)
        winner = settlement is not None and settlement.winner_id == viewer.id
        if not (winner or viewer.id == auction.seller_id or "admin" in viewer.roles):
            raise AppError("FORBIDDEN", "only the winner and the seller see the settlement", 403)
        if settlement is None:
            raise AppError("NOT_FOUND", "the auction has no settlement yet", 404)
        user = None
        if settlement.winner_id is not None:
            user = await UserRepository(session).get(settlement.winner_id)
        payment = await PaymentRepository(session).for_settlement(settlement.id)
    return settlement_dict(settlement, user) | {"payment": payment_view(payment)}
