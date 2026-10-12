"""Outbox persister: moves accepted bids from Redis into the bid ledger (FR-41, FR-43, NFR-10).

Bids are acknowledged before they are durable (FR-40). They wait in the per-auction outbox
stream until this persister commits them to Postgres; only then are they deleted from the stream.

Idempotent and safe to run concurrently: the insert skips existing (auction_id, sequence_no)
pairs, extensions only ever move end_time forward, and stream entries are deleted only after the
commit. A crash between commit and delete just replays the batch.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories import AuctionRepository, AuditLogRepository, BidRepository
from app.services.live_state import auction_id_from_key, dead_letter_key, from_ms, outbox_key

log = logging.getLogger("ledger")

BATCH_SIZE = 500
TIME_BUDGET_S = 2.0  # anything left is picked up by the next run or the sweeper


@dataclass(frozen=True)
class OutboxEntry:
    stream_id: str
    raw: dict[str, str]
    bidder_id: UUID
    amount: int
    sequence_no: int
    bid_type: str
    accepted_at: datetime
    end_time_after: datetime | None
    extension_count: int | None

    @classmethod
    def parse(cls, stream_id: str, f: dict[str, str]) -> "OutboxEntry":
        extended = "end_time_after" in f
        return cls(
            stream_id=stream_id,
            raw=f,
            bidder_id=UUID(f["bidder_id"]),
            amount=int(f["amount"]),
            sequence_no=int(f["sequence_no"]),
            bid_type=f["bid_type"],
            accepted_at=from_ms(int(f["accepted_at_ms"])),
            end_time_after=from_ms(int(f["end_time_after"])) if extended else None,
            extension_count=int(f["extension_count"]) if extended else None,
        )

    def ledger_row(self, auction_id: UUID) -> dict[str, Any]:
        return {
            "auction_id": auction_id,
            "bidder_id": self.bidder_id,
            "amount": self.amount,
            "sequence_no": self.sequence_no,
            "bid_type": self.bid_type,
            "end_time_after": self.end_time_after,
            "created_at": self.accepted_at,  # acceptance time from Redis TIME, not insert time
        }


async def _write_batch(
    auction_id: UUID, entries: list[OutboxEntry], sessions: async_sessionmaker[AsyncSession]
) -> tuple[int, list[OutboxEntry]]:
    """One transaction: ledger rows, extensions and their audit entries (FR-41, FR-55, FR-78).

    Returns (rows inserted, colliding entries).
    """
    async with sessions() as session, session.begin():
        bids = BidRepository(session)
        audit = AuditLogRepository(session)
        inserted = await bids.insert_ignore_conflicts([e.ledger_row(auction_id) for e in entries])

        # A skipped entry is normally a replay of a row already written. If the existing row
        # differs, Redis reissued a sequence number (e.g. after an AOF rollback): keep the bid.
        skipped = [e for e in entries if e.sequence_no not in inserted]
        collisions: list[OutboxEntry] = []
        if skipped:
            existing = await bids.get_by_sequences(auction_id, [e.sequence_no for e in skipped])
            collisions = [
                e
                for e in skipped
                if existing.get(e.sequence_no) != (e.bidder_id, e.amount, e.bid_type)
            ]
        for e in collisions:
            await audit.add(
                entity_type="bid",
                entity_id=f"{auction_id}:{e.sequence_no}",
                action="sequence_collision",
                after=e.raw,
            )

        extended = [e for e in entries if e.end_time_after is not None]
        if extended:
            await AuctionRepository(session).apply_extension(
                auction_id,
                max(e.end_time_after for e in extended if e.end_time_after),
                max(e.extension_count or 0 for e in extended),
            )
            for e in extended:
                if e.sequence_no in inserted:  # audit each extension once, not on replays
                    await audit.add(
                        entity_type="auction",
                        entity_id=str(auction_id),
                        action="soft_close_extended",
                        after={
                            "sequence_no": e.sequence_no,
                            "end_time_after": e.raw["end_time_after"],
                            "extension_count": e.extension_count,
                        },
                    )
    return len(inserted), collisions


async def persist_outbox(
    auction_id: UUID, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> int:
    """Drain an auction's outbox into the ledger. Returns the number of rows inserted."""
    key = outbox_key(auction_id)
    deadline = time.monotonic() + TIME_BUDGET_S
    persisted = 0
    while True:
        batch = await redis.xrange(key, count=BATCH_SIZE)
        if not batch:
            break
        entries = [OutboxEntry.parse(sid, fields) for sid, fields in batch]
        inserted, collisions = await _write_batch(auction_id, entries, sessions)
        for e in collisions:
            await redis.xadd(dead_letter_key(auction_id), e.raw)
            log.critical(
                "sequence collision: accepted bid kept in audit_log and dead-letter stream",
                extra={
                    "fields": {
                        "alert": "SEQUENCE_COLLISION",
                        "auction_id": str(auction_id),
                        "entry": e.raw,
                    }
                },
            )
        await redis.xdel(key, *[e.stream_id for e in entries])
        persisted += inserted
        if time.monotonic() >= deadline:
            break
    return persisted


async def persist_all_pending(redis: Redis, sessions: async_sessionmaker[AsyncSession]) -> int:
    """Sweeper safety net: persist every non-empty outbox."""
    total = 0
    # shortcut: SCAN walks the keyspace; fine at this scale, use a pending-set index if it grows
    async for key in redis.scan_iter(match="auction:*:bids", _type="stream", count=500):
        if not await redis.xlen(key):
            continue
        auction_id = auction_id_from_key(key)
        try:
            total += await persist_outbox(auction_id, redis, sessions)
        except Exception:
            log.exception("sweep failed", extra={"fields": {"auction_id": str(auction_id)}})
    return total
