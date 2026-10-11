"""catalog and auctions: categories, auctions, auction_images, original_end_time trigger

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('simple', coalesce(description, '')), 'B')"
)


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["categories.id"],
            name=op.f("fk_categories_parent_id_categories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("slug", name=op.f("uq_categories_slug")),
    )
    op.create_index(op.f("ix_categories_parent_id"), "categories", ["parent_id"])

    op.create_table(
        "auctions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("seller_id", sa.UUID(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        # money is integer paise (DC-6, OR-1)
        sa.Column("starting_price", sa.BigInteger(), nullable=False),
        sa.Column("reserve_price", sa.BigInteger(), nullable=True),
        sa.Column("min_increment", sa.BigInteger(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("antisnipe_threshold_s", sa.Integer(), server_default="60", nullable=False),
        sa.Column("antisnipe_extension_s", sa.Integer(), server_default="120", nullable=False),
        sa.Column("extension_count", sa.Integer(), server_default="0", nullable=False),
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
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_VECTOR_SQL, persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'scheduled', 'live', 'closing', "
            "'closed_sold', 'closed_no_sale', 'cancelled')",
            name=op.f("ck_auctions_status"),
        ),
        sa.CheckConstraint("end_time > start_time", name=op.f("ck_auctions_end_after_start")),
        sa.CheckConstraint(
            "extension_count BETWEEN 0 AND 10", name=op.f("ck_auctions_extension_count_range")
        ),
        sa.CheckConstraint("min_increment > 0", name=op.f("ck_auctions_min_increment_pos")),
        sa.CheckConstraint(
            "reserve_price IS NULL OR reserve_price >= starting_price",
            name=op.f("ck_auctions_reserve_ge_start"),
        ),
        sa.CheckConstraint("starting_price >= 0", name=op.f("ck_auctions_starting_price_nonneg")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_auctions_category_id_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["seller_id"],
            ["users.id"],
            name=op.f("fk_auctions_seller_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auctions")),
    )
    op.create_index(op.f("ix_auctions_category_id"), "auctions", ["category_id"])
    op.create_index(op.f("ix_auctions_seller_id"), "auctions", ["seller_id"])
    # scheduler: auctions due to start or close (OR-5)
    op.create_index("ix_auctions_status_start_time", "auctions", ["status", "start_time"])
    op.create_index("ix_auctions_status_end_time", "auctions", ["status", "end_time"])
    # full-text search over title and description (OR-5)
    op.create_index(
        "ix_auctions_search_vector", "auctions", ["search_vector"], postgresql_using="gin"
    )

    # original_end_time is set once at creation and never modified (FR-14, FR-55)
    op.execute(
        """
        CREATE FUNCTION auctions_original_end_time_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.original_end_time IS DISTINCT FROM OLD.original_end_time THEN
                RAISE EXCEPTION 'auctions.original_end_time is immutable (FR-14)'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_auctions_original_end_time_immutable
        BEFORE UPDATE OF original_end_time ON auctions
        FOR EACH ROW EXECUTE FUNCTION auctions_original_end_time_immutable()
        """
    )

    op.create_table(
        "auction_images",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("auction_id", sa.UUID(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["auction_id"],
            ["auctions.id"],
            name=op.f("fk_auction_images_auction_id_auctions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auction_images")),
    )
    op.create_index(op.f("ix_auction_images_auction_id"), "auction_images", ["auction_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_auction_images_auction_id"), table_name="auction_images")
    op.drop_table("auction_images")
    op.execute("DROP TRIGGER trg_auctions_original_end_time_immutable ON auctions")
    op.execute("DROP FUNCTION auctions_original_end_time_immutable()")
    op.drop_index("ix_auctions_search_vector", table_name="auctions")
    op.drop_index("ix_auctions_status_end_time", table_name="auctions")
    op.drop_index("ix_auctions_status_start_time", table_name="auctions")
    op.drop_index(op.f("ix_auctions_seller_id"), table_name="auctions")
    op.drop_index(op.f("ix_auctions_category_id"), table_name="auctions")
    op.drop_table("auctions")
    op.drop_index(op.f("ix_categories_parent_id"), table_name="categories")
    op.drop_table("categories")
