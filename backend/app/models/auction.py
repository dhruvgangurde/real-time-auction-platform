import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

AUCTION_STATUSES = (
    "draft",
    "scheduled",
    "live",
    "closing",
    "closed_sold",
    "closed_no_sale",
    "cancelled",
)

# 'simple' config: no language stemming, titles are product names (OR-5)
SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('simple', coalesce(description, '')), 'B')"
)


class Auction(Base):
    __tablename__ = "auctions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'scheduled', 'live', 'closing', "
            "'closed_sold', 'closed_no_sale', 'cancelled')",
            name="status",
        ),
        CheckConstraint("end_time > start_time", name="end_after_start"),
        CheckConstraint("starting_price > 0", name="starting_price_pos"),  # FR-37 100x limit
        CheckConstraint("min_increment > 0", name="min_increment_pos"),
        CheckConstraint(
            "reserve_price IS NULL OR reserve_price >= starting_price", name="reserve_ge_start"
        ),
        CheckConstraint("extension_count BETWEEN 0 AND 10", name="extension_count_range"),
        CheckConstraint("current_price IS NULL OR current_price > 0", name="current_price_pos"),
        CheckConstraint("bid_count >= 0", name="bid_count_nonneg"),
        Index("ix_auctions_status_start_time", "status", "start_time"),
        Index("ix_auctions_status_end_time", "status", "end_time"),
        Index("ix_auctions_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # money is integer paise (DC-6, OR-1)
    starting_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserve_price: Mapped[int | None] = mapped_column(BigInteger)
    min_increment: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # set once at creation, immutable by trigger (FR-14, FR-55)
    original_end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    antisnipe_threshold_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    antisnipe_extension_s: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="120"
    )
    extension_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # D7: from the ledger, kept by the persister; live state in Redis may be ahead
    current_price: Mapped[int | None] = mapped_column(BigInteger)
    bid_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # a demo lot (brief 12.3): kept out of listings and notifications, never payable
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed(SEARCH_VECTOR_SQL, persisted=True)
    )


class AuctionImage(Base):
    __tablename__ = "auction_images"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auctions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
