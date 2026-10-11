from celery import Celery

from app.core.config import settings

celery_app = Celery("auction", broker=settings.celery_broker_url)
celery_app.conf.beat_schedule = {}  # schedules arrive in later phases
celery_app.conf.timezone = "UTC"


@celery_app.task(name="ping")
def ping() -> str:
    return "pong"
