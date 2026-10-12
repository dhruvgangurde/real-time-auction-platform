"""proxy_bids.arm_seq: arbitration-point counter that orders equal maxima (FR-49)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # grants from 0005 cover the table; a new column needs none
    op.add_column(
        "proxy_bids",
        sa.Column("arm_seq", sa.BigInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("proxy_bids", "arm_seq")
