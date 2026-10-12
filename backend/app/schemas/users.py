from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ProfileUpdate(BaseModel):
    """Only name and avatar are self-service editable (FR-9); anything else is rejected."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    avatar_url: HttpUrl | None = Field(default=None, max_length=2048)
    clear_avatar: bool = False


class BidHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    auction_id: UUID
    amount: int
    sequence_no: int
    bid_type: str
    created_at: datetime


class BidHistoryPage(BaseModel):
    items: list[BidHistoryItem]
    next_cursor: str | None
