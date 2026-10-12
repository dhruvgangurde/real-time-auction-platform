"""Unexpected errors on a WebSocket are logged and answered, never swallowed (NFR-32, FR-42)."""

import asyncio
import logging
from datetime import datetime

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import deps
from app.api import ws as ws_module
from app.core.ratelimit import RateLimiter
from app.core.redis import create_redis
from app.schemas.ws import CloseCode
from app.services import live_events
from app.services.auth import AuthService
from app.services.denylist import Denylist
from tests.auth.conftest import FakeClock, RecordingEmail
from tests.realtime.client import Client
from tests.realtime.proxy import Proxy


def _closed_lines(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [r.fields for r in caplog.records if r.name == "ws" and r.msg == "ws closed"]


async def test_postgres_and_redis_lost_mid_bid_is_logged_and_answered(
    start_instance,
    make_bidder,
    auction,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    limiter: RateLimiter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The session check reaches Postgres and Redis through proxies that are cut after
    subscribe, so a bid's denylist re-check fails on Redis and then on its Postgres fallback
    (everything else stays up). The bid gets SERVICE_UNAVAILABLE (retry contract), the error is
    logged with its traceback, the socket keeps working, and the close is logged with the code
    the client really sent."""
    pg = await Proxy("postgres", 5432).start()
    rd = await Proxy("redis", 6379).start()
    engine = create_async_engine(sessions.kw["bind"].url.set(host="127.0.0.1", port=pg.port))
    cut_sessions = async_sessionmaker(engine, expire_on_commit=False)
    cut_redis: Redis = create_redis(f"redis://127.0.0.1:{rd.port}/15")
    auth = AuthService(
        cut_sessions, cut_redis, clock, RecordingEmail(), limiter, Denylist(cut_redis, None)
    )
    node = await start_instance(overrides={deps.get_auth_service: auth})
    try:
        a = await auction()
        _, token = await make_bidder()
        client = await Client.open(node.ws, token=token)
        await client.subscribe(a)
        pg.cut()
        rd.cut()
        with caplog.at_level(logging.INFO):
            await client.send(
                {"type": "place_bid", "auction_id": str(a), "amount": 10_000, "request_id": "x1"}
            )
            rejected = await client.expect("bid_rejected", request_id="x1")
            assert rejected["code"] == "SERVICE_UNAVAILABLE"
            await client.send({"type": "ping"})
            await client.expect("pong")  # one failed message does not end the connection
            await client.close()
            for _ in range(200):
                if _closed_lines(caplog):
                    break
                await asyncio.sleep(0.01)
        [failed] = [r for r in caplog.records if r.msg == "ws message failed"]
        assert failed.levelno == logging.ERROR and failed.exc_info is not None
        [bid_line] = [r.fields for r in caplog.records if r.msg == "bid"]
        assert bid_line["code"] == "SERVICE_UNAVAILABLE" and bid_line["bid_request_id"] == "x1"
        [closed] = _closed_lines(caplog)
        assert closed["code"] == 1000 and closed["reason"] == "client closed"
    finally:
        await engine.dispose()
        await cut_redis.aclose()


async def test_unexpected_error_in_a_non_bid_message_gets_internal_error(
    start_instance,
    make_bidder,
    auction,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing subscribe is logged and answered with error INTERNAL_ERROR; the room is left
    and the socket keeps working."""

    async def broken(*_: object) -> dict:
        raise RuntimeError("snapshot fault")

    monkeypatch.setattr(ws_module, "live_snapshot", broken)
    node = await start_instance()
    a = await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    with caplog.at_level(logging.INFO, logger="ws"):
        await client.send({"type": "subscribe", "auction_id": str(a)})
        error = await client.expect("error")
        await client.send({"type": "ping"})
        await client.expect("pong")
    assert error["code"] == "INTERNAL_ERROR" and error["request_id"] is None
    [failed] = [r for r in caplog.records if r.msg == "ws message failed"]
    assert failed.levelno == logging.ERROR and failed.exc_info is not None
    assert client.of_type("subscribed") == []
    assert all(a not in c.rooms for c in node.hub._conns)  # joined, then left again


async def test_publish_failure_after_the_ack_is_logged_never_answered(
    start_instance,
    make_bidder,
    auction,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-40: once acknowledged, a bid is never rejected; a failed publish is only logged."""

    async def broken(*_: object) -> None:
        raise RuntimeError("publish fault")

    monkeypatch.setattr(live_events, "publish_bid_events", broken)
    node = await start_instance()
    a = await auction()
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    await client.subscribe(a)
    with caplog.at_level(logging.INFO, logger="ws"):
        await client.send(
            {"type": "place_bid", "auction_id": str(a), "amount": 10_000, "request_id": "p1"}
        )
        accepted = await client.expect("bid_accepted", request_id="p1")
        await client.send({"type": "ping"})
        await client.expect("pong")  # anything for p1 would have arrived before this
    assert accepted["sequence_no"] == 1
    assert [f for f in client.frames if f.get("type") in ("bid_rejected", "error")] == []
    [failed] = [r for r in caplog.records if r.msg == "bid events not published"]
    assert failed.levelno == logging.ERROR and failed.exc_info is not None


class BrokenClock(FakeClock):
    """A clock that starts failing on demand, to break the connection's watch task."""

    broken = False

    def now(self) -> datetime:
        if self.broken:
            raise RuntimeError("clock fault")
        return super().now()


async def test_a_failing_connection_task_closes_1011_and_logs(
    start_instance, make_bidder, caplog: pytest.LogCaptureFixture
) -> None:
    clock = BrokenClock()
    node = await start_instance(overrides={deps.get_clock: clock})
    _, token = await make_bidder()
    client = await Client.open(node.ws, token=token)
    with caplog.at_level(logging.INFO, logger="ws"):
        clock.broken = True
        assert await client.closed() == CloseCode.INTERNAL
        for _ in range(200):
            if _closed_lines(caplog):
                break
            await asyncio.sleep(0.01)
    [failed] = [r for r in caplog.records if r.msg == "ws connection task failed"]
    assert failed.levelno == logging.ERROR and failed.exc_info is not None
    assert failed.fields["task"] == "watch"
    [closed] = _closed_lines(caplog)
    assert closed["code"] == CloseCode.INTERNAL and closed["reason"] == "internal error"
