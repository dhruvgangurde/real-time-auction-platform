import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ACTIVE_STATES = ("requested", "starting", "running", "settling")
ACTIVE_STATES_SQL = "state IN ('requested', 'starting', 'running', 'settling')"


class DemoRun(Base):
    """One demo run (brief 12.4). The partial unique index allows one active run per kind, in
    Postgres, so it holds across both API instances and a Redis loss."""

    __tablename__ = "demo_runs"
    __table_args__ = (
        # hero: reserved for the deferred hero loop (B2 Q9)
        CheckConstraint("kind IN ('stress', 'hero')", name="kind"),
        CheckConstraint(
            "state IN ('requested', 'starting', 'running', 'settling', "
            "'verified', 'failed', 'aborted')",
            name="state",
        ),
        Index(
            "uq_demo_runs_active_kind",
            "kind",
            unique=True,
            postgresql_where=text(ACTIVE_STATES_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    auction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auctions.id", ondelete="RESTRICT"), unique=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="requested")
    reason: Mapped[str | None] = mapped_column(Text)
    # raised on every state write, so a client ignores an older poll from the other instance
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    checks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # the bid_accepted frames the bots received: [[sequence_no, bidder_id, amount], ...]
    acks: Mapped[list[list[Any]] | None] = mapped_column(JSONB)
