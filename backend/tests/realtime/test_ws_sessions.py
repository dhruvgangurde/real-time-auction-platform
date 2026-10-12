"""One socket per session, the session cap, and the protocol keepalive (CI-3, D3, D4)."""

import asyncio
import itertools

from app.core.config import settings
from app.schemas.ws import CloseCode
from tests.realtime.client import Client, open_silent
from tests.realtime.conftest import Instance


async def _authenticate(url: str, token: str) -> Client:
    client = await Client.open(url)
    await client.send({"type": "authenticate", "token": token})
    return client


async def test_newest_socket_of_a_session_replaces_older_ones_on_every_instance(
    instances: tuple[Instance, Instance], make_bidder
) -> None:
    """CI-3: a session establishes at most one connection; the newest wins."""
    a_node, b_node = instances
    _, token = await make_bidder()
    first = await Client.open(a_node.ws, token=token)
    second = await Client.open(b_node.ws, token=token)
    assert await first.closed() == CloseCode.SESSION_REPLACED  # across instances
    third = await Client.open(b_node.ws, token=token)
    assert await second.closed() == CloseCode.SESSION_REPLACED  # on the same instance
    await third.send({"type": "ping"})
    await third.expect("pong")


async def test_session_cap_counts_sessions_not_sockets(
    instances: tuple[Instance, Instance], make_bidder, new_session
) -> None:
    a_node, b_node = instances
    user_id, token = await make_bidder()
    tokens = [token] + [
        (await new_session(user_id)).access_token
        for _ in range(settings.ws_max_sessions_per_user - 1)
    ]
    held = [
        await Client.open(node.ws, token=t)
        for node, t in zip(itertools.cycle((a_node, b_node)), tokens, strict=False)
    ]
    extra = await _authenticate(b_node.ws, (await new_session(user_id)).access_token)
    assert await extra.closed() == CloseCode.TOO_MANY_SESSIONS
    again = await Client.open(a_node.ws, token=tokens[1])  # a held session may reconnect at the cap
    assert await held[1].closed() == CloseCode.SESSION_REPLACED
    await again.send({"type": "ping"})
    await again.expect("pong")


async def test_half_open_sockets_hold_their_slot_only_until_the_keepalive_drops_them(
    start_instance, make_bidder, new_session
) -> None:
    """Production runs the uvicorn keepalive at 20 s / 20 s; 0.2 s here. A peer that vanished
    without a close: its own session reconnects at once (replacement), and other sessions'
    slots free up as soon as the keepalive gives up on the silent sockets."""
    node = await start_instance(ws_ping_interval=0.2)
    user_id, token = await make_bidder()
    tokens = [token] + [
        (await new_session(user_id)).access_token
        for _ in range(settings.ws_max_sessions_per_user - 1)
    ]
    silent = [await open_silent("127.0.0.1", node.port, t) for t in tokens]
    try:
        fresh = (await new_session(user_id)).access_token
        refused = await _authenticate(node.ws, fresh)
        assert await refused.closed() == CloseCode.TOO_MANY_SESSIONS
        again = await Client.open(node.ws, token=tokens[0])  # replaces its silent predecessor
        await again.send({"type": "ping"})
        await again.expect("pong")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        while True:
            client = await _authenticate(node.ws, fresh)
            try:
                await client.expect("authenticated", timeout=1)
                break
            except AssertionError:
                assert await client.closed() == CloseCode.TOO_MANY_SESSIONS
                assert loop.time() < deadline, "keepalive never freed the silent sessions"
                await asyncio.sleep(0.1)
    finally:
        for _, writer in silent:
            writer.close()


async def test_a_quiet_spectator_stays_connected(start_instance, make_bidder, auction) -> None:
    """D4: no idle timeout; a subscribed socket that never sends a message stays open as long
    as it answers the protocol keepalive."""
    node = await start_instance(ws_ping_interval=0.1)
    a = await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.subscribe(a)
    await asyncio.sleep(1.5)  # 15 keepalive rounds, 5 presence ticks
    assert not client._reader.done()
    assert len(client.of_type("presence")) >= 3


async def test_connection_id_is_never_taken_from_the_client(
    start_instance, make_bidder, auction
) -> None:
    """N2: two sockets that send the same X-Request-ID are still two connections."""
    node = await start_instance()
    a = await auction()
    clients = [
        await Client.open(node.ws, token=(await make_bidder())[1], headers={"X-Request-ID": "same"})
        for _ in range(2)
    ]
    for client in clients:
        await client.subscribe(a)
    await clients[0].wait(lambda f: f.get("type") == "presence" and f["participants"] == 2)
