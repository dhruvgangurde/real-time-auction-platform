"""Outbox persister and sweeper: exactly-once ledger rows, extensions, collisions
(FR-41, FR-55, FR-77, NFR-10)."""

import asyncio
import json
import logging
from uuid import uuid4

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import BidService
from app.services.ledger import persist_all_pending, persist_outbox
from app.services.live_state import dead_letter_key, from_ms, outbox_key, to_ms
from tests.bidding.conftest import AuctionFactory


async def _place(service: BidService, a, bidders, start: int, count: int, step: int = 500) -> None:
    for i in range(count):
        r = await service.place_bid(a, bidders[i % 2], start + i * step, f"p{start}-{i}")
        assert r.accepted


async def _ledger(db: asyncpg.Connection, a) -> list[int]:
    rows = await db.fetch("SELECT sequence_no FROM bids WHERE auction_id = $1 ORDER BY 1", a)
    return [r["sequence_no"] for r in rows]


async def test_persist_twice_sequentially_is_idempotent(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    a = await auction()
    await _place(service, a, [await new_user(), await new_user()], 10_000, 5)
    assert await persist_outbox(a, redis, sessions) == 5
    assert await persist_outbox(a, redis, sessions) == 0
    assert await _ledger(db, a) == [1, 2, 3, 4, 5]
    assert await redis.xlen(outbox_key(a)) == 0


async def test_replayed_batch_is_skipped(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    """A crash between commit and XDEL replays the batch: no duplicates, no collision alert."""
    a = await auction()
    await _place(service, a, [await new_user(), await new_user()], 10_000, 3)
    entries = await redis.xrange(outbox_key(a))
    await persist_outbox(a, redis, sessions)
    for _, fields in entries:  # put the already-persisted entries back
        await redis.xadd(outbox_key(a), fields)
    assert await persist_outbox(a, redis, sessions) == 0
    assert await _ledger(db, a) == [1, 2, 3]
    assert await redis.xlen(dead_letter_key(a)) == 0


async def test_concurrent_persisters_never_duplicate(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    a = await auction()
    await _place(service, a, [await new_user(), await new_user()], 10_000, 20)
    inserted = await asyncio.gather(*(persist_outbox(a, redis, sessions) for _ in range(4)))
    assert sum(inserted) == 20
    assert await _ledger(db, a) == list(range(1, 21))
    assert await redis.xlen(outbox_key(a)) == 0


async def test_extension_reaches_auction_row_with_audit(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    """FR-55: the new end time is persisted with the bid that caused it; FR-14 untouched.
    FR-77: each extension's audit row has the state before and after, chained in order."""
    a = await auction(ends_in_ms=59_000, threshold_s=600, extension_s=60)
    original = await db.fetchval("SELECT original_end_time FROM auctions WHERE id = $1", a)
    first = await service.place_bid(a, await new_user(), 10_000, "late1")
    second = await service.place_bid(a, await new_user(), 10_500, "late2")
    assert first.extended and second.extended
    await persist_outbox(a, redis, sessions)  # both extensions in one batch

    row = await db.fetchrow(
        "SELECT end_time, extension_count, original_end_time FROM auctions WHERE id = $1", a
    )
    assert row["end_time"] == from_ms(second.end_time_ms)
    assert row["extension_count"] == 2
    assert row["original_end_time"] == original
    bid = await db.fetchrow("SELECT end_time_after FROM bids WHERE auction_id = $1", a)
    assert bid["end_time_after"] == from_ms(first.end_time_ms)
    audits = await db.fetch(
        "SELECT action, before, after FROM audit_log "
        "WHERE entity_type = 'auction' AND entity_id = $1 ORDER BY id",
        str(a),
    )
    assert [x["action"] for x in audits] == ["soft_close_extended"] * 2
    assert [json.loads(x["before"]) for x in audits] == [
        {"end_time_ms": to_ms(original), "extension_count": 0},
        {"end_time_ms": first.end_time_ms, "extension_count": 1},
    ]
    assert json.loads(audits[1]["after"])["extension_count"] == 2


async def test_sweeper_drains_every_outbox_and_survives_a_failing_one(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NFR-10: the beat safety net persists every non-empty outbox; an auction whose batch
    fails is logged, keeps its entries, and never stops the others."""
    a, b = await auction(), await auction()
    bidders = [await new_user(), await new_user()]
    await _place(service, a, bidders, 10_000, 2)
    await _place(service, b, bidders, 10_000, 3)
    ghost = uuid4()  # an outbox for an auction Postgres does not have: the insert hits the FK
    await redis.xadd(
        outbox_key(ghost),
        {
            "kind": "bid",
            "bidder_id": str(bidders[0]),
            "amount": "10000",
            "sequence_no": "1",
            "bid_type": "manual",
            "accepted_at_ms": "0",
        },
    )
    with caplog.at_level(logging.ERROR, logger="ledger"):
        assert await persist_all_pending(redis, sessions) == 5
    assert await _ledger(db, a) == [1, 2]
    assert await _ledger(db, b) == [1, 2, 3]
    assert await redis.xlen(outbox_key(ghost)) == 1  # kept for the next run, never dropped
    assert [r.getMessage() for r in caplog.records if r.name == "ledger"] == ["sweep failed"]


async def test_sequence_collision_keeps_the_bid(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NFR-10: a reissued sequence number (e.g. after an AOF rollback) never loses the bid:
    it is written to audit_log in the persister's transaction and to a dead-letter stream."""
    a = await auction()
    alice, mallory = await new_user(), await new_user()
    assert (await service.place_bid(a, alice, 10_000, "first")).accepted
    await persist_outbox(a, redis, sessions)

    forged = {
        "auction_id": str(a),
        "bidder_id": str(mallory),
        "amount": "12345",
        "sequence_no": "1",
        "bid_type": "manual",
        "accepted_at_ms": "1760000000000",
    }
    await redis.xadd(outbox_key(a), forged)
    with caplog.at_level(logging.CRITICAL, logger="ledger"):
        assert await persist_outbox(a, redis, sessions) == 0

    audit = await db.fetchrow(
        "SELECT actor_id, after FROM audit_log WHERE action = 'sequence_collision' "
        "AND entity_id = $1",
        f"{a}:1",
    )
    assert audit["actor_id"] is None
    assert json.loads(audit["after"]) == forged
    dead = await redis.xrange(dead_letter_key(a))
    assert [fields for _, fields in dead] == [forged]
    assert await redis.xlen(outbox_key(a)) == 0
    assert any(
        getattr(rec, "fields", {}).get("alert") == "SEQUENCE_COLLISION" for rec in caplog.records
    )
    # the original ledger row is untouched
    original = await db.fetchrow("SELECT bidder_id, amount FROM bids WHERE auction_id = $1", a)
    assert (original["bidder_id"], original["amount"]) == (alice, 10_000)
