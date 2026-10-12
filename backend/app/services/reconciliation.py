"""Periodic reconciliation of Redis sequence counters against the bid ledger (FR-43, NFR-10).

Never touches the bid path; it reads and raises operator alerts (CRITICAL log lines with an
`alert` field). Each run ends by dropping Redis state left for auctions that are no longer live
or closing (D2).
"""

import logging
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories import AuctionRepository, BidRepository
from app.services.live_state import outbox_key, remove_stale_state, state_key

log = logging.getLogger("reconciliation")


def _ranges(numbers: list[int]) -> list[list[int]]:
    """[1, 2, 3, 7] -> [[1, 3], [7, 7]]"""
    out: list[list[int]] = []
    for n in numbers:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


async def reconcile_auction(
    auction_id: UUID, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> str | None:
    """Check one auction. Returns the alert code raised, if any."""
    # read order matters: sequence, then outbox, then ledger, so a concurrent persister
    # (which writes the ledger before deleting from the outbox) can never cause a false gap
    raw_seq = await redis.hget(state_key(auction_id), "sequence")
    if raw_seq is None:
        log.warning(
            "live auction has no live state",
            extra={"fields": {"auction_id": str(auction_id)}},
        )
        return None
    redis_seq = int(raw_seq)
    pending = {
        int(f["sequence_no"])
        for _, f in await redis.xrange(outbox_key(auction_id))
        if f.get("kind", "bid") == "bid"  # proxy entries carry no sequence number
    }
    async with sessions() as session:
        bids = BidRepository(session)
        ledger_max = await bids.max_sequence(auction_id)
        missing = await bids.missing_sequences(auction_id, redis_seq) if redis_seq else []

    if ledger_max > redis_seq:
        # Redis lost acknowledged writes (e.g. AOF rollback) and may reissue sequence numbers
        log.critical(
            "ledger is ahead of the live sequence",
            extra={
                "fields": {
                    "alert": "SEQUENCE_REGRESSION",
                    "auction_id": str(auction_id),
                    "redis_sequence": redis_seq,
                    "ledger_max": ledger_max,
                }
            },
        )
        return "SEQUENCE_REGRESSION"

    lost = [n for n in missing if n not in pending]
    if lost:
        log.critical(
            "accepted bids missing from ledger and outbox",
            extra={
                "fields": {
                    "alert": "SEQUENCE_GAP",
                    "auction_id": str(auction_id),
                    "missing_ranges": _ranges(lost),
                    "redis_sequence": redis_seq,
                    "ledger_max": ledger_max,
                }
            },
        )
        return "SEQUENCE_GAP"
    return None


async def reconcile(redis: Redis, sessions: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    """Reconcile every live auction, then drop stale live state (D2).

    Returns {auction_id: alert_code} for alerts raised.
    """
    async with sessions() as session:
        live = await AuctionRepository(session).list_live_ids()
    alerts: dict[str, str] = {}
    for auction_id in live:
        try:
            code = await reconcile_auction(auction_id, redis, sessions)
        except Exception:
            # one broken auction must never hide the other auctions' gaps
            log.critical(
                "reconciliation failed for an auction",
                exc_info=True,
                extra={"fields": {"alert": "RECONCILE_FAILED", "auction_id": str(auction_id)}},
            )
            code = "RECONCILE_FAILED"
        if code:
            alerts[str(auction_id)] = code
    await remove_stale_state(redis, sessions)
    return alerts
