from celery import Celery

from app.core.config import settings

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
    # denylist safety net: revocations committed in Postgres always reach Redis (FR-6)
    "sync-denylist": {"task": "sync_denylist", "schedule": 30.0, "options": {"expires": 30}},
}


@celery_app.task(name="ping")
def ping() -> str:
    return "pong"
