"""Registration (FR-1, FR-2, FR-3, FR-4 rate limit, NFR-19)."""

import asyncpg
import pytest
from httpx import AsyncClient

from tests.auth.conftest import PASSWORD, FakeClock, RecordingEmail, fresh_email, register


async def test_register_creates_pending_bidder_with_argon2id_hash(
    client: AsyncClient, email: RecordingEmail, db: asyncpg.Connection
) -> None:
    address = fresh_email()
    r = await register(client, address, full_name="Ada Lovelace")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == address
    assert body["status"] == "pending_verification"
    assert body["roles"] == ["bidder"]
    assert "password" not in r.text

    row = await db.fetchrow("SELECT password_hash, status FROM users WHERE id = $1", body["id"])
    assert row["password_hash"].startswith("$argon2id$")  # FR-2: salted one-way hash
    assert PASSWORD not in row["password_hash"]
    assert [t for t, _, _ in email.sent] == [address]
    assert "verify-email#token=" in email.sent[0][2]  # a fragment: never sent to a server


async def test_email_is_normalised_and_unique_case_insensitively(
    client: AsyncClient, email: RecordingEmail
) -> None:
    address = fresh_email()
    r = await register(client, address.upper())
    assert r.status_code == 201 and r.json()["email"] == address
    dup = await register(client, address.title())
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "EMAIL_TAKEN"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"email": "not-an-email", "password": PASSWORD, "full_name": "A"}, "invalid email"),
        ({"email": "a@example.com", "password": "short9ch!", "full_name": "A"}, "9 chars"),
        ({"email": "a@example.com", "password": "x" * 129, "full_name": "A"}, "129 chars"),
        ({"email": "a@example.com", "password": PASSWORD, "full_name": ""}, "empty name"),
        (
            {"email": "a@example.com", "password": PASSWORD, "full_name": "A", "roles": ["admin"]},
            "extra",
        ),
    ],
)
async def test_invalid_input_rejected(client: AsyncClient, payload: dict, reason: str) -> None:
    r = await client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR", reason


async def test_password_equal_to_email_rejected(client: AsyncClient) -> None:
    address = fresh_email()
    r = await register(client, address, password=address.upper())
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_registration_rate_limited_per_ip_and_resets_by_clock(
    client: AsyncClient, clock: FakeClock
) -> None:
    """FR-4, NFR-22: 5 per hour per source address; the window is a clock bucket."""
    clock.advance(3600 - clock.now().timestamp() % 3600)  # start of a window
    for _ in range(5):
        assert (await register(client, fresh_email())).status_code == 201
    sixth = await register(client, fresh_email())
    assert sixth.status_code == 429 and sixth.json()["error"]["code"] == "RATE_LIMITED"
    assert int(sixth.headers["Retry-After"]) >= 1
    clock.advance(3600)
    assert (await register(client, fresh_email())).status_code == 201
