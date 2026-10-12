"""Celery tasks: bid ledger, lifecycle, closing, denylist. Thin wrappers: all logic lives in
app/services."""

import logging
from uuid import UUID

from celery import Task

from app.core.clock import SystemClock
from app.core.config import settings
from app.services import closing, denylist, ledger, reconciliation
from app.services.closing import Closer, Outcome
from app.services.scheduler import Scheduler
from app.workers import runtime
from app.workers.celery_app import celery_app

log = logging.getLogger("workers")

PERSIST_MAX_RETRIES = 5
CLOSE_MAX_RETRIES = 8


@celery_app.task(name="persist_bids", bind=True, acks_late=True, max_retries=PERSIST_MAX_RETRIES)
def persist_bids(self: Task, auction_id: str) -> int:
    """Drain one auction's outbox into the ledger (FR-41). Safe to run any number of times."""
    try:
        return runtime.run(
            ledger.persist_outbox(UUID(auction_id), runtime.redis(), runtime.sessions())
        )
    except Exception as exc:
        if self.request.retries >= PERSIST_MAX_RETRIES:
            # entries stay in the outbox; the sweeper keeps retrying, but an operator must know
            log.critical(
                "ledger write failed after retries",
                extra={"fields": {"alert": "LEDGER_WRITE_FAILED", "auction_id": auction_id}},
            )
            raise
        raise self.retry(exc=exc, countdown=min(2**self.request.retries, 30)) from exc


@celery_app.task(name="sweep_outboxes")
def sweep_outboxes() -> int:
    """Safety net: persist every non-empty outbox, whether or not a persist task was enqueued."""
    return runtime.run(ledger.persist_all_pending(runtime.redis(), runtime.sessions()))


@celery_app.task(name="reconcile_live_auctions")
def reconcile_live_auctions() -> dict[str, str]:
    return runtime.run(reconciliation.reconcile(runtime.redis(), runtime.sessions()))


def enqueue_close(auction_id: UUID) -> None:
    celery_app.send_task("close_auction", [str(auction_id)])


async def _on_closing(auction_id: UUID) -> None:
    """The scheduler's hand-off to settlement. A failed enqueue is repaired by sweep_closing."""
    try:
        enqueue_close(auction_id)
    except Exception as exc:
        log.error(
            "close_auction enqueue failed; sweep_closing retries",
            extra={"fields": {"auction_id": str(auction_id), "error": repr(exc)}},
        )


@celery_app.task(name="run_scheduler")
def run_scheduler() -> dict[str, int]:
    """Lifecycle transitions: activate, repair, close (FR-15, FR-16, D9)."""
    scheduler = Scheduler(runtime.redis(), runtime.sessions(), on_closing=_on_closing)
    report = runtime.run(scheduler.run())
    return {k: len(v) for k, v in vars(report).items()}


@celery_app.task(name="close_auction", bind=True, acks_late=True, max_retries=CLOSE_MAX_RETRIES)
def close_auction(self: Task, auction_id: str) -> str:
    """Settle one closing auction (FR-57 to FR-62). Any number of runs, concurrent or retried,
    produce one settlement and one notification set (NFR-11)."""
    try:
        outcome = runtime.run(Closer(runtime.redis(), runtime.sessions()).close(UUID(auction_id)))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=min(2**self.request.retries, 60)) from exc
    if outcome is Outcome.DEFERRED and self.request.retries < CLOSE_MAX_RETRIES:
        raise self.retry(countdown=min(2**self.request.retries, 60))
    return outcome.value  # still deferred: sweep_closing re-enqueues, UNRESOLVED_CLOSE alerts


@celery_app.task(name="sweep_closing")
def sweep_closing() -> dict[str, int]:
    """Repair and alert (NFR-12): re-enqueue unsettled and unfinished closes."""
    return runtime.run(
        closing.sweep(runtime.redis(), runtime.sessions(), enqueue_close, settings.close_grace_s)
    )


@celery_app.task(name="denylist_revoke", bind=True, acks_late=True, max_retries=10)
def denylist_revoke(self: Task, keys: list[str], ttl_s: int) -> None:
    """Retry a denylist write the API could not complete (FR-6)."""
    try:
        runtime.run(denylist.write_keys(runtime.redis(), keys, ttl_s))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=min(2**self.request.retries, 30)) from exc


@celery_app.task(name="sync_denylist")
def sync_denylist() -> int:
    """Safety net: rewrite every recent revocation from Postgres into Redis (FR-6)."""
    return runtime.run(denylist.resync(runtime.redis(), runtime.sessions(), SystemClock()))
