"""identity: users, roles, sessions, verification and reset tokens

Revision ID: 0001
Revises:
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default="pending_verification", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending_verification', 'active', 'suspended')",
            name=op.f("ck_users_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    # case-insensitive email uniqueness
    op.create_index(
        "ix_users_lower_email", "users", [sa.literal_column("lower(email)")], unique=True
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "role IN ('bidder', 'seller', 'admin')", name=op.f("ck_user_roles_role")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_roles_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("user_id", "role", name=op.f("pk_user_roles")),
    )

    # server-side sessions make logout revoke immediately (FR-6)
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("refresh_token_hash", name=op.f("uq_sessions_refresh_token_hash")),
    )

    for table in ("email_verification_tokens", "password_reset_tokens"):
        op.create_table(
            table,
            sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("token_hash", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name=op.f(f"fk_{table}_user_id_users"),
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
            sa.UniqueConstraint("token_hash", name=op.f(f"uq_{table}_token_hash")),
        )


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
    op.drop_table("email_verification_tokens")
    op.drop_table("sessions")
    op.drop_table("user_roles")
    op.drop_index("ix_users_lower_email", table_name="users")
    op.drop_table("users")
