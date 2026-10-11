"""CI-2 single error envelope, DC-11 schema validation."""

from httpx import AsyncClient


def _is_envelope(body: dict) -> bool:
    return set(body) == {"error"} and set(body["error"]) == {"code", "message"}


async def test_404_envelope(client: AsyncClient) -> None:
    r = await client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert _is_envelope(r.json())
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_validation_error_envelope(client: AsyncClient) -> None:
    r = await client.post("/_test/validate", json={"n": "not-an-int"})
    assert r.status_code == 422
    assert _is_envelope(r.json())
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_unhandled_error_envelope_without_traceback(client: AsyncClient) -> None:
    r = await client.get("/_test/boom")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "INTERNAL_ERROR", "message": "internal server error"}}
    assert "Traceback" not in r.text
    assert "RuntimeError" not in r.text
