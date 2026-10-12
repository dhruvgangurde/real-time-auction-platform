"""Fire-and-forget task dispatch from async request handlers."""

import asyncio
import logging
from typing import Any

from app.workers.celery_app import celery_app

log = logging.getLogger("workers")


def _log_failure(future: asyncio.Future[object], name: str, args: list[Any] | None) -> None:
    if future.exception() is not None:
        fields: dict[str, Any] = {"task": name, "error": repr(future.exception())}
        if args is not None:
            fields["args"] = args
        log.error("enqueue failed", extra={"fields": fields})


def enqueue(name: str, args: list[Any], *, log_args: bool = True) -> None:
    """Send a task without blocking the caller's response (FR-40). Failures are logged
    (without the arguments when they carry secrets such as email tokens). Tasks with a beat
    safety net redo the work; the others are listed in currentstate known issues."""
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, celery_app.send_task, name, args)
    future.add_done_callback(lambda f: _log_failure(f, name, args if log_args else None))


def schedule_persist(auction_id: Any) -> None:
    enqueue("persist_bids", [str(auction_id)])


def schedule_outbid(auction_id: Any, users: list[Any], sequence_no: int, price: int) -> None:
    """Stored outbid notifications, after the bid was acknowledged (FR-70)."""
    enqueue("record_outbid", [str(auction_id), [str(u) for u in users], sequence_no, price])
