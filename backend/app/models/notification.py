import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

NOTIFICATION_TYPES = (
    "outbid",
    "ending_soon",
    "auction_won",
    "auction_lost",
    "payment_due",
    "payment_received",
    "auction_sold",  # the seller's close outcome (FR-62, migration 0009)
    "auction_no_sale",
    "payment_failed",  # FR-68: the bidder is told a payment attempt failed (migration 0010)
)
EMAIL_STATUSES = ("pending", "sent", "skipped", "failed")
_TYPE_CHECK = "type IN (" + ", ".join(f"'{t}'" for t in NOTIFICATION_TYPES) + ")"


class Notification(Base):
    """dedupe_key makes creation idempotent (DC-5). Email delivery (FR-72): pending rows are
    claimed with a lease (email_next_at), sent once per (user, type, auction) group, then
    sent, skipped (preference off or account not active) or failed after the last attempt."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(_TYPE_CHECK, name="type"),
        CheckConstraint(
            "email_status IN ('pending', 'sent', 'skipped', 'failed')", name="email_status"
        ),
        Index(
            "ix_notifications_user_unread",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("read_at IS NULL"),
        ),
        Index(
            "ix_notifications_email_pending",
            "user_id",
            "type",
            "auction_id",
            "created_at",
            postgresql_where=text("email_status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    auction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auctions.id", ondelete="RESTRICT")
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    email_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    email_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    email_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (CheckConstraint(_TYPE_CHECK, name="type"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    type: Mapped[str] = mapped_column(String(32), primary_key=True)
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
