"""Table-driven proxy resolution (OR-13, NFR-34, FR-45 to FR-50, BR-14).

Amounts are written in increments above the start price: u(k) = start + k * inc, with
start = 1000 * inc, so every scenario runs unchanged at increments 1, 50 and 1000. Expected
outcomes are hand-computed from FR-46 to FR-49. Fractional k (sub-increment cases) only runs at
increments where it is a whole number of paise.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import BidService
from app.services.ledger import persist_outbox
from app.services.live_state import proxies_key, state_key
from tests.bidding.conftest import AuctionFactory

INCS = (1, 50, 1000)
SUB = (50, 1000)  # scenarios with half-increment amounts
LIMIT = 99_000  # u(99_000) == 100 x start price exactly

# op: (kind, who, k, expected decision or code, expected automatic bids [(who, k)], active?)
# final: (highest k, leader, sequence, ledger [(who, k, type)], active proxy owners)
SCENARIOS = [
    (
        "single proxy before any bid: first automatic bid at the start price (FR-48)",
        INCS,
        [("proxy", "alice", 40, "armed", [("alice", 0)], True)],
        (0, "alice", 1, [("alice", 0, "automatic")], {"alice"}),
    ),
    (
        "single proxy responds one increment above a manual bid (FR-48)",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 40, "armed", [("alice", 1)], True),
            ("bid", "carol", 5, "accepted", [("alice", 6)], None),
        ],
        (
            6,
            "alice",
            4,
            [
                ("bob", 0, "manual"),
                ("alice", 1, "automatic"),
                ("carol", 5, "manual"),
                ("alice", 6, "automatic"),
            ],
            {"alice"},
        ),
    ),
    (
        "two proxies, different maxima: higher leads one increment above the lower (FR-49)",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "carol", 20, "armed", [("carol", 1)], True),
            ("proxy", "alice", 40, "armed", [("carol", 20), ("alice", 21)], True),
        ],
        (
            21,
            "alice",
            4,
            [
                ("bob", 0, "manual"),
                ("carol", 1, "automatic"),
                ("carol", 20, "automatic"),
                ("alice", 21, "automatic"),
            ],
            {"alice"},
        ),
    ),
    (
        "equal maxima, alice armed first: alice leads at the common value (FR-49)",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 30, "armed", [("alice", 1)], True),
            ("proxy", "carol", 30, "armed", [("alice", 30)], False),
        ],
        (
            30,
            "alice",
            3,
            [("bob", 0, "manual"), ("alice", 1, "automatic"), ("alice", 30, "automatic")],
            {"alice"},
        ),
    ),
    (
        "equal maxima, carol armed first: carol leads at the common value (FR-49)",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "carol", 30, "armed", [("carol", 1)], True),
            ("proxy", "alice", 30, "armed", [("carol", 30)], False),
        ],
        (
            30,
            "carol",
            3,
            [("bob", 0, "manual"), ("carol", 1, "automatic"), ("carol", 30, "automatic")],
            {"carol"},
        ),
    ),
    (
        "three proxies armed in ascending order",
        INCS,
        [
            ("proxy", "dave", 20, "armed", [("dave", 0)], True),
            ("proxy", "carol", 30, "armed", [("dave", 20), ("carol", 21)], True),
            ("proxy", "alice", 40, "armed", [("carol", 30), ("alice", 31)], True),
        ],
        (
            31,
            "alice",
            5,
            [
                ("dave", 0, "automatic"),
                ("dave", 20, "automatic"),
                ("carol", 21, "automatic"),
                ("carol", 30, "automatic"),
                ("alice", 31, "automatic"),
            ],
            {"alice"},
        ),
    ),
    (
        "manual bid below the maximum: proxy responds",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 40, "armed", [("alice", 1)], True),
            ("bid", "carol", 10, "accepted", [("alice", 11)], None),
        ],
        (
            11,
            "alice",
            4,
            [
                ("bob", 0, "manual"),
                ("alice", 1, "automatic"),
                ("carol", 10, "manual"),
                ("alice", 11, "automatic"),
            ],
            {"alice"},
        ),
    ),
    (
        "manual bid equal to the maximum: manual bid wins, proxy exhausted (FR-48 'exceeds')",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 40, "armed", [("alice", 1)], True),
            ("bid", "carol", 40, "accepted", [], None),
        ],
        (
            40,
            "carol",
            3,
            [("bob", 0, "manual"), ("alice", 1, "automatic"), ("carol", 40, "manual")],
            set(),
        ),
    ),
    (
        "manual bid above the maximum: proxy exhausted (UC-02 5a)",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 40, "armed", [("alice", 1)], True),
            ("bid", "carol", 50, "accepted", [], None),
        ],
        (
            50,
            "carol",
            3,
            [("bob", 0, "manual"), ("alice", 1, "automatic"), ("carol", 50, "manual")],
            set(),
        ),
    ),
    (
        "raise: no bid while leading unchallenged, the raised maximum answers later",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 40, "armed", [("alice", 1)], True),
            ("proxy", "alice", 60, "armed", [], True),
            ("bid", "carol", 45, "accepted", [("alice", 46)], None),
        ],
        (
            46,
            "alice",
            4,
            [
                ("bob", 0, "manual"),
                ("alice", 1, "automatic"),
                ("carol", 45, "manual"),
                ("alice", 46, "automatic"),
            ],
            {"alice"},
        ),
    ),
    (
        "lower or equal maximum rejected, state untouched (FR-46)",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 40, "armed", [("alice", 1)], True),
            ("proxy", "alice", 30, "PROXY_CANNOT_DECREASE", [], None),
            ("proxy", "alice", 40, "PROXY_CANNOT_DECREASE", [], None),
        ],
        (1, "alice", 2, [("bob", 0, "manual"), ("alice", 1, "automatic")], {"alice"}),
    ),
    (
        "cap at own maximum below a full increment (FR-48 cap)",
        SUB,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 40, "armed", [("alice", 1)], True),
            ("bid", "carol", 39.5, "accepted", [("alice", 40)], None),
            ("bid", "dave", 40, "BID_TOO_LOW", [], None),
        ],
        (
            40,
            "alice",
            4,
            [
                ("bob", 0, "manual"),
                ("alice", 1, "automatic"),
                ("carol", 39.5, "manual"),
                ("alice", 40, "automatic"),
            ],
            {"alice"},
        ),
    ),
    (
        "two proxies within one increment: leader at own maximum (FR-49 own-maximum clause)",
        SUB,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 40, "armed", [("alice", 1)], True),
            ("proxy", "carol", 39.5, "armed", [("carol", 39.5), ("alice", 40)], False),
        ],
        (
            40,
            "alice",
            4,
            [
                ("bob", 0, "manual"),
                ("alice", 1, "automatic"),
                ("carol", 39.5, "automatic"),
                ("alice", 40, "automatic"),
            ],
            {"alice"},
        ),
    ),
    (
        "owner already leading: arming places no bid, competitor triggers a response",
        INCS,
        [
            ("bid", "alice", 0, "accepted", [], None),
            ("proxy", "alice", 40, "armed", [], True),
            ("bid", "bob", 5, "accepted", [("alice", 6)], None),
        ],
        (
            6,
            "alice",
            3,
            [("alice", 0, "manual"), ("bob", 5, "manual"), ("alice", 6, "automatic")],
            {"alice"},
        ),
    ),
    (
        "maximum above 100x start price rejected, exactly 100x accepted (BR-14)",
        INCS,
        [
            ("proxy", "alice", LIMIT + 1, "BID_ABOVE_LIMIT", [], None),
            ("proxy", "alice", LIMIT, "armed", [("alice", 0)], True),
        ],
        (0, "alice", 1, [("alice", 0, "automatic")], {"alice"}),
    ),
    (
        "maximum below the current minimum next bid rejected (FR-46)",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 0, "BELOW_CURRENT_PRICE", [], None),
            ("proxy", "alice", 1, "armed", [("alice", 1)], True),
        ],
        (1, "alice", 2, [("bob", 0, "manual"), ("alice", 1, "automatic")], {"alice"}),
    ),
    (
        "exhausted proxy re-armed with a higher maximum",
        INCS,
        [
            ("bid", "bob", 0, "accepted", [], None),
            ("proxy", "alice", 10, "armed", [("alice", 1)], True),
            ("bid", "carol", 20, "accepted", [], None),
            ("proxy", "alice", 50, "armed", [("alice", 21)], True),
        ],
        (
            21,
            "alice",
            4,
            [
                ("bob", 0, "manual"),
                ("alice", 1, "automatic"),
                ("carol", 20, "manual"),
                ("alice", 21, "automatic"),
            ],
            {"alice"},
        ),
    ),
]

CASES = [
    pytest.param(scenario, inc, id=f"{scenario[0][:40]}|inc={inc}")
    for scenario in SCENARIOS
    for inc in scenario[1]
]


@pytest.mark.parametrize(("scenario", "inc"), CASES)
async def test_resolution_table(
    scenario: tuple,
    inc: int,
    service: BidService,
    auction: AuctionFactory,
    new_user: Callable[[], Awaitable[UUID]],
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    _, _, ops, final = scenario
    start = 1000 * inc

    def u(k: float) -> int:
        amount = start + k * inc
        assert amount == int(amount)
        return int(amount)

    a = await auction(start_price=start, min_increment=inc, self_outbid=False)
    ids: dict[str, UUID] = {}
    names: dict[UUID, str] = {}

    async def who(name: str) -> UUID:
        if name not in ids:
            ids[name] = await new_user()
            names[ids[name]] = name
        return ids[name]

    for i, (kind, name, k, expect, autos, active) in enumerate(ops):
        bidder = await who(name)
        if kind == "bid":
            r = await service.place_bid(a, bidder, u(k), f"t{i}")
            got = "accepted" if r.accepted else r.code
            got_autos = r.automatic_bids
        else:
            p = await service.set_proxy(a, bidder, u(k), f"t{i}")
            got = "armed" if p.armed else p.code
            got_autos = p.automatic_bids
            if active is not None:
                assert p.active is active, f"op {i} active"
            if p.armed:
                assert p.max_amount == u(k)
        assert got == expect, f"op {i} {kind} {name} {k}"
        assert [(names[x.bidder_id], x.amount) for x in got_autos] == [
            (n, u(kk)) for n, kk in autos
        ], f"op {i} automatic bids"

    highest, leader, seq, ledger, active_owners = final
    live = await redis.hgetall(state_key(a))
    assert int(live["highest_amount"]) == u(highest)
    assert names.get(UUID(live["highest_bidder"])) == leader
    assert int(live["sequence"]) == seq
    assert {names[UUID(b)] for b in await redis.hkeys(proxies_key(a))} == active_owners

    # FR-50: automatic bids reach the ledger marked automatic, in sequence order
    await persist_outbox(a, redis, sessions)
    rows = await db.fetch(
        "SELECT bidder_id, amount, bid_type FROM bids WHERE auction_id = $1 ORDER BY sequence_no", a
    )
    assert [(names[r["bidder_id"]], r["amount"], r["bid_type"]) for r in rows] == [
        (n, u(k), t) for n, k, t in ledger
    ]
    active_rows = await db.fetch(
        "SELECT bidder_id FROM proxy_bids WHERE auction_id = $1 AND is_active", a
    )
    assert {names[r["bidder_id"]] for r in active_rows} == active_owners


async def test_automatic_bid_inside_window_extends(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    """FR-50, UC-02 6a: an automatic bid gets the same soft-close evaluation as a manual bid."""
    a = await auction(ends_in_ms=59_000, threshold_s=60, extension_s=120)
    end = int(await redis.hget(state_key(a), "end_time_ms"))
    p = await service.set_proxy(a, await new_user(), 50_000, "arm")
    [auto] = p.automatic_bids
    assert auto.extended and auto.end_time_ms == end + 120_000 and auto.extension_count == 1
    assert int(await redis.hget(state_key(a), "end_time_ms")) == end + 120_000


async def test_manual_and_automatic_in_one_trigger_extend_once(
    service: BidService, auction: AuctionFactory, new_user, redis: Redis
) -> None:
    """The manual bid extends; the automatic response at the same instant is now outside the
    window (end moved by 120 s), so it does not extend again (FR-53 'then-current end time')."""
    a = await auction(ends_in_ms=3_600_000, threshold_s=60, extension_s=120)
    assert (await service.set_proxy(a, await new_user(), 50_000, "arm")).armed
    await redis.hset(
        state_key(a), "end_time_ms", int(await redis.hget(state_key(a), "end_time_ms")) - 3_541_000
    )
    end = int(await redis.hget(state_key(a), "end_time_ms"))
    r = await service.place_bid(a, await new_user(), 20_000, "late")
    assert r.extended and r.end_time_ms == end + 120_000
    [auto] = r.automatic_bids
    assert not auto.extended and auto.end_time_ms == end + 120_000 and auto.extension_count == 1
