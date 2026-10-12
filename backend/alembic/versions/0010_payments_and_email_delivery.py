"""payments: Razorpay order and payment references, reconcile bookkeeping, processed gateway
events (FR-65 to FR-69, DC-5, NFR-25); notifications: email delivery state and the
payment_failed type (FR-68, FR-72)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = (
    "outbid",
    "ending_soon",
    "auction_won",
    "auction_lost",
    "payment_due",
    "payment_received",
    "auction_sold",
    "auction_no_sale",
)
NEW_TYPES = (*OLD_TYPES, "payment_failed")
TYPE_TABLES = ("notifications", "notification_preferences")


def _types(types: tuple[str, ...]) -> None:
    # fixed identifiers from this file, never user input
    for table in TYPE_TABLES:
        op.drop_constraint(op.f(f"ck_{table}_type"), table, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table}_type"), table, "type IN (" + ", ".join(f"'{t}'" for t in types) + ")"
        )


def _grant_append_only(table: str) -> None:
    """Every new table grants explicitly (0005); payment_events is append-only (DC-4)."""
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
                EXECUTE format('GRANT SELECT, INSERT ON {table} TO %I', r);
                EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO %I', r);
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    # one row per Razorpay order; gateway_payment_id holds the captured payment (FR-66, FR-67)
    op.alter_column("payments", "gateway_ref", new_column_name="gateway_payment_id")
    op.execute(
        "ALTER TABLE payments RENAME CONSTRAINT uq_payments_gateway_ref "
        "TO uq_payments_gateway_payment_id"
    )
    # no code wrote payments before 0010, so the table is empty and NOT NULL needs no default
    op.add_column("payments", sa.Column("gateway_order_id", sa.String(64), nullable=False))
    op.create_unique_constraint(
        op.f("uq_payments_gateway_order_id"), "payments", ["gateway_order_id"]
    )
    # the reconciler checks the least recently checked orders first (FR-69)
    op.add_column("payments", sa.Column("reconciled_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(op.f("ck_payments_amount_pos"), "payments", "amount > 0")

    # every applied confirmation, once (DC-5, FR-67): webhook:{event id} |
    # callback:{payment id} | reconcile:{payment id}:{status}
    op.create_table(
        "payment_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=True),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("gateway_payment_id", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('callback', 'webhook', 'reconcile')", name=op.f("ck_payment_events_source")
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed')", name=op.f("ck_payment_events_outcome")
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name=op.f("fk_payment_events_payment_id_payments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_events")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_payment_events_dedupe_key")),
    )
    op.create_index(op.f("ix_payment_events_payment_id"), "payment_events", ["payment_id"])
    _grant_append_only("payment_events")

    # email delivery state (FR-72): claimed with a lease, sent at least once per group
    op.add_column(
        "notifications",
        sa.Column("email_status", sa.String(16), server_default="pending", nullable=False),
    )
    op.add_column(
        "notifications",
        sa.Column("email_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("notifications", sa.Column("email_next_at", sa.DateTime(timezone=True)))
    op.add_column("notifications", sa.Column("emailed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        op.f("ck_notifications_email_status"),
        "notifications",
        "email_status IN ('pending', 'sent', 'skipped', 'failed')",
    )
    # rows written before email delivery existed are never emailed
    op.execute("UPDATE notifications SET email_status = 'skipped'")
    op.create_index(
        "ix_notifications_email_pending",
        "notifications",
        ["user_id", "type", "auction_id", "created_at"],
        postgresql_where=sa.text("email_status = 'pending'"),
    )
    _types(NEW_TYPES)


def downgrade() -> None:
    # refuses (check violation) while payment_failed rows exist: no silent data loss
    _types(OLD_TYPES)
    op.drop_index("ix_notifications_email_pending", table_name="notifications")
    op.drop_constraint(op.f("ck_notifications_email_status"), "notifications", type_="check")
    for column in ("emailed_at", "email_next_at", "email_attempts", "email_status"):
        op.drop_column("notifications", column)

    op.drop_index(op.f("ix_payment_events_payment_id"), table_name="payment_events")
    op.drop_table("payment_events")

    op.drop_constraint(op.f("ck_payments_amount_pos"), "payments", type_="check")
    op.drop_column("payments", "reconciled_at")
    op.drop_constraint(op.f("uq_payments_gateway_order_id"), "payments", type_="unique")
    op.drop_column("payments", "gateway_order_id")
    op.execute(
        "ALTER TABLE payments RENAME CONSTRAINT uq_payments_gateway_payment_id "
        "TO uq_payments_gateway_ref"
    )
    op.alter_column("payments", "gateway_payment_id", new_column_name="gateway_ref")
