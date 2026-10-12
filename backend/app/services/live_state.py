"""Live auction state in Redis and its rebuild from Postgres (FR-43, DC-1).

Redis holds only rebuildable state; Postgres is the source of truth. Rebuild runs only when an
auction's Redis state has been lost (for example Redis restarted without its AOF). It is never
used to refresh existing live state, because the outbox may still hold accepted bids that
Postgres has not seen. init_state.lua refuses to rebuild while the outbox is non-empty.
"""

import logging
from datetime import UTC, datetime, timedelta
from itertools import chain
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import lua
from app.core.config import settings
from app.repositories import AuctionRepository, BidRepository

log = logging.getLogger("live_state")

REBUILD_ATTEMPTS = 3
REBUILD_LEASE_MS = 5_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# Keys share the {auction_id} hash tag so every key a script touches maps to one slot.
def state_key(auction_id: UUID) -> str:
    return f"auction:{{{auction_id}}}:state"


def outbox_key(auction_id: UUID) -> str:
    return f"auction:{{{auction_id}}}:bids"


def dead_letter_key(auction_id: UUID) -> str:
    return f"auction:{{{auction_id}}}:bids:dead"


def request_key(auction_id: UUID, bidder_id: UUID, request_id: str) -> str:
    # scoped by bidder so two bidders reusing a request_id never see each other's result
    return f"auction:{{{auction_id}}}:req:{bidder_id}:{request_id}"


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


async def _snapshot(
    sessions: async_sessionmaker[AsyncSession], auction_id: UUID
) -> tuple[dict[str, str], str, int]:
    """Read the auction row and its last ledger bid. Returns (hash fields, status, sequence)."""
    async with sessions() as session:
        auction = await AuctionRepository(session).get(auction_id)
        if auction is None:
            raise AuctionNotFound(f"auction {auction_id} not found")
        last = await BidRepository(session).last_bid(auction_id)
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
    }
    return fields, auction.status, sequence


async def init_live_state(
    auction_id: UUID, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> bool:
    """Build an auction's live state from Postgres, only if it is absent (FR-43).

    Returns True if this call initialised it, False if live state already existed (or another
    instance won the race). Raises LiveStateUnavailable when it cannot be done safely now.
    """
    init = redis.register_script(lua.source("init_state"))
    finalize = redis.register_script(lua.source("finalize_state"))
    state = state_key(auction_id)

    for attempt in range(1, REBUILD_ATTEMPTS + 1):
        fields, status, sequence = await _snapshot(sessions, auction_id)
        token = uuid4().hex
        outcome = await init(
            keys=[state, outbox_key(auction_id)],
            args=[REBUILD_LEASE_MS, token, *chain.from_iterable(fields.items())],
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
            committed = await finalize(keys=[state], args=[token, "commit", status])
            if committed:
                log.info(
                    "live state rebuilt",
                    extra={"fields": {"auction_id": str(auction_id), "sequence": sequence}},
                )
            return bool(committed)  # 0: our lease expired and another rebuild took over

        await finalize(keys=[state], args=[token, "abort", ""])
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
