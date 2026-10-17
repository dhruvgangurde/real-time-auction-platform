"""The ledger checks of "Try to break it" (brief 8.3, 12.4; B2 Q7), computed in Postgres after the
close. A clean run passes all six. Each deliberate corruption makes its check fail, reported as
plainly as a pass and listed first: the corruptions are made inside an owner transaction (the only
role that can make the ledger lie), the same queries and evaluation run on it, and it is rolled
back, so nothing stays corrupted."""

from collections.abc import AsyncIterator
from uuid import UUID

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.repositories.demo import LedgerCheckRepository
from app.services.closing import Closer, Outcome
from app.services.demo_checks import evaluate, verify
from app.services.live_events import alias
from tests.closing.conftest import Ended
from tests.db.conftest import make_user

CHECKS = ["gapless", "distinct", "winner", "one_settlement", "closed_once", "acked"]
Acks = list[tuple[int, str, int]]


@pytest.fixture
async def owner_sessions(test_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = make_url(settings.migration_database_url).set(database=test_db)
    engine = create_async_engine(url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def settled(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> tuple[UUID, Acks, UUID]:
    """A closed demo lot with three bids, its settlement and the acknowledgements the bots got."""
    first, second = await make_user(db), await make_user(db)
    bids = [(first, 10_000), (second, 10_500), (first, 11_000)]
    lot = await ended(bids=bids)
    await db.execute("UPDATE auctions SET is_demo = true WHERE id = $1", lot)
    assert await Closer(redis, sessions).close(lot) is Outcome.SETTLED
    acks = [(seq, str(who), amount) for seq, (who, amount) in enumerate(bids, 1)]
    return lot, acks, first


async def test_a_clean_run_passes_every_check(
    settled: tuple[UUID, Acks, UUID], sessions: async_sessionmaker[AsyncSession]
) -> None:
    lot, acks, winner = settled
    results = await verify(sessions, lot, acks)
    assert [r["id"] for r in results] == CHECKS
    assert all(r["passed"] for r in results), results
    by_id = {r["id"]: r for r in results}
    assert by_id["gapless"]["value"] == {"n": 3, "lowest": 1, "highest": 3, "missing": []}
    assert by_id["winner"]["value"] == {
        "winner": alias(lot, winner),
        "final_price": 11_000,
        "sequence_no": 3,
        "expected_winner": alias(lot, winner),
        "expected_price": 11_000,
    }
    assert by_id["acked"]["value"] == {"acknowledged": 3, "missing": 0}
    assert all(r["sql"].lstrip().startswith("SELECT") for r in results)  # "How we checked"


CORRUPTIONS = {
    "a bid deleted (sequence 2)": (
        ["DELETE FROM bids WHERE auction_id = :lot AND sequence_no = 2"],
        {"gapless", "acked"},
    ),
    "the settlement's price changed": (
        ["UPDATE settlements SET final_price = final_price - 500 WHERE auction_id = :lot"],
        {"winner"},
    ),
    "the settlement deleted": (
        ["DELETE FROM settlements WHERE auction_id = :lot"],
        {"one_settlement", "winner"},
    ),
    "closed twice in the audit log": (
        [
            "INSERT INTO audit_log (entity_type, entity_id, action, before, after) VALUES "
            "('auction', :lot_text, 'status_changed', '{\"status\": \"closing\"}', "
            '\'{"status": "closed_sold"}\')'
        ],
        {"closed_once"},
    ),
    "a sequence number duplicated (unique constraint dropped)": (
        [
            "ALTER TABLE bids DROP CONSTRAINT uq_bids_auction_id_sequence_no",
            "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type) "
            "SELECT auction_id, bidder_id, amount, sequence_no, bid_type FROM bids "
            "WHERE auction_id = :lot AND sequence_no = 2",
        ],
        {"distinct", "gapless"},  # four rows numbered 1 to 3: both the count and the set disagree
    ),
}


@pytest.mark.parametrize("corruption", list(CORRUPTIONS))
async def test_each_corruption_is_reported_as_plainly_as_a_pass(
    settled: tuple[UUID, Acks, UUID],
    owner_sessions: async_sessionmaker[AsyncSession],
    corruption: str,
) -> None:
    lot, acks, _ = settled
    statements, failing = CORRUPTIONS[corruption]
    async with owner_sessions() as session:
        try:
            for statement in statements:
                await session.execute(text(statement), {"lot": lot, "lot_text": str(lot)})
            rows = await LedgerCheckRepository(session).rows(lot, acks)
        finally:
            await session.rollback()  # nothing stays corrupted
    results = evaluate(lot, rows)
    assert {r["id"] for r in results if not r["passed"]} == failing
    assert [r["passed"] for r in results] == [False] * len(failing) + [True] * (6 - len(failing))
    for result in results[: len(failing)]:  # the failing checks first, with what they found
        assert result["value"] and result["sql"]


async def test_the_app_role_cannot_make_the_ledger_skip_or_repeat_a_number(
    settled: tuple[UUID, Acks, UUID], sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The two corruptions that would fake a gap or a duplicate need the owner: the application's
    role may neither delete a bid (append-only grants, DC-4) nor drop the unique constraint."""
    lot, _, _ = settled
    for statement in (
        "DELETE FROM bids WHERE auction_id = :lot AND sequence_no = 2",
        "UPDATE bids SET sequence_no = 9 WHERE auction_id = :lot AND sequence_no = 2",
        "ALTER TABLE bids DROP CONSTRAINT uq_bids_auction_id_sequence_no",
    ):
        async with sessions() as session:
            with pytest.raises(ProgrammingError) as info:
                await session.execute(text(statement), {"lot": lot})
            assert "permission denied" in str(info.value) or "must be owner" in str(info.value)
