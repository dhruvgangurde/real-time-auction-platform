"""NFR-1: the arbitration decision completes within 10 ms at p95.

Measures 1000 sequential calls of place_bid.lua (all accepted, the most expensive path: state
update plus outbox XADD), round trip from the API container to Redis.
"""

import statistics
import time

import pytest
from redis.asyncio import Redis

from app import lua
from app.services.live_state import outbox_key, proxies_key, request_key, state_key
from tests.bidding.conftest import AuctionFactory

CALLS = 1000


async def test_arbitration_p95_under_10ms(
    auction: AuctionFactory, new_user, redis: Redis, capsys: pytest.CaptureFixture[str]
) -> None:
    a = await auction(start_price=10_000, min_increment=1)
    bidders = [str(await new_user()), str(await new_user())]
    script = redis.register_script(lua.source("place_bid"))
    timings_ms: list[float] = []
    for i in range(CALLS):
        bidder = bidders[i % 2]
        keys = [state_key(a), outbox_key(a), request_key(a, bidder, f"l{i}"), proxies_key(a)]
        args = [str(a), bidder, str(10_000 + i), 600_000]
        start = time.perf_counter()
        raw = await script(keys=keys, args=args)
        timings_ms.append((time.perf_counter() - start) * 1000)
        assert '"accepted"' in raw

    q = statistics.quantiles(timings_ms, n=100)
    p50, p95, p99 = statistics.median(timings_ms), q[94], q[98]
    with capsys.disabled():
        print(
            f"\n[NFR-1] place_bid.lua x{CALLS}: "
            f"p50={p50:.3f} ms  p95={p95:.3f} ms  p99={p99:.3f} ms  max={max(timings_ms):.3f} ms"
        )
    assert p95 < 10.0
