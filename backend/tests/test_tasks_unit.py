"""Celery task wrappers with the services substituted (FR-41, FR-43, NFR-10)."""

import asyncio
import logging
from typing import Any
from uuid import uuid4

import pytest

from app.workers import tasks


def _alerts(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.fields["alert"] for r in caplog.records if "alert" in getattr(r, "fields", {})]


@pytest.fixture
def failing_persister(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_: Any) -> int:
        raise RuntimeError("db down")

    monkeypatch.setattr(tasks.ledger, "persist_outbox", boom)
    monkeypatch.setattr(tasks.runtime, "redis", lambda: None)
    monkeypatch.setattr(tasks.runtime, "sessions", lambda: None)
    monkeypatch.setattr(tasks.runtime, "run", asyncio.run)


def test_persist_bids_alerts_after_final_retry(
    failing_persister: None, caplog: pytest.LogCaptureFixture
) -> None:
    task = tasks.persist_bids
    task.push_request(id="t1", retries=tasks.PERSIST_MAX_RETRIES, called_directly=True)
    try:
        with caplog.at_level(logging.CRITICAL, logger="workers"), pytest.raises(RuntimeError):
            task.run(str(uuid4()))
    finally:
        task.pop_request()
    assert _alerts(caplog) == ["LEDGER_WRITE_FAILED"]


def test_persist_bids_retries_without_alert_before_the_limit(
    failing_persister: None, caplog: pytest.LogCaptureFixture
) -> None:
    task = tasks.persist_bids
    # called directly, Celery's retry() re-raises the original error instead of re-queuing
    task.push_request(id="t2", retries=1, called_directly=True)
    try:
        with caplog.at_level(logging.CRITICAL, logger="workers"), pytest.raises(RuntimeError):
            task.run(str(uuid4()))
    finally:
        task.pop_request()
    assert _alerts(caplog) == []


# ---------------------------------------------------------------- closing (FR-57, NFR-12)


@pytest.fixture
def no_runtime(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[Any]]]:
    sent: list[tuple[str, list[Any]]] = []
    monkeypatch.setattr(tasks.runtime, "redis", lambda: None)
    monkeypatch.setattr(tasks.runtime, "sessions", lambda: None)
    monkeypatch.setattr(tasks.runtime, "run", asyncio.run)
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda name, args: sent.append((name, args)))
    return sent


def _closer_returning(monkeypatch: pytest.MonkeyPatch, outcome: tasks.Outcome) -> None:
    class FakeCloser:
        def __init__(self, *_: Any) -> None:
            pass

        async def close(self, _: Any) -> tasks.Outcome:
            return outcome

    monkeypatch.setattr(tasks, "Closer", FakeCloser)


def test_scheduler_hook_enqueues_close_auction(no_runtime: list[tuple[str, list[Any]]]) -> None:
    """FR-57: an auction moved to closing is handed to the close task."""
    auction_id = uuid4()
    asyncio.run(tasks._on_closing(auction_id))
    assert no_runtime == [("close_auction", [str(auction_id)])]


def test_scheduler_hook_survives_a_failed_enqueue(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The broker is down: the scheduler run goes on; sweep_closing re-enqueues later."""

    def broken(*_: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.celery_app, "send_task", broken)
    with caplog.at_level(logging.ERROR, logger="workers"):
        asyncio.run(tasks._on_closing(uuid4()))
    assert "close_auction enqueue failed; sweep_closing retries" in caplog.messages


def test_run_scheduler_passes_the_close_hook(
    monkeypatch: pytest.MonkeyPatch, no_runtime: list[tuple[str, list[Any]]]
) -> None:
    hooks: list[Any] = []

    class FakeScheduler:
        def __init__(self, *_: Any, on_closing: Any) -> None:
            hooks.append(on_closing)

        async def run(self) -> Any:
            return type("Report", (), {"activated": [], "repaired": [], "closed": []})()

    monkeypatch.setattr(tasks, "Scheduler", FakeScheduler)
    assert tasks.run_scheduler.run() == {}
    assert hooks == [tasks._on_closing]


def test_close_auction_returns_the_outcome(
    monkeypatch: pytest.MonkeyPatch, no_runtime: list[tuple[str, list[Any]]]
) -> None:
    _closer_returning(monkeypatch, tasks.Outcome.SETTLED)
    assert tasks.close_auction.run(str(uuid4())) == "settled"


def test_close_auction_retries_a_deferred_close_then_leaves_it_to_the_sweep(
    monkeypatch: pytest.MonkeyPatch, no_runtime: list[tuple[str, list[Any]]]
) -> None:
    from celery.exceptions import Retry

    _closer_returning(monkeypatch, tasks.Outcome.DEFERRED)
    task = tasks.close_auction
    task.push_request(id="c1", retries=0, called_directly=True)
    try:
        with pytest.raises(Retry):
            task.run(str(uuid4()))
    finally:
        task.pop_request()
    task.push_request(id="c2", retries=tasks.CLOSE_MAX_RETRIES, called_directly=True)
    try:
        assert task.run(str(uuid4())) == "deferred"
    finally:
        task.pop_request()


def test_close_auction_retries_on_an_error(
    monkeypatch: pytest.MonkeyPatch, no_runtime: list[tuple[str, list[Any]]]
) -> None:
    class Boom:
        def __init__(self, *_: Any) -> None:
            pass

        async def close(self, _: Any) -> tasks.Outcome:
            raise RuntimeError("db down")

    monkeypatch.setattr(tasks, "Closer", Boom)
    task = tasks.close_auction
    task.push_request(id="c3", retries=0, called_directly=True)
    try:
        with pytest.raises(RuntimeError):
            task.run(str(uuid4()))
    finally:
        task.pop_request()


def test_sweep_closing_enqueues_through_celery(
    monkeypatch: pytest.MonkeyPatch, no_runtime: list[tuple[str, list[Any]]]
) -> None:
    auction_id = uuid4()

    async def fake_sweep(_redis: Any, _sessions: Any, enqueue: Any, grace_s: int) -> dict:
        enqueue(auction_id)
        return {"enqueued": 1, "unresolved": 0, "grace_s": grace_s}

    monkeypatch.setattr(tasks.closing, "sweep", fake_sweep)
    report = tasks.sweep_closing.run()
    assert report == {"enqueued": 1, "unresolved": 0, "grace_s": tasks.settings.close_grace_s}
    assert no_runtime == [("close_auction", [str(auction_id)])]
