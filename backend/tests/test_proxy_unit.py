"""BidService.set_proxy with Redis and repositories substituted (DC-8, FR-46, FR-51)."""

import json
from typing import Any
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.bidding import BidService
from tests.test_bidding_unit import FakeRedis, FakeScript, FakeSession

BIDDER = uuid4()
ARMED = json.dumps(
    {
        "decision": "armed",
        "max_amount": "50000",
        "arm_seq": "3",
        "active": True,
        "automatic_bids": [
            {
                "bidder_id": str(BIDDER),
                "amount": "10500",
                "sequence_no": "2",
                "extended": False,
                "end_time_ms": "1760000000000",
                "extension_count": "0",
            }
        ],
    }
)


def make(script: FakeScript, persist: Any = None) -> BidService:
    return BidService(FakeRedis(script), FakeSession, persist or (lambda _: None))  # type: ignore[arg-type]


async def test_armed_result_mapped_and_persist_scheduled() -> None:
    calls: list[Any] = []
    a = uuid4()
    p = await make(FakeScript(ARMED), calls.append).set_proxy(a, BIDDER, 50_000, "r")
    assert (p.armed, p.max_amount, p.arm_seq, p.active) == (True, 50_000, 3, True)
    [auto] = p.automatic_bids
    assert (auto.bidder_id, auto.amount, auto.sequence_no) == (BIDDER, 10_500, 2)
    assert calls == [a]


async def test_rejection_mapped_without_persist() -> None:
    raw = json.dumps({"decision": "rejected", "code": "BELOW_CURRENT_PRICE", "min_next": "10500"})
    calls: list[Any] = []
    p = await make(FakeScript(raw), calls.append).set_proxy(uuid4(), BIDDER, 10_000, "r")
    assert (p.armed, p.code, p.min_next, p.max_amount) == (
        False,
        "BELOW_CURRENT_PRICE",
        10_500,
        None,
    )
    assert calls == []


async def test_redis_error_fails_closed() -> None:
    p = await make(FakeScript(RedisConnectionError("down"))).set_proxy(uuid4(), BIDDER, 10_000, "r")
    assert (p.armed, p.code) == (False, "SERVICE_UNAVAILABLE")


@pytest.mark.parametrize("amount", [0, 2**53, True, "x"])
async def test_validation_never_calls_script(amount: Any) -> None:
    script = FakeScript()
    p = await make(script).set_proxy(uuid4(), BIDDER, amount, "r")
    assert p.code == "VALIDATION_ERROR" and script.calls == 0


async def test_proxy_log_line_carries_no_amount(caplog: pytest.LogCaptureFixture) -> None:
    """FR-51: the maximum is never logged, not even by its owner's own request."""
    import logging

    with caplog.at_level(logging.INFO, logger="bidding"):
        await make(FakeScript(ARMED)).set_proxy(uuid4(), BIDDER, 50_000, "r")
    [record] = [r for r in caplog.records if r.getMessage() == "proxy"]
    assert "50000" not in str(record.fields) and "amount" not in record.fields
