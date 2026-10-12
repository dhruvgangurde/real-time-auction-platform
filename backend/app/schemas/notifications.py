"""Notification preference schema (FR-74, DC-11): per-type email switches, known types only."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

NotificationType = Literal[
    "outbid",
    "ending_soon",
    "auction_won",
    "auction_lost",
    "payment_due",
    "payment_received",
    "auction_sold",
    "auction_no_sale",
    "payment_failed",
]


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: dict[NotificationType, bool]
