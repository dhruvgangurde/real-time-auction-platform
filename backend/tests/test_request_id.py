"""NFR-32: correlation ID on every request."""

from httpx import AsyncClient


async def test_request_id_echoed(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


async def test_request_id_generated_when_absent(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health/live")
    rid = r.headers["x-request-id"]
    assert len(rid) == 32
    int(rid, 16)  # hex uuid
