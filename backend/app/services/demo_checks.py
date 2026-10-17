"""The ledger checks of "Try to break it" (brief 8.3, 12.4; B2 Q7), computed in Postgres after the
lot has closed, in one REPEATABLE READ, READ ONLY transaction, so every check sees the same
state. Each result keeps the SQL that produced it, for "How we checked". A failed check is
reported exactly as a pass is, with what it found, and the failing ones are listed first.

Two checks cannot fail while the schema stands, and the result says so rather than hiding it:
UNIQUE (auction_id, sequence_no) rules out a duplicate number, and UNIQUE (auction_id) on
settlements rules out a second settlement (a missing one can still be found). The check that can
fail in practice is the last: every bid the bots were told was accepted must be in the ledger
(NFR-28: zero lost accepted bids), which a Redis crash losing its last second of writes would break.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.demo import CHECK_SQL, MISSING_SQL, LedgerCheckRepository
from app.services.live_events import alias

Rows = dict[str, list[dict[str, Any]]]

LABELS = {
    "gapless": "Sequence numbers run from 1 to N with no gaps",
    "distinct": "N bids carry N distinct sequence numbers: no duplicates",
    "winner": "One winner: the highest bid, the earlier one on a tie",
    "one_settlement": "One settlement",
    "closed_once": "Closed once",
    "acked": "Every bid acknowledged as accepted is in the ledger",
}
ALSO_GUARANTEED = {
    "distinct": "The schema also guarantees this: UNIQUE (auction_id, sequence_no) on bids.",
    "one_settlement": (
        "The schema rules out a second settlement, UNIQUE (auction_id) on settlements; "
        "this check proves the one settlement was written."
    ),
}


async def verify(
    sessions: async_sessionmaker[AsyncSession], auction_id: UUID, acks: list[tuple[int, str, int]]
) -> list[dict[str, Any]]:
    async with sessions() as session:
        checks = LedgerCheckRepository(session)
        await checks.consistent_snapshot()
        rows = await checks.rows(auction_id, acks)
        await session.rollback()  # read only: nothing to keep
    return evaluate(auction_id, rows)


def evaluate(auction_id: UUID, rows: Rows) -> list[dict[str, Any]]:
    """The results from the checks' rows: failing first, otherwise in the order above."""

    def name(bidder: Any) -> str | None:  # the pseudonym the page shows (NFR-27)
        return alias(auction_id, bidder) if bidder is not None else None

    gap = rows["gapless"][0]
    n, lowest, highest = gap["n"], gap["lowest"], gap["highest"]
    distinct = rows["distinct"][0]
    settlements = rows["winner"]
    sale = settlements[0] if len(settlements) == 1 else None
    winner_ok = sale is not None and (
        (sale["winner_id"], sale["final_price"])
        == (sale["expected_winner_id"], sale["expected_price"])
    )
    acked = rows["acked"][0]
    results = [
        _result(
            "gapless",
            n > 0 and lowest == 1 and highest == n,
            {
                "n": n,
                "lowest": lowest,
                "highest": highest,
                "missing": [r["missing"] for r in rows["missing"]],
            },
            "at least one bid, the lowest number 1 and the highest equal to the count",
            CHECK_SQL["gapless"] + ";\n\n" + MISSING_SQL,
        ),
        _result(
            "distinct",
            distinct["distinct_n"] == distinct["n"],
            {"n": distinct["n"], "distinct": distinct["distinct_n"]},
            "as many distinct numbers as bids",
        ),
        _result(
            "winner",
            winner_ok,
            {
                "winner": name(sale["winner_id"]) if sale else None,
                "final_price": sale["final_price"] if sale else None,
                "sequence_no": sale["sequence_no"] if sale else None,
                "expected_winner": name(sale["expected_winner_id"]) if sale else None,
                "expected_price": sale["expected_price"] if sale else None,
            },
            "one settlement whose winner and price are the ledger's highest bid",
        ),
        _result(
            "one_settlement",
            rows["one_settlement"][0]["settlements"] == 1,
            {"settlements": rows["one_settlement"][0]["settlements"]},
            "exactly 1",
        ),
        _result(
            "closed_once",
            rows["closed_once"][0]["closes"] == 1,
            {"closes": rows["closed_once"][0]["closes"]},
            "exactly 1 closing-to-closed transition in the audit log",
        ),
        _result(
            "acked",
            acked["missing"] == 0,
            {"acknowledged": acked["acknowledged"], "missing": acked["missing"]},
            "0 missing",
        ),
    ]
    return sorted(results, key=lambda r: r["passed"])  # stable: failing first, order kept


def _result(
    check: str, passed: bool, value: dict[str, Any], expected: str, sql: str | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": check,
        "label": LABELS[check],
        "passed": passed,
        "value": value,
        "expected": expected,
        "sql": sql or CHECK_SQL[check],
    }
    if check in ALSO_GUARANTEED:
        result["note"] = ALSO_GUARANTEED[check]
    return result
