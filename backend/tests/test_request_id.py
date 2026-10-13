"""NFR-32: correlation ID on every request and every worker task."""

from celery.signals import task_prerun
from httpx import AsyncClient

from app.core.logging import request_id_var
from app.workers.celery_app import celery_app


async def test_request_id_echoed(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


async def test_request_id_generated_when_absent(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health/live")
    rid = r.headers["x-request-id"]
    assert len(rid) == 32
    int(rid, 16)  # hex uuid


def test_worker_task_id_is_the_correlation_id() -> None:
    """Every worker task logs under its own task id, set before the task body runs."""
    token = request_id_var.set("-")
    try:
        task_prerun.send(sender=celery_app.tasks["ping"], task_id="task-123", args=(), kwargs={})
        assert request_id_var.get() == "task-123"
    finally:
        request_id_var.reset(token)
