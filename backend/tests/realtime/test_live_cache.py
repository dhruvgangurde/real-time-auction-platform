"""The anonymous /live micro-cache and the single-flight rebuild (brief 12.2 item 1, FR-43).

Anonymous bodies are cached per instance for 1 s and built once for concurrent misses;
authenticated reads are always fresh and carry the viewer's own fields, which never enter the
cache. A presented token must be valid (401 otherwise), so no request skips both the cache and
the anonymous limit. Waiters share flights through asyncio.shield."""

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest
from redis.asyncio import Redis

from app.services import snapshot
from app.services.ledger import persist_outbox
from app.services.live_state import state_key
from app.services.snapshot import SnapshotCache, live_snapshot
from tests.realtime.conftest import Instance

OWN = {"you_lead", "your_max", "your_result"}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _counting(monkeypatch: pytest.MonkeyPatch, gate: asyncio.Event | None = None) -> list[UUID]:
    """Count init_live_state calls made through the snapshot path; optionally hold them."""
    calls: list[UUID] = []
    real = snapshot.init_live_state

    async def counted(auction_id: UUID, redis: Redis, sessions: Any) -> bool:
        calls.append(auction_id)
        if gate is not None:
            await gate.wait()
        return await real(auction_id, redis, sessions)

    monkeypatch.setattr(snapshot, "init_live_state", counted)
    return calls


@dataclass
class Call:
    start: float
    end: float
    before: dict[str, str]  # the live state hash when the call began
    after: dict[str, str]  # and when it returned


def _recording(monkeypatch: pytest.MonkeyPatch, redis: Redis) -> list[Call]:
    """Record every init_live_state call made through the snapshot path: when it ran, and the
    live state hash before and after it."""
    calls: list[Call] = []
    real = snapshot.init_live_state
    loop = asyncio.get_running_loop()

    async def recorded(auction_id: UUID, client: Redis, sessions: Any) -> bool:
        start, before = loop.time(), await redis.hgetall(state_key(auction_id))
        try:
            return await real(auction_id, client, sessions)
        finally:
            after = await redis.hgetall(state_key(auction_id))
            calls.append(Call(start, loop.time(), before, after))

    monkeypatch.setattr(snapshot, "init_live_state", recorded)
    return calls


async def _bid(http: httpx.AsyncClient, a: UUID, token: str, amount: int) -> None:
    r = await http.post(
        f"/api/v1/auctions/{a}/bids",
        json={"amount": amount, "request_id": f"r{amount}"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text


async def test_concurrent_reads_share_one_rebuild(
    start_instance, make_bidder, auction, redis: Redis, sessions, monkeypatch
) -> None:
    """On one instance no two rebuilds of an auction overlap, and a rebuild that starts after
    another one finished finds the state present and writes nothing."""
    node: Instance = await start_instance()
    a = await auction(self_outbid=False)
    _, token = await make_bidder()
    async with httpx.AsyncClient(base_url=node.http) as http:
        await _bid(http, a, token, 10_000)
        await persist_outbox(a, redis, sessions)
        calls = _recording(monkeypatch, redis)

        await redis.delete(state_key(a))  # 50 anonymous requests find no live state
        anonymous = await asyncio.gather(
            *(http.get(f"/api/v1/auctions/{a}/live") for _ in range(50))
        )
        assert {r.status_code for r in anonymous} == {200}
        bodies = [{k: v for k, v in r.json().items() if k != "server_time_ms"} for r in anonymous]
        assert all(b == bodies[0] for b in bodies) and bodies[0]["sequence_no"] == 1
        first_phase = len(calls)

        await redis.delete(state_key(a))  # authenticated requests bypass the cache
        authed = await asyncio.gather(
            *(http.get(f"/api/v1/auctions/{a}/live", headers=_auth(token)) for _ in range(20))
        )
        assert {r.status_code for r in authed} == {200}

    for phase in (calls[:first_phase], calls[first_phase:]):
        assert phase, "the missing state was rebuilt"
        ordered = sorted(phase, key=lambda c: c.start)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            assert later.start >= earlier.end, "two rebuilds overlapped on one instance"
        rebuilt, *later_calls = ordered
        assert not rebuilt.before and rebuilt.after["status"] == "live"
        for call in later_calls:  # found the state present and wrote nothing
            assert call.before and call.after == call.before


async def test_anonymous_is_cached_authenticated_is_fresh(
    start_instance, make_bidder, auction
) -> None:
    node: Instance = await start_instance()
    a = await auction(self_outbid=False)
    _, token = await make_bidder()
    async with httpx.AsyncClient(base_url=node.http) as http:
        await _bid(http, a, token, 10_000)
        first = await http.get(f"/api/v1/auctions/{a}/live")
        assert first.headers["cache-control"] == "public, max-age=1"
        assert first.headers["vary"] == "Authorization"
        assert not OWN & set(first.json())

        await _bid(http, a, token, 10_500)
        cached = (await http.get(f"/api/v1/auctions/{a}/live")).json()
        fresh = await http.get(f"/api/v1/auctions/{a}/live", headers=_auth(token))
        assert cached["sequence_no"] == 1  # up to 1 s old for anonymous viewers
        assert fresh.json()["sequence_no"] == 2 and fresh.json()["you_lead"] is True
        assert fresh.headers["cache-control"] == "private, no-store"
        assert fresh.headers["vary"] == "Authorization"

        # the same cached body, but server_time_ms is stamped on each response
        await asyncio.sleep(0.05)
        again = (await http.get(f"/api/v1/auctions/{a}/live")).json()
        assert again["sequence_no"] == 1 and again["server_time_ms"] > cached["server_time_ms"]

        await asyncio.sleep(1.1)
        assert (await http.get(f"/api/v1/auctions/{a}/live")).json()["sequence_no"] == 2

    # nothing a viewer may own is in the cache, whatever was asked before
    flights = node.app.state.snapshot_cache._flights
    for task in flights.values():
        assert not OWN & set(task.result())


async def test_drafts_and_bad_tokens_never_reach_the_cache(
    start_instance, make_bidder, auction, db
) -> None:
    node: Instance = await start_instance()
    seller, seller_token = await make_bidder()
    draft = await auction(status="draft", init=False)
    await db.execute("UPDATE auctions SET seller_id = $1 WHERE id = $2", seller, draft)
    live = await auction()
    cache: SnapshotCache = node.app.state.snapshot_cache
    async with httpx.AsyncClient(base_url=node.http) as http:
        own = await http.get(f"/api/v1/auctions/{draft}/live", headers=_auth(seller_token))
        assert own.status_code == 200 and own.json()["status"] == "draft"
        assert (await http.get(f"/api/v1/auctions/{draft}/live")).status_code == 404
        assert draft not in cache._flights  # neither the owner's body nor the 404 is kept

        # a presented token must be valid: 401, never the anonymous path (optional_user)
        for header in ("Bearer not-a-token", "Basic dXNlcjpwYXNz", "Bearer", "Bearer a.b.c"):
            r = await http.get(f"/api/v1/auctions/{live}/live", headers={"Authorization": header})
            assert r.status_code == 401, header
            assert r.json()["error"]["code"] == "INVALID_TOKEN"
        assert live not in cache._flights
        # an absent header is anonymous: cached
        assert (await http.get(f"/api/v1/auctions/{live}/live")).status_code == 200
        assert live in cache._flights


async def test_a_cancelled_waiter_never_cancels_the_flight(
    auction, make_bidder, redis: Redis, sessions, monkeypatch
) -> None:
    """Amendment 1: a client that disconnects mid-flight (its request task is cancelled) leaves
    the shared build and the shared rebuild running for every other waiter."""
    a = await auction()
    await redis.delete(state_key(a))
    gate = asyncio.Event()
    calls = _counting(monkeypatch, gate)
    cache = SnapshotCache()

    def build() -> Any:
        return live_snapshot(a, None, redis, sessions)

    waiters = [asyncio.create_task(cache.get(a, build)) for _ in range(3)]
    while not calls:  # the flight is inside init_live_state, held at the gate
        await asyncio.sleep(0.01)
    waiters[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiters[0]
    gate.set()
    bodies = await asyncio.gather(*waiters[1:])
    assert calls == [a]
    assert bodies[0] == bodies[1] and bodies[0]["status"] == "live"

    # the same for the rebuild flight shared by fresh (authenticated) reads
    await redis.delete(state_key(a))
    gate.clear()
    reads = [asyncio.create_task(snapshot._rebuild(a, redis, sessions)) for _ in range(3)]
    while len(calls) < 2:
        await asyncio.sleep(0.01)
    reads[1].cancel()
    gate.set()
    results = await asyncio.gather(*reads, return_exceptions=True)
    assert isinstance(results[1], asyncio.CancelledError)
    assert results[0] is None and results[2] is None
    assert calls == [a, a] and await redis.exists(state_key(a))
