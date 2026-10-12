from typing import Any

from celery import Celery
from celery.signals import setup_logging

from app.core.config import settings
from app.core.logging import configure_logging

celery_app = Celery("auction", broker=settings.celery_broker_url, include=["app.workers.tasks"])
celery_app.conf.timezone = "UTC"
celery_app.conf.beat_schedule = {
    # safety net for the ledger write (FR-41); expires so a busy worker never piles them up
    "sweep-outboxes": {"task": "sweep_outboxes", "schedule": 5.0, "options": {"expires": 5}},
    # sequence vs ledger gap detection (FR-43, NFR-10)
    "reconcile-live-auctions": {
        "task": "reconcile_live_auctions",
        "schedule": 30.0,
        "options": {"expires": 30},
    },
    # auction lifecycle: scheduled -> live -> closing (FR-15, FR-16, D9)
    "run-scheduler": {
        "task": "run_scheduler",
        "schedule": settings.scheduler_interval_s,
        "options": {"expires": settings.scheduler_interval_s},
    },
    # closing repair and the unresolved-close alert (FR-57, NFR-12)
    "sweep-closing": {"task": "sweep_closing", "schedule": 30.0, "options": {"expires": 30}},
    # denylist safety net: revocations committed in Postgres always reach Redis (FR-6)
    "sync-denylist": {"task": "sync_denylist", "schedule": 30.0, "options": {"expires": 30}},
}


@setup_logging.connect
def _json_logging(**_: Any) -> None:
    """Worker and beat log the same JSON lines as the API, so alert fields such as
    UNRESOLVED_CLOSE and LEDGER_WRITE_FAILED reach the log with their auction ids (NFR-32)."""
    configure_logging(settings.log_level)


@celery_app.task(name="ping")
def ping() -> str:
    return "pong"
