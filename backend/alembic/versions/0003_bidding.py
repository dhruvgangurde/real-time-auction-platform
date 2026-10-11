"""bidding: bids ledger, proxy_bids, watchlist

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # append-only ledger; the app role gets no UPDATE/DELETE in 0005 (FR-41, DC-4)
    op.create_table(
        "bids",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("auction_id", sa.UUID(), nullable=False),
        sa.Column("bidder_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("bid_type", sa.String(length=16), nullable=False),
        # end time after this bid when soft close extended the auction (FR-55)
        sa.Column("end_time_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("bid_type IN ('manual', 'automatic')", name=op.f("ck_bids_bid_type")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_bids_amount_pos")),
        sa.ForeignKeyConstraint(
            ["auction_id"],
            ["auctions.id"],
            name=op.f("fk_bids_auction_id_auctions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bidder_id"], ["users.id"], name=op.f("fk_bids_bidder_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bids")),
        # one ledger row per arbitration sequence number (FR-41, OR-3)
        sa.UniqueConstraint(
            "auction_id", "sequence_no", name=op.f("uq_bids_auction_id_sequence_no")
        ),
    )
    # paginated history per auction, and a user's bids across auctions (OR-5)
    op.create_index(
        "ix_bids_auction_id_sequence_no_desc",
        "bids",
        ["auction_id", sa.literal_column("sequence_no DESC")],
    )
    op.create_index(
        "ix_bids_bidder_id_created_at_desc",
        "bids",
        ["bidder_id", sa.literal_column("created_at DESC")],
    )

    op.create_table(
        "proxy_bids",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("auction_id", sa.UUID(), nullable=False),
        sa.Column("bidder_id", sa.UUID(), nullable=False),
        sa.Column("max_amount", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "armed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("max_amount > 0", name=op.f("ck_proxy_bids_max_amount_pos")),
        sa.ForeignKeyConstraint(
            ["auction_id"],
            ["auctions.id"],
            name=op.f("fk_proxy_bids_auction_id_auctions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bidder_id"],
            ["users.id"],
            name=op.f("fk_proxy_bids_bidder_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proxy_bids")),
    )
    # at most one active maximum per (auction, bidder) (FR-45, OR-3)
    op.create_index(
        "uq_proxy_bids_active_auction_bidder",
        "proxy_bids",
        ["auction_id", "bidder_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "watchlist",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("auction_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["auction_id"],
            ["auctions.id"],
            name=op.f("fk_watchlist_auction_id_auctions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_watchlist_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("user_id", "auction_id", name=op.f("pk_watchlist")),
    )


def downgrade() -> None:
    op.drop_table("watchlist")
    op.drop_index("uq_proxy_bids_active_auction_bidder", table_name="proxy_bids")
    op.drop_table("proxy_bids")
    op.drop_index("ix_bids_bidder_id_created_at_desc", table_name="bids")
    op.drop_index("ix_bids_auction_id_sequence_no_desc", table_name="bids")
    op.drop_table("bids")
