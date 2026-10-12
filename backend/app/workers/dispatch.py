"""Fire-and-forget task dispatch from async request handlers."""

import asyncio
import logging
from uuid import UUID

from app.workers.celery_app import celery_app

log = logging.getLogger("workers")


def _log_failure(future: asyncio.Future[object], auction_id: UUID) -> None:
    if future.exception() is not None:
        # the bid is already safe in the outbox; the sweeper will persist it
        log.error(
            "persist enqueue failed",
            extra={"fields": {"auction_id": str(auction_id), "error": repr(future.exception())}},
        )


def schedule_persist(auction_id: UUID) -> None:
    """Enqueue persist_bids without blocking the caller's acknowledgement (FR-40)."""
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, celery_app.send_task, "persist_bids", [str(auction_id)])
    future.add_done_callback(lambda f: _log_failure(f, auction_id))
