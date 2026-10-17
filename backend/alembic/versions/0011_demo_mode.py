"""demo mode (brief 12.3, 12.4): demo flags on users and auctions, the demo settlement status
(a demo lot's sale is never payable, B2 Q1) and demo_runs with one active run per kind

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = ("pending_payment", "paid", "no_sale")
NEW_STATUSES = (*OLD_STATUSES, "demo")


def _settlement_statuses(statuses: tuple[str, ...]) -> None:
    # fixed identifiers from this file, never user input
    op.drop_constraint(op.f("ck_settlements_status"), "settlements", type_="check")
    op.create_check_constraint(
        op.f("ck_settlements_status"),
        "settlements",
        "status IN (" + ", ".join(f"'{s}'" for s in statuses) + ")",
    )


def _grant_read_write(table: str) -> None:
    """Every new table grants explicitly (0005). Runs move through states: no DELETE."""
    op.execute(
        sa.text("SELECT set_config('auction.app_user', :u, false)").bindparams(
            u=settings.app_db_user
        )
    )
    op.execute(
        f"""
        DO $$
        DECLARE r text := current_setting('auction.app_user');
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('GRANT SELECT, INSERT, UPDATE ON {table} TO %I', r);
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    for table in ("users", "auctions"):
        op.add_column(
            table,
            sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        )
    _settlement_statuses(NEW_STATUSES)

    op.create_table(
        "demo_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("auction_id", sa.UUID(), nullable=True),
        sa.Column("state", sa.String(16), server_default="requested", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "counters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("acks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("kind IN ('stress', 'hero')", name=op.f("ck_demo_runs_kind")),
        sa.CheckConstraint(
            "state IN ('requested', 'starting', 'running', 'settling', "
            "'verified', 'failed', 'aborted')",
            name=op.f("ck_demo_runs_state"),
        ),
        sa.ForeignKeyConstraint(
            ["auction_id"],
            ["auctions.id"],
            name=op.f("fk_demo_runs_auction_id_auctions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_runs")),
        sa.UniqueConstraint("auction_id", name=op.f("uq_demo_runs_auction_id")),
    )
    # one global run at a time per kind (brief 12.4), whichever API instance takes the request
    op.create_index(
        "uq_demo_runs_active_kind",
        "demo_runs",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("state IN ('requested', 'starting', 'running', 'settling')"),
    )
    _grant_read_write("demo_runs")


def downgrade() -> None:
    op.drop_index("uq_demo_runs_active_kind", table_name="demo_runs")
    op.drop_table("demo_runs")
    # refuses (check violation) while demo settlements exist: no silent change to a payable one
    _settlement_statuses(OLD_STATUSES)
    for table in ("auctions", "users"):
        op.drop_column(table, "is_demo")
