"""Request schemas for auctions, categories and listing filters (DC-11, NFR-19, FR-12).

Types and value ranges are checked here; rules across fields (end after start, reserve at least
the starting price, start not in the past, anti-snipe bounds) are checked by AuctionService so
each failing field gets its own message.
"""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.services.bidding import MAX_AMOUNT

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)
]
Money = Annotated[int, Field(ge=1, le=MAX_AMOUNT, strict=True)]
Seconds = Annotated[int, Field(ge=1, le=86_400, strict=True)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)]

# statuses a listing may ask for: drafts and cancelled auctions are never listed (FR-30)
ListedStatus = Literal["scheduled", "live", "closing", "closed_sold", "closed_no_sale"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuctionCreate(_Strict):
    title: Title
    description: Description
    category_id: int = Field(ge=1, strict=True)
    starting_price: Money
    reserve_price: Money | None = None  # absent means no reserve (FR-12)
    min_increment: Money
    start_time: AwareDatetime
    end_time: AwareDatetime
    antisnipe_threshold_s: Seconds = 60  # FR-14 defaults
    antisnipe_extension_s: Seconds = 120


class AuctionUpdate(_Strict):
    """Only the fields sent are changed. reserve_price may be set to null (no reserve); no
    other field may."""

    title: Title | None = None
    description: Description | None = None
    category_id: int | None = Field(default=None, ge=1, strict=True)
    starting_price: Money | None = None
    reserve_price: Money | None = None
    min_increment: Money | None = None
    start_time: AwareDatetime | None = None
    end_time: AwareDatetime | None = None
    antisnipe_threshold_s: Seconds | None = None
    antisnipe_extension_s: Seconds | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        for name in self.model_fields_set - {"reserve_price"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class CategoryCreate(_Strict):
    name: Name
    slug: Slug
    parent_id: int | None = Field(default=None, ge=1, strict=True)
    reason: Reason  # every administrative action states a reason (FR-75, BR-12)


class CategoryUpdate(_Strict):
    """Rename and/or re-parent; parent_id null moves the category to the root."""

    name: Name | None = None
    slug: Slug | None = None
    parent_id: int | None = Field(default=None, ge=1, strict=True)
    reason: Reason

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        changing = self.model_fields_set - {"reason"}
        if not changing:
            raise ValueError("nothing to change")
        for name in changing - {"parent_id"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class AdminReason(_Strict):
    reason: Reason
