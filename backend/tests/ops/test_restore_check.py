"""OR-6's restore drill, the check half (app/restore_check.py), on the test database standing in
for the restored copy: every table's rows against the counts taken at the dump, the six ledger
checks of the latest verified stress run against the live database's and the ones recorded at the
run, and every image key against the restored and the live media. Anything off fails the drill,
named; the evidence page carries a machine-readable summary line for the load-test report."""

import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.loadtest import summary_line
from app.repositories.ops import OpsRepository
from app.restore_check import check, evidence_markdown, media_check
from app.services.closing import Closer, Outcome
from app.services.demo_checks import verify
from tests.closing.conftest import Ended
from tests.db.conftest import make_user


@pytest.fixture
async def owner_sessions(test_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(make_url(settings.migration_database_url).set(database=test_db))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def verified_run(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> UUID:
    """A settled stress lot, its bots' acknowledgements and its checks, recorded as verified."""
    first, second = await make_user(db), await make_user(db)
    bids = [(first, 10_000), (second, 10_500), (first, 11_000)]
    lot = await ended(bids=bids)
    await db.execute("UPDATE auctions SET is_demo = true WHERE id = $1", lot)
    assert await Closer(redis, sessions).close(lot) is Outcome.SETTLED
    await db.execute(  # its photo, so the drill has an image to restore
        "INSERT INTO auction_images (auction_id, object_key, position) VALUES ($1, $2, 0)",
        lot,
        f"auctions/{lot}/photo.jpg",
    )
    acks = [[seq, str(who), amount] for seq, (who, amount) in enumerate(bids, 1)]
    checks = await verify(sessions, lot, [tuple(a) for a in acks])  # type: ignore[misc]
    await db.execute(
        "DELETE FROM demo_runs WHERE state IN ('requested', 'starting', 'running', 'settling')"
    )
    await db.execute(
        "INSERT INTO demo_runs (kind, state, auction_id, finished_at, acks, checks) "
        "VALUES ('stress', 'verified', $1, now() + interval '1 day', $2::jsonb, $3::jsonb)",
        lot,
        json.dumps(acks),
        json.dumps(checks),
    )
    return lot


async def _media(owner_sessions: async_sessionmaker[AsyncSession], *dirs: Path) -> list[str]:
    """Every image the database names, written into each folder with the same bytes."""
    async with owner_sessions() as session:
        keys = await OpsRepository(session).image_keys()
    for key in keys:
        for d in dirs:
            path = d / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(hashlib.sha256(key.encode()).digest())
    return keys


async def test_a_faithful_restore_passes_every_part(
    verified_run: UUID,
    owner_sessions: async_sessionmaker[AsyncSession],
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    restored, live = tmp_path / "restored", tmp_path / "live"
    await _media(owner_sessions, restored, live)
    async with owner_sessions() as session:
        counts = await OpsRepository(session).table_counts()
    result = await check(owner_sessions, sessions, sessions, counts, restored, live)
    assert result["result"] == "pass", result
    assert result["tables"]["problems"] == [] and "bids" in result["tables"]["restored"]
    run = result["run"]
    assert run["auction_id"] == str(verified_run)
    assert [c["id"] for c in run["restored"]] == [
        "gapless",
        "distinct",
        "winner",
        "one_settlement",
        "closed_once",
        "acked",
    ]
    assert all(c["passed"] for c in run["restored"])
    assert run["same_as_live"] and run["same_as_recorded"]
    assert result["media"]["problems"] == []

    page = evidence_markdown(result, "2026-10-19", {"dump": 1.5, "restore": 2.0})
    summary = summary_line(page, "restore-drill")
    assert summary is not None
    assert (
        summary["result"] == "pass"
        and summary["checks"] == "6/6"
        and summary["date"] == "2026-10-19"
    )
    assert (
        int(summary["tables"]) == len(counts) and int(summary["media"]) == result["media"]["files"]
    )
    assert "@" not in page  # no email address, no token: the page is published


async def test_a_row_count_that_differs_fails_the_drill_and_says_where(
    verified_run: UUID,
    owner_sessions: async_sessionmaker[AsyncSession],
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    restored, live = tmp_path / "restored", tmp_path / "live"
    await _media(owner_sessions, restored, live)
    async with owner_sessions() as session:
        counts = await OpsRepository(session).table_counts()
    counts["bids"] += 1  # the dump held one more bid than came back
    result = await check(owner_sessions, sessions, sessions, counts, restored, live)
    assert result["result"] == "fail"
    assert result["tables"]["problems"] == [
        f"bids: {counts['bids']} before the dump, {counts['bids'] - 1} restored"
    ]


def test_an_image_missing_or_changed_fails_the_media_part(tmp_path: Path) -> None:
    restored, live = tmp_path / "restored", tmp_path / "live"
    for d in (restored, live):
        (d / "auctions" / "a").mkdir(parents=True)
    (live / "auctions/a/1.jpg").write_bytes(b"one")
    (restored / "auctions/a/1.jpg").write_bytes(b"one")
    (live / "auctions/a/2.jpg").write_bytes(b"two")
    (restored / "auctions/a/2.jpg").write_bytes(b"TWO")  # changed
    (live / "auctions/a/3.jpg").write_bytes(b"three")  # not restored
    media = media_check(
        ["auctions/a/1.jpg", "auctions/a/2.jpg", "auctions/a/3.jpg"], restored, live
    )
    assert (media["files"], media["matched"]) == (3, 1)
    assert media["problems"] == [
        "auctions/a/2.jpg: not the live file's bytes",
        "auctions/a/3.jpg: missing from the restored media",
    ]
    assert len(media["digest"]) == 64
