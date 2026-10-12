"""WebSocket handshake and token lifecycle (FR-28, CI-3, CI-4, FR-5, FR-6)."""

import pytest
from websockets.exceptions import InvalidStatus

from app.core.config import settings
from app.schemas.ws import CloseCode
from app.services.auth import AuthService
from tests.auth.conftest import FakeClock
from tests.realtime.client import Client
from tests.realtime.conftest import Instance


@pytest.fixture
async def node(start_instance) -> Instance:
    return await start_instance()


async def test_no_authenticate_within_timeout_closes_4001(node: Instance) -> None:
    client = await Client.open(node.ws)
    assert await client.closed() == CloseCode.AUTH_REQUIRED


async def test_other_first_message_closes_4001(node: Instance) -> None:
    client = await Client.open(node.ws)
    await client.send({"type": "subscribe", "auction_id": "00000000-0000-0000-0000-000000000000"})
    assert await client.closed() == CloseCode.AUTH_REQUIRED


async def test_bad_token_closes_4002(node: Instance) -> None:
    client = await Client.open(node.ws)
    await client.send({"type": "authenticate", "token": "not.a.token"})
    assert await client.closed() == CloseCode.AUTH_FAILED


async def test_token_in_url_is_ignored(node: Instance, make_bidder) -> None:
    """CI-4: tokens are read only from the first message, never from the URL."""
    _, token = await make_bidder()
    client = await Client.open(f"{node.ws}?token={token}")
    assert await client.closed() == CloseCode.AUTH_REQUIRED
    assert client.of_type("authenticated") == []


async def test_foreign_origin_refused_missing_origin_allowed(node: Instance, make_bidder) -> None:
    with pytest.raises(InvalidStatus) as info:
        await Client.open(node.ws, origin="https://evil.example.com")
    assert info.value.response.status_code == 403
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token, origin=None)  # non-browser client
    await client.send({"type": "ping"})
    await client.expect("pong")


async def test_expiry_without_renewal_closes_4003(
    node: Instance, make_bidder, clock: FakeClock
) -> None:
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    clock.advance(15 * 60 + 1)
    assert await client.closed() == CloseCode.TOKEN_EXPIRED


async def test_reauthentication_extends_the_connection(
    node: Instance, make_bidder, new_session, auth_service: AuthService, clock: FakeClock
) -> None:
    user_id, _ = await make_bidder()
    tokens = await new_session(user_id)
    client = await Client.open(node.ws, token=tokens.access_token)
    clock.advance(14 * 60)
    fresh = (await auth_service.refresh(tokens.refresh_token)).access_token  # same session
    await client.send({"type": "authenticate", "token": fresh})
    first = client.of_type("authenticated")[0]
    renewed = await client.wait(lambda f: f.get("type") == "authenticated" and f is not first)
    assert renewed["expires_at_ms"] > first["expires_at_ms"]
    clock.advance(2 * 60)  # the original token has expired; the socket lives on
    await client.send({"type": "ping"})
    await client.expect("pong")
    clock.advance(14 * 60)  # now the fresh one has too
    assert await client.closed() == CloseCode.TOKEN_EXPIRED


async def test_reauthentication_as_another_user_closes_4002(node: Instance, make_bidder) -> None:
    _, token_a = await make_bidder()
    _, token_b = await make_bidder()
    client = await Client.open(node.ws, token=token_a)
    await client.send({"type": "authenticate", "token": token_b})
    assert await client.closed() == CloseCode.AUTH_FAILED


async def test_reauthentication_with_another_sessions_token_closes_4002(
    node: Instance, make_bidder, new_session
) -> None:
    """CI-3: a socket belongs to one session; another session's token needs its own socket."""
    user_id, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    other = (await new_session(user_id)).access_token
    await client.send({"type": "authenticate", "token": other})
    assert await client.closed() == CloseCode.AUTH_FAILED


async def test_bad_reauthentication_closes_4002(node: Instance, make_bidder) -> None:
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.send({"type": "authenticate", "token": "not.a.token"})
    assert await client.closed() == CloseCode.AUTH_FAILED


async def test_expired_token_on_a_bid_closes_4003_not_4004(
    node: Instance, make_bidder, auction, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-6: the bid's re-check sees the expiry before the watcher does; it is still an expiry."""
    monkeypatch.setattr(settings, "ws_watch_interval_s", 60.0)  # the watcher must not win
    a = await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.subscribe(a)
    clock.advance(15 * 60 + 1)
    await client.send(
        {"type": "place_bid", "auction_id": str(a), "amount": 10_000, "request_id": "late"}
    )
    rejected = await client.expect("bid_rejected", request_id="late")
    assert rejected["code"] == "TOKEN_EXPIRED"
    assert await client.closed() == CloseCode.TOKEN_EXPIRED
