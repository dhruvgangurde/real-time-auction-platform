"""Live auction snapshot (FR-26, FR-30, FR-32, NFR-13, NFR-27).

Live and closing auctions are read from the Redis live state in preference to Postgres, so
price and end time reflect the most recent accepted bid; the state is rebuilt from Postgres
when absent (FR-43). Every other status has no live state (D2) and is served from Postgres,
writing nothing to Redis.

State, outbox and presence are read in one MULTI, then the ledger; bids above the state's
sequence are left out, so recent_bids never runs ahead of current_price. A bid accepted after
the read reaches the client as a live event with a higher sequence_no (client rule: buffer room
events received before `subscribed`, apply only those above snapshot.sequence_no).

If Redis is unreachable the snapshot is served from Postgres with `degraded: true`, so viewing
keeps working read-only (NFR-13).

Anonymous /live bodies are cached per process for 1 s with single flight (SnapshotCache, brief
12.2), and a missing live state is rebuilt by one flight per auction per process, shared by every
caller (anonymous, authenticated, `subscribed`). Waiters await the shared task through
asyncio.shield, so a caller that disconnects never cancels the work for the others.
"""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import AppError
from app.models import Auction
from app.repositories import (
    AuctionRepository,
    BidRepository,
    ProxyBidRepository,
    SettlementRepository,
)
from app.services.auth import CurrentUser
from app.services.fanout import presence_key
from app.services.live_events import alias, server_time_ms
from app.services.live_state import (
    LIVE_STATES,
    AuctionNotLive,
    LiveStateUnavailable,
    init_live_state,
    outbox_key,
    proxies_key,
    state_key,
    to_ms,
)

RECENT_BIDS = 20

Outbox = list[tuple[str, dict[str, str]]]


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
    auction_id: UUID,
    sessions: async_sessionmaker[AsyncSession],
    outbox: Outbox | None = None,
    upto: int | None = None,
) -> list[dict[str, Any]]:
    """Ledger merged with not-yet-persisted outbox bids, newest first, at most `upto`."""
    async with sessions() as session:
        rows = await BidRepository(session).recent(auction_id, RECENT_BIDS)
    # placed_at_ms is the Redis TIME of acceptance: created_at once persisted (ledger.py),
    # accepted_at_ms while still in the outbox (DC-3)
    merged = {
        r.sequence_no: (str(r.bidder_id), r.amount, r.bid_type, to_ms(r.created_at)) for r in rows
    }
    for _, f in outbox or []:
        if f.get("kind", "bid") == "bid":
            merged[int(f["sequence_no"])] = (
                f["bidder_id"],
                int(f["amount"]),
                f["bid_type"],
                int(f["accepted_at_ms"]),
            )
    return [
        {
            "sequence_no": seq,
            "amount": amount,
            "bidder_alias": alias(auction_id, bidder),
            "bid_type": kind,
            "placed_at_ms": placed_at,
        }
        for seq, (bidder, amount, kind, placed_at) in sorted(merged.items(), reverse=True)
        if upto is None or seq <= upto
    ][:RECENT_BIDS]


async def read_live(
    auction_id: UUID, redis: Redis, viewer_id: UUID | None = None
) -> tuple[dict[str, str], Outbox, int, str | None]:
    """State, outbox, participant count and the viewer's own armed maximum ("max|arm_seq", or
    None) as of one instant (one MULTI)."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hgetall(state_key(auction_id))
        pipe.xrange(outbox_key(auction_id))
        pipe.zcount(presence_key(auction_id), server_time_ms(), "+inf")
        if viewer_id is not None:
            pipe.hget(proxies_key(auction_id), str(viewer_id))
        state, outbox, participants, *own = await pipe.execute()
    return state, outbox, participants, own[0] if own else None


async def live_snapshot(
    auction_id: UUID,
    viewer: CurrentUser | None,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """The room snapshot. With a viewer (authenticated) it also carries that viewer's own
    you_lead, your_max and your_result; without one it is the same for everyone. Per-viewer
    fields never go into a room broadcast or a shared cache."""
    auction = await load_visible_auction(auction_id, viewer, sessions)
    if auction.status not in LIVE_STATES:
        snap = await _postgres_snapshot(auction, sessions, redis, degraded=False)
        return snap | await _own_from_postgres(auction, viewer, sessions) if viewer else snap
    viewer_id = viewer.id if viewer else None
    try:
        state, outbox, participants, own_max = await read_live(auction_id, redis, viewer_id)
        if not state or state.get("status") == "rebuilding":
            try:
                await _rebuild(auction_id, redis, sessions)
            except (LiveStateUnavailable, AuctionNotLive) as exc:
                raise AppError("SERVICE_UNAVAILABLE", "live state is being rebuilt", 503) from exc
            state, outbox, participants, own_max = await read_live(auction_id, redis, viewer_id)
            if not state or state.get("status") == "rebuilding":
                raise AppError("SERVICE_UNAVAILABLE", "live state is being rebuilt", 503)
    except RedisError:
        snap = await _postgres_snapshot(auction, sessions, None, degraded=True)
        return snap | await _own_from_postgres(auction, viewer, sessions) if viewer else snap

    sequence = int(state["sequence"])
    highest = int(state["highest_amount"])
    increment = int(state["min_increment"])
    reserve = auction.reserve_price  # read here, never returned (BR-6)
    own: dict[str, Any] = {}
    if viewer is not None:  # from the same MULTI as the state, so consistent with it
        own = {
            # exact, from the user id in the state, never from alias equality (brief 13 item 15)
            "you_lead": bool(sequence) and state["highest_bidder"] == str(viewer.id),
            "your_max": int(own_max.split("|")[0]) if own_max else None,  # the owner's own only
            "your_result": None,  # only once the auction is closed
        }
    return own | {
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
        "recent_bids": await _recent_bids(auction_id, sessions, outbox, sequence),
        "degraded": False,
        "reserve_met": None if reserve is None else bool(sequence) and highest >= reserve,
        # FR-38 as this auction enforces it: copied into its live state at init
        "self_outbid_blocked": state.get("self_outbid") == "1",
        "is_demo": auction.is_demo,  # labelled "Demo listing" wherever it is shown
        "server_time_ms": server_time_ms(),
    }


Flight = asyncio.Task[Any]
_rebuilds: dict[tuple[int, UUID], Flight] = {}


async def _rebuild(
    auction_id: UUID, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """init_live_state, one flight per auction and Redis client in this process: concurrent
    callers share it. Across processes init_state.lua still lets one writer win."""
    key = (id(redis), auction_id)
    task = _rebuilds.get(key)
    if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.ensure_future(init_live_state(auction_id, redis, sessions))
        _rebuilds[key] = task

        def done(t: Flight) -> None:
            if not t.cancelled():
                t.exception()  # retrieved here too, in case every waiter was cancelled
            if _rebuilds.get(key) is t:
                del _rebuilds[key]

        task.add_done_callback(done)
    await asyncio.shield(task)


class SnapshotCache:
    """Anonymous /live bodies per auction for `ttl_s` after they are built, with single flight:
    concurrent misses share one build (brief 12.2 item 1). Only bodies built without a viewer go
    in, so no per-viewer field is ever cached. A failed build is shared by its waiters but not
    kept. One per app instance (app.state), so reads per auction are bounded by instances."""

    def __init__(self, ttl_s: float = 1.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_s, self._clock = ttl_s, clock
        self._flights: dict[UUID, Flight] = {}
        self._expires: dict[UUID, float] = {}  # set when a flight succeeds

    async def get(self, auction_id: UUID, build: Callable[[], Awaitable[dict[str, Any]]]) -> Any:
        task = self._flights.get(auction_id)
        fresh = task is not None and (
            not task.done() or self._clock() < self._expires.get(auction_id, 0.0)
        )
        if not fresh:
            self._prune()
            task = asyncio.ensure_future(build())
            self._flights[auction_id] = task
            self._expires.pop(auction_id, None)
            task.add_done_callback(lambda t: self._settled(auction_id, t))
        assert task is not None
        return await asyncio.shield(task)  # a cancelled waiter leaves the flight running

    def _settled(self, auction_id: UUID, task: Flight) -> None:
        if self._flights.get(auction_id) is not task:
            return
        if task.cancelled() or task.exception() is not None:
            del self._flights[auction_id]  # never keep a failure
        else:
            self._expires[auction_id] = self._clock() + self._ttl_s

    def clear(self) -> None:
        """Forget every cached body (tests only). A flight in progress keeps running for its
        waiters; the next request starts a new one."""
        self._flights.clear()
        self._expires.clear()

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, until in self._expires.items() if until <= now]:
            del self._expires[key]
            self._flights.pop(key, None)


async def _postgres_snapshot(
    auction: Auction,
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis | None,
    *,
    degraded: bool,
) -> dict[str, Any]:
    """From Postgres alone: every status without live state (D2), and the read-only fallback
    while Redis is unreachable (NFR-13, where the end time may lag, see the FR-58 note)."""
    from app.services.closing import reserve_met  # closing imports this module

    recent = await _recent_bids(auction.id, sessions)
    top = recent[0] if recent else None
    sequence = top["sequence_no"] if top else 0
    participants: int | None = None
    if redis is not None:
        with contextlib.suppress(RedisError):  # presence is display only
            participants = await redis.zcount(presence_key(auction.id), server_time_ms(), "+inf")
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
        "participants": participants,
        "recent_bids": recent,
        "degraded": degraded,
        "reserve_met": reserve_met(auction),  # the same value auction_closed carries
        "self_outbid_blocked": settings.self_outbid_policy_enabled,  # FR-38, copied at go-live
        "is_demo": auction.is_demo,
        "server_time_ms": server_time_ms(),
    }


async def _own_from_postgres(
    auction: Auction, viewer: CurrentUser, sessions: async_sessionmaker[AsyncSession]
) -> dict[str, Any]:
    """The viewer's own you_lead, your_max and your_result without live state. Maxima are
    deactivated at close and on cancel (FR-51), so your_max is null afterwards; your_result is
    "won" or "lost" once the auction sold, null otherwise (no sale, cancelled, never took part)."""
    async with sessions() as session:
        bids = BidRepository(session)
        last = await bids.last_bid(auction.id)
        active = await ProxyBidRepository(session).list_active(auction.id)
        result = None
        if auction.status == "closed_sold":
            settlement = await SettlementRepository(session).for_auction(auction.id)
            if settlement is not None and settlement.winner_id == viewer.id:
                result = "won"
            elif viewer.id in await bids.participant_ids(auction.id):
                result = "lost"
    return {
        "you_lead": last is not None and last.bidder_id == viewer.id,
        "your_max": next((m for bidder, m, _ in active if bidder == viewer.id), None),
        "your_result": result,
    }
