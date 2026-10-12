"""Outbox persister: moves accepted bids and proxy changes from Redis into Postgres
(FR-41, FR-43, FR-45, NFR-10), and keeps auctions.current_price / bid_count in step (D7).

Bids are acknowledged before they are durable (FR-40). They wait in the per-auction outbox
stream until this persister commits them to Postgres; only then are they deleted from the stream.
Proxy changes (kind=proxy_set, kind=proxy_deactivated) travel through the same stream and are
applied in stream order inside the same transaction as the bids of their batch.

Idempotent and safe to run concurrently: the bid insert skips existing (auction_id, sequence_no)
pairs, extensions are applied only with the insert of their bid and only ever move end_time
forward, proxy rows are raised only by a newer arm_seq and deactivated only for their own
arm_seq, and stream entries are deleted only after the commit. A crash between commit and
delete just replays the batch.

Proxy maxima never reach audit_log or log lines (FR-51, BR-7).
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories import (
    AuctionRepository,
    AuditLogRepository,
    BidRepository,
    ProxyBidRepository,
)
from app.services.live_state import (
    auction_id_from_key,
    dead_letter_key,
    from_ms,
    outbox_key,
    to_ms,
)

log = logging.getLogger("ledger")

BATCH_SIZE = 500
TIME_BUDGET_S = 2.0  # anything left is picked up by the next run or the sweeper


@dataclass(frozen=True)
class OutboxEntry:
    stream_id: str
    raw: dict[str, str]
    kind: str  # bid | proxy_set | proxy_deactivated
    bidder_id: UUID
    at: datetime  # accepted_at / armed_at / deactivated_at, from Redis TIME
    # kind=bid
    amount: int | None = None
    sequence_no: int | None = None
    bid_type: str | None = None
    end_time_after: datetime | None = None
    extension_count: int | None = None
    # kind=proxy_set / proxy_deactivated
    max_amount: int | None = None
    arm_seq: int | None = None

    @classmethod
    def parse(cls, stream_id: str, f: dict[str, str]) -> "OutboxEntry":
        kind = f.get("kind", "bid")  # entries written before kinds existed are bids
        bidder_id = UUID(f["bidder_id"])
        if kind == "proxy_set":
            return cls(
                stream_id,
                f,
                kind,
                bidder_id,
                from_ms(int(f["armed_at_ms"])),
                max_amount=int(f["max_amount"]),
                arm_seq=int(f["arm_seq"]),
            )
        if kind == "proxy_deactivated":
            return cls(
                stream_id,
                f,
                kind,
                bidder_id,
                from_ms(int(f["deactivated_at_ms"])),
                arm_seq=int(f["arm_seq"]),
            )
        extended = "end_time_after" in f
        return cls(
            stream_id,
            f,
            "bid",
            bidder_id,
            from_ms(int(f["accepted_at_ms"])),
            amount=int(f["amount"]),
            sequence_no=int(f["sequence_no"]),
            bid_type=f["bid_type"],
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
            "created_at": self.at,  # acceptance time from Redis TIME, not insert time
        }


async def _write_batch(
    auction_id: UUID, entries: list[OutboxEntry], sessions: async_sessionmaker[AsyncSession]
) -> tuple[int, list[OutboxEntry]]:
    """One transaction: ledger rows, extensions and their audit entries, proxy rows
    (FR-41, FR-45, FR-55, FR-78). Returns (bid rows inserted, colliding bid entries)."""
    bid_entries = [e for e in entries if e.kind == "bid"]
    async with sessions() as session, session.begin():
        bids = BidRepository(session)
        audit = AuditLogRepository(session)
        inserted = await bids.insert_ignore_conflicts(
            [e.ledger_row(auction_id) for e in bid_entries]
        )

        # A skipped entry is normally a replay of a row already written. If the existing row
        # differs, Redis reissued a sequence number (e.g. after an AOF rollback): keep the bid.
        skipped = [e for e in bid_entries if e.sequence_no not in inserted]
        collisions: list[OutboxEntry] = []
        if skipped:
            existing = await bids.get_by_sequences(
                auction_id, [e.sequence_no for e in skipped if e.sequence_no is not None]
            )
            collisions = [
                e
                for e in skipped
                if existing.get(e.sequence_no or 0) != (e.bidder_id, e.amount, e.bid_type)
            ]
        for e in collisions:
            await audit.add(
                entity_type="bid",
                entity_id=f"{auction_id}:{e.sequence_no}",
                action="sequence_collision",
                after=e.raw,
            )

        auctions = AuctionRepository(session)
        new = [e for e in bid_entries if e.sequence_no in inserted]
        if new:  # D7: listing price and bid count, in the same transaction as the ledger rows
            top = max(new, key=lambda e: e.sequence_no or 0)
            assert top.amount is not None and top.sequence_no is not None
            await auctions.apply_bids(auction_id, top.amount, top.sequence_no)

        # only bids this transaction inserted: a replayed row's extension was applied when it
        # was inserted, and re-applying it could move a force-closed end time back out (FR-63)
        extended = [
            e for e in bid_entries if e.end_time_after is not None and e.sequence_no in inserted
        ]
        if extended:
            end_time, count = await auctions.extension_state(auction_id)  # row locked
            before = {"end_time_ms": to_ms(end_time), "extension_count": count}
            await auctions.apply_extension(
                auction_id,
                max(e.end_time_after for e in extended if e.end_time_after),
                max(e.extension_count or 0 for e in extended),
            )
            # audit each new extension once, chained in sequence order so each row's before is
            # the previous row's after (FR-77)
            for e in sorted(extended, key=lambda e: e.sequence_no or 0):
                after = {
                    "sequence_no": e.sequence_no,
                    "end_time_after": e.raw["end_time_after"],
                    "extension_count": e.extension_count,
                }
                await audit.add(
                    entity_type="auction",
                    entity_id=str(auction_id),
                    action="soft_close_extended",
                    before=before,
                    after=after,
                )
                before = {
                    "end_time_ms": int(e.raw["end_time_after"]),
                    "extension_count": e.extension_count,
                }

        # proxy changes in stream order; each is idempotent on replay
        proxy_repo = ProxyBidRepository(session)
        for e in entries:
            if e.kind == "proxy_set" and e.max_amount is not None and e.arm_seq is not None:
                await proxy_repo.upsert_active(
                    auction_id, e.bidder_id, e.max_amount, e.arm_seq, e.at
                )
            elif e.kind == "proxy_deactivated" and e.arm_seq is not None:
                await proxy_repo.deactivate(auction_id, e.bidder_id, e.arm_seq)
    return len(inserted), collisions


async def persist_outbox(
    auction_id: UUID, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> int:
    """Drain an auction's outbox into Postgres. Returns the number of bid rows inserted."""
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
                        "entry": e.raw,  # a bid entry: no proxy maximum in it
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
