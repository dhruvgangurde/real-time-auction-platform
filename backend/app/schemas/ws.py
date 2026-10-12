"""WebSocket message schemas (CI-3, CI-5, DC-11) and application close codes (CI-4).

Every client message has an explicit `type`; anything else, any extra field, or any value out
of range is rejected before it reaches business logic.
"""

from enum import IntEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.services.bidding import MAX_AMOUNT

REQUEST_ID = r"^[A-Za-z0-9_-]{1,64}$"


class CloseCode(IntEnum):
    """Close codes; docs/architecture.md section 10 is the reference."""

    NORMAL = 1000
    GOING_AWAY = 1001
    POLICY = 1008  # Origin refused (sent before accept, surfaces as HTTP 403)
    TOO_BIG = 1009  # frame above the protocol cap (uvicorn --ws-max-size)
    INTERNAL = 1011
    AUTH_REQUIRED = 4001  # no authenticate as the first message within the timeout
    AUTH_FAILED = 4002  # token invalid, or re-authentication as a different user
    TOKEN_EXPIRED = 4003  # access token expired without a fresh authenticate
    SESSION_REVOKED = 4004  # logout, password reset or suspension
    SLOW_CONSUMER = 4008  # send queue overflowed
    POLICY_VIOLATION = 4009  # repeated invalid or over-limit messages
    TOO_MANY_CONNECTIONS = 4010  # per-user connection limit across instances
    IDLE_TIMEOUT = 4011  # no client message within the idle timeout


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Authenticate(_Message):
    type: Literal["authenticate"]
    token: str = Field(min_length=1, max_length=4096)


class Subscribe(_Message):
    type: Literal["subscribe"]
    auction_id: UUID


class Unsubscribe(_Message):
    type: Literal["unsubscribe"]
    auction_id: UUID


class PlaceBid(_Message):
    type: Literal["place_bid"]
    auction_id: UUID
    amount: int = Field(ge=1, le=MAX_AMOUNT, strict=True)
    request_id: str = Field(pattern=REQUEST_ID)


class SetProxy(_Message):
    type: Literal["set_proxy"]
    auction_id: UUID
    max_amount: int = Field(ge=1, le=MAX_AMOUNT, strict=True)
    request_id: str = Field(pattern=REQUEST_ID)


class Ping(_Message):
    type: Literal["ping"]


ClientMessage = Annotated[
    Authenticate | Subscribe | Unsubscribe | PlaceBid | SetProxy | Ping,
    Field(discriminator="type"),
]
CLIENT_MESSAGE = TypeAdapter(ClientMessage)
CLIENT_TYPES = frozenset(
    {"authenticate", "subscribe", "unsubscribe", "place_bid", "set_proxy", "ping"}
)
