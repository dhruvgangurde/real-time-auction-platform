"""CI-2 single error envelope, DC-11 schema validation, FR-12 per-field errors."""

from httpx import AsyncClient


def _is_envelope(body: dict) -> bool:
    """code and message always; fields only on validation errors."""
    return set(body) == {"error"} and {"code", "message"} <= set(body["error"]) <= {
        "code",
        "message",
        "fields",
    }


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
    assert set(r.json()["error"]["fields"]) == {"n"}  # per field (FR-12)


async def test_unhandled_error_envelope_without_traceback(client: AsyncClient) -> None:
    r = await client.get("/_test/boom")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "INTERNAL_ERROR", "message": "internal server error"}}
    assert "Traceback" not in r.text
    assert "RuntimeError" not in r.text
