"""NFR-32: correlation ID on every request and every worker task."""

import io
import json
import logging

from httpx import AsyncClient

from app.core.logging import JsonFormatter
from app.workers.celery_app import celery_app

probe_log = logging.getLogger("tests.correlation")


@celery_app.task(name="tests.correlation_probe")
def correlation_probe(fail: bool = False) -> None:
    probe_log.info("in task")
    if fail:
        raise RuntimeError("probe failed")


async def test_request_id_echoed(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


async def test_request_id_generated_when_absent(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health/live")
    rid = r.headers["x-request-id"]
    assert len(rid) == 32
    int(rid, 16)  # hex uuid


def test_correlation_id_cleared_after_task() -> None:
    """A task logs under its own id; once it has finished or failed, code outside any task
    logs with no correlation id ("-"), and the next task logs under its own id only."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())  # formats at emit time, while the id is set
    probe_log.addHandler(handler)
    probe_log.setLevel(logging.INFO)
    try:
        correlation_probe.apply(task_id="task-A")
        probe_log.info("between tasks")
        correlation_probe.apply(kwargs={"fail": True}, task_id="task-F")
        probe_log.info("after a failed task")
        correlation_probe.apply(task_id="task-B")
    finally:
        probe_log.removeHandler(handler)
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [(line["msg"], line["request_id"]) for line in lines] == [
        ("in task", "task-A"),
        ("between tasks", "-"),
        ("in task", "task-F"),
        ("after a failed task", "-"),
        ("in task", "task-B"),
    ]
