"""Proxy rows through the outbox persister: idempotent, replay-safe, never resurrecting
an exhausted maximum (FR-45, FR-46, FR-51, NFR-10)."""

import asyncio
from uuid import UUID

import asyncpg
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import BidService
from app.services.ledger import OutboxEntry, _write_batch, persist_outbox
from app.services.live_state import init_live_state, outbox_key, proxies_key, state_key
from tests.bidding.conftest import AuctionFactory


async def _rows(db: asyncpg.Connection, a: UUID) -> list[tuple[UUID, int, int, bool]]:
    rows = await db.fetch(
        "SELECT bidder_id, max_amount, arm_seq, is_active FROM proxy_bids "
        "WHERE auction_id = $1 ORDER BY arm_seq",
        a,
    )
    return [(r["bidder_id"], r["max_amount"], r["arm_seq"], r["is_active"]) for r in rows]


async def _arm_and_exhaust(service: BidService, a: UUID, alice: UUID, bob: UUID) -> None:
    assert (await service.set_proxy(a, alice, 20_000, "arm")).armed  # arm_seq 1
    r = await service.place_bid(a, bob, 30_000, "outbid")  # alice exhausted
    assert r.accepted and r.automatic_bids == ()


async def test_set_then_exhaust_then_rearm_persist(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    a = await auction()
    alice, bob = await new_user(), await new_user()
    await _arm_and_exhaust(service, a, alice, bob)
    kinds = [f["kind"] for _, f in await redis.xrange(outbox_key(a))]
    assert kinds == ["proxy_set", "bid", "bid", "proxy_deactivated"]
    await persist_outbox(a, redis, sessions)
    assert await _rows(db, a) == [(alice, 20_000, 1, False)]

    assert (await service.set_proxy(a, alice, 50_000, "rearm")).armed  # arm_seq 2
    await persist_outbox(a, redis, sessions)
    assert await _rows(db, a) == [(alice, 20_000, 1, False), (alice, 50_000, 2, True)]


async def test_raise_updates_active_row_never_lowering(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    a = await auction()
    alice = await new_user()
    assert (await service.set_proxy(a, alice, 20_000, "arm")).armed
    entries = await redis.xrange(outbox_key(a))
    assert (await service.set_proxy(a, alice, 30_000, "raise")).armed
    await persist_outbox(a, redis, sessions)
    assert await _rows(db, a) == [(alice, 30_000, 2, True)]
    armed_at = await db.fetchval("SELECT armed_at FROM proxy_bids WHERE auction_id = $1", a)

    # replaying the original set (arm_seq 1) must not lower the maximum or reset armed_at
    for _, fields in entries:
        if fields["kind"] == "proxy_set":
            await redis.xadd(outbox_key(a), fields)
    await persist_outbox(a, redis, sessions)
    assert await _rows(db, a) == [(alice, 30_000, 2, True)]
    assert await db.fetchval("SELECT armed_at FROM proxy_bids WHERE auction_id = $1", a) == armed_at


async def test_full_replay_is_idempotent(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    a = await auction()
    alice, bob = await new_user(), await new_user()
    await _arm_and_exhaust(service, a, alice, bob)
    assert (await service.set_proxy(a, alice, 50_000, "rearm")).armed
    entries = await redis.xrange(outbox_key(a))
    await persist_outbox(a, redis, sessions)
    before = await _rows(db, a)
    for _, fields in entries:
        await redis.xadd(outbox_key(a), fields)
    assert await persist_outbox(a, redis, sessions) == 0
    assert await _rows(db, a) == before
    assert await redis.xlen(outbox_key(a)) == 0


async def test_replayed_deactivation_keeps_newer_maximum(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    a = await auction()
    alice, bob = await new_user(), await new_user()
    await _arm_and_exhaust(service, a, alice, bob)
    stale = [f for _, f in await redis.xrange(outbox_key(a)) if f["kind"] == "proxy_deactivated"]
    assert (await service.set_proxy(a, alice, 50_000, "rearm")).armed
    await persist_outbox(a, redis, sessions)
    await redis.xadd(outbox_key(a), stale[0])  # deactivation of arm_seq 1 arrives again
    await persist_outbox(a, redis, sessions)
    assert await _rows(db, a) == [(alice, 20_000, 1, False), (alice, 50_000, 2, True)]


async def test_stale_set_replay_cannot_resurrect_exhausted_maximum(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    """Commit set(arm 1), commit deactivate(arm 1), then replay the stale set(arm 1): no active
    row may exist, and a later rebuild must not re-arm the dead maximum."""
    a = await auction()
    alice, bob, carol = await new_user(), await new_user(), await new_user()
    await _arm_and_exhaust(service, a, alice, bob)
    stale_set = [f for _, f in await redis.xrange(outbox_key(a)) if f["kind"] == "proxy_set"][0]
    await persist_outbox(a, redis, sessions)
    assert await _rows(db, a) == [(alice, 20_000, 1, False)]

    await redis.xadd(outbox_key(a), stale_set)
    await persist_outbox(a, redis, sessions)
    assert await _rows(db, a) == [(alice, 20_000, 1, False)], "stale set resurrected the proxy"

    await redis.delete(state_key(a), proxies_key(a))
    assert await init_live_state(a, redis, sessions) is True
    assert await redis.hgetall(proxies_key(a)) == {}
    assert await redis.hget(state_key(a), "arm_seq") == "1"
    # and the next competing bid gets no automatic response from the dead maximum
    r = await service.place_bid(a, carol, 31_000, "after")
    assert r.accepted and r.automatic_bids == ()


async def test_concurrent_stale_persisters_cannot_both_insert(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    """Persister A holds [set 1, deactivate 1], persister B holds the stale [set 1]. Whatever the
    interleaving, the advisory lock serialises the check-and-write: no active row survives."""
    a = await auction()
    alice, bob = await new_user(), await new_user()
    await _arm_and_exhaust(service, a, alice, bob)
    entries = [OutboxEntry.parse(sid, f) for sid, f in await redis.xrange(outbox_key(a))]
    proxy_entries = [e for e in entries if e.kind != "bid"]
    assert [e.kind for e in proxy_entries] == ["proxy_set", "proxy_deactivated"]
    for _ in range(5):
        await db.execute("DELETE FROM proxy_bids WHERE auction_id = $1", a)
        await asyncio.gather(
            _write_batch(a, proxy_entries, sessions),
            _write_batch(a, proxy_entries[:1], sessions),
            _write_batch(a, proxy_entries[:1], sessions),
        )
        assert await _rows(db, a) == [(alice, 20_000, 1, False)]
    await redis.delete(outbox_key(a))
