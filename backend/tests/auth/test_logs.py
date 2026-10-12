"""No password or raw token ever reaches a log line (FR-2, NFR-17)."""

import logging

import pytest
from httpx import AsyncClient

from tests.auth.conftest import PASSWORD, RecordingEmail, bearer, fresh_email, register

CSRF = {"X-Requested-With": "XMLHttpRequest"}


async def test_secrets_absent_from_logs(
    client: AsyncClient, email: RecordingEmail, caplog: pytest.LogCaptureFixture
) -> None:
    address = fresh_email()
    with caplog.at_level(logging.DEBUG):
        assert (await register(client, address)).status_code == 201
        verify_token = email.last_token(address)
        await client.post("/api/v1/auth/verify-email", json={"token": verify_token})
        r = await client.post("/api/v1/auth/login", json={"email": address, "password": PASSWORD})
        access = r.json()["access_token"]
        refresh = client.cookies.get("refresh_token", path="/api/v1/auth")
        await client.post("/api/v1/auth/refresh", headers=CSRF)
        await client.post("/api/v1/auth/forgot-password", json={"email": address})
        reset_token = email.last_token(address)
        await client.post(
            "/api/v1/auth/reset-password",
            json={"token": reset_token, "password": "another fine passphrase"},
        )
        await client.get("/api/v1/users/me", headers=bearer(access))

    everything = caplog.text + "".join(str(getattr(r, "fields", "")) for r in caplog.records)
    assert len(caplog.records) > 5
    for secret in (PASSWORD, "another fine passphrase", verify_token, reset_token, refresh, access):
        assert secret not in everything, f"secret leaked into logs: {secret[:6]}..."
