"""auctions.starting_price must be positive so the FR-37 100x limit is meaningful

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_auctions_starting_price_nonneg"), "auctions", type_="check")
    op.create_check_constraint(
        op.f("ck_auctions_starting_price_pos"), "auctions", "starting_price > 0"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_auctions_starting_price_pos"), "auctions", type_="check")
    op.create_check_constraint(
        op.f("ck_auctions_starting_price_nonneg"), "auctions", "starting_price >= 0"
    )
