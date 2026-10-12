"""Property-based cross-check of the Lua scripts against the reference model (NFR-34, OR-13).

Random sequences of manual bids and proxy sets/raises from up to five bidders at random
increments run through the real place_bid.lua and set_proxy.lua (state hash written directly,
no Postgres) and, step by step, through tests/bidding/proxy_model.py. Decision, code, automatic
bids, highest, leader, sequence and the active maxima (with arm_seq) must agree after every op.
derandomize=True fixes the seed so CI is deterministic.
"""

import json
from uuid import UUID, uuid4, uuid5

import redis as redis_sync
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app import lua
from app.services.live_state import (
    outbox_key,
    proxies_key,
    proxy_request_key,
    request_key,
    state_key,
)
from tests.bidding.conftest import TEST_REDIS_URL
from tests.bidding.proxy_model import AuctionModel

NAMESPACE = UUID("8f2b0f9e-6b3e-4f6e-9d9c-5a7c1e2d3f40")
NAMES = ["alice", "bob", "carol", "dave", "erin"]
IDS = {name: uuid5(NAMESPACE, name) for name in NAMES}
BY_ID = {str(v): k for k, v in IDS.items()}
MAX_OPS = 30

_client: redis_sync.Redis | None = None
_scripts: dict[str, object] = {}


def _redis() -> redis_sync.Redis:
    global _client
    if _client is None:
        _client = redis_sync.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
        _scripts["bid"] = _client.register_script(lua.source("place_bid"))
        _scripts["proxy"] = _client.register_script(lua.source("set_proxy"))
    return _client


@st.composite
def runs(draw: st.DrawFn) -> tuple[int, int, list[tuple[str, str, int]]]:
    inc = draw(st.sampled_from([1, 7, 50, 1000]))
    start = draw(st.integers(1, 20)) * inc
    bidders = draw(st.integers(1, len(NAMES)))
    ops = draw(
        st.lists(
            st.tuples(
                st.sampled_from(["bid", "proxy"]),
                st.sampled_from(NAMES[:bidders]),
                st.integers(0, 60),  # increments above the start price
                st.integers(0, inc - 1),  # sub-increment offset
            ),
            min_size=1,
            max_size=MAX_OPS,
        )
    )
    return inc, start, [(kind, who, start + k * inc + off) for kind, who, k, off in ops]


@settings(
    max_examples=200,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(runs())
def test_scripts_match_reference_model(run: tuple[int, int, list[tuple[str, str, int]]]) -> None:
    inc, start, ops = run
    r = _redis()
    auction_id = uuid4()
    sec, usec = r.time()
    now = sec * 1000 + usec // 1000
    r.hset(
        state_key(auction_id),
        mapping={
            "status": "live",
            "start_time_ms": now - 60_000,
            "end_time_ms": now + 3_600_000,
            "highest_amount": 0,
            "highest_bidder": "",
            "sequence": 0,
            "start_price": start,
            "min_increment": inc,
            "extension_count": 0,
            "threshold_ms": 60_000,
            "extension_ms": 120_000,
            "self_outbid": "0",
            "arm_seq": 0,
        },
    )
    model = AuctionModel(start_price=start, inc=inc)
    try:
        for i, (kind, who, amount) in enumerate(ops):
            bidder = IDS[who]
            if kind == "bid":
                req = request_key(auction_id, bidder, f"p{i}")
                expected = model.place_bid(who, amount)
            else:
                req = proxy_request_key(auction_id, bidder, f"p{i}")
                expected = model.set_proxy(who, amount)
            raw = _scripts[kind](
                keys=[state_key(auction_id), outbox_key(auction_id), req, proxies_key(auction_id)],
                args=[str(auction_id), str(bidder), str(amount), 600_000],
            )
            got = json.loads(raw)
            step = f"op {i}: {kind} {who} {amount} (inc {inc}, start {start})"
            assert (got["decision"], got.get("code")) == (expected.decision, expected.code), step
            autos = [
                (BY_ID[a["bidder_id"]], int(a["amount"])) for a in got.get("automatic_bids", [])
            ]
            assert autos == list(expected.autos), step

            live = r.hgetall(state_key(auction_id))
            assert int(live["highest_amount"]) == model.highest, step
            assert BY_ID.get(live["highest_bidder"]) == model.leader, step
            assert int(live["sequence"]) == model.seq, step
            armed = {
                BY_ID[b]: tuple(int(x) for x in v.split("|"))
                for b, v in r.hgetall(proxies_key(auction_id)).items()
            }
            assert armed == model.proxies, step
    finally:
        keys = r.keys(f"auction:{{{auction_id}}}:*")
        if keys:
            r.delete(*keys)
