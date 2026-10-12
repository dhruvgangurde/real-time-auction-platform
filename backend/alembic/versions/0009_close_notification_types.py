"""notification types for the seller's close outcome: auction_sold, auction_no_sale (FR-62)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("outbid", "ending_soon", "auction_won", "auction_lost", "payment_due", "payment_received")
NEW = (*OLD, "auction_sold", "auction_no_sale")
TABLES = ("notifications", "notification_preferences")


def _check(types: tuple[str, ...]) -> str:
    # fixed identifiers from this file, never user input
    return "type IN (" + ", ".join(f"'{t}'" for t in types) + ")"


def _replace(types: tuple[str, ...]) -> None:
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_type"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_type"), table, _check(types))


def upgrade() -> None:
    _replace(NEW)


def downgrade() -> None:
    # refuses (check violation) while rows of the new types exist: no silent data loss
    _replace(OLD)
