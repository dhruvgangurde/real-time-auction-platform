"""Concurrent bids on one auction: no lost update, no duplicate sequence (NFR-28, FR-35, NFR-29).

20 bidders, each on its own Redis connection pool (as separate API instances would be), race to
place 25 accepted bids each. A bidder always bids exactly the minimum next amount the script
reported, so a bid is accepted only if nobody else got in first; every BID_TOO_LOW is a real
interleaving. The accepted set must be exactly 500 gapless sequence numbers.
"""

import asyncio

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import BidResult, BidService
from app.services.ledger import persist_outbox
from app.services.live_state import outbox_key, state_key
from tests.bidding.conftest import AuctionFactory, PersistRecorder, new_redis

BIDDERS = 20
BIDS_PER_BIDDER = 25
BIDS = BIDDERS * BIDS_PER_BIDDER
START_PRICE = 10_000
MAX_ATTEMPTS_PER_BID = 2_000  # bound against an infinite loop if arbitration were broken


async def _bidder(service: BidService, auction, bidder, tag: str) -> tuple[list[BidResult], int]:
    accepted: list[BidResult] = []
    rejected = 0
    amount = START_PRICE
    for n in range(BIDS_PER_BIDDER):
        for attempt in range(MAX_ATTEMPTS_PER_BID):
            r = await service.place_bid(auction, bidder, amount, f"{tag}-{n}-{attempt}")
            if r.accepted:
                accepted.append(r)
                amount = r.amount + 1
                break
            assert r.code == "BID_TOO_LOW", r
            assert r.min_next is not None
            rejected += 1
            amount = r.min_next
        else:
            pytest.fail(f"bidder {tag} could not get bid {n} accepted")
    return accepted, rejected


async def test_500_concurrent_bids_from_20_bidders(
    auction: AuctionFactory,
    new_user,
    redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    a = await auction(start_price=START_PRICE, min_increment=1, self_outbid=False)
    bidders = [await new_user() for _ in range(BIDDERS)]
    clients = [new_redis() for _ in range(BIDDERS)]
    try:
        outcomes = await asyncio.gather(
            *(
                _bidder(BidService(c, sessions, PersistRecorder()), a, bidder, f"b{i}")
                for i, (c, bidder) in enumerate(zip(clients, bidders, strict=True))
            )
        )
    finally:
        for c in clients:
            await c.aclose()

    accepted = sorted(
        (r.sequence_no, r.amount, bidder)
        for (results, _), bidder in zip(outcomes, bidders, strict=True)
        for r in results
    )
    rejected = sum(n for _, n in outcomes)
    with capsys.disabled():
        print(
            f"\n[NFR-28] {BIDDERS} bidders x {BIDS_PER_BIDDER} bids on 20 connections: "
            f"{len(accepted)} accepted, {rejected} BID_TOO_LOW retries (lost races)"
        )
    assert rejected > 0, "no contention observed; the test did not exercise concurrency"

    seqs = [s for s, _, _ in accepted]
    amounts = [amt for _, amt, _ in accepted]
    assert seqs == list(range(1, BIDS + 1)), "gap or duplicate sequence number"
    assert all(x < y for x, y in zip(amounts, amounts[1:], strict=False)), "lost update"
    live = await redis.hgetall(state_key(a))
    assert int(live["sequence"]) == BIDS
    assert int(live["highest_amount"]) == max(amounts)

    # every accepted bid reaches the ledger exactly once (FR-41)
    await persist_outbox(a, redis, sessions)
    assert await redis.xlen(outbox_key(a)) == 0
    rows = await db.fetch(
        "SELECT sequence_no, amount, bidder_id FROM bids WHERE auction_id = $1 ORDER BY 1", a
    )
    assert [(r["sequence_no"], r["amount"], r["bidder_id"]) for r in rows] == accepted
