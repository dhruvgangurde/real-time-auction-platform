"""FR-81's administrative dashboard and NFR-8's counters: GET /api/v1/admin/overview, for
administrators only, with the bids of the last minute, sockets per instance, queue depth,
reconciliation gaps, 5xx per minute, dependency health and the newest audit entries."""

from typing import Any

from httpx import AsyncClient
from redis.asyncio import Redis

from app.services.metrics import Metrics
from tests.lifecycle.conftest import bearer


async def test_the_overview_shows_every_counter(
    api: AsyncClient, make_user: Any, redis: Redis
) -> None:
    _, token = await make_user("admin")
    m = Metrics(redis)
    await m.bump_bids(accepted=4)
    await m.bump_bids(rejected="BID_TOO_LOW")
    await m.bump_5xx()
    await m.publish_instance("api", sockets=7)
    await m.record_reconciliation(checked=2, gaps=1, regressions=0, failed=0)
    await redis.rpush("celery", "job")
    await redis.xadd("auction:a1:bids", {"k": "1"})

    r = await api.get("/api/v1/admin/overview", headers=bearer(token))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {
        "server_time_ms",
        "bids",
        "sockets",
        "queues",
        "reconciliation",
        "errors",
        "health",
        "recent_audit",
    }
    assert body["bids"]["accepted_60s"] == 4 and body["bids"]["rejected_60s"] == {"BID_TOO_LOW": 1}
    assert body["bids"]["rejection_rate"] == 0.2 and len(body["bids"]["per_second"]) == 60
    assert body["sockets"] == [{"instance": "api", "sockets": 7, "dropped": 0}]
    assert body["queues"] == {"celery_waiting": 1, "celery_unacked": 0, "outbox_backlog": 1}
    assert body["reconciliation"]["gaps_total"] == 1
    assert sum(body["errors"]["per_minute"]) == 1
    assert body["health"] == {"postgres": "ok", "redis": "ok", "broker": "ok"}
    assert isinstance(body["recent_audit"], list) and len(body["recent_audit"]) <= 10


async def test_recent_audit_is_the_newest_ten(api: AsyncClient, make_user: Any) -> None:
    _, token = await make_user("admin")
    victim, _ = await make_user()
    for i in range(5):  # ten audited actions by this admin, newest last
        for action in ("suspend", "reinstate"):
            r = await api.post(
                f"/api/v1/admin/users/{victim}/{action}",
                json={"reason": f"check {i}"},
                headers=bearer(token),
            )
            assert r.status_code == 200, r.text
    recent = (await api.get("/api/v1/admin/overview", headers=bearer(token))).json()["recent_audit"]
    assert len(recent) == 10
    ids = [e["id"] for e in recent]
    assert ids == sorted(ids, reverse=True)
    assert [e["reason"] for e in recent[:2]] == ["check 4", "check 4"]
