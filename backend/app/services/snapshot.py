"""Live auction snapshot (FR-26, FR-30, FR-32, NFR-13, NFR-27).

Read from the Redis live state in preference to Postgres, so price and end time reflect the most
recent accepted bid; rebuilt from Postgres when absent (FR-43). The last bids merge the ledger
with outbox entries not yet persisted. If Redis is unreachable the snapshot is served from
Postgres alone with `degraded: true`, so viewing keeps working read-only (NFR-13).
"""

from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.models import Auction
from app.repositories import AuctionRepository, BidRepository
from app.services.auth import CurrentUser
from app.services.fanout import presence_key
from app.services.live_events import alias, server_time_ms
from app.services.live_state import (
    LiveStateUnavailable,
    init_live_state,
    outbox_key,
    state_key,
    to_ms,
)

RECENT_BIDS = 20


def visible(auction: Auction, viewer: CurrentUser | None) -> bool:
    """Drafts are visible to their seller and to admins only (FR-30)."""
    if auction.status != "draft":
        return True
    return viewer is not None and (viewer.id == auction.seller_id or "admin" in viewer.roles)


async def load_visible_auction(
    auction_id: UUID, viewer: CurrentUser | None, sessions: async_sessionmaker[AsyncSession]
) -> Auction:
    async with sessions() as session:
        auction = await AuctionRepository(session).get(auction_id)
    if auction is None or not visible(auction, viewer):
        raise AppError("NOT_FOUND", "auction not found", 404)
    return auction


async def _recent_bids(
    auction_id: UUID, redis: Redis | None, sessions: async_sessionmaker[AsyncSession]
) -> list[dict[str, Any]]:
    async with sessions() as session:
        rows = await BidRepository(session).recent(auction_id, RECENT_BIDS)
    merged = {r.sequence_no: (str(r.bidder_id), r.amount, r.bid_type) for r in rows}
    if redis is not None:
        for _, f in await redis.xrange(outbox_key(auction_id)):
            if f.get("kind", "bid") == "bid":
                merged[int(f["sequence_no"])] = (f["bidder_id"], int(f["amount"]), f["bid_type"])
    return [
        {
            "sequence_no": seq,
            "amount": amount,
            "bidder_alias": alias(auction_id, bidder),
            "bid_type": kind,
        }
        for seq, (bidder, amount, kind) in sorted(merged.items(), reverse=True)[:RECENT_BIDS]
    ]


async def live_snapshot(
    auction_id: UUID,
    viewer: CurrentUser | None,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    auction = await load_visible_auction(auction_id, viewer, sessions)
    try:
        state = await redis.hgetall(state_key(auction_id))
        if not state or state.get("status") == "rebuilding":
            try:
                await init_live_state(auction_id, redis, sessions)
            except LiveStateUnavailable as exc:
                raise AppError("SERVICE_UNAVAILABLE", "live state is being rebuilt", 503) from exc
            state = await redis.hgetall(state_key(auction_id))
            if not state or state.get("status") == "rebuilding":
                raise AppError("SERVICE_UNAVAILABLE", "live state is being rebuilt", 503)
        participants: int | None = await redis.zcount(
            presence_key(auction_id), server_time_ms(), "+inf"
        )
        recent = await _recent_bids(auction_id, redis, sessions)
        degraded = False
    except RedisError:
        return await _postgres_snapshot(auction, sessions)

    sequence = int(state["sequence"])
    highest = int(state["highest_amount"])
    increment = int(state["min_increment"])
    return {
        "auction_id": str(auction_id),
        "status": state["status"],
        "current_price": highest if sequence else None,
        "leader_alias": alias(auction_id, state["highest_bidder"]) if sequence else None,
        "sequence_no": sequence,
        "bid_count": sequence,
        "start_price": int(state["start_price"]),
        "min_increment": increment,
        "min_next": highest + increment if sequence else int(state["start_price"]),
        "end_time_ms": int(state["end_time_ms"]),
        "extension_count": int(state["extension_count"]),
        "participants": participants,
        "recent_bids": recent,
        "degraded": degraded,
        "server_time_ms": server_time_ms(),
    }


async def _postgres_snapshot(
    auction: Auction, sessions: async_sessionmaker[AsyncSession]
) -> dict[str, Any]:
    """Read-only fallback while Redis is unreachable (NFR-13). End time may lag (FR-58 note)."""
    recent = await _recent_bids(auction.id, None, sessions)
    top = recent[0] if recent else None
    sequence = top["sequence_no"] if top else 0
    return {
        "auction_id": str(auction.id),
        "status": auction.status,
        "current_price": top["amount"] if top else None,
        "leader_alias": top["bidder_alias"] if top else None,
        "sequence_no": sequence,
        "bid_count": sequence,
        "start_price": auction.starting_price,
        "min_increment": auction.min_increment,
        "min_next": (top["amount"] + auction.min_increment) if top else auction.starting_price,
        "end_time_ms": to_ms(auction.end_time),
        "extension_count": auction.extension_count,
        "participants": None,
        "recent_bids": recent,
        "degraded": True,
        "server_time_ms": server_time_ms(),
    }
