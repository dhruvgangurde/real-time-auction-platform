"""Read limits (brief 12.2 item 2): anonymous /live and bid history per client address, /live with
a token per user. Both fail open on a Redis error, because both reads have a Postgres fallback
(NFR-13); limits that protect writes still fail closed."""

import httpx
import pytest
from redis.asyncio import Redis

from app.api import deps
from app.core.config import settings
from app.core.ratelimit import RateLimiter
from app.core.redis import create_redis
from app.core.storage import LocalStorage
from app.services.catalog import CatalogService
from tests.realtime.conftest import DEAD_REDIS_URL, Instance

PROXY = "127.0.0.1"  # trusted here, standing in for nginx


def _from(address: str) -> dict[str, str]:
    return {"X-Forwarded-For": address}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def catalog(sessions, redis: Redis, tmp_path) -> dict:
    """Bid history served from the test database (the realtime servers do not override it)."""
    return {deps.get_catalog_service: CatalogService(sessions, redis, LocalStorage(str(tmp_path)))}


async def test_anonymous_reads_are_limited_per_client_address(
    start_instance, auction, redis: Redis, monkeypatch: pytest.MonkeyPatch, catalog
) -> None:
    monkeypatch.setattr(settings, "rate_limit_anon_read_ip", "2/1")
    node: Instance = await start_instance(forwarded_allow_ips=PROXY, overrides=catalog)
    a = await auction()
    live, history = f"/api/v1/auctions/{a}/live", f"/api/v1/auctions/{a}/bids"
    async with httpx.AsyncClient(base_url=node.http) as http:
        codes = [(await http.get(live, headers=_from("203.0.113.1"))).status_code for _ in range(3)]
        assert codes == [200, 200, 429]  # cached or not, every anonymous read counts
        refused = await http.get(live, headers=_from("203.0.113.1"))
        assert refused.json()["error"]["code"] == "RATE_LIMITED"
        assert int(refused.headers["retry-after"]) >= 1
        assert (await http.get(live, headers=_from("203.0.113.2"))).status_code == 200
        # bid history has its own bucket per address
        codes = [
            (await http.get(history, headers=_from("203.0.113.1"))).status_code for _ in range(3)
        ]
        assert codes == [200, 200, 429]
    keys = sorted(k.split(":")[1:3] for k in await redis.keys("rl:anon_*"))
    assert keys == [
        ["anon_bids", "203.0.113.1"],
        ["anon_live", "203.0.113.1"],
        ["anon_live", "203.0.113.2"],
    ]


async def test_a_spoofed_header_buys_no_reads(start_instance, auction, monkeypatch) -> None:
    """Straight to an api instance the peer address counts, whatever X-Forwarded-For says."""
    monkeypatch.setattr(settings, "rate_limit_anon_read_ip", "2/1")
    node: Instance = await start_instance()  # trusts only the compose proxy address
    a = await auction()
    async with httpx.AsyncClient(base_url=node.http) as http:
        codes = [
            (
                await http.get(f"/api/v1/auctions/{a}/live", headers=_from(f"198.51.100.{i}"))
            ).status_code
            for i in range(3)
        ]
    assert codes == [200, 200, 429]


async def test_authenticated_live_is_limited_per_user(
    start_instance, make_bidder, auction, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Amendment 3: rate_limit_auth_live_user; a token is never counted per address, and an
    invalid token is a 401 that skips neither the cache nor the anonymous limit."""
    monkeypatch.setattr(settings, "rate_limit_auth_live_user", "2/1")
    monkeypatch.setattr(settings, "rate_limit_anon_read_ip", "1/1")
    node: Instance = await start_instance()
    a = await auction()
    _, alice = await make_bidder()
    _, bob = await make_bidder()
    url = f"/api/v1/auctions/{a}/live"
    async with httpx.AsyncClient(base_url=node.http) as http:
        codes = [(await http.get(url, headers=_auth(alice))).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        assert (await http.get(url, headers=_auth(bob))).status_code == 200  # another user
        assert (await http.get(url)).status_code == 200  # tokens did not use the address bucket
        assert (await http.get(url)).status_code == 429
        bad = await http.get(url, headers={"Authorization": "Bearer not-a-token"})
        assert bad.status_code == 401  # not an anonymous read that slipped past the limit


async def test_read_limits_fail_open_write_limits_fail_closed(
    start_instance, make_bidder, auction, clock, catalog
) -> None:
    """With the limiter's Redis unreachable the reads still answer; a bid is still refused."""
    dead_limiter = RateLimiter(create_redis(DEAD_REDIS_URL), clock)
    node: Instance = await start_instance(overrides={**catalog, deps.get_limiter: dead_limiter})
    a = await auction()
    _, token = await make_bidder()
    async with httpx.AsyncClient(base_url=node.http) as http:
        assert (await http.get(f"/api/v1/auctions/{a}/live")).status_code == 200
        assert (
            await http.get(f"/api/v1/auctions/{a}/live", headers=_auth(token))
        ).status_code == 200
        assert (await http.get(f"/api/v1/auctions/{a}/bids")).status_code == 200
        bid = await http.post(
            f"/api/v1/auctions/{a}/bids",
            json={"amount": 10_000, "request_id": "r1"},
            headers=_auth(token),
        )
        assert bid.status_code == 503  # the FR-44 bid limit fails closed, as before
