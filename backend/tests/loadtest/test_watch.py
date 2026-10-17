"""The load test beside and after k6 (Phase 11, loadtest/README.md), on the test database and
Redis: the ledger reads (NFR-4, NFR-7), the watcher (NFR-1's slow log, NFR-7's first sight), and
a run read back and checked in Postgres with the six ledger checks of Try to break it (NFR-28),
published without the tokens the run used."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import lua
from app.loadtest import CITED, document, evidence, machine, markdown, watch
from app.repositories.loadtest import LoadTestRepository
from app.services.closing import Closer, Outcome
from app.services.live_state import outbox_key, proxies_key, request_key, state_key
from tests.closing.conftest import Ended
from tests.db.conftest import make_auction, make_user


async def test_the_ledger_reads_for_the_report(
    db: asyncpg.Connection, sessions: async_sessionmaker[AsyncSession]
) -> None:
    lot, bidder = await make_auction(db, status="live"), await make_user(db)
    second = datetime(2026, 10, 17, 12, 0, 5, tzinfo=UTC)
    offsets = [0.1, 0.4, 0.9, 1.2, 3.0]  # three in one second, one in the next, one later
    for seq, offset in enumerate(offsets, 1):
        await db.execute(
            "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type, created_at) "
            "VALUES ($1, $2, $3, $4, 'manual', $5)",
            lot,
            bidder,
            10_000 + seq * 500,
            seq,
            second + timedelta(seconds=offset),
        )
    async with sessions() as session:
        repo = LoadTestRepository(session)
        assert [s for s, _ in await repo.bids_after(lot, 2)] == [3, 4, 5]
        assert [s for s, _ in await repo.bids_after(lot, 0, limit=2)] == [1, 2]
        assert await repo.accepted_per_second(lot) == [
            (second, 3),
            (second + timedelta(seconds=1), 1),
            (second + timedelta(seconds=3), 1),
        ]


async def test_the_watcher_measures_until_the_lot_is_settled_and_puts_the_slow_log_back(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    first, second = await make_user(db), await make_user(db)
    lot = await ended(bids=[(first, 10_000), (second, 10_500)])  # closing, not yet settled
    before = await redis.config_get("slowlog-*")
    watcher = asyncio.create_task(watch(sessions, redis, redis, lot, time.time() + 30, 0.02))
    async with asyncio.timeout(5):  # the watcher has turned the slow log on for everything
        while (await redis.config_get("slowlog-log-slower-than"))["slowlog-log-slower-than"] != "0":
            await asyncio.sleep(0.01)
    # one arbitration while it watches: a lot without live state, rejected, nothing written
    nowhere = uuid4()
    keys = [state_key(nowhere), outbox_key(nowhere), request_key(nowhere, first, "r1")]
    place_bid = redis.register_script(lua.source("place_bid"))
    raw = await place_bid(
        keys=[*keys, proxies_key(nowhere)], args=[str(nowhere), str(first), "10000", "60000"]
    )
    assert "NOT_INITIALISED" in raw
    assert await Closer(redis, sessions).close(lot) is Outcome.SETTLED

    watched = await asyncio.wait_for(watcher, 10)
    assert watched["settled"] is True
    assert len(watched["persistence_ms"]) == 2
    assert all(ms >= 0 for ms in watched["persistence_ms"])
    assert len(watched["arbitration_us"]) >= 1  # the place_bid execution above, by its SHA
    assert await redis.config_get("slowlog-*") == before


async def test_the_watcher_gives_up_at_its_deadline_and_still_puts_the_slow_log_back(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    lot = await ended(bids=[(await make_user(db), 10_000)])  # never settled here
    before = await redis.config_get("slowlog-*")
    watched = await watch(sessions, redis, redis, lot, time.time() + 0.3, 0.02)
    assert watched["settled"] is False
    assert len(watched["persistence_ms"]) == 1
    assert await redis.config_get("slowlog-*") == before


def write_run(
    run: Path, plan: dict[str, Any], acks: list[dict[str, Any]], users: list[str]
) -> None:
    """What loadtest/run.sh leaves in loadtest/.run, for a small run."""

    def k6(record: dict[str, Any]) -> str:
        return json.dumps({"level": "info", "msg": json.dumps(record), "source": "console"})

    final = len(acks)
    summaries = [
        {
            "e": "rx_summary",
            "vu": vu,
            "bidder": user,
            "subscribed": True,
            "from_seq": 0,
            "distinct": final,
            "duplicates": 0,
            "max_seq": final,
            "saw_closed": True,
            "final_seq": final,
            "missing": 0,
            "sent": 2,
            "accepted": sum(a["bidder"] == user for a in acks),
            "rejected": {},
            "timeouts": 0,
            "errors": {},
        }
        for vu, user in enumerate(users)
    ]
    (run / "plan.json").write_text(json.dumps(plan))
    (run / "events.log").write_text("\n".join(k6(r) for r in [*acks, *summaries]))
    (run / "summary.json").write_text(json.dumps({"metrics": {}}))
    (run / "watch.json").write_text(
        json.dumps(
            {
                "settled": True,
                "arbitration_us": [90, 120, 80],
                "slowlog_entries": 50,
                "persistence_ms": [150.0, 160.0, 140.0],
                "presence_peak": 2,
                "celery_waiting_peak": 1,
                "celery_in_flight_peak": 1,
            }
        )
    )
    log = [
        json.dumps({"msg": "ws authenticated", "conn_id": f"c{i}", "user_id": user})
        for i, user in enumerate(users)
    ] + [
        json.dumps({"msg": "ws subscribed", "conn_id": f"c{i}", "auction_id": plan["auction_id"]})
        for i in range(len(users))
    ]
    (run / "api.log").write_text("\n".join(log))
    (run / "api-2.log").write_text("")
    cited = [node for nodes in CITED.values() for node in nodes]
    (run / "pytest.txt").write_text("\n".join(f"PASSED {node}" for node in cited))
    (run / "host.json").write_text(
        json.dumps({"cpu": "Test CPU", "cores": 8, "threads": 16, "ram_bytes": 32 * 2**30})
    )
    (run / "docker-info.json").write_text(
        json.dumps({"ServerVersion": "28.0", "NCPU": 16, "MemTotal": 16 * 2**30})
    )
    (run / "k6-version.txt").write_text("k6 v2.3.0\n")


async def test_a_run_is_checked_in_postgres_and_reported_without_its_tokens(
    ended: Ended,
    db: asyncpg.Connection,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    first, second = await make_user(db), await make_user(db)
    bids = [(first, 10_000), (second, 10_500), (first, 11_000)]
    lot = await ended(bids=bids)
    assert await Closer(redis, sessions).close(lot) is Outcome.SETTLED
    now_ms = int(time.time() * 1000)
    tokens = ["first-bidder-token-never-published", "second-bidder-token-never-published"]
    users = [str(first), str(second)]
    plan = {
        "auction_id": str(lot),
        "start_ms": now_ms - 120_000,
        "end_ms": now_ms - 1_000,
        "soft_close_s": 10,
        "start_price": 10_000,
        "min_increment": 500,
        "minted_at_ms": now_ms,
        "expires_at_ms": now_ms + 900_000,
        "bidders": [{"id": u, "token": t} for u, t in zip(users, tokens, strict=True)],
    }
    acks = [
        {
            "e": "ack",
            "vu": 0,
            "seq": seq,
            "bidder": str(who),
            "amount": amount,
            "sent": 1,
            "acked": 2,
        }
        for seq, (who, amount) in enumerate(bids, 1)
    ]
    write_run(tmp_path, plan, acks, users)

    ev = await evidence(sessions, tmp_path)
    assert [c["passed"] for c in ev.checks] == [True] * 6, ev.checks
    assert ev.instances["api"]["subscribed_users"] == sorted(users)
    doc = document(ev, machine(tmp_path))
    rows = {r["id"]: r for r in doc["requirements"]}
    assert rows["NFR-28"]["pass"] is True
    assert rows["NFR-28"]["measured"].startswith("6/6 ledger checks passed on 3 bids")
    assert doc["run"]["ledger_bids"] == 3 and doc["run"]["bids_acknowledged"] == 3
    assert doc["machine"]["ram_gib"] == 32.0 and doc["machine"]["docker"]["memory_gib"] == 16.0
    published = json.dumps(doc) + markdown(doc)
    assert not any(token in published for token in tokens)
