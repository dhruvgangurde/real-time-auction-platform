"""Live auction state in Redis and its rebuild from Postgres (FR-43, DC-1).

Redis holds only rebuildable state; Postgres is the source of truth. Only live and closing
auctions have live state (D2); every other status is served from Postgres. Rebuild runs only
when a live auction's Redis state is missing (for example Redis restarted without its AOF). It
is never used to refresh existing live state, because the outbox may still hold accepted bids
and proxy changes that Postgres has not seen. init_state.lua refuses to rebuild while the outbox
is non-empty.
"""

import logging
from datetime import UTC, datetime, timedelta
from itertools import chain
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import lua
from app.core.config import settings
from app.repositories import AuctionRepository, BidRepository, ProxyBidRepository

log = logging.getLogger("live_state")

REBUILD_ATTEMPTS = 3
REBUILD_LEASE_MS = 5_000
LIVE_STATES = frozenset({"live", "closing"})  # the only statuses with Redis state (D2)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# Keys share the {auction_id} hash tag so every key a script touches maps to one slot.
def state_key(auction_id: UUID) -> str:
    return f"auction:{{{auction_id}}}:state"


def outbox_key(auction_id: UUID) -> str:
    return f"auction:{{{auction_id}}}:bids"


def dead_letter_key(auction_id: UUID) -> str:
    return f"auction:{{{auction_id}}}:bids:dead"


def proxies_key(auction_id: UUID) -> str:
    # bidder -> "max|arm_seq"; never read by anything that answers another bidder (FR-51)
    return f"auction:{{{auction_id}}}:proxies"


def request_key(auction_id: UUID, bidder_id: UUID, request_id: str) -> str:
    # scoped by bidder so two bidders reusing a request_id never see each other's result
    return f"auction:{{{auction_id}}}:req:{bidder_id}:{request_id}"


def proxy_request_key(auction_id: UUID, bidder_id: UUID, request_id: str) -> str:
    # separate namespace: a bid and a proxy set with the same request_id never collide
    return f"auction:{{{auction_id}}}:preq:{bidder_id}:{request_id}"


def auction_id_from_key(key: str) -> UUID:
    return UUID(key.split("{", 1)[1].split("}", 1)[0])


def to_ms(dt: datetime) -> int:
    return (dt - _EPOCH) // timedelta(milliseconds=1)  # exact integer arithmetic


def from_ms(ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=ms)


class LiveStateUnavailable(Exception):
    """Live state cannot be established safely right now; the bid path must fail closed."""


class AuctionNotFound(LookupError):
    """The auction does not exist in Postgres."""


class AuctionNotLive(Exception):
    """The auction is neither live nor closing, so it gets no Redis state (D2)."""


async def _snapshot(
    sessions: async_sessionmaker[AsyncSession], auction_id: UUID
) -> tuple[dict[str, str], str, int, dict[str, str]]:
    """Read the auction row, its last ledger bid and its armed maxima.

    Returns (state hash fields, status, sequence, proxies hash fields).
    """
    async with sessions() as session:
        auction = await AuctionRepository(session).get(auction_id)
        if auction is None:
            raise AuctionNotFound(f"auction {auction_id} not found")
        last = await BidRepository(session).last_bid(auction_id)
        proxy_repo = ProxyBidRepository(session)
        active = await proxy_repo.list_active(auction_id)
        arm_seq = await proxy_repo.max_arm_seq(auction_id)
    sequence = last.sequence_no if last else 0
    fields = {
        "start_time_ms": str(to_ms(auction.start_time)),
        "end_time_ms": str(to_ms(auction.end_time)),
        "highest_amount": str(last.amount if last else 0),
        "highest_bidder": str(last.bidder_id) if last else "",
        "sequence": str(sequence),
        "start_price": str(auction.starting_price),
        "min_increment": str(auction.min_increment),
        "extension_count": str(auction.extension_count),
        "threshold_ms": str(auction.antisnipe_threshold_s * 1000),
        "extension_ms": str(auction.antisnipe_extension_s * 1000),
        "self_outbid": "1" if settings.self_outbid_policy_enabled else "0",
        # counter restarts above every arm_seq ever persisted, so ties stay ordered (FR-49)
        "arm_seq": str(arm_seq),
    }
    proxies = {str(bidder): f"{max_amount}|{seq}" for bidder, max_amount, seq in active}
    return fields, auction.status, sequence, proxies


async def init_live_state(
    auction_id: UUID, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> bool:
    """Build an auction's live state from Postgres, only if it is absent (FR-43).

    Returns True if this call initialised it, False if live state already existed (or another
    instance won the race). Raises AuctionNotLive for any status but live or closing (D2), and
    LiveStateUnavailable when it cannot be done safely now.
    """
    init = redis.register_script(lua.source("init_state"))
    finalize = redis.register_script(lua.source("finalize_state"))
    keys = [state_key(auction_id), outbox_key(auction_id), proxies_key(auction_id)]
    finalize_keys = [state_key(auction_id), proxies_key(auction_id)]

    for attempt in range(1, REBUILD_ATTEMPTS + 1):
        fields, status, sequence, proxies = await _snapshot(sessions, auction_id)
        if status not in LIVE_STATES:
            raise AuctionNotLive(f"auction {auction_id} is {status}")
        token = uuid4().hex
        flat_state = list(chain.from_iterable(fields.items()))
        flat_proxies = list(chain.from_iterable(proxies.items()))
        outcome = await init(
            keys=keys,
            args=[REBUILD_LEASE_MS, token, len(flat_state), *flat_state, *flat_proxies],
        )
        if outcome == "EXISTS":
            return False
        if outcome == "OUTBOX_PENDING":
            raise LiveStateUnavailable("outbox not yet persisted")

        # A persister may have committed bids between the snapshot and the init. The hash is
        # still 'rebuilding', so no bid has been arbitrated against it yet.
        async with sessions() as session:
            ledger_max = await BidRepository(session).max_sequence(auction_id)
        if ledger_max <= sequence:
            committed = await finalize(keys=finalize_keys, args=[token, "commit", status])
            if committed:
                log.info(
                    "live state rebuilt",
                    extra={
                        "fields": {
                            "auction_id": str(auction_id),
                            "sequence": sequence,
                            "proxies": len(proxies),
                        }
                    },
                )
            return bool(committed)  # 0: our lease expired and another rebuild took over

        await finalize(keys=finalize_keys, args=[token, "abort", ""])
        log.warning(
            "ledger moved during rebuild, retrying",
            extra={
                "fields": {
                    "auction_id": str(auction_id),
                    "attempt": attempt,
                    "snapshot_sequence": sequence,
                    "ledger_max": ledger_max,
                }
            },
        )
    raise LiveStateUnavailable("ledger kept moving during rebuild")


async def remove_stale_state(redis: Redis, sessions: async_sessionmaker[AsyncSession]) -> int:
    """Drop Redis state left for auctions that Postgres says are neither live nor closing (D2).

    cleanup_state.lua re-checks inside Redis and keeps anything that may be in use (a hash whose
    own status is live, closing or rebuilding, or a non-empty outbox), so racing an activation
    or a rebuild never deletes live state. Returns the number of auctions cleaned.
    """
    # shortcut: SCAN walks the keyspace; fine at this scale, keep an index set if it grows
    ids = [
        auction_id_from_key(key)
        async for key in redis.scan_iter(match="auction:*:state", _type="hash", count=500)
    ]
    if not ids:
        return 0
    async with sessions() as session:
        statuses = await AuctionRepository(session).statuses(ids)
    cleanup = redis.register_script(lua.source("cleanup_state"))
    removed = 0
    for auction_id in ids:
        if statuses.get(auction_id) in LIVE_STATES:
            continue
        keys = [state_key(auction_id), outbox_key(auction_id), proxies_key(auction_id)]
        removed += int(await cleanup(keys=keys))
    if removed:
        log.info("stale live state removed", extra={"fields": {"auctions": removed}})
    return removed
