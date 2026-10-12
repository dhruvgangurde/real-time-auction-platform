"""Email verification (FR-3): single use, 24 h, no enumeration on resend."""

import asyncpg
from httpx import AsyncClient

from tests.auth.conftest import FakeClock, RecordingEmail, fresh_email, register


async def _verify(client: AsyncClient, token: str):
    return await client.post("/api/v1/auth/verify-email", json={"token": token})


async def test_valid_token_activates_once(
    client: AsyncClient, email: RecordingEmail, db: asyncpg.Connection
) -> None:
    address = fresh_email()
    user_id = (await register(client, address)).json()["id"]
    token = email.last_token(address)
    assert (await _verify(client, token)).status_code == 200
    assert await db.fetchval("SELECT status FROM users WHERE id = $1", user_id) == "active"
    again = await _verify(client, token)
    assert again.status_code == 400 and again.json()["error"]["code"] == "INVALID_TOKEN"


async def test_expired_token(client: AsyncClient, email: RecordingEmail, clock: FakeClock) -> None:
    address = fresh_email()
    await register(client, address)
    clock.advance(24 * 3600 + 1)
    r = await _verify(client, email.last_token(address))
    assert r.status_code == 400 and r.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_wrong_token(client: AsyncClient) -> None:
    r = await _verify(client, "A" * 43)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_TOKEN"
    malformed = await _verify(client, "too-short")
    assert malformed.status_code == 422


async def test_resend_is_identical_for_unknown_and_known_and_new_token_works(
    client: AsyncClient, email: RecordingEmail, db: asyncpg.Connection
) -> None:
    address = fresh_email()
    user_id = (await register(client, address)).json()["id"]
    sent_before = len(email.sent)

    unknown = await client.post("/api/v1/auth/resend-verification", json={"email": fresh_email()})
    known = await client.post("/api/v1/auth/resend-verification", json={"email": address})
    assert (
        (unknown.status_code, unknown.json())
        == (known.status_code, known.json())
        == (
            202,
            {"status": "accepted"},
        )
    )
    assert len(email.sent) == sent_before + 1  # only the registered address got mail
    assert (await _verify(client, email.last_token(address))).status_code == 200
    assert await db.fetchval("SELECT status FROM users WHERE id = $1", user_id) == "active"

    # an active account gets no further verification mail
    active = await client.post("/api/v1/auth/resend-verification", json={"email": address})
    assert active.status_code == 202 and len(email.sent) == sent_before + 1
