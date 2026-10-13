"""Closing and settlement (FR-51, FR-55, FR-57 to FR-62, FR-64, NFR-11, NFR-15, NFR-28, BR-5,
BR-6, OR-14, UC-03), against real Postgres, Redis TIME and pub/sub."""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import JsonFormatter, request_id_var
from app.services import closing
from app.services.bidding import BidService
from app.services.closing import Closer, Outcome, closed_sent_key
from app.services.live_state import outbox_key, proxies_key, state_key
from app.services.scheduler import Scheduler
from tests.bidding.conftest import PersistRecorder, redis_now_ms
from tests.closing.conftest import (
    Ended,
    Room,
    notifications,
    settlement_rows,
    terminal_audits,
)
from tests.db.conftest import make_user

RESERVE = 77_700  # distinct from every amount below, so a leak is visible


async def _status(db: asyncpg.Connection, auction_id: UUID) -> str:
    return await db.fetchval("SELECT status FROM auctions WHERE id = $1", auction_id)


async def _active_proxies(db: asyncpg.Connection, auction_id: UUID) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM proxy_bids WHERE auction_id = $1 AND is_active", auction_id
    )


async def _users(db: asyncpg.Connection, n: int) -> list[UUID]:
    return [await make_user(db) for _ in range(n)]


# ------------------------------------------------------------------ exactly once (OR-14)


async def test_ten_concurrent_closes_settle_once(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """NFR-28, OR-14, FR-57, FR-61: ten closers at once. One settles; the others find the lock
    taken or the auction settled and exit cleanly. One settlement, one terminal transition, one
    notification set, one auction_closed."""
    b1, b2, b3 = await _users(db, 3)
    auction_id = await ended(bids=[(b1, 10_000), (b2, 10_500), (b3, 11_000)])
    async with Room(auction_id) as room:
        outcomes = await asyncio.gather(
            *(Closer(redis, sessions).close(auction_id) for _ in range(10))
        )
    assert outcomes.count(Outcome.SETTLED) == 1
    assert set(outcomes) <= {Outcome.SETTLED, Outcome.LOCKED, Outcome.ALREADY_CLOSED}
    assert len(await settlement_rows(db, auction_id)) == 1
    assert len(await terminal_audits(db, auction_id)) == 1
    seller = await db.fetchval("SELECT seller_id FROM auctions WHERE id = $1", auction_id)
    assert await notifications(db, auction_id) == sorted(
        [
            (b3, "auction_won"),
            (b3, "payment_due"),
            (seller, "auction_sold"),
            (b1, "auction_lost"),
            (b2, "auction_lost"),
        ]
    )
    assert [e["type"] for e in room.events] == ["auction_closed"]


async def test_constraint_alone_keeps_one_settlement(
    ended: Ended,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """OR-14 backstop: with the lock disabled, ten closers all decide at once (a barrier) and
    all insert; UNIQUE(settlements.auction_id) admits one and the other nine roll back cleanly,
    with no second notification set."""
    b1, b2 = await _users(db, 2)
    auction_id = await ended(bids=[(b1, 10_000), (b2, 10_500)])
    barrier = asyncio.Barrier(10)
    decide = Closer._decide

    async def decide_together(session: AsyncSession, auction: Any) -> closing.Decision:
        decision = await decide(session, auction)
        await barrier.wait()
        return decision

    monkeypatch.setattr(Closer, "_decide", staticmethod(decide_together))
    with caplog.at_level(logging.INFO, logger="closing"):
        outcomes = await asyncio.gather(
            *(Closer(redis, sessions, use_lock=False).close(auction_id) for _ in range(10))
        )
    assert outcomes.count(Outcome.SETTLED) == 1
    assert outcomes.count(Outcome.ALREADY_CLOSED) == 9
    rolled_back = [r for r in caplog.records if r.msg == "settlement already exists; rolled back"]
    assert len(rolled_back) == 9  # the constraint, not the lock, stopped them
    assert len(await settlement_rows(db, auction_id)) == 1
    assert len(await terminal_audits(db, auction_id)) == 1
    assert len(await notifications(db, auction_id)) == 4  # won, payment_due, sold, lost


async def test_retry_after_success_does_nothing(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """NFR-11, FR-61: a repeated close (a Celery retry or redelivery) writes and sends nothing."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)])
    async with Room(auction_id) as room:
        assert await Closer(redis, sessions).close(auction_id) is Outcome.SETTLED
        assert await Closer(redis, sessions).close(auction_id) is Outcome.ALREADY_CLOSED
    assert [e["type"] for e in room.events] == ["auction_closed"]
    assert len(await settlement_rows(db, auction_id)) == 1
    assert len(await notifications(db, auction_id)) == 3  # won, payment_due, sold
    assert not await redis.exists(state_key(auction_id), proxies_key(auction_id))


# ------------------------------------------------------------------ execution-time checks


async def test_future_end_time_at_execution_leaves_auction_unchanged(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-58: a forced Redis end time in the future at execution: no settlement, no audit, no
    notification, maxima still armed, Redis untouched. With D9 this guard never fires."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)], proxies=[b1])
    future = await redis_now_ms(redis) + 60_000
    await redis.hset(state_key(auction_id), "end_time_ms", str(future))

    assert await Closer(redis, sessions).close(auction_id) is Outcome.NOT_ENDED
    assert await _status(db, auction_id) == "closing"
    assert await settlement_rows(db, auction_id) == []
    assert await terminal_audits(db, auction_id) == []
    assert await notifications(db, auction_id) == []
    assert await _active_proxies(db, auction_id) == 1
    assert await redis.hget(state_key(auction_id), "status") == "closing"


async def test_redis_live_while_postgres_closing_is_refused(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-58: the arbitration point still says live: nothing written."""
    auction_id = await ended()
    await redis.hset(state_key(auction_id), "status", "live")
    assert await Closer(redis, sessions).close(auction_id) is Outcome.NOT_ENDED
    assert await settlement_rows(db, auction_id) == []


async def test_redis_sequence_ahead_of_ledger_defers(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """NFR-15: Redis assigned a sequence the ledger lacks (lost outbox entries): never settle on
    that ledger."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)])
    await redis.hset(state_key(auction_id), "sequence", "2")
    assert await Closer(redis, sessions).close(auction_id) is Outcome.DEFERRED
    assert await settlement_rows(db, auction_id) == []


async def test_without_live_state_postgres_decides(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-58 when Redis lost the state: Postgres end_time passed and the ledger is all there is.
    The settler broadcasts (MISSING); a retry does not."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)], live_state=False)
    async with Room(auction_id) as room:
        assert await Closer(redis, sessions).close(auction_id) is Outcome.SETTLED
        assert await Closer(redis, sessions).close(auction_id) is Outcome.ALREADY_CLOSED
    assert [e["type"] for e in room.events] == ["auction_closed"]

    future = await ended(end_s=60, live_state=False)
    assert await Closer(redis, sessions).close(future) is Outcome.NOT_ENDED


async def test_not_closing_is_left_alone(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    for status in ("live", "cancelled", "scheduled"):
        auction_id = await ended(status=status, live_state=False)
        assert await Closer(redis, sessions).close(auction_id) is Outcome.NOT_CLOSING
        assert await _status(db, auction_id) == status


async def test_pending_outbox_defers_then_settles_with_the_full_ledger(
    auction: Any,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-55, NFR-15: bids still in the outbox at close. Without a drain the close defers and
    writes nothing; once drained it settles on the full ledger, and the bid accepted just
    before the end wins."""
    a = await auction(ends_in_ms=2_500, threshold_s=1, extension_s=1)
    bids = BidService(redis, sessions, PersistRecorder())  # nothing persists by itself
    b1, b2 = await _users(db, 2)
    assert (await bids.place_bid(a, b1, 10_000, "first")).accepted
    last = await bids.place_bid(a, b2, 10_500, "last")
    assert last.accepted and not last.extended
    while await redis_now_ms(redis) < last.end_time_ms:
        await asyncio.sleep(0.05)
    assert await Scheduler(redis, sessions).close_ended() == [a]
    assert await redis.xlen(outbox_key(a)) == 2

    async def no_drain(*_: Any) -> int:
        return 0

    monkeypatch.setattr(closing, "persist_outbox", no_drain)
    assert await Closer(redis, sessions).close(a) is Outcome.DEFERRED
    assert await settlement_rows(db, a) == []

    async def failing_drain(*_: Any) -> int:
        raise RedisConnectionError("redis down")

    monkeypatch.setattr(closing, "persist_outbox", failing_drain)
    assert await Closer(redis, sessions).close(a) is Outcome.DEFERRED
    assert await _status(db, a) == "closing"

    monkeypatch.undo()
    assert await Closer(redis, sessions).close(a) is Outcome.SETTLED
    (settlement,) = await settlement_rows(db, a)
    assert (settlement["winner_id"], settlement["final_price"]) == (b2, 10_500)
    assert await db.fetchval("SELECT count(*) FROM bids WHERE auction_id = $1", a) == 2


# ------------------------------------------------------------------ outcome (FR-59, FR-60)


@pytest.mark.parametrize(
    ("reserve", "amounts", "status", "reserve_met"),
    [
        (None, [10_000, 10_500], "closed_sold", None),  # no reserve: any valid bid wins (BR-5)
        (10_500, [10_000, 10_500], "closed_sold", True),  # meets it exactly
        (RESERVE, [10_000, 10_500], "closed_no_sale", False),  # below the reserve (FR-60)
        (None, [], "closed_no_sale", None),  # no bids
        (RESERVE, [], "closed_no_sale", False),
    ],
)
async def test_outcome_by_reserve(
    ended: Ended,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    reserve: int | None,
    amounts: list[int],
    status: str,
    reserve_met: bool | None,
) -> None:
    """FR-59, FR-60, FR-61, FR-62, FR-64, BR-5, BR-6: the auction status, one settlement
    (payable only when sold), the audit rows, the notification set and auction_closed. The
    reserve value appears in no event and no notification."""
    seller, b1, b2, holder = await _users(db, 4)
    bidders = [b1, b2][: len(amounts)]
    auction_id = await ended(
        bids=list(zip(bidders, amounts, strict=True)),
        proxies=[b1, holder],  # holder armed a maximum but never bid
        reserve=reserve,
        seller=seller,
    )
    async with Room(auction_id) as room:
        assert await Closer(redis, sessions).close(auction_id) is Outcome.SETTLED

    assert await _status(db, auction_id) == status
    (settlement,) = await settlement_rows(db, auction_id)
    sold = status == "closed_sold"
    assert settlement["status"] == ("pending_payment" if sold else "no_sale")
    assert settlement["winner_id"] == (b2 if sold else None)
    assert settlement["final_price"] == (10_500 if sold else None)
    assert settlement["idempotency_key"] == f"settle:{auction_id}"
    (transition,) = await terminal_audits(db, auction_id)
    assert transition["actor_id"] is None  # the system acted (FR-77)
    assert json.loads(transition["after"])["status"] == status
    created = await db.fetchrow(
        "SELECT after FROM audit_log WHERE entity_type = 'settlement' AND entity_id = $1 "
        "AND action = 'settlement_created'",
        str(settlement["id"]),
    )
    assert json.loads(created["after"])["status"] == settlement["status"]  # FR-64

    expected = (
        [(seller, "auction_sold"), (b2, "auction_won"), (b2, "payment_due")]
        if sold
        else [(seller, "auction_no_sale")]
    )
    losers = {b1, holder} | ({b2} if bidders == [b1, b2] and not sold else set())
    expected += [(u, "auction_lost") for u in losers]
    assert await notifications(db, auction_id) == sorted(expected)
    assert await _active_proxies(db, auction_id) == 0  # FR-51

    (closed,) = room.events
    assert closed["type"] == "auction_closed"
    assert closed["outcome"] == status
    assert closed["final_price"] == (10_500 if sold else None)
    assert closed["reserve_met"] is reserve_met
    assert closed["final_sequence_no"] == len(amounts)
    payloads = await db.fetch("SELECT payload FROM notifications WHERE auction_id = $1", auction_id)
    assert {json.loads(p["payload"])["reserve_met"] for p in payloads} == {reserve_met}
    for text in [json.dumps(closed)] + [p["payload"] for p in payloads]:
        assert str(RESERVE) not in text  # BR-6
        assert "reserve_price" not in text


@pytest.mark.parametrize(
    ("reserve", "amount", "status", "reserve_met"),
    [
        (None, 10_000, "closed_sold", None),  # no reserve
        (10_500, 10_500, "closed_sold", True),  # reserve met
        (RESERVE, 10_000, "closed_no_sale", False),  # reserve not met
    ],
)
async def test_auction_closed_reserve_met_values(
    ended: Ended,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    reserve: int | None,
    amount: int,
    status: str,
    reserve_met: bool | None,
) -> None:
    """BR-5, BR-6, FR-62: reserve_met is null only without a reserve, true or false otherwise;
    no key naming the reserve holds a number, in auction_closed or the close notifications."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, amount)], reserve=reserve)
    async with Room(auction_id) as room:
        assert await Closer(redis, sessions).close(auction_id) is Outcome.SETTLED
    (closed,) = room.events
    assert (closed["outcome"], closed["reserve_met"]) == (status, reserve_met)
    rows = await db.fetch("SELECT payload FROM notifications WHERE auction_id = $1", auction_id)
    payloads = [closed, *(json.loads(r["payload"]) for r in rows)]
    assert len(payloads) > 1
    for payload in payloads:
        assert payload["reserve_met"] is reserve_met
        assert [k for k, v in payload.items() if "reserve" in k and type(v) in (int, float)] == []


async def test_equal_amounts_resolve_by_sequence(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-59: equal amounts, the lower sequence number wins."""
    b1, b2 = await _users(db, 2)
    auction_id = await ended(bids=[(b1, 12_000), (b2, 12_000)])
    assert await Closer(redis, sessions).close(auction_id) is Outcome.SETTLED
    (settlement,) = await settlement_rows(db, auction_id)
    assert settlement["winner_id"] == b1


# ------------------------------------------------------------------ after the commit


async def test_crash_after_commit_is_repaired_with_one_broadcast(
    ended: Ended,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NFR-12: the closer dies after its commit, before Redis, the broadcast and the cleanup.
    The sweep re-enqueues it; the next run finishes those steps exactly once."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)])
    crashed = Closer(redis, sessions)

    async def die(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(crashed, "_finish", die)
    assert await crashed.close(auction_id) is Outcome.SETTLED
    assert await redis.hget(state_key(auction_id), "status") == "closing"

    enqueued: list[UUID] = []
    await closing.sweep(redis, sessions, enqueued.append, grace_s=300)
    assert enqueued == [auction_id]

    async with Room(auction_id) as room:
        assert await Closer(redis, sessions).close(auction_id) is Outcome.ALREADY_CLOSED
        assert await Closer(redis, sessions).close(auction_id) is Outcome.ALREADY_CLOSED
    assert [e["type"] for e in room.events] == ["auction_closed"]
    assert not await redis.exists(state_key(auction_id))
    enqueued.clear()
    await closing.sweep(redis, sessions, enqueued.append, grace_s=300)
    assert enqueued == []


async def test_settler_and_retry_finishing_together_broadcast_once(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-62: only the caller whose closing -> terminal flip returned OK broadcasts."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)])
    settler = Closer(redis, sessions)
    assert await settler._settle(auction_id) is Outcome.SETTLED
    async with Room(auction_id) as room:
        await asyncio.gather(
            settler._finish(auction_id, settled=True),
            Closer(redis, sessions)._finish(auction_id, settled=False),
        )
    assert [e["type"] for e in room.events] == ["auction_closed"]


async def test_retry_first_then_settler_broadcasts_once(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-62: the retry flips, broadcasts and removes the live state; the settler then finds no
    live state (MISSING) and, the marker being set, sends nothing."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)])
    settler = Closer(redis, sessions)
    assert await settler._settle(auction_id) is Outcome.SETTLED
    async with Room(auction_id) as room:
        await Closer(redis, sessions)._finish(auction_id, settled=False)
        assert not await redis.exists(state_key(auction_id))
        await settler._finish(auction_id, settled=True)
    assert [e["type"] for e in room.events] == ["auction_closed"]


async def test_settler_first_then_retry_broadcasts_once(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-62: the settler flips and broadcasts; the retry finds no live state and is no settler."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)])
    settler = Closer(redis, sessions)
    assert await settler._settle(auction_id) is Outcome.SETTLED
    async with Room(auction_id) as room:
        await settler._finish(auction_id, settled=True)
        await Closer(redis, sessions)._finish(auction_id, settled=False)
    assert [e["type"] for e in room.events] == ["auction_closed"]


async def test_marker_survives_live_state_cleanup(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-62: after a full close the marker is there with its lifetime, outside the live-state
    keys that cleanup removed."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)])
    assert await Closer(redis, sessions).close(auction_id) is Outcome.SETTLED
    assert 0 < await redis.ttl(closed_sent_key(auction_id)) <= closing.CLOSED_SENT_TTL_S
    live = (state_key(auction_id), outbox_key(auction_id), proxies_key(auction_id))
    assert await redis.exists(*live) == 0


async def test_closed_payload_identical_both_paths(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-62, BR-6: the OK-flip path and the no-live-state path send the same event for one
    settlement, built from the committed rows (server_time_ms aside: every event is stamped
    when it is built)."""
    b1, b2 = await _users(db, 2)
    auction_id = await ended(bids=[(b1, 80_000), (b2, 80_500)], reserve=RESERVE)
    settler = Closer(redis, sessions)
    assert await settler._settle(auction_id) is Outcome.SETTLED
    async with Room(auction_id) as room:
        await Closer(redis, sessions)._finish(auction_id, settled=False)  # OK flip
        await redis.delete(closed_sent_key(auction_id))  # as if the marker had expired
        await settler._finish(auction_id, settled=True)  # no live state left: MISSING
    flipped, missing = ({k: v for k, v in e.items() if k != "server_time_ms"} for e in room.events)
    assert flipped == missing
    price, sequence = await db.fetchrow(
        "SELECT s.final_price, a.bid_count FROM settlements s JOIN auctions a "
        "ON a.id = s.auction_id WHERE s.auction_id = $1",
        auction_id,
    )
    assert flipped == {
        "type": "auction_closed",
        "auction_id": str(auction_id),
        "outcome": "closed_sold",
        "final_price": price,
        "reserve_met": True,
        "final_sequence_no": sequence,
    }
    assert (price, sequence) == (80_500, 2)
    assert str(RESERVE) not in json.dumps(room.events)  # BR-6


async def test_broadcast_redis_error_does_not_fail_close(
    ended: Ended,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-62, NFR-32: the broadcast script fails; the close stays committed, the live state is
    still removed and a warning carries the auction and the correlation id."""
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)])
    closer = Closer(redis, sessions)

    async def unreachable(*_: Any, **__: Any) -> int:
        raise RedisConnectionError("redis down")

    monkeypatch.setattr(closer, "_publish_once", unreachable)
    token = request_id_var.set("corr-close-1")
    try:
        with caplog.at_level(logging.WARNING, logger="closing"):
            assert await closer.close(auction_id) is Outcome.SETTLED
        [record] = [r for r in caplog.records if r.msg == "auction_closed not broadcast"]
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert (line["level"], line["auction_id"], line["request_id"]) == (
        "WARNING",
        str(auction_id),
        "corr-close-1",
    )
    assert await _status(db, auction_id) == "closed_sold"
    assert len(await settlement_rows(db, auction_id)) == 1
    assert await redis.exists(state_key(auction_id)) == 0  # the cleanup still ran


async def test_redis_failure_after_commit_leaves_the_rest_to_the_sweep(
    ended: Ended,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (b1,) = await _users(db, 1)
    auction_id = await ended(bids=[(b1, 10_000)])
    closer = Closer(redis, sessions)

    async def unreachable(*_: Any, **__: Any) -> str:
        raise RedisConnectionError("redis down")

    monkeypatch.setattr(closer, "_set_status", unreachable)
    assert await closer.close(auction_id) is Outcome.SETTLED
    assert await redis.hget(state_key(auction_id), "status") == "closing"


# ------------------------------------------------------------------ sweep (NFR-12)


async def test_sweep_reenqueues_unsettled_and_alerts_unresolved(
    ended: Ended,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NFR-12: a closing auction without a settlement is re-enqueued; any auction past
    end_time + grace without a terminal status raises UNRESOLVED_CLOSE; terminal ones and those
    within the grace period do not."""
    unsettled = await ended()
    stuck_live = await ended(status="live", end_s=-400, live_state=False)
    stuck_closing = await ended(end_s=-400, live_state=False)
    await ended(status="live", end_s=-10, live_state=False)  # within the grace period
    await ended(status="closed_no_sale", end_s=-400, live_state=False)

    enqueued: list[UUID] = []
    with caplog.at_level(logging.CRITICAL, logger="closing"):
        report = await closing.sweep(redis, sessions, enqueued.append, grace_s=300)
    assert set(enqueued) == {unsettled, stuck_closing}
    alerted = {
        r.fields["auction_id"]: r.fields["status"]
        for r in caplog.records
        if getattr(r, "fields", {}).get("alert") == "UNRESOLVED_CLOSE"
    }
    assert alerted == {str(stuck_live): "live", str(stuck_closing): "closing"}
    assert report == {"enqueued": 2, "unresolved": 2}


async def test_rebuild_in_progress_defers(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    auction_id = await ended()
    await redis.hset(state_key(auction_id), "status", "rebuilding")
    assert await Closer(redis, sessions).close(auction_id) is Outcome.DEFERRED
    assert await settlement_rows(db, auction_id) == []


async def test_close_notifications_are_pushed_live_once(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """FR-71: each recipient's channel gets the stored notifications after the commit; a
    retried close inserts nothing and pushes nothing."""
    from app.services.live_events import user_channel

    winner, loser = await _users(db, 2)
    auction_id = await ended(bids=[(loser, 10_000), (winner, 10_500)])
    async with (
        Room(channel=user_channel(winner)) as winner_room,
        Room(channel=user_channel(loser)) as loser_room,
    ):
        assert await Closer(redis, sessions).close(auction_id) is Outcome.SETTLED
        assert await Closer(redis, sessions).close(auction_id) is Outcome.ALREADY_CLOSED
    assert sorted(e["notification_type"] for e in winner_room.events) == [
        "auction_won",
        "payment_due",
    ]
    assert [e["notification_type"] for e in loser_room.events] == ["auction_lost"]
    assert all(e["auction_id"] == str(auction_id) for e in winner_room.events)
