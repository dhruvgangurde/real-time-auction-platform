"""BidService unit tests with Redis and repositories substituted (DC-8, NFR-33)."""

import asyncio
import json
import threading
import time
from typing import Any
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.services import bidding, live_state
from app.services.bidding import BidService
from app.workers import dispatch

ACCEPTED = json.dumps(
    {
        "decision": "accepted",
        "sequence_no": "7",
        "amount": "12000",
        "extended": True,
        "end_time_ms": "1760000120000",
        "extension_count": "1",
    }
)
NOT_INITIALISED = json.dumps({"decision": "rejected", "code": "NOT_INITIALISED"})


class FakeScript:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self, keys: list[str], args: list[Any]) -> str:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeRedis:
    def __init__(self, script: FakeScript) -> None:
        self.script = script

    def register_script(self, _source: str) -> FakeScript:
        return self.script


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def get(self, *_: Any) -> None:
        return None  # the seller check finds no auction; arbitration decides as before


class FakeAuction:
    def __init__(self, status: str) -> None:
        self.status = status
        self.seller_id = uuid4()  # never the bidder in these tests


def fake_repo(auction: FakeAuction | None) -> type:
    class Repo:
        def __init__(self, _session: Any) -> None:
            pass

        async def get(self, _id: Any) -> FakeAuction | None:
            return auction

    return Repo


def make(script: FakeScript, persist: Any = None) -> BidService:
    return BidService(FakeRedis(script), FakeSession, persist or (lambda _: None))  # type: ignore[arg-type]


async def test_acceptance_mapped_and_persist_scheduled() -> None:
    calls: list[Any] = []
    a = uuid4()
    r = await make(FakeScript(ACCEPTED), calls.append).place_bid(a, uuid4(), 12_000, "r")
    assert (r.accepted, r.sequence_no, r.amount, r.extended, r.extension_count) == (
        True,
        7,
        12_000,
        True,
        1,
    )
    assert r.end_time_ms == 1_760_000_120_000
    assert calls == [a]


async def test_rejection_mapped_with_minimum() -> None:
    raw = json.dumps(
        {
            "decision": "rejected",
            "code": "BID_TOO_LOW",
            "current_highest": "10000",
            "min_next": "10500",
        }
    )
    calls: list[Any] = []
    r = await make(FakeScript(raw), calls.append).place_bid(uuid4(), uuid4(), 10_100, "r")
    assert (r.accepted, r.code, r.current_highest, r.min_next) == (
        False,
        "BID_TOO_LOW",
        10_000,
        10_500,
    )
    assert calls == []


@pytest.mark.parametrize("error", [RedisConnectionError("down"), RedisTimeoutError("slow")])
async def test_redis_errors_fail_closed(error: Exception) -> None:
    """FR-42: never accept when Redis cannot establish the order."""
    calls: list[Any] = []
    r = await make(FakeScript(error), calls.append).place_bid(uuid4(), uuid4(), 10_000, "r")
    assert (r.accepted, r.code) == (False, "SERVICE_UNAVAILABLE")
    assert calls == []


async def test_validation_never_calls_script() -> None:
    script = FakeScript()
    r = await make(script).place_bid(uuid4(), uuid4(), 0, "r")
    assert r.code == "VALIDATION_ERROR" and script.calls == 0


async def test_not_initialised_unknown_auction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bidding, "AuctionRepository", fake_repo(None))
    r = await make(FakeScript(NOT_INITIALISED)).place_bid(uuid4(), uuid4(), 10_000, "r")
    assert r.code == "NOT_FOUND"


async def test_not_initialised_auction_vanishes_during_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def vanished(*_: Any) -> bool:
        raise live_state.AuctionNotFound("gone")

    monkeypatch.setattr(bidding, "AuctionRepository", fake_repo(FakeAuction("live")))
    monkeypatch.setattr(live_state, "init_live_state", vanished)
    r = await make(FakeScript(NOT_INITIALISED)).place_bid(uuid4(), uuid4(), 10_000, "r")
    assert r.code == "NOT_FOUND"


async def test_not_initialised_after_rebuild_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Another instance is mid-rebuild: reject rather than wait or guess."""

    async def lost_race(*_: Any) -> bool:
        return False

    monkeypatch.setattr(bidding, "AuctionRepository", fake_repo(FakeAuction("live")))
    monkeypatch.setattr(live_state, "init_live_state", lost_race)
    script = FakeScript(NOT_INITIALISED, NOT_INITIALISED)
    r = await make(script).place_bid(uuid4(), uuid4(), 10_000, "r")
    assert r.code == "SERVICE_UNAVAILABLE" and script.calls == 2


async def test_enqueue_failure_does_not_change_acceptance() -> None:
    def broken(_: Any) -> None:
        raise RuntimeError("broker down")

    r = await make(FakeScript(ACCEPTED), broken).place_bid(uuid4(), uuid4(), 12_000, "r")
    assert r.accepted


async def test_slow_enqueue_does_not_delay_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-40: the real dispatch helper runs send_task in an executor and does not await it."""
    sent = threading.Event()
    captured: list[Any] = []

    def slow_send_task(name: str, args: list[str]) -> None:
        time.sleep(0.5)  # a slow broker
        captured.append((name, args))
        sent.set()

    monkeypatch.setattr(dispatch.celery_app, "send_task", slow_send_task)
    a = uuid4()
    service = make(FakeScript(ACCEPTED), dispatch.schedule_persist)

    start = time.perf_counter()
    r = await service.place_bid(a, uuid4(), 12_000, "r")
    elapsed = time.perf_counter() - start

    assert r.accepted
    assert elapsed < 0.1, f"place_bid waited for the enqueue ({elapsed:.3f} s)"
    assert await asyncio.to_thread(sent.wait, 2)  # the enqueue still happens
    assert captured == [("persist_bids", [str(a)])]
