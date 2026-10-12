"""Celery tasks for the bid ledger. Thin wrappers: all logic lives in app/services."""

import logging
from uuid import UUID

from celery import Task

from app.core.clock import SystemClock
from app.services import denylist, ledger, reconciliation
from app.workers import runtime
from app.workers.celery_app import celery_app

log = logging.getLogger("workers")

PERSIST_MAX_RETRIES = 5


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
