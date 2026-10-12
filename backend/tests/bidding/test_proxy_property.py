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


def _money_values(node: object) -> list[int]:
    """Every amount-like value in a script result, whatever its nesting."""
    out: list[int] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("amount", "max_amount"):
                out.append(int(value))
            else:
                out.extend(_money_values(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_money_values(item))
    return out


def _check_invariants(
    r: redis_sync.Redis,
    auction_id: UUID,
    requester: str,
    result: dict,
    max_before: dict[str, int],
    step: str,
) -> None:
    """Model-independent invariants, read straight from Redis state and the outbox."""
    live = r.hgetall(state_key(auction_id))
    highest, leader = int(live["highest_amount"]), live["highest_bidder"]
    active = {b: int(v.split("|")[0]) for b, v in r.hgetall(proxies_key(auction_id)).items()}
    bids = sorted(
        (int(f["sequence_no"]), int(f["amount"]), f["bidder_id"], f["bid_type"])
        for _, f in r.xrange(outbox_key(auction_id))
        if f.get("kind", "bid") == "bid"
    )

    # 1. gapless sequence numbers, amounts strictly increasing (NFR-28, NFR-29)
    assert [s for s, _, _, _ in bids] == list(range(1, int(live["sequence"]) + 1)), step
    amounts = [a for _, a, _, _ in bids]
    assert all(x < y for x, y in zip(amounts, amounts[1:], strict=False)), step

    # 2. a remaining maximum belongs to the leader or still exceeds the highest bid (FR-48)
    for owner, max_amount in active.items():
        assert owner == leader or max_amount > highest, step

    # 3. an automatic bid never carries the leader above its own maximum (FR-48, FR-49).
    #    A manual bid by the leader above their own armed maximum is allowed and keeps the
    #    maximum armed, so the check applies when the highest bid is automatic.
    if leader in active and bids and bids[-1][3] == "automatic":
        assert highest <= active[leader], step

    # 4. no automatic bid exceeds its owner's maximum at the time it was placed (FR-48)
    for auto in result.get("automatic_bids", []):
        assert int(auto["amount"]) <= max_before[auto["bidder_id"]], step

    # 5. a leading maximum that has not been bid at is disclosed to nobody else (FR-51, BR-7)
    if active:
        top_owner, top_max = max(active.items(), key=lambda kv: kv[1])
        if top_owner != requester and top_max > highest:
            assert top_max not in _money_values(result), step


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
            # maxima as they stand when this op's automatic bids are placed (invariant 4)
            max_before = {
                b: int(v.split("|")[0]) for b, v in r.hgetall(proxies_key(auction_id)).items()
            }
            if kind == "proxy":
                max_before[str(bidder)] = amount
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
            _check_invariants(r, auction_id, str(bidder), got, max_before, step)
    finally:
        keys = r.keys(f"auction:{{{auction_id}}}:*")
        if keys:
            r.delete(*keys)
