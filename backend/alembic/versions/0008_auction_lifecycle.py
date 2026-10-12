"""auction lifecycle: original_end_time editable before go-live (D1), denormalised current
price and bid count (D7), listing and watchlist indexes (FR-14, FR-19, FR-22, FR-23, FR-25, OR-5)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FROZEN_FROM_GO_LIVE = """
CREATE OR REPLACE FUNCTION auctions_original_end_time_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- D1: follows end_time while draft or scheduled; frozen from go-live, so
    -- end_time - original_end_time is the total soft-close extension (FR-14, FR-55)
    IF NEW.original_end_time IS DISTINCT FROM OLD.original_end_time
       AND OLD.status NOT IN ('draft', 'scheduled') THEN
        RAISE EXCEPTION 'auctions.original_end_time is immutable once live (FR-14)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$
"""

_ALWAYS_FROZEN = """
CREATE OR REPLACE FUNCTION auctions_original_end_time_immutable() RETURNS trigger
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


def upgrade() -> None:
    op.execute(_FROZEN_FROM_GO_LIVE)

    # D7: kept by the persister in the same transaction as the ledger insert, never decreasing
    op.add_column("auctions", sa.Column("current_price", sa.BigInteger(), nullable=True))
    op.add_column(
        "auctions", sa.Column("bid_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_auctions_current_price_pos"),
        "auctions",
        "current_price IS NULL OR current_price > 0",
    )
    op.create_check_constraint(op.f("ck_auctions_bid_count_nonneg"), "auctions", "bid_count >= 0")
    # backfill from the ledger: the highest sequence is the highest amount and the bid count
    op.execute(
        """
        UPDATE auctions AS a
        SET current_price = last.amount, bid_count = last.sequence_no
        FROM (
            SELECT DISTINCT ON (auction_id) auction_id, amount, sequence_no
            FROM bids
            ORDER BY auction_id, sequence_no DESC
        ) AS last
        WHERE a.id = last.auction_id
        """
    )

    # default listing, newest first (FR-22); the seller dashboard uses ix_auctions_seller_id
    op.create_index(op.f("ix_auctions_created_at"), "auctions", ["created_at"])
    # watchers of an auction: ending-soon recipients (FR-25, FR-73)
    op.create_index(op.f("ix_watchlist_auction_id"), "watchlist", ["auction_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_watchlist_auction_id"), table_name="watchlist")
    op.drop_index(op.f("ix_auctions_created_at"), table_name="auctions")
    op.drop_constraint(op.f("ck_auctions_bid_count_nonneg"), "auctions", type_="check")
    op.drop_constraint(op.f("ck_auctions_current_price_pos"), "auctions", type_="check")
    op.drop_column("auctions", "bid_count")
    op.drop_column("auctions", "current_price")
    op.execute(_ALWAYS_FROZEN)
