import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Bid(Base):
    """Append-only ledger (FR-41, DC-4). The app role has no UPDATE or DELETE on this table."""

    __tablename__ = "bids"
    __table_args__ = (
        UniqueConstraint("auction_id", "sequence_no"),
        CheckConstraint("amount > 0", name="amount_pos"),
        CheckConstraint("bid_type IN ('manual', 'automatic')", name="bid_type"),
        Index("ix_bids_auction_id_sequence_no_desc", "auction_id", text("sequence_no DESC")),
        Index("ix_bids_bidder_id_created_at_desc", "bidder_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auctions.id", ondelete="RESTRICT"), nullable=False
    )
    bidder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bid_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # end time after this bid, set when soft close extended the auction (FR-55)
    end_time_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProxyBid(Base):
    __tablename__ = "proxy_bids"
    __table_args__ = (
        CheckConstraint("max_amount > 0", name="max_amount_pos"),
        # at most one active maximum per (auction, bidder) (FR-45, OR-3)
        Index(
            "uq_proxy_bids_active_auction_bidder",
            "auction_id",
            "bidder_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auctions.id", ondelete="RESTRICT"), nullable=False
    )
    bidder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    max_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # arbitration-point counter; equal maxima are won by the lower arm_seq (FR-49)
    arm_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    # a raise resets armed_at and takes a new arm_seq: priority goes to who committed first
    armed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
