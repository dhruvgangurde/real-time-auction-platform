"""Concurrent manual bids and proxy sets on one auction (NFR-28, FR-47, NFR-29).

Ten manual bidders loop on the reported minimum and five proxy setters raise their maxima, all
at once on separate connections. Every accepted bid, manual or automatic, must carry a unique
gapless sequence number, amounts must rise with the sequence, and the ledger must equal the
accepted set exactly.
"""

import asyncio
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import BidService
from app.services.ledger import persist_outbox
from app.services.live_state import outbox_key, proxies_key, state_key
from tests.bidding.conftest import AuctionFactory, PersistRecorder, new_redis

MANUAL_BIDDERS = 10
BIDS_PER_BIDDER = 20
PROXY_SETTERS = 5
RAISES_PER_SETTER = 6
START = 10_000
Accepted = tuple[int, int, UUID, str]  # sequence, amount, bidder, type


def _collect(autos, out: list[Accepted]) -> None:
    out.extend((x.sequence_no, x.amount, x.bidder_id, "automatic") for x in autos)


async def _manual(service: BidService, a: UUID, bidder: UUID, tag: str) -> list[Accepted]:
    out: list[Accepted] = []
    amount = START
    for n in range(BIDS_PER_BIDDER):
        for attempt in range(500):
            r = await service.place_bid(a, bidder, amount, f"{tag}-{n}-{attempt}")
            if r.accepted:
                assert r.sequence_no and r.amount
                out.append((r.sequence_no, r.amount, bidder, "manual"))
                _collect(r.automatic_bids, out)
                amount = (r.automatic_bids[-1].amount if r.automatic_bids else r.amount) + 1
                break
            assert r.code == "BID_TOO_LOW" and r.min_next, r
            amount = r.min_next
        else:
            pytest.fail(f"{tag} never got bid {n} accepted")
    return out


async def _setter(
    service: BidService, a: UUID, bidder: UUID, base: int, tag: str
) -> list[Accepted]:
    out: list[Accepted] = []
    for k in range(RAISES_PER_SETTER):
        p = await service.set_proxy(a, bidder, base + k * 3_000, f"{tag}-{k}")
        if p.armed:
            _collect(p.automatic_bids, out)
        else:
            assert p.code in {"BELOW_CURRENT_PRICE", "PROXY_CANNOT_DECREASE"}, p
    return out


async def test_mixed_manual_and_proxy_traffic_is_gapless(
    auction: AuctionFactory,
    new_user,
    redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    a = await auction(start_price=START, min_increment=1, self_outbid=False)
    manual = [await new_user() for _ in range(MANUAL_BIDDERS)]
    setters = [await new_user() for _ in range(PROXY_SETTERS)]
    clients = [new_redis() for _ in range(MANUAL_BIDDERS + PROXY_SETTERS)]
    try:
        services = [BidService(c, sessions, PersistRecorder()) for c in clients]
        results = await asyncio.gather(
            *(_manual(services[i], a, b, f"m{i}") for i, b in enumerate(manual)),
            *(
                _setter(services[MANUAL_BIDDERS + j], a, b, 12_000 + j * 1_000, f"s{j}")
                for j, b in enumerate(setters)
            ),
        )
    finally:
        for c in clients:
            await c.aclose()

    accepted = sorted(x for part in results for x in part)
    live = await redis.hgetall(state_key(a))
    n = int(live["sequence"])
    seqs = [s for s, _, _, _ in accepted]
    amounts = [amt for _, amt, _, _ in accepted]
    automatic = sum(1 for _, _, _, t in accepted if t == "automatic")
    with capsys.disabled():
        print(
            f"\n[NFR-28] {len(accepted)} accepted bids ({automatic} automatic) from "
            f"{MANUAL_BIDDERS} manual bidders and {PROXY_SETTERS} proxy setters, sequence 1..{n}"
        )
    assert automatic > 0, "no automatic bids: proxies never engaged"
    assert seqs == list(range(1, n + 1)), "gap or duplicate sequence number"
    assert all(x < y for x, y in zip(amounts, amounts[1:], strict=False)), "lost update"
    assert int(live["highest_amount"]) == amounts[-1]

    await persist_outbox(a, redis, sessions)
    assert await redis.xlen(outbox_key(a)) == 0
    rows = await db.fetch(
        "SELECT sequence_no, amount, bidder_id, bid_type FROM bids WHERE auction_id = $1 "
        "ORDER BY sequence_no",
        a,
    )
    assert [tuple(r) for r in rows] == accepted
    active_db = {
        r["bidder_id"]
        for r in await db.fetch(
            "SELECT bidder_id FROM proxy_bids WHERE auction_id = $1 AND is_active", a
        )
    }
    assert {UUID(b) for b in await redis.hkeys(proxies_key(a))} == active_db
