from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.models.demo import ACTIVE_STATES, ACTIVE_STATES_SQL, DemoRun
from app.repositories.base import BaseRepository

RUNNER_LOCK_KEY = 0x44454D4F  # "DEMO": the one demo runner's advisory lock


class DemoRunRepository(BaseRepository):
    async def latest_verified_auction(self, kind: str) -> UUID | None:
        """The lot of the most recent run whose checks all passed: what the hero replays."""
        return await self.session.scalar(
            select(DemoRun.auction_id)
            .where(DemoRun.kind == kind, DemoRun.state == "verified")
            .order_by(DemoRun.finished_at.desc())
            .limit(1)
        )

    async def active(self, kind: str) -> DemoRun | None:
        stmt = select(DemoRun).where(DemoRun.kind == kind, DemoRun.state.in_(ACTIVE_STATES))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def requested_today(self, kind: str) -> int:
        """Runs requested in the last 24 hours, whatever became of them (the daily cap)."""
        since = func.now() - timedelta(days=1)
        stmt = select(func.count()).where(DemoRun.kind == kind, DemoRun.requested_at > since)
        return int(await self.session.scalar(stmt) or 0)

    async def last_finished(self, kind: str) -> tuple[datetime | None, datetime]:
        """(when the latest run of this kind finished, the database's now), one clock for every
        API instance."""
        stmt = select(func.max(DemoRun.finished_at), func.now()).where(DemoRun.kind == kind)
        last, now = (await self.session.execute(stmt)).one()
        return last, now

    async def request(self, kind: str) -> UUID | None:
        """A new run, or None if one is already active: the partial unique index decides, so
        two starts racing on two instances create one run."""
        stmt = (
            insert(DemoRun)
            .values(kind=kind, state="requested")
            .on_conflict_do_nothing(index_elements=["kind"], index_where=text(ACTIVE_STATES_SQL))
            .returning(DemoRun.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def try_runner_lock(self) -> bool:
        """A session-level advisory lock: held while this connection stays open."""
        return bool(await self.session.scalar(select(func.pg_try_advisory_lock(RUNNER_LOCK_KEY))))

    async def abort_unfinished(self, reason: str) -> list[UUID]:
        """Every run still active: a new runner cannot finish what another one started."""
        result = await self.session.execute(
            update(DemoRun)
            .where(DemoRun.state.in_(ACTIVE_STATES))
            .values(
                state="aborted",
                reason=reason,
                finished_at=func.now(),
                version=DemoRun.version + 1,
            )
            .returning(DemoRun.id)
        )
        return list(result.scalars())


# The ledger checks of "Try to break it" (brief 8.3, 12.4), exactly as run and as shown under
# "How we checked". :auction_id is the uuid and :entity_id the same id as text (audit rows key on
# text): one bind is never used as both types.
CHECK_SQL = {
    "gapless": """SELECT count(*) AS n, min(sequence_no) AS lowest, max(sequence_no) AS highest
FROM bids
WHERE auction_id = :auction_id""",
    "distinct": """SELECT count(*) AS n, count(DISTINCT sequence_no) AS distinct_n
FROM bids
WHERE auction_id = :auction_id""",
    "winner": """SELECT s.winner_id, s.final_price,
       top.bidder_id AS expected_winner_id, top.amount AS expected_price, top.sequence_no
FROM settlements AS s
LEFT JOIN LATERAL (
  SELECT bidder_id, amount, sequence_no
  FROM bids
  WHERE auction_id = s.auction_id
  ORDER BY amount DESC, sequence_no ASC
  LIMIT 1
) AS top ON true
WHERE s.auction_id = :auction_id""",
    "one_settlement": """SELECT count(*) AS settlements
FROM settlements
WHERE auction_id = :auction_id""",
    "closed_once": """SELECT count(*) AS closes
FROM audit_log
WHERE entity_type = 'auction'
  AND entity_id = :entity_id
  AND action = 'status_changed'
  AND before ->> 'status' = 'closing'
  AND after ->> 'status' IN ('closed_sold', 'closed_no_sale')""",
    "acked": """SELECT cardinality(CAST(:seqs AS bigint[])) AS acknowledged, count(*) AS missing
FROM unnest(CAST(:seqs AS bigint[]), CAST(:bidders AS uuid[]), CAST(:amounts AS bigint[]))
     AS ack(sequence_no, bidder_id, amount)
WHERE NOT EXISTS (
  SELECT 1 FROM bids AS b
  WHERE b.auction_id = :auction_id AND b.sequence_no = ack.sequence_no
    AND b.bidder_id = ack.bidder_id AND b.amount = ack.amount
)""",
}
# shown with the gap check when it fails: which numbers are missing
MISSING_SQL = """SELECT s AS missing
FROM generate_series(1, (SELECT max(sequence_no) FROM bids WHERE auction_id = :auction_id)) AS s
WHERE NOT EXISTS (SELECT 1 FROM bids WHERE auction_id = :auction_id AND sequence_no = s)
ORDER BY s
LIMIT 20"""


class LedgerCheckRepository(BaseRepository):
    async def consistent_snapshot(self) -> None:
        """Every check after this sees one state of the database: the first statement of the
        transaction (Postgres refuses it later)."""
        await self.session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )

    async def rows(
        self, auction_id: UUID, acks: list[tuple[int, str, int]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Each check's rows, plus the missing numbers; all bound parameters (NFR-20)."""
        params: dict[str, Any] = {
            "auction_id": auction_id,
            "entity_id": str(auction_id),
            "seqs": [a[0] for a in acks],
            "bidders": [UUID(a[1]) for a in acks],
            "amounts": [a[2] for a in acks],
        }
        found: dict[str, list[dict[str, Any]]] = {}
        for check, sql in {**CHECK_SQL, "missing": MISSING_SQL}.items():
            result = await self.session.execute(text(sql), params)
            found[check] = [dict(row) for row in result.mappings()]
        return found
