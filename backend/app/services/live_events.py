"""Real-time event construction and publication (FR-31, FR-40, FR-50, FR-51, NFR-27, CI-7).

Room events go to `auction:{id}:events`, personal events to `user:{id}:events`; every API
instance's hub fans them out to its local sockets. Room events carry pseudonyms, never user
ids, and never a proxy maximum. Every event carries `sequence_no` where it concerns a bid and
`server_time_ms`, so clients order events and derive countdowns from server time.

Publishing happens after the submitter's acknowledgement has been queued (FR-40). Pub/sub is
at-most-once: a lost event is recovered by the client from the next presence tick or /live.
"""

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.services.bidding import BidResult, OutbidRecorder, ProxyResult

log = logging.getLogger("live_events")

_DEV_ALIAS_SECRET = "dev-only-alias-secret-not-for-deployment-012345"
FINAL_EXTENSION = 10  # FR-54


def room_channel(auction_id: UUID) -> str:
    return f"auction:{{{auction_id}}}:events"


def user_channel(user_id: UUID) -> str:
    return f"user:{user_id}:events"


def server_time_ms() -> int:
    # display time for clients (UI-4); arbitration time comes from Redis TIME (DC-3)
    return time.time_ns() // 1_000_000


def event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, **fields, "server_time_ms": server_time_ms()}


def _alias_secret() -> bytes:
    secret = settings.alias_secret
    return (secret if len(secret.encode()) >= 32 else _DEV_ALIAS_SECRET).encode()


def alias(auction_id: UUID, bidder_id: UUID | str) -> str:
    """Per-auction pseudonym: Bidder- + 4 hex of HMAC-SHA256 (NFR-27). Not linkable across
    auctions without the secret."""
    digest = hmac.new(
        _alias_secret(), f"{auction_id}:{bidder_id}".encode(), hashlib.sha256
    ).hexdigest()
    return f"Bidder-{digest[:4]}"


def bid_events(
    auction_id: UUID, submitter: UUID, result: BidResult | ProxyResult
) -> tuple[list[dict[str, Any]], dict[UUID, dict[str, Any]]]:
    """(room events, {user_id: outbid_notice}) for an accepted bid or an armed maximum."""
    bids: list[tuple[UUID, int, int, str, int, bool, int, int | None, bool | None]] = []
    if isinstance(result, BidResult) and result.accepted:
        assert result.sequence_no and result.amount and result.end_time_ms is not None
        bids.append(
            (
                submitter,
                result.amount,
                result.sequence_no,
                "manual",
                result.end_time_ms,
                result.extended,
                result.extension_count or 0,
                result.placed_at_ms,
                result.reserve_met,
            )
        )
    for a in result.automatic_bids:
        bids.append(
            (
                a.bidder_id,
                a.amount,
                a.sequence_no,
                "automatic",
                a.end_time_ms,
                a.extended,
                a.extension_count,
                a.placed_at_ms,
                a.reserve_met,
            )
        )
    room: list[dict[str, Any]] = []
    for bidder, amount, seq, kind, end_ms, extended, ext_count, placed_at, met in bids:
        room.append(
            event(
                "bid_placed",
                auction_id=str(auction_id),
                sequence_no=seq,
                amount=amount,
                bidder_alias=alias(auction_id, bidder),
                bid_type=kind,
                end_time_ms=end_ms,
                extended=extended,
                extension_count=ext_count,
                placed_at_ms=placed_at,  # arbitration time (DC-3); automatic bids share it
                reserve_met=met,  # null without a reserve; never the reserve itself (BR-6)
            )
        )
        if extended:
            room.append(
                event(
                    "timer_extended",
                    auction_id=str(auction_id),
                    sequence_no=seq,
                    end_time_ms=end_ms,
                    extension_count=ext_count,
                    final=ext_count >= FINAL_EXTENSION,  # FR-54: no further extension
                )
            )
    notices: dict[UUID, dict[str, Any]] = {}
    if bids:
        leader, price, last_seq = bids[-1][0], bids[-1][1], bids[-1][2]
        # everyone displaced by this trigger: the leader before it, every maximum it exhausted,
        # and anyone whose bid in it was immediately exceeded (e.g. a manual bid answered by a
        # proxy). Minus the new leader; each recipient once (FR-51, UC-01 post-condition).
        candidates = set(result.exhausted) | {b[0] for b in bids}
        if result.previous_leader is not None:
            candidates.add(result.previous_leader)
        for user in candidates - {leader}:
            notices[user] = event(
                "outbid_notice",
                auction_id=str(auction_id),
                current_price=price,
                sequence_no=last_seq,
            )
    return room, notices


async def publish_bid_events(
    redis: Redis,
    auction_id: UUID,
    submitter: UUID,
    result: BidResult | ProxyResult,
    record_outbid: OutbidRecorder | None = None,
) -> None:
    room, notices = bid_events(auction_id, submitter, result)
    if notices and record_outbid is not None:
        # stored outbid notifications for the same recipients as the live notice (FR-70);
        # fire-and-forget: it can never delay or fail the bid, already acknowledged (FR-40)
        notice = next(iter(notices.values()))
        try:
            record_outbid(auction_id, list(notices), notice["sequence_no"], notice["current_price"])
        except Exception:
            log.exception(
                "outbid notification not queued", extra={"fields": {"auction_id": str(auction_id)}}
            )
    if not room and not notices:
        return
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for e in room:
                pipe.publish(room_channel(auction_id), json.dumps(e))
            for user, e in notices.items():
                pipe.publish(user_channel(user), json.dumps(e))
            await pipe.execute()
    except RedisError as exc:
        # the ledger and /live stay correct; clients recover through presence or /live
        log.warning(
            "event publish failed",
            extra={"fields": {"auction_id": str(auction_id), "error": repr(exc)}},
        )


async def publish_session_revoked(redis: Redis, user_id: UUID, sid: UUID | None) -> None:
    """Tell every instance to close this user's sockets (one session, or all when sid is None)."""
    payload = event("session_revoked", sid=str(sid) if sid else None)
    try:
        await redis.publish(user_channel(user_id), json.dumps(payload))
    except RedisError as exc:
        # sockets are still denied at their next bid by the denylist re-check (FR-6)
        log.warning(
            "session_revoked publish failed",
            extra={"fields": {"user_id": str(user_id), "error": repr(exc)}},
        )


def auction_closed_event(
    auction_id: UUID,
    *,
    outcome: str,
    final_price: int | None,
    reserve_met: bool | None,
    final_sequence_no: int,
) -> dict[str, Any]:
    """The room's last event (FR-62): outcome, final price and whether a reserve was met, never
    the reserve itself (BR-6). final_sequence_no equals the last bid's sequence, so clients
    apply it regardless of the sequence rule. Published only by Closer.broadcast_closed."""
    return event(
        "auction_closed",
        auction_id=str(auction_id),
        outcome=outcome,
        final_price=final_price,
        reserve_met=reserve_met,
        final_sequence_no=final_sequence_no,
    )


async def publish_notifications(redis: Redis, rows: Sequence[Any]) -> None:
    """Live in-app delivery of stored notifications on each recipient's channel (FR-71), after
    the commit. At-most-once: the notifications table is the record, read through the API."""
    if not rows:
        return
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for row in rows:
                payload = event(
                    "notification",
                    id=row.id,
                    notification_type=row.type,
                    auction_id=str(row.auction_id) if row.auction_id else None,
                    payload=row.payload,
                    created_at_ms=int(row.created_at.timestamp() * 1000),
                )
                pipe.publish(user_channel(row.user_id), json.dumps(payload))
            await pipe.execute()
    except RedisError as exc:
        log.warning(
            "notification publish failed", extra={"fields": {"rows": len(rows), "error": repr(exc)}}
        )
