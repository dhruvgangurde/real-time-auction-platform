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
