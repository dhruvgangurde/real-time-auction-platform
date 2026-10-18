"""The load test's set-up (Phase 11, loadtest/README.md): `python -m app.cli loadtest-setup`, run
from the `loadtest-tools` service of docker-compose.loadtest.yml.

Development only. It creates, once, a load-test seller and N bidder accounts: real users, not
demo ones, at reserved `.invalid` addresses and with an unusable password hash, so nobody can
sign in as them. It signs the bidders in the way login does after its password check, a session
row and an access token, as the demo bots are (B2), so each k6 socket authenticates with a real
token and meets the per-user limits like anyone. It creates and publishes the load-test auction
through the real lifecycle service, and returns the plan k6 runs from, tokens included, which
the CLI writes to a gitignored file. The tokens last access_token_ttl_s (15 minutes): the run
must end before they do, which the CLI checks before it starts.
"""

import asyncio
import hashlib
import json
import math
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import lua
from app.core.clock import SystemClock
from app.core.config import settings
from app.core.ratelimit import RateLimiter
from app.core.security import issue_access_token, new_token
from app.core.storage import LocalStorage
from app.repositories.auction import AuctionRepository
from app.repositories.category import CategoryRepository
from app.repositories.loadtest import LoadTestRepository
from app.repositories.session import SessionRepository
from app.repositories.settlement import SettlementRepository
from app.repositories.user import UserRepository
from app.services.auctions import AuctionService
from app.services.auth import CurrentUser
from app.services.catalog import CatalogService
from app.services.demo_checks import verify
from app.services.fanout import presence_key
from app.services.live_state import to_ms
from app.services.metrics import BIDS, Metrics
from app.workers.celery_app import celery_app

DOMAIN = "loadtest.invalid"  # reserved: no mail is delivered there, and the API refuses it
UNUSABLE_HASH = "!loadtest"  # not an argon2 hash, so no password ever matches it
IMAGE = Path(__file__).resolve().parents[1] / "demo" / "images" / "bidri-pandan.jpg"  # CC0
CATEGORY = "home-and-garden"
LOT = {
    "starting_price": 1_000_000,  # INR 10,000 in paise; room for 99,000 increments (100x cap)
    "min_increment": 1_000,  # INR 10
    "reserve_price": None,
}
# the soft close of the lot: the shortest allowed, so the final minute's bids extend it by at
# most 10 x 10 s (FR-54) and the run stays inside its tokens' life
SOFT_CLOSE_S = 10
SETTLE_MARGIN_S = 120  # after the last possible end: closing, settling, the clients' last frames


def refusal() -> str | None:
    if settings.app_env != "local":
        return (
            "the load test runs only in development (APP_ENV=local), "
            f"not APP_ENV={settings.app_env}"
        )
    if settings.e2e_stack:
        return "the e2e overlay is active (E2E_STACK=1): run the load test on the dev stack"
    return None


def longest_run_s(starts_in_s: int, live_s: int) -> int:
    """From set-up to the clients' last frame, with every extension the lot can take."""
    return starts_in_s + live_s + 10 * SOFT_CLOSE_S + SETTLE_MARGIN_S


@dataclass(frozen=True)
class Account:
    id: UUID
    roles: tuple[str, ...]


async def _accounts(sessions: async_sessionmaker[AsyncSession], bidders: int) -> list[Account]:
    """The seller first, then the bidders; created when missing, never adopted from an account
    that was not made here."""
    specs = [
        ("loadtest-seller", "Load test seller", ("bidder", "seller")),
        *(
            (f"loadtest-{n:03d}", f"Load test bidder {n:03d}", ("bidder",))
            for n in range(1, bidders + 1)
        ),
    ]
    found: list[Account] = []
    async with sessions() as session, session.begin():
        users = UserRepository(session)
        for local, name, roles in specs:
            email = f"{local}@{DOMAIN}"
            user = await users.get_by_email(email)
            if user is None:
                user = await users.create(email, UNUSABLE_HASH, name, roles, status="active")
            elif user.password_hash != UNUSABLE_HASH or user.is_demo:
                raise RuntimeError(f"{email} exists and is not a load-test account")
            found.append(Account(user.id, roles))
    return found


async def _mint(
    sessions: async_sessionmaker[AsyncSession], accounts: list[Account], now: datetime
) -> dict[UUID, str]:
    """A session and an access token per account, the session expiring with the token."""
    tokens: dict[UUID, str] = {}
    async with sessions() as session, session.begin():
        repo = SessionRepository(session)
        for account in accounts:
            _, digest = new_token()  # the refresh token is never used
            expires = now + timedelta(seconds=settings.access_token_ttl_s)
            sid = await repo.create(account.id, digest, expires)
            tokens[account.id] = issue_access_token(account.id, account.roles, sid, now)
    return tokens


async def _file(path: Path) -> AsyncIterator[bytes]:
    yield path.read_bytes()


async def setup(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    *,
    bidders: int,
    starts_in_s: int,
    live_s: int,
) -> dict[str, Any]:
    clock = SystemClock()
    seller, *people = await _accounts(sessions, bidders)
    await CatalogService(sessions, redis, LocalStorage("/")).seed_defaults()
    async with sessions() as session:
        category = await CategoryRepository(session).get_by_slug(CATEGORY)
    assert category is not None, "the default categories were just made sure of"
    auctions = AuctionService(
        sessions, redis, clock, RateLimiter(redis, clock), LocalStorage(settings.media_root)
    )
    now = clock.now()
    start = now + timedelta(seconds=starts_in_s)
    stamp = now.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    user = CurrentUser(seller.id, seller.roles, uuid4())
    view = await auctions.create(
        user,
        {
            **LOT,
            "title": f"Load test lot, {stamp}",
            "description": (
                "A load-test listing (Phase 11, loadtest/README.md): 100 and more simulated "
                "bidders bid on it through the real bid path.\n\n"
                "Photo of a comparable piece: Cleveland Museum of Art, 2020.206, CC0."
            ),
            "category_id": category.id,
            "start_time": start,
            "end_time": start + timedelta(seconds=live_s),
            "antisnipe_threshold_s": SOFT_CLOSE_S,
            "antisnipe_extension_s": SOFT_CLOSE_S,
        },
    )
    lot = UUID(view["id"])
    await auctions.add_image(user, lot, _file(IMAGE))
    await auctions.publish(user, lot)
    minted = clock.now()
    tokens = await _mint(sessions, people, minted)
    return {
        "auction_id": str(lot),
        "start_ms": to_ms(start),
        "end_ms": to_ms(start + timedelta(seconds=live_s)),
        "soft_close_s": SOFT_CLOSE_S,
        "start_price": LOT["starting_price"],
        "min_increment": LOT["min_increment"],
        "minted_at_ms": to_ms(minted),
        "expires_at_ms": to_ms(minted + timedelta(seconds=settings.access_token_ttl_s)),
        "bidders": [{"id": str(p.id), "token": tokens[p.id]} for p in people],
    }


# ---------------------------------------------------------------- during the run: the watcher

PLACE_BID_SHA = hashlib.sha1(lua.source("place_bid").encode()).hexdigest()
SLOWLOG_LEN = 100_000  # kept between two reads: far more than a second of the stack's commands
SETTLED_GRACE_S = 5.0  # the slow log is read this long past the settlement
CLOSED = ("closed_sold", "closed_no_sale")


def place_bid_executions(entries: Iterable[list[Any]]) -> list[tuple[int, int]]:
    """(start in Unix seconds, duration in microseconds) of each execution of the place_bid
    script among raw SLOWLOG GET entries: [id, start, duration, [command, arguments...],
    client address, client name]."""
    found = []
    for entry in entries:
        words = [w.decode(errors="replace") if isinstance(w, bytes) else str(w) for w in entry[3]]
        if len(words) > 1 and words[0].upper() == "EVALSHA" and words[1].lower() == PLACE_BID_SHA:
            found.append((int(entry[1]), int(entry[2])))
    return found


def place_bid_durations(entries: Iterable[list[Any]]) -> list[int]:
    """The execution times, in microseconds, of the place_bid script (place_bid_executions)."""
    return [us for _, us in place_bid_executions(entries)]


async def watch(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    broker: Redis,
    auction_id: UUID,
    deadline: float,
    poll_s: float = 0.1,
    grace_s: float = SETTLED_GRACE_S,
) -> dict[str, Any]:
    """Beside k6, until the lot is closed and settled and every bid row has been seen, then
    `grace_s` longer, or until `deadline` (epoch seconds). Returns what it measured:

    - NFR-1: the slow log records every command for the run (threshold 0). Each second it is
      read and cleared in one transaction, so no entry falls between the two, and the
      executions of the place_bid script are kept. The settings in force are read back from
      Redis at the start and at the end, and the reading goes on `grace_s` past the settlement,
      for bids decided while the lot closed.
    - NFR-7: each bid row's first sight in Postgres, polled every `poll_s`, minus its acceptance
      time (bids.created_at, Redis TIME). The time is taken after the read, so never too early.
    - NFR-5: the room's presence count, each second; and the Celery queue, waiting and in
      flight, at each poll.
    - NFR-8: the platform's own counters, each second: every completed second's bucket of
      accepted and rejected bids, read once, the socket count each instance published, and the
      queue depth, so the report can hold them against the run's own measurements.
    - When the slowest place_bid execution and the slowest persistence happened.

    The slow-log settings are put back however the watcher ends.
    """
    saved = await redis.config_get("slowlog-*")
    await redis.config_set("slowlog-max-len", SLOWLOG_LEN)
    await redis.config_set("slowlog-log-slower-than", 0)
    await redis.slowlog_reset()
    in_force = {"start": await redis.config_get("slowlog-*"), "end": None}
    seen: dict[int, float] = {}  # sequence_no -> ms from acceptance to first sight
    floor = 0  # every sequence number up to here has been seen
    durations: list[int] = []
    slowest = (0, 0)  # (microseconds, start in Unix seconds) of the slowest place_bid
    persisted_max = (0.0, 0.0)  # (ms, epoch ms when seen) of the slowest first sight
    metrics = Metrics(redis, broker)
    counted: dict[str, Any] = {"accepted": 0, "rejected": Counter()}
    sockets_peak: dict[str, int] = {}
    queue_peak = {"celery_waiting": 0, "celery_unacked": 0, "outbox_backlog": 0}
    last_second = int((await redis.time())[0]) - 1  # buckets from here on are this run's
    entries = presence_peak = waiting_peak = in_flight_peak = 0
    sampled = 0.0
    settled_at: float | None = None

    async def read_slowlog() -> None:
        nonlocal entries
        # raw replies: redis-py's slow-log parser fails inside a transaction with decoded replies
        async with redis.pipeline(transaction=True) as pipe:
            pipe.execute_command("SLOWLOG", "GET", SLOWLOG_LEN)
            pipe.execute_command("SLOWLOG", "RESET")
            logged, _ = await pipe.execute()
        nonlocal slowest
        entries += len(logged)
        for start_s, us in place_bid_executions(logged):
            durations.append(us)
            slowest = max(slowest, (us, start_s))

    async def read_counters(final: bool = False) -> None:
        """Each completed second's bid bucket once; at the end the current one too."""
        nonlocal last_second
        now_s = int((await redis.time())[0])
        upto = now_s if final else now_s - 1
        for second in range(last_second + 1, upto + 1):
            for field, n in (await redis.hgetall(f"{BIDS}:{second}")).items():
                if field == "accepted":
                    counted["accepted"] += int(n)
                elif field.startswith("rejected:"):
                    counted["rejected"][field.removeprefix("rejected:")] += int(n)
        last_second = max(last_second, upto)
        for row in await metrics.instances():
            sockets_peak[row["instance"]] = max(
                sockets_peak.get(row["instance"], 0), row["sockets"]
            )
        for name, depth in (await metrics.queues()).items():
            queue_peak[name] = max(queue_peak[name], depth)

    try:
        while time.time() < deadline:
            async with sessions() as session:
                rows = await LoadTestRepository(session).bids_after(auction_id, floor)
                auction = await AuctionRepository(session).get(auction_id)
                settlement = await SettlementRepository(session).for_auction(auction_id)
            now = time.time()
            for sequence_no, accepted_at in rows:
                if sequence_no not in seen:
                    seen[sequence_no] = now * 1000 - to_ms(accepted_at)
                    persisted_max = max(persisted_max, (seen[sequence_no], now * 1000))
            while floor + 1 in seen:
                floor += 1
            waiting_peak = max(waiting_peak, await broker.llen("celery"))
            in_flight_peak = max(in_flight_peak, await broker.hlen("unacked"))
            if now - sampled >= 1:
                sampled = now
                await read_slowlog()
                await read_counters()
                present = await redis.zcount(presence_key(auction_id), int(now * 1000), "+inf")
                presence_peak = max(presence_peak, present)
            if settled_at is None and (
                auction is not None
                and auction.status in CLOSED
                and settlement is not None
                and len(seen) >= auction.bid_count
            ):
                settled_at = now
            if settled_at is not None and now - settled_at >= grace_s:
                break
            await asyncio.sleep(poll_s)
        await read_slowlog()  # the last second's executions
        await read_counters(final=True)
        in_force["end"] = await redis.config_get("slowlog-*")
    finally:
        for name, value in saved.items():
            await redis.config_set(name, value)
        await redis.slowlog_reset()
    return {
        "settled": settled_at is not None,
        "arbitration_us": durations,
        "slowlog_entries": entries,
        "slowlog_settings": in_force,
        "persistence_ms": [seen[s] for s in sorted(seen)],
        "presence_peak": presence_peak,
        "celery_waiting_peak": waiting_peak,
        "celery_in_flight_peak": in_flight_peak,
        "arbitration_max_at_ms": slowest[1] * 1000 if durations else None,
        "persistence_max_at_ms": persisted_max[1] if seen else None,
        "counters": {
            "accepted": counted["accepted"],
            "rejected": dict(sorted(counted["rejected"].items())),
            "sockets_peak": dict(sorted(sockets_peak.items())),
            "queue_peak": queue_peak,
        },
    }


# ---------------------------------------------------------------- after the run: the report

NFR4_WORDING = (
    "The system shall sustain at least 20 accepted bids per second against a single auction "
    "without violating NFR-1 or NFR-2 and without any lost or duplicated bid."
)
STEADY_FROM_S = 10  # NFR-4's steady phase: from this long after the opening ...
FINAL_MINUTE_S = 60  # ... to the final minute, where room.js doubles the bidders' pace
WINDOW_S = 5
# every application limit docker-compose.loadtest.yml raises for the test, with its reason
LIMITS_RAISED: list[dict[str, str]] = []
# the existing tests the report cites, run after the load by loadtest/run.sh
CITED = {
    "NFR-1": ["tests/bidding/test_latency.py::test_arbitration_p95_under_10ms"],
    "NFR-6": ["tests/discovery/test_nfr6.py::test_listing_search_and_detail_p95_under_500_ms"],
    "NFR-7": [
        "tests/bidding/test_reconciliation.py::test_gap_not_covered_by_outbox_raises_alert",
        "tests/test_tasks_unit.py::test_persist_bids_alerts_after_final_retry",
    ],
    "NFR-28": [
        "tests/closing/test_closing.py::test_ten_concurrent_closes_settle_once",
        "tests/closing/test_closing.py::test_constraint_alone_keeps_one_settlement",
    ],
    "OR-14": [  # the same tests: NFR-28's last clause is what OR-14 verifies
        "tests/closing/test_closing.py::test_ten_concurrent_closes_settle_once",
        "tests/closing/test_closing.py::test_constraint_alone_keeps_one_settlement",
    ],
    "OR-15": [
        "tests/bidding/test_soft_close.py::test_bid_61s_before_end_does_not_extend",
        "tests/bidding/test_soft_close.py::test_bid_59s_before_end_extends_by_extension",
        "tests/bidding/test_soft_close.py::test_three_consecutive_late_bids_each_extend",
        "tests/bidding/test_soft_close.py::test_extension_cap_of_ten",
    ],
}
OUTCOME = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (\S+)", re.MULTILINE)
PRINTED = re.compile(r"\[NFR-\d+\][^\n]*")


def percentile(values: list[float], p: float) -> float | None:
    """Nearest rank: the smallest value with at least p percent of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def stats(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else None,
    }


def k6_records(lines: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """room.js's records from k6's JSON log: acks, rejections, sampled receipts and socket
    summaries; the other warnings and errors k6 logged go under "other"."""
    found: dict[str, list[dict[str, Any]]] = {
        "ack": [],
        "rej": [],
        "rx": [],
        "rx_summary": [],
        "other": [],
    }
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        try:
            record = json.loads(entry.get("msg", ""))
        except (ValueError, TypeError):
            record = None
        if isinstance(record, dict) and record.get("e") in ("ack", "rej", "rx", "rx_summary"):
            found[record["e"]].append(record)
        elif entry.get("level") in ("warning", "error"):
            found["other"].append(entry)
    return found


def end_to_end_ms(acks: list[dict[str, Any]], rx: list[dict[str, Any]]) -> list[float]:
    """NFR-2: each sampled receipt of a bid_placed minus that bid's send time, joined on the
    sequence number (one k6 process, one clock)."""
    sent = {a["seq"]: a["sent"] for a in acks}
    return [r["t"] - sent[r["seq"]] for r in rx if r["seq"] in sent]


def steady_window(plan: dict[str, Any]) -> tuple[int, int]:
    """NFR-4's steady phase in whole seconds, from STEADY_FROM_S after the opening to the final
    minute, so the per-second windows, the offered bids and the gaps cover the same time."""
    start = math.ceil((plan["start_ms"] + STEADY_FROM_S * 1000) / 1000) * 1000
    end = (plan["end_ms"] - FINAL_MINUTE_S * 1000) // 1000 * 1000
    return start, end


def per_second(counts: list[tuple[datetime, int]], start_ms: int, end_ms: int) -> list[int]:
    """Accepted bids in each whole second of [start, end)."""
    by_second = {to_ms(at) // 1000: n for at, n in counts}
    return [by_second.get(s, 0) for s in range(math.ceil(start_ms / 1000), end_ms // 1000)]


def window_rates(seconds: list[int], width_s: int = WINDOW_S) -> list[float]:
    """Bids per second in each whole window of width_s consecutive seconds."""
    return [
        sum(seconds[i : i + width_s]) / width_s
        for i in range(0, len(seconds) - width_s + 1, width_s)
    ]


def instance_log(text: str, auction_id: str) -> dict[str, Any]:
    """From one api instance's JSON log: the users whose sockets subscribed to the lot through
    it (the user of a connection is in its "ws authenticated" line), how those sockets closed,
    and its FR-44 bid lines for the lot by decision."""
    users: dict[str, str] = {}  # conn_id -> user_id
    subscribed: dict[str, str] = {}
    closes: Counter[str] = Counter()
    bids: Counter[str] = Counter()
    for line in text.splitlines():
        if not line.startswith("{"):
            continue  # uvicorn's own lines
        try:
            record = json.loads(line)
        except ValueError:
            continue
        msg, conn = record.get("msg"), record.get("conn_id")
        if msg == "ws authenticated":
            users[conn] = record.get("user_id")
        elif msg == "ws subscribed" and record.get("auction_id") == auction_id:
            subscribed[conn] = users.get(conn, "")
        elif msg == "ws closed" and conn in subscribed:
            closes[str(record.get("code"))] += 1
        elif msg == "bid" and record.get("auction_id") == auction_id:
            bids["accepted" if record.get("decision") == "accepted" else record.get("code")] += 1
    return {
        "subscribed_users": sorted(u for u in subscribed.values() if u),
        "closes": dict(closes),
        "bids": dict(bids),
    }


def pytest_results(text: str) -> tuple[dict[str, str], list[str]]:
    """From `pytest -s -rA` output: each test's outcome, and the measurements tests printed."""
    return {node: outcome for outcome, node in OUTCOME.findall(text)}, PRINTED.findall(text)


@dataclass(frozen=True)
class Evidence:
    """Everything one run left behind, read back (tokens left out)."""

    plan: dict[str, Any]
    bidder_ids: list[str]
    watched: dict[str, Any]
    records: dict[str, list[dict[str, Any]]]
    summary: dict[str, Any]  # k6's end-of-test data (handleSummary)
    instances: dict[str, dict[str, Any]]
    counts: list[tuple[datetime, int]]  # accepted bids per second of acceptance
    acceptances: list[tuple[int, int, int]]  # (sequence_no, amount, acceptance ms) in order
    auction: dict[str, Any]
    checks: list[dict[str, Any]]
    outcomes: dict[str, str]
    printed: list[str]
    reconcile_s: float
    drill: dict[str, str] | None = None  # OR-6: the newest restore drill's summary line
    matrix: dict[str, str] | None = None  # OR-12: the verification matrix's summary line


def _ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f} ms" if value < 1 else f"{value:.1f} ms"


def _trend(ev: Evidence, name: str) -> dict[str, float] | None:
    metric = ev.summary.get("metrics", {}).get(name)
    return metric["values"] if metric else None


def nfr1_verdict(p95_ms: float | None, executions: int, decided: int) -> bool | None:
    """NFR-1 from the slow log: assessed only when it holds one place_bid execution for every
    bid decided (accepted plus rejected); a partial sample is not assessed (None)."""
    if decided == 0 or executions != decided or p95_ms is None:
        return None
    return p95_ms <= 10


def _slowlog_settings(watched: dict[str, Any]) -> str:
    def one(found: dict[str, str] | None) -> str:
        if not found:
            return "not read"
        return (
            f"slowlog-log-slower-than {found['slowlog-log-slower-than']}, "
            f"slowlog-max-len {found['slowlog-max-len']}"
        )

    read = watched.get("slowlog_settings")
    if not read:
        return "not recorded by this run's watcher"
    return f"at the start {one(read['start'])}; at the end {one(read['end'])}"


def offered_bids(
    acks: list[dict[str, Any]],
    rejections: list[dict[str, Any]],
    acceptances: list[tuple[int, int, int]],
    start_ms: int,
    end_ms: int,
) -> dict[str, int]:
    """NFR-4's offered load: the bids k6 sent in [start, end), and how many of them could still
    win when sent, that is the accepted ones and those that lost a race in flight (their price
    was taken by a bid accepted at or after their send). k6's clock against Redis's: one kernel
    clock in the Docker VM."""
    taken_at = {amount: at for _, amount, at in acceptances}
    sent = winnable = 0
    for a in acks:
        if start_ms <= a["sent"] < end_ms:
            sent += 1
            winnable += 1
    for j in rejections:
        if start_ms <= j["sent"] < end_ms:
            sent += 1
            winnable += j["code"] == "BID_TOO_LOW" and taken_at.get(j["amount"], -1) >= j["sent"]
    return {"sent": sent, "winnable": winnable}


def gap_distribution(acceptances: list[tuple[int, int, int]], start_ms: int, end_ms: int) -> str:
    """The gaps between consecutive acceptances in [start, end): percentiles, and the share in
    each 10 ms band."""
    times = [at for _, _, at in acceptances if start_ms <= at < end_ms]
    gaps = [float(b - a) for a, b in zip(times, times[1:], strict=False)]
    if not gaps:
        return "n/a"
    bands = Counter(min(int(g) // 10, 6) for g in gaps)
    shares = ", ".join(
        f"{'60+' if b == 6 else f'{b * 10}-{b * 10 + 9}'} {bands[b] / len(gaps):.0%}"
        for b in range(7)
    )
    p = {q: percentile(gaps, q) for q in (10, 50, 90, 99)}
    return (
        f"p10 {p[10]:.0f} ms, p50 {p[50]:.0f} ms, p90 {p[90]:.0f} ms, p99 {p[99]:.0f} ms "
        f"({len(gaps):,} gaps); by 10 ms band: {shares}"
    )


def _word(passed: bool | None) -> str:
    return {True: "pass", False: "not met", None: "not assessed"}[passed]


def _cited(ev: Evidence, requirement: str) -> tuple[bool, str]:
    nodes = CITED[requirement]
    passed = sum(ev.outcomes.get(n) == "PASSED" for n in nodes)
    return passed == len(nodes), f"{passed}/{len(nodes)} passed"


def _outcomes(ev: Evidence, requirement: str) -> list[str]:
    return [f"{n}: {ev.outcomes.get(n, 'not run')}" for n in CITED[requirement]]


SUMMARY = re.compile(r"<!-- (restore-drill|verification) ((?:[a-z_]+=\S+ ?)+)-->")


def summary_line(text: str, kind: str) -> dict[str, str] | None:
    """The machine-readable summary an evidence file carries: <!-- kind key=value ... -->."""
    for found, pairs in SUMMARY.findall(text):
        if found == kind:
            return dict(pair.split("=", 1) for pair in pairs.split())
    return None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _figures(s: dict[str, Any]) -> dict[str, Any]:
    """A stats() dict as the figures block: n and the percentiles in ms."""
    return {
        "n": s["n"],
        "p50_ms": _round(s["p50"]),
        "p95_ms": _round(s["p95"]),
        "p99_ms": _round(s["p99"]),
        "max_ms": _round(s["max"]),
    }


def _after_open(ev: Evidence, at_ms: float | None) -> str:
    """Where in the run a moment fell: seconds after the lot opened."""
    if at_ms is None:
        return "when: not recorded"
    return f"at +{(at_ms - ev.plan['start_ms']) / 1000:.0f} s after the opening"


def requirements(ev: Evidence) -> list[dict[str, Any]]:
    """One row per requirement: target, measured, pass (None: not assessed here), method."""
    w, r = ev.watched, ev.records
    ledger_n = sum(n for _, n in ev.counts)
    rows: dict[str, dict[str, Any]] = {}

    def row(
        rid: str,
        target: str,
        measured: str,
        passed: bool | None,
        method: str,
        details: list[str],
        note: str | None = None,  # where an unmet or unassessed requirement is taken up
        figures: dict[str, Any] | None = None,  # the same figures as numbers (slice 5 Q1 b)
    ) -> None:
        rows[rid] = {
            "id": rid,
            "target": target,
            "measured": measured,
            "pass": passed,
            "note": note,
            "method": method,
            "details": details,
            "figures": figures,
        }

    accepted = sum(i["bids"].get("accepted", 0) for i in ev.instances.values())
    rejected = sum(sum(i["bids"].values()) for i in ev.instances.values()) - accepted
    arb = stats([us / 1000 for us in w["arbitration_us"]])
    verdict = nfr1_verdict(arb["p95"], arb["n"], accepted + rejected)
    counts = f"{arb['n']:,} EVALSHA entries for {accepted:,} accepted + {rejected:,} rejected bids"
    row(
        "NFR-1",
        "Arbitration execution within 10 ms at p95",
        f"p95 {_ms(arb['p95'])}; {counts}" if verdict is not None else f"not every bid: {counts}",
        verdict,
        "Server side: Redis's slow log at threshold 0 for the run, every execution of the "
        "place_bid script (by its SHA-1); assessed only when it holds one for every bid decided "
        "(both instances' FR-44 lines)",
        [
            f"settings in force, read back from Redis: {_slowlog_settings(w)}",
            f"p50 {_ms(arb['p50'])}, p99 {_ms(arb['p99'])}, max {_ms(arb['max'])}, "
            f"the max {_after_open(ev, w.get('arbitration_max_at_ms'))}",
            *[p for p in ev.printed if p.startswith("[NFR-1]")],
            *_outcomes(ev, "NFR-1"),
        ],
        figures={**_figures(arb), "executions": arb["n"], "decided": accepted + rejected},
    )

    instance_of = {u: name for name, i in ev.instances.items() for u in i["subscribed_users"]}
    sockets: dict[int, str] = {
        x["vu"]: instance_of.get(ev.bidder_ids[x["vu"]], "unknown") for x in r["rx"]
    }
    e2e = stats(end_to_end_ms(r["ack"], r["rx"]))
    sent_at = {a["seq"]: a["sent"] for a in r["ack"]}
    joined_rx = [x for x in r["rx"] if x["seq"] in sent_at]
    slowest_rx = max(joined_rx, key=lambda x: x["t"] - sent_at[x["seq"]], default=None)
    by_instance = []
    for name, count in sorted(Counter(sockets.values()).items()):
        s = stats(end_to_end_ms(r["ack"], [x for x in r["rx"] if sockets[x["vu"]] == name]))
        by_instance.append(f"{name}: {count} sampled sockets, p95 {_ms(s['p95'])} (n {s['n']})")
    own = _trend(ev, "nfr2_own_broadcast_ms")
    row(
        "NFR-2",
        "Submission to delivery of the acceptance event to subscribed clients within 300 ms at p95",
        f"p95 {_ms(e2e['p95'])}",
        e2e["p95"] is not None and e2e["p95"] <= 300,
        "Client side, k6: each bid's send time joined on its sequence number with its "
        "bid_placed receipt at every fifth socket, one clock",
        [
            f"{e2e['n']} deliveries at {len(sockets)} sockets; p50 {_ms(e2e['p50'])}, "
            f"p99 {_ms(e2e['p99'])}, max {_ms(e2e['max'])}, the max "
            f"{_after_open(ev, slowest_rx['t'] if slowest_rx else None)}",
            *by_instance,
            "the submitter's own bid_placed: "
            + (f"p95 {_ms(own['p(95)'])} (n {int(own['count'])})" if own else "n/a"),
        ],
        figures=_figures(e2e),
    )

    delivery = _trend(ev, "nfr3_delivery_ms")
    ack = _trend(ev, "bid_ack_ms")
    row(
        "NFR-3",
        "Broadcast to client receipt within 150 ms at p95",
        f"p95 {_ms(delivery['p(95)'] if delivery else None)}",
        delivery is not None and delivery["p(95)"] <= 150,
        "Client side, k6: each room event's receipt at every socket minus its server_time_ms, "
        "stamped by the publishing instance before the PUBLISH",
        [
            (
                f"{int(delivery['count'])} deliveries; median {_ms(delivery['med'])}, "
                f"p99 {_ms(delivery['p(99)'])}, max {_ms(delivery['max'])}"
            )
            if delivery
            else "no deliveries",
            "the submitter's acknowledgement (send to bid_accepted): "
            + (f"p95 {_ms(ack['p(95)'])} (n {int(ack['count'])})" if ack else "n/a"),
            "the max: when not recorded (k6 keeps only the trend's summary)",
        ],
        figures=None
        if delivery is None
        else {
            "n": int(delivery["count"]),
            "p50_ms": _round(delivery["med"]),
            "p95_ms": _round(delivery["p(95)"]),
            "p99_ms": _round(delivery["p(99)"]),
            "max_ms": _round(delivery["max"]),
        },
    )

    failing = [c for c in ev.checks if not c["passed"]]
    closes_ok, closes = _cited(ev, "NFR-28")
    acked = r["ack"]
    logged = sum(i["bids"].get("accepted", 0) for i in ev.instances.values())
    row(
        "NFR-28",
        "Zero lost and zero duplicated bids under load; zero duplicate settlements under "
        "concurrent close",
        f"{len(ev.checks) - len(failing)}/{len(ev.checks)} ledger checks passed on "
        f"{ledger_n} bids; concurrent-close tests {closes} (OR-14)",
        bool(ev.checks) and not failing and closes_ok,
        "Server side, after the close: the six ledger checks of Try to break it on the lot, fed "
        "every acknowledgement k6 received; and the concurrent-close tests",
        [
            *[
                f"{c['label']}: {'pass' if c['passed'] else 'FAIL'} ({c['value']})"
                for c in ev.checks
            ],
            f"acknowledged to k6: {len(acked)} ({len({a['seq'] for a in acked})} distinct); "
            f"accepted in the instances' logs: {logged}; in the ledger: {ledger_n}",
            *_outcomes(ev, "NFR-28"),
        ],
        figures={
            "bids": ledger_n,
            "checks_passed": len(ev.checks) - len(failing),
            "checks": len(ev.checks),
            "concurrent_close_passed": closes_ok,
        },
    )

    start, end = steady_window(ev.plan)
    seconds = per_second(ev.counts, start, end)
    rates = window_rates(seconds)
    slowest = min(rates) if rates else None
    offered = offered_bids(r["ack"], r["rej"], ev.acceptances, start, end)
    window_s = max(1, len(seconds))
    in_window = sum(seconds)
    ratio = f"{in_window / offered['sent']:.0%}" if offered["sent"] else "n/a"
    row(
        "NFR-4",
        "At least 20 accepted bids/s on one auction, within NFR-1 and NFR-2, none lost or "
        "duplicated",
        f"slowest {WINDOW_S} s window {slowest:.1f} bids/s" if slowest is not None else "n/a",
        slowest is not None
        and slowest >= 20
        and all(rows[k]["pass"] for k in ("NFR-1", "NFR-2", "NFR-28")),
        f"Server side, the ledger: accepted bids per {WINDOW_S} s window of acceptance time, "
        f"from {STEADY_FROM_S} s after the opening to the final minute",
        [
            f'SRS: "{NFR4_WORDING}" Window: the steady phase, from {STEADY_FROM_S} s after the '
            f"opening to the final minute ({len(seconds)} s), in {WINDOW_S} s windows of "
            "acceptance time; to sustain is read as every window at 20 or more.",
            f"offered: {offered['sent']:,} bids sent in the window, "
            f"{offered['sent'] / window_s:.1f}/s; {offered['winnable']:,} of them winnable when "
            f"sent, {offered['winnable'] / window_s:.1f}/s (nobody had yet taken their price)",
            f"accepted: slowest {WINDOW_S} s window "
            + (f"{slowest:.1f}/s" if slowest is not None else "n/a")
            + f", median window {percentile(rates, 50) or 0:.1f}/s, slowest single second "
            f"{min(seconds) if seconds else 'n/a'}; acceptance ratio {ratio} "
            f"({in_window:,} of {offered['sent']:,})",
            f"gaps between acceptances: {gap_distribution(ev.acceptances, start, end)}",
            f"{ledger_n:,} bids accepted in the whole run",
            "NFR-1, NFR-2 and NFR-28 in the same run: "
            + ", ".join(f"{k} {_word(rows[k]['pass'])}" for k in ("NFR-1", "NFR-2", "NFR-28")),
        ],
        figures={
            "slowest_window_per_s": _round(slowest),
            "median_window_per_s": _round(percentile(rates, 50)),
            "slowest_second": min(seconds) if seconds else None,
            "window_s": WINDOW_S,
            "offered_per_s": _round(offered["sent"] / window_s),
            "winnable_per_s": _round(offered["winnable"] / window_s),
            "accepted_run": ledger_n,
        },
    )

    summaries = r["rx_summary"]
    joined = [s for s in summaries if s["subscribed"]]
    per_instance = {name: len(i["subscribed_users"]) for name, i in ev.instances.items()}
    missing = sum(s["missing"] or 0 for s in joined)
    duplicates = sum(s["duplicates"] for s in joined)
    unclosed = sum(not s["saw_closed"] for s in joined)
    short = sum(s["saw_closed"] and s["final_seq"] != ledger_n for s in joined)
    split = ", ".join(f"{k} {v}" for k, v in sorted(per_instance.items()))
    row(
        "NFR-5",
        "At least 100 connected bidders in one room across at least 2 instances, no loss of "
        "event delivery",
        f"{len(joined)} sockets ({split}); {missing} missing, {duplicates} repeated deliveries",
        len(joined) >= 100
        and sum(n > 0 for n in per_instance.values()) >= 2
        and w["presence_peak"] >= 100
        and missing == 0
        and duplicates == 0
        and unclosed == 0
        and short == 0,
        "k6 sockets through the web service (round robin); subscriptions per instance from each "
        "instance's log; presence from Redis; every socket's bid_placed sequence numbers "
        "against the ledger's 1 to N",
        [
            f"{len(summaries)} sockets opened, {len(joined)} subscribed; presence peak "
            f"{w['presence_peak']}",
            f"{len(joined) - unclosed} saw auction_closed; {short} of those with a final "
            f"sequence number other than the ledger's {ledger_n}",
            *[
                f"{k}: sockets closed by code {v['closes']}"
                for k, v in sorted(ev.instances.items())
            ],
        ],
        figures={
            "sockets": len(joined),
            "per_instance": dict(sorted(per_instance.items())),
            "presence_peak": w["presence_peak"],
            "missing": missing,
            "duplicates": duplicates,
        },
    )

    reads = {name: _trend(ev, f"rest_{name}_ms") for name in ("listing", "search", "detail")}
    checked = ev.summary.get("metrics", {}).get("checks", {}).get("values", {})
    row(
        "NFR-6",
        "Listings, search results and auction detail within 500 ms at p95 under AS-4",
        "; ".join(f"{k} p95 {_ms(v['p(95)'] if v else None)}" for k, v in reads.items()),
        all(v is not None and v["p(95)"] <= 500 for v in reads.values())
        and checked.get("fails", 1) == 0,
        "Client side, k6: request duration per endpoint through the web service, during the run",
        [
            *[
                f"{k}: n {int(v['count'])}, median {_ms(v['med'])}, max {_ms(v['max'])}"
                for k, v in reads.items()
                if v
            ],
            f"answered 200: {checked.get('passes', 0)}, otherwise: {checked.get('fails', 'n/a')}",
            *[p for p in ev.printed if p.startswith("[NFR-6]")],
            *_outcomes(ev, "NFR-6"),
        ],
        figures={f"{k}_p95_ms": _round(v["p(95)"]) if v else None for k, v in reads.items()},
    )

    persisted = stats(w["persistence_ms"])
    reconcile_ok, reconcile = _cited(ev, "NFR-7")
    row(
        "NFR-7",
        "Durable persistence within 2 s at p95 of acceptance; a failure to persist detected by "
        "reconciliation within 60 s",
        f"p95 {_ms(persisted['p95'])}; detection: tests {reconcile}, reconciler every "
        f"{ev.reconcile_s:g} s",
        persisted["p95"] is not None
        and persisted["p95"] <= 2000
        and reconcile_ok
        and ev.reconcile_s <= 60,
        "Server side: Postgres polled every 100 ms, each bid row's first sight minus its "
        "acceptance time; the second clause by tests and the reconciler's schedule",
        [
            f"{persisted['n']} rows seen of {ledger_n}; p50 {_ms(persisted['p50'])}, "
            f"p99 {_ms(persisted['p99'])}, max {_ms(persisted['max'])}, the max "
            f"{_after_open(ev, w.get('persistence_max_at_ms'))} (the 100 ms poll adds up to "
            "100 ms to each figure)",
            f"Celery queue peak: {w['celery_waiting_peak']} waiting, "
            f"{w['celery_in_flight_peak']} in flight",
            *_outcomes(ev, "NFR-7"),
        ],
        figures=_figures(persisted),
    )

    nfr8_target = (
        "Counters exposed: accepted bids per second, rejections by reason, sockets per "
        "instance, queue depth, reconciliation gaps"
    )
    nfr8_method = (
        "The platform's own counters (GET /api/v1/admin/overview, services/metrics.py), read "
        "by the watcher each second, held against the run's own figures: the ledger, both "
        "instances' FR-44 lines, the subscriptions per instance"
    )
    c = w.get("counters")
    if c is None:
        row("NFR-8", nfr8_target, "not read by this run's watcher", None, nfr8_method, [])
    else:
        fr44_rejected: Counter[str] = Counter()
        for i in ev.instances.values():
            fr44_rejected.update({k: n for k, n in i["bids"].items() if k != "accepted"})
        peaks = {k: c["sockets_peak"].get(k, 0) for k in per_instance}
        same = {
            "accepted": c["accepted"] == ledger_n,
            "rejected": c["rejected"] == dict(fr44_rejected),
            "sockets": peaks == per_instance,
        }
        rejected_n = sum(c["rejected"].values())
        row(
            "NFR-8",
            nfr8_target,
            f"counted {c['accepted']:,} accepted (ledger {ledger_n:,}) and {rejected_n:,} "
            f"rejected by reason ({'as' if same['rejected'] else 'not as'} the FR-44 lines); "
            "sockets peak "
            + ", ".join(f"{k} {v}" for k, v in sorted(peaks.items()))
            + f" ({'as' if same['sockets'] else 'not as'} subscribed)",
            all(same.values()),
            nfr8_method,
            [
                f"rejected, counted: {c['rejected'] or 'none'}; in the FR-44 lines: "
                f"{dict(sorted(fr44_rejected.items())) or 'none'}",
                f"sockets peak per instance, counted: {c['sockets_peak']}; subscribed: "
                f"{dict(sorted(per_instance.items()))}",
                f"queue depth peak, counted: {c['queue_peak']}",
                "every bid in the window is this lot's: nothing else bids on the dev stack "
                "during a run",
            ],
            figures={
                "accepted_counted": c["accepted"],
                "accepted_ledger": ledger_n,
                "rejected_counted": c["rejected"],
                "rejected_fr44": dict(sorted(fr44_rejected.items())),
                "sockets_peak": c["sockets_peak"],
                "sockets_subscribed": dict(sorted(per_instance.items())),
                "queue_peak": c["queue_peak"],
            },
        )
    d = ev.drill
    or6_method = (
        "Demonstration: the restore drill (backend/scripts/restore-drill.sh), its evidence in "
        "ops/evidence/; inspection: the backup service of docker-compose.yml, its retention "
        "tested (tests/ops/test_backup_script.py)"
    )
    if d is None:
        row(
            "OR-6",
            "Daily logical backup, kept 7 days, restore demonstrated",
            "no restore drill recorded",
            False,
            or6_method,
            [],
            note="no restore drill yet",
        )
    else:
        row(
            "OR-6",
            "Daily logical backup, kept 7 days, restore demonstrated",
            f"restore drill of {d.get('date')}: {d.get('result')}; {d.get('tables')} tables' rows, "
            f"the six ledger checks ({d.get('checks')}) and {d.get('media')} media files as before",
            d.get("result") == "pass",
            or6_method,
            [f"ops/evidence/restore-drill-{d.get('date')}.md"],
            figures={k: d[k] for k in sorted(d)},
        )
    m = ev.matrix
    or12_method = (
        "Inspection: docs/verification.md, generated and checked by "
        "frontend/scripts/verification.mjs (npm run verification:check)"
    )
    if m is None:
        row(
            "OR-12",
            "Every requirement mapped to a verification activity",
            "no verification matrix found",
            None,
            or12_method,
            [],
        )
    else:
        gaps = int(m.get("gaps", "0"))
        row(
            "OR-12",
            "Every requirement mapped to a verification activity",
            f"{m.get('mapped')} of {m.get('ids')} requirements mapped; {gaps} without a "
            "verification activity, listed as gaps in docs/verification.md",
            gaps == 0,
            or12_method,
            [],
            figures={
                "ids": int(m.get("ids", "0")),
                "mapped": int(m.get("mapped", "0")),
                "gaps": gaps,
            },
        )
    row(
        "OR-14",
        "Settlement uniqueness verified with concurrent close invocations, and separately with "
        "the settlement lock disabled, the database constraint alone preventing duplication",
        f"concurrent-close tests {closes}",
        closes_ok,
        "Tests on the test database: ten closers at once settle once; with the lock disabled, "
        "the UNIQUE (auction_id) constraint alone keeps one settlement",
        _outcomes(ev, "OR-14"),
    )
    soft_ok, soft = _cited(ev, "OR-15")
    row(
        "OR-15",
        "Soft close verified at the boundary both ways and across repeated extensions to the "
        "cap; ordering under sustained load with zero lost and zero duplicated bids",
        f"boundary tests {soft}; ordering under load: NFR-28's checks "
        f"{'pass' if not failing and ev.checks else 'FAIL'}",
        soft_ok and bool(ev.checks) and not failing,
        "The soft-close tests; the ledger checks of this run",
        [
            f"this run: {ev.auction['extension_count']} extensions, closed {ev.auction['status']}",
            *_outcomes(ev, "OR-15"),
        ],
    )
    order = ["NFR-1", "NFR-2", "NFR-3", "NFR-4", "NFR-5", "NFR-6", "NFR-7", "NFR-8", "NFR-28"]
    return [rows[k] for k in [*order, "OR-6", "OR-12", "OR-14", "OR-15"]]


def _newest_summary(folder: Path, pattern: str, kind: str) -> dict[str, str] | None:
    files = sorted(folder.glob(pattern)) if folder.is_dir() else []
    return summary_line(files[-1].read_text(encoding="utf-8"), kind) if files else None


async def evidence(
    sessions: async_sessionmaker[AsyncSession],
    run: Path,
    ops: Path = Path("/ops"),
    docs: Path = Path("/docs"),
) -> Evidence:
    """Reads back what loadtest/run.sh left in `run`, and the lot's ledger; and the newest
    restore drill (`ops`/evidence) and the verification matrix (`docs`/verification.md)."""
    plan = json.loads((run / "plan.json").read_text())
    bidder_ids = [b["id"] for b in plan.pop("bidders")]  # the tokens go no further
    lot = UUID(plan["auction_id"])
    with (run / "events.log").open(encoding="utf-8") as lines:
        records = k6_records(lines)
    outcomes, printed = pytest_results((run / "pytest.txt").read_text(encoding="utf-8"))
    async with sessions() as session:
        counts = await LoadTestRepository(session).accepted_per_second(lot)
        accepted = await LoadTestRepository(session).acceptances(lot)
        auction = await AuctionRepository(session).get(lot)
    assert auction is not None, "the plan's lot"
    acks = sorted((a["seq"], a["bidder"], a["amount"]) for a in records["ack"])
    return Evidence(
        plan=plan,
        bidder_ids=bidder_ids,
        watched=json.loads((run / "watch.json").read_text()),
        records=records,
        summary=json.loads((run / "summary.json").read_text()),
        instances={
            name: instance_log((run / f"{name}.log").read_text(encoding="utf-8"), str(lot))
            for name in ("api", "api-2")
        },
        counts=counts,
        acceptances=[(seq, amount, to_ms(at)) for seq, amount, at in accepted],
        auction={
            "status": auction.status,
            "end_time": auction.end_time.isoformat(),
            "extension_count": auction.extension_count,
        },
        checks=await verify(sessions, lot, acks),
        outcomes=outcomes,
        printed=printed,
        # NFR-7's second clause: how often the reconciler looks for a gap
        reconcile_s=float(celery_app.conf.beat_schedule["reconcile-live-auctions"]["schedule"]),
        drill=_newest_summary(ops / "evidence", "restore-drill-*.md", "restore-drill"),
        matrix=_newest_summary(docs, "verification.md", "verification"),
    )


def machine(run: Path) -> dict[str, Any]:
    """What loadtest/run.sh recorded of the machine: the host's CPU and RAM, the resources of
    Docker's VM (which every container shares) and the k6 version."""
    host = json.loads((run / "host.json").read_text(encoding="utf-8-sig"))
    docker = json.loads((run / "docker-info.json").read_text(encoding="utf-8-sig"))
    gib = 2**30
    return {
        "cpu": host.get("cpu"),
        "cores": host.get("cores"),
        "threads": host.get("threads"),
        "ram_gib": round(int(host["ram_bytes"]) / gib, 1) if host.get("ram_bytes") else None,
        "os": host.get("os"),
        "docker": {
            "server_version": docker.get("ServerVersion"),
            "operating_system": docker.get("OperatingSystem"),
            "kernel": docker.get("KernelVersion"),
            "cpus": docker.get("NCPU"),
            "memory_gib": round(docker["MemTotal"] / gib, 1) if docker.get("MemTotal") else None,
        },
        "k6": (run / "k6-version.txt").read_text(encoding="utf-8").strip(),
    }


REPRODUCE = [
    "cp .env.example .env    # first run only",
    "docker compose up -d --build",
    "bash loadtest/run.sh    # about 10 minutes; writes this page and results/<date>.json",
]
VERDICT = {True: "Pass", False: "Not met", None: "Not assessed"}


def verdict(row: dict[str, Any]) -> str:
    """The Result cell: a measured miss in bold; a requirement taken up later, with where."""
    word = VERDICT[row["pass"]]
    if row.get("note"):
        return f"{word} - {row['note']}"
    return f"**{word}**" if row["pass"] is False else word


COMPARED = ("NFR-1", "NFR-2")  # p95 beside the previous run (Phase 11 part 2, Q3's condition)
P95 = re.compile(r"\bp95 (\d+(?:\.\d+)?) ms\b")


def _p95(row: dict[str, Any]) -> float | None:
    """A row's p95 in ms: from its figures, else from its sentence (files before figures)."""
    figures = row.get("figures") or {}
    if figures.get("p95_ms") is not None:
        return float(figures["p95_ms"])
    found = P95.search(row.get("measured", ""))
    return float(found.group(1)) if found else None


def comparison(
    rows: list[dict[str, Any]], previous: dict[str, Any] | None
) -> dict[str, Any] | None:
    """NFR-1 and NFR-2 p95, this run beside the previous results file."""
    if previous is None:
        return None
    before = {r["id"]: r for r in previous["requirements"]}
    now = {r["id"]: r for r in rows}
    return {
        "date": previous["date"],
        "rows": [
            {"id": k, "previous_p95_ms": _p95(before[k]), "p95_ms": _p95(now[k])}
            for k in COMPARED
            if k in before and k in now
        ],
    }


def document(
    ev: Evidence, host: dict[str, Any], previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The results file: the requirements, the run, the machine, the limits raised, and NFR-1
    and NFR-2 beside the previous run's."""
    rows = requirements(ev)
    opened = datetime.fromtimestamp(ev.plan["start_ms"] / 1000, UTC)
    summaries = ev.records["rx_summary"]
    rejected: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    for s in summaries:
        rejected.update(s["rejected"])
        errors.update(s["errors"])
    reads = [
        ev.summary.get("metrics", {}).get(f"rest_{n}_ms") for n in ("listing", "search", "detail")
    ]
    return {
        "date": opened.date().isoformat(),
        "requirements": rows,
        "compared_with": comparison(rows, previous),
        "run": {
            "auction_id": ev.plan["auction_id"],
            "opened_at": opened.isoformat(),
            "scheduled_end_at": datetime.fromtimestamp(ev.plan["end_ms"] / 1000, UTC).isoformat(),
            "ended_at": ev.auction["end_time"],
            "extensions": ev.auction["extension_count"],
            "status": ev.auction["status"],
            "bidder_sockets": len(ev.bidder_ids),
            "bids_sent": sum(s["sent"] for s in summaries),
            "bids_acknowledged": sum(s["accepted"] for s in summaries),
            "bids_rejected": dict(sorted(rejected.items())),
            "replies_timed_out": sum(s["timeouts"] for s in summaries),
            "error_frames": dict(sorted(errors.items())),
            "ledger_bids": sum(n for _, n in ev.counts),
            "rest_reads": sum(int(m["values"]["count"]) for m in reads if m),
            "k6_warnings": [str(o.get("msg")) for o in ev.records["other"][:20]],
            "celery_queue_peak": {
                "waiting": ev.watched["celery_waiting_peak"],
                "in_flight": ev.watched["celery_in_flight_peak"],
            },
            "slowlog_entries_read": ev.watched["slowlog_entries"],
        },
        "stack": (
            "Docker Compose dev stack: web (nginx) in front of api and api-2 (one uvicorn "
            "process each), worker (Celery, CELERY_CONCURRENCY processes, default 4), beat, "
            "Postgres 16, Redis 7; production limits"
        ),
        "machine": host,
        "limits_raised": LIMITS_RAISED,
        "reproduce": REPRODUCE,
    }


def _compared(compared: dict[str, Any] | None) -> list[str]:
    if not compared:
        return []

    def ms(v: float | None) -> str:
        return _ms(v)

    return [
        f"## Compared with the run of {compared['date']}",
        "",
        "| Requirement | p95, " + compared["date"] + " | p95, this run |",
        "|---|---|---|",
        *[
            f"| {r['id']} | {ms(r['previous_p95_ms'])} | {ms(r['p95_ms'])} |"
            for r in compared["rows"]
        ],
        "",
    ]


def markdown(doc: dict[str, Any]) -> str:
    """loadtest/RESULTS.md, from the results file only."""

    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|")

    run, host, date = doc["run"], doc["machine"], doc["date"]
    lines = [
        "# Load test results",
        "",
        f"The run of {date}, against the {doc['stack']}. How each figure is measured, and why "
        "at that point, is in [README.md](README.md). Every figure here comes from "
        f"[results/{date}.json](results/{date}.json), which `bash loadtest/run.sh` writes "
        "together with this page.",
        "",
        "| Requirement | Target | Measured | Result | Method |",
        "|---|---|---|---|---|",
        *[
            f"| {r['id']} | {cell(r['target'])} | {cell(r['measured'])} | "
            f"{verdict(r)} | {cell(r['method'])} |"
            for r in doc["requirements"]
        ],
        "",
        *_compared(doc.get("compared_with")),
        "## The run",
        "",
        f"- One auction, `{run['auction_id']}`: opened {run['opened_at']}, scheduled to end "
        f"{run['scheduled_end_at']}; {run['extensions']} soft-close extensions, ended "
        f"{run['ended_at']}, {run['status']}.",
        f"- {run['bidder_sockets']} bidders, one socket each, through the web service. They bid "
        "the minimum they had last seen and held back while leading, each on its own jittered "
        "timer: every 2 s in the steady phase, every 1 s in the final minute (AS-4: bidding "
        "concentrated in the final minute).",
        f"- Bids: {run['bids_sent']} sent, {run['bids_acknowledged']} acknowledged as accepted, "
        f"{run['ledger_bids']} in the ledger; rejected by code {run['bids_rejected'] or 'none'}; "
        f"{run['replies_timed_out']} replies over 5 s; error frames "
        f"{run['error_frames'] or 'none'}.",
        f"- REST reads during the run: {run['rest_reads']}. Celery queue peak: "
        f"{run['celery_queue_peak']['waiting']} waiting, "
        f"{run['celery_queue_peak']['in_flight']} in flight.",
        f"- k6 warnings and errors: {len(run['k6_warnings']) or 'none'}.",
        *[f"  - {cell(w)}" for w in run["k6_warnings"]],
        "",
        "## Details",
        "",
    ]
    for r in doc["requirements"]:
        if r["details"]:
            lines += [f"### {r['id']}", "", *[f"- {cell(d)}" for d in r["details"]], ""]
    docker = host["docker"]
    lines += [
        "## Machine",
        "",
        "| | |",
        "|---|---|",
        f"| CPU | {host['cpu']}, {host['cores']} cores, {host['threads']} threads |",
        f"| RAM | {host['ram_gib']} GiB |",
        f"| OS | {host['os']} |",
        f"| Docker | {docker['server_version']} on {docker['operating_system']} (kernel "
        f"{docker['kernel']}): {docker['cpus']} CPUs, {docker['memory_gib']} GiB for every "
        "container together, the load generator included |",
        f"| k6 | {host['k6']}, the grafana/k6 image on the compose network |",
        "",
        "## Limits raised for the test",
        "",
    ]
    if doc["limits_raised"]:
        lines += [
            "| Limit | Production | For the test | Why |",
            "|---|---|---|---|",
            *[
                f"| {cell(x['limit'])} | {cell(x['production'])} | {cell(x['test'])} | "
                f"{cell(x['why'])} |"
                for x in doc["limits_raised"]
            ],
        ]
    else:
        lines.append("None. Every application limit kept its production value.")
    lines += ["", "## Reproduce", "", "```", *doc["reproduce"], "```", ""]
    return "\n".join(lines)
