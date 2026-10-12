"""Fire-and-forget task dispatch from async request handlers."""

import asyncio
import logging
from typing import Any

from app.workers.celery_app import celery_app

log = logging.getLogger("workers")


def _log_failure(future: asyncio.Future[object], name: str, args: list[Any]) -> None:
    if future.exception() is not None:
        log.error(
            "enqueue failed",
            extra={"fields": {"task": name, "args": args, "error": repr(future.exception())}},
        )


def enqueue(name: str, args: list[Any]) -> None:
    """Send a task without blocking the caller's response (FR-40). Failures are logged;
    every task dispatched this way has a beat safety net that redoes the work."""
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, celery_app.send_task, name, args)
    future.add_done_callback(lambda f: _log_failure(f, name, args))


def schedule_persist(auction_id: Any) -> None:
    enqueue("persist_bids", [str(auction_id)])
