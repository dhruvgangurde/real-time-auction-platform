"""settlement, payments, notifications, audit log

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOTIFICATION_TYPE_CHECK = (
    "type IN ('outbid', 'ending_soon', 'auction_won', 'auction_lost', "
    "'payment_due', 'payment_received')"
)


def upgrade() -> None:
    op.create_table(
        "settlements",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("auction_id", sa.UUID(), nullable=False),
        sa.Column("winner_id", sa.UUID(), nullable=True),
        sa.Column("final_price", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        # winner and price are unset exactly when there is no sale (FR-61)
        sa.CheckConstraint(
            "(status = 'no_sale' AND winner_id IS NULL AND final_price IS NULL) OR "
            "(status <> 'no_sale' AND winner_id IS NOT NULL AND final_price IS NOT NULL)",
            name=op.f("ck_settlements_winner_matches_status"),
        ),
        sa.CheckConstraint(
            "status IN ('pending_payment', 'paid', 'no_sale')", name=op.f("ck_settlements_status")
        ),
        sa.ForeignKeyConstraint(
            ["auction_id"],
            ["auctions.id"],
            name=op.f("fk_settlements_auction_id_auctions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["winner_id"],
            ["users.id"],
            name=op.f("fk_settlements_winner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlements")),
        # at most one settlement per auction, backstop for the close lock (FR-61, OR-3)
        sa.UniqueConstraint("auction_id", name=op.f("uq_settlements_auction_id")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_settlements_idempotency_key")),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("settlement_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("gateway_ref", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('created', 'succeeded', 'failed')", name=op.f("ck_payments_status")
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.id"],
            name=op.f("fk_payments_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        # a redelivered callback cannot create a second record (FR-67, OR-3)
        sa.UniqueConstraint("gateway_ref", name=op.f("uq_payments_gateway_ref")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_payments_idempotency_key")),
    )
    # failed attempts may be retried, but at most one succeeded payment per settlement
    op.create_index(
        "uq_payments_settlement_succeeded",
        "payments",
        ["settlement_id"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("auction_id", sa.UUID(), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(NOTIFICATION_TYPE_CHECK, name=op.f("ck_notifications_type")),
        sa.ForeignKeyConstraint(
            ["auction_id"],
            ["auctions.id"],
            name=op.f("fk_notifications_auction_id_auctions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        # idempotent dispatch (DC-5)
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_notifications_dedupe_key")),
    )
    # unread notifications per user (OR-5)
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id", sa.literal_column("created_at DESC")],
        postgresql_where=sa.text("read_at IS NULL"),
    )

    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(NOTIFICATION_TYPE_CHECK, name=op.f("ck_notification_preferences_type")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_preferences_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", "type", name=op.f("pk_notification_preferences")),
    )

    # append-only; the app role gets no UPDATE/DELETE in 0005 (FR-79, DC-4)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_log_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    # audit query by entity, actor and date (OR-5, FR-80)
    op.create_index("ix_audit_log_entity_type_entity_id", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_audit_log_actor_id_created_at", "audit_log", ["actor_id", "created_at"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_id_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_entity_type_entity_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("notification_preferences")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("uq_payments_settlement_succeeded", table_name="payments")
    op.drop_table("payments")
    op.drop_table("settlements")
