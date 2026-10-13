"""Client address behind the web proxy (NFR-22, brief 12.2 item 2). X-Forwarded-For counts only
when the peer is the trusted proxy address; a client that reaches an api instance directly cannot
pick the address its per-IP limits key on."""

import httpx

from tests.auth.conftest import PASSWORD, fresh_email
from tests.realtime.conftest import Instance

PROXY = "127.0.0.1"  # the test client's own address, trusted here to stand in for nginx


async def _register(http: httpx.AsyncClient, forwarded_for: str) -> int:
    r = await http.post(
        "/api/v1/auth/register",
        json={"email": fresh_email(), "password": PASSWORD, "full_name": "Forwarded"},
        headers={"X-Forwarded-For": forwarded_for},
    )
    return r.status_code


async def test_trusted_proxy_gives_each_client_its_own_bucket(start_instance) -> None:
    """Behind the proxy, two clients get separate per-IP buckets (register: 5 per hour)."""
    node: Instance = await start_instance(forwarded_allow_ips=PROXY)
    async with httpx.AsyncClient(base_url=node.http) as http:
        assert [await _register(http, "203.0.113.1") for _ in range(5)] == [201] * 5
        assert await _register(http, "203.0.113.1") == 429
        assert await _register(http, "203.0.113.2") == 201  # another client, its own bucket


async def test_spoofed_forwarded_for_is_ignored_from_an_untrusted_peer(start_instance) -> None:
    """Straight to the api (peer not the proxy): every request is the peer itself, whatever
    X-Forwarded-For says, so changing the header buys no new bucket."""
    node: Instance = await start_instance()  # trusts only the compose proxy address
    async with httpx.AsyncClient(base_url=node.http) as http:
        codes = [await _register(http, f"198.51.100.{i}") for i in range(6)]
    assert codes == [201] * 5 + [429]
