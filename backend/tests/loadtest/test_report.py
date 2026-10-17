"""The load test's report (Phase 11, loadtest/README.md), from synthetic runs: how each
requirement's figure is computed from what the run left behind, and when it passes. Each target
is the SRS's own (NFR-1 to NFR-7, NFR-28, OR-15); NFR-8 and OR-6 are not met by inspection and
are reported so, whatever the run measured."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.loadtest import (
    CITED,
    PLACE_BID_SHA,
    Evidence,
    document,
    end_to_end_ms,
    gap_distribution,
    instance_log,
    k6_records,
    markdown,
    nfr1_verdict,
    offered_bids,
    per_second,
    percentile,
    place_bid_durations,
    pytest_results,
    requirements,
    verdict,
    window_rates,
)

LOT = "6f1b2c3d-0000-4000-8000-000000000001"
IN_FORCE = {"slowlog-log-slower-than": "0", "slowlog-max-len": "100000"}
START_MS = 1_800_000_000_000
END_MS = START_MS + 120_000  # steady phase: 10 s after the opening to 60 s before the end


def test_every_cited_test_exists() -> None:
    """A renamed test would be reported "not run"; this says so at once instead."""
    backend = Path(__file__).resolve().parents[2]
    for nodes in CITED.values():
        for node in nodes:
            path, name = node.split("::")
            assert f"def {name}(" in (backend / path).read_text(encoding="utf-8"), node


def test_place_bid_executions_are_picked_out_by_the_script_sha() -> None:
    other = "0123456789abcdef0123456789abcdef01234567"
    entries = [  # raw SLOWLOG GET entries: id, start, duration (us), words, client, name
        [9, 1, 41, [b"EVALSHA", PLACE_BID_SHA.encode(), b"4", b"k"], "10.0.0.2:1", ""],
        [8, 1, 7, ["evalsha", PLACE_BID_SHA.upper(), "4", "k"], "10.0.0.2:1", ""],
        [7, 1, 900, ["EVALSHA", other, "1", "k"], "10.0.0.2:1", ""],
        [6, 1, 3, ["PUBLISH", "auction:x", "{...}"], "10.0.0.2:1", ""],
        [5, 1, 1, ["EVALSHA"], "10.0.0.2:1", ""],
        [4, 1, 1, ["PING"], "10.0.0.2:1", ""],
    ]
    assert place_bid_durations(entries) == [41, 7]


@pytest.mark.parametrize(
    ("p95_ms", "executions", "decided", "verdict"),
    [
        (0.08, 6000, 6000, True),  # one execution per bid decided, well within 10 ms
        (10.0, 6000, 6000, True),
        (10.5, 6000, 6000, False),  # every bid sampled, over the target
        (0.08, 5999, 6000, None),  # one bid without its execution: not assessed from it
        (0.08, 6001, 6000, None),  # more executions than bids (a retried call): not assessed
        (None, 0, 0, None),  # no bids at all
    ],
)
def test_nfr1_is_assessed_only_when_the_slow_log_holds_every_bid(
    p95_ms: float | None, executions: int, decided: int, verdict: bool | None
) -> None:
    assert nfr1_verdict(p95_ms, executions, decided) is verdict


def test_percentiles_are_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]  # 1 .. 100
    assert percentile(values, 95) == 95
    assert percentile(values, 50) == 50
    assert percentile([3.0], 99) == 3
    assert percentile([], 95) is None


def k6_line(record: dict[str, Any], level: str = "info") -> str:
    return json.dumps({"level": level, "msg": json.dumps(record), "source": "console"})


def test_k6_records_are_read_from_its_json_log() -> None:
    lines = [
        k6_line({"e": "ack", "vu": 0, "seq": 1, "bidder": "u", "amount": 5, "sent": 1, "acked": 2}),
        k6_line({"e": "rx", "vu": 4, "seq": 1, "t": 3, "st": 2}),
        k6_line({"e": "error_frame", "vu": 4, "code": "RATE_LIMITED"}, level="error"),
        json.dumps({"level": "warning", "msg": "Request Failed", "source": "k6"}),
        json.dumps({"level": "info", "msg": "not a record"}),
        "not json at all",
    ]
    found = k6_records(lines)
    assert [a["seq"] for a in found["ack"]] == [1]
    assert [x["t"] for x in found["rx"]] == [3]
    assert [o["level"] for o in found["other"]] == ["error", "warning"]


def test_nfr2_joins_each_receipt_with_its_bid_send_time() -> None:
    acks = [{"seq": 1, "sent": 1000}, {"seq": 2, "sent": 2000}]
    rx = [{"seq": 1, "t": 1012}, {"seq": 2, "t": 2030}, {"seq": 2, "t": 2009}, {"seq": 3, "t": 9}]
    assert end_to_end_ms(acks, rx) == [12, 30, 9]  # seq 3 was never acknowledged to k6


def test_nfr4_counts_whole_windows_and_quiet_seconds() -> None:
    at = datetime.fromtimestamp(START_MS / 1000, UTC)
    counts = [(at + timedelta(seconds=s), 30) for s in range(12) if s != 3]  # second 3 had none
    seconds = per_second(counts, START_MS, START_MS + 12_000)
    assert seconds == [30, 30, 30, 0, 30, 30, 30, 30, 30, 30, 30, 30]
    assert window_rates(seconds) == [24.0, 30.0]  # the last two seconds make no whole window


def test_an_instance_log_gives_its_sockets_on_the_lot_and_its_bid_decisions() -> None:
    def line(msg: str, **fields: Any) -> str:
        return json.dumps({"ts": "t", "level": "INFO", "msg": msg, **fields})

    other = "6f1b2c3d-0000-4000-8000-000000000002"
    log = "\n".join(
        [
            "INFO:     Started server process [7]",
            line("ws connected", conn_id="c1"),
            line("ws authenticated", conn_id="c1", user_id="u1"),
            line("ws subscribed", conn_id="c1", auction_id=LOT),
            line("ws authenticated", conn_id="c2", user_id="u2"),
            line("ws subscribed", conn_id="c2", auction_id=other),
            line("bid", auction_id=LOT, decision="accepted", code=None),
            line("bid", auction_id=LOT, decision="rejected", code="BID_TOO_LOW"),
            line("bid", auction_id=other, decision="accepted", code=None),
            line("ws closed", conn_id="c1", code=1000, reason=""),
            line("ws closed", conn_id="c2", code=1006, reason=""),
        ]
    )
    assert instance_log(log, LOT) == {
        "subscribed_users": ["u1"],
        "closes": {"1000": 1},
        "bids": {"accepted": 1, "BID_TOO_LOW": 1},
    }


def test_pytest_outcomes_and_printed_measurements() -> None:
    text = (
        "tests/bidding/test_latency.py [NFR-1] place_bid.lua x1000: p50=0.05 ms p95=0.08 ms\n"
        ".\n"
        "PASSED tests/bidding/test_latency.py::test_arbitration_p95_under_10ms\n"
        "FAILED tests/test_tasks_unit.py::test_persist_bids_alerts_after_final_retry - assert\n"
    )
    outcomes, printed = pytest_results(text)
    assert outcomes == {
        "tests/bidding/test_latency.py::test_arbitration_p95_under_10ms": "PASSED",
        "tests/test_tasks_unit.py::test_persist_bids_alerts_after_final_retry": "FAILED",
    }
    assert printed == ["[NFR-1] place_bid.lua x1000: p50=0.05 ms p95=0.08 ms"]


# ---------------------------------------------------------------- the verdicts


def trend(p95: float, n: int = 1000) -> dict[str, Any]:
    return {"values": {"med": p95 / 2, "p(95)": p95, "p(99)": p95, "max": p95, "count": n}}


BIDS = {"accepted": 1800, "BID_TOO_LOW": 1200}  # each instance's FR-44 lines


def accepted_ms(seq: int) -> int:
    """The synthetic run's acceptances: 30 a second from the opening, 33 ms apart."""
    second, i = divmod(seq - 1, 30)
    return START_MS + second * 1000 + i * 33


def amount(seq: int) -> int:
    return 1_000_000 + seq * 1_000


def passing() -> Evidence:
    """A run that meets every target the load test measures: 110 sockets, 55 on each instance,
    30 accepted bids every second, every delivery made once. Two in three price steps are also
    bid for by a second bidder a millisecond after the winner, so 50 bids a second are offered,
    all of them winnable when sent."""
    users = [f"u{i}" for i in range(110)]
    at = datetime.fromtimestamp(START_MS / 1000, UTC)
    counts = [(at + timedelta(seconds=s), 30) for s in range(120)]
    n = 30 * 120
    acks = [
        {
            "vu": s % 110,
            "seq": s,
            "amount": amount(s),
            "sent": accepted_ms(s) - 2,
            "acked": accepted_ms(s) + 2,
        }
        for s in range(1, n + 1)
    ]
    rejections = [
        {
            "vu": (s + 1) % 110,
            "amount": amount(s),
            "sent": accepted_ms(s) - 1,
            "code": "BID_TOO_LOW",
        }
        for s in range(1, n + 1)
        if s % 3
    ]
    rx = [
        {"vu": vu, "seq": s, "t": accepted_ms(s) + 38, "st": accepted_ms(s) + 3}
        for vu in (4, 9)
        for s in (1, 2)
    ]
    summaries = [
        {
            "vu": i,
            "subscribed": True,
            "duplicates": 0,
            "missing": 0,
            "saw_closed": True,
            "final_seq": n,
            "sent": 40,
            "accepted": 30,
            "rejected": {"BID_TOO_LOW": 10},
            "timeouts": 0,
            "errors": {},
        }
        for i in range(110)
    ]
    checks = [{"id": c, "label": c, "passed": True, "value": {}} for c in ("gapless", "acked")]
    return Evidence(
        plan={"auction_id": LOT, "start_ms": START_MS, "end_ms": END_MS, "soft_close_s": 10},
        bidder_ids=users,
        watched={
            "settled": True,
            "arbitration_us": [80] * 5760 + [400] * 240,  # one per bid decided: 3,600 + 2,400
            "slowlog_entries": 20_000,
            "slowlog_settings": {"start": IN_FORCE, "end": IN_FORCE},
            "persistence_ms": [120.0] * n,
            "presence_peak": 110,
            "celery_waiting_peak": 3,
            "celery_in_flight_peak": 4,
        },
        records={"ack": acks, "rej": rejections, "rx": rx, "rx_summary": summaries, "other": []},
        summary={
            "metrics": {
                "nfr3_delivery_ms": trend(20.0),
                "nfr2_own_broadcast_ms": trend(25.0),
                "bid_ack_ms": trend(5.0),
                "rest_listing_ms": trend(40.0),
                "rest_search_ms": trend(60.0),
                "rest_detail_ms": trend(30.0),
                "checks": {"values": {"passes": 3000, "fails": 0, "rate": 1}},
            }
        },
        instances={
            "api": {"subscribed_users": users[0::2], "closes": {"1000": 55}, "bids": BIDS},
            "api-2": {"subscribed_users": users[1::2], "closes": {"1000": 55}, "bids": BIDS},
        },
        counts=counts,
        acceptances=[(s, amount(s), accepted_ms(s)) for s in range(1, n + 1)],
        auction={"status": "closed_sold", "end_time": "t", "extension_count": 10},
        checks=checks,
        outcomes={node: "PASSED" for nodes in CITED.values() for node in nodes},
        printed=["[NFR-1] place_bid.lua x1000: p95=0.08 ms", "[NFR-6] listing n=200 p95=40 ms"],
        reconcile_s=30.0,
    )


def verdicts(ev: Evidence) -> dict[str, bool | None]:
    return {r["id"]: r["pass"] for r in requirements(ev)}


def test_a_run_meeting_every_measured_target() -> None:
    assert verdicts(passing()) == {
        "NFR-1": True,
        "NFR-2": True,
        "NFR-3": True,
        "NFR-4": True,
        "NFR-5": True,
        "NFR-6": True,
        "NFR-7": True,
        "NFR-8": False,  # no counters are exposed, whatever the run shows
        "NFR-28": True,
        "OR-6": False,  # no scheduled backup, no restore demonstrated
        "OR-12": None,  # not a load-test measurement
        "OR-14": True,
        "OR-15": True,
    }
    rows = {r["id"]: r for r in requirements(passing())}
    assert rows["NFR-1"]["measured"] == (
        "p95 0.080 ms; 6,000 EVALSHA entries for 3,600 accepted + 2,400 rejected bids"
    )
    assert rows["NFR-1"]["details"][0] == (
        "settings in force, read back from Redis: at the start slowlog-log-slower-than 0, "
        "slowlog-max-len 100000; at the end slowlog-log-slower-than 0, slowlog-max-len 100000"
    )
    assert rows["NFR-2"]["measured"] == "p95 40.0 ms"
    assert rows["NFR-4"]["measured"] == "slowest 5 s window 30.0 bids/s"
    assert rows["NFR-5"]["measured"] == (
        "110 sockets (api 55, api-2 55); 0 missing, 0 repeated deliveries"
    )


def test_nfr4_reports_what_was_offered_accepted_and_the_gaps_beside_the_srs_wording() -> None:
    details = {r["id"]: r for r in requirements(passing())}["NFR-4"]["details"]
    assert details[0].startswith(
        'SRS: "The system shall sustain at least 20 accepted bids per second against a single '
        'auction without violating NFR-1 or NFR-2 and without any lost or duplicated bid." '
        "Window: the steady phase, from 10 s after the opening to the final minute (50 s)"
    )
    assert details[1] == (
        "offered: 2,500 bids sent in the window, 50.0/s; 2,500 of them winnable when sent, "
        "50.0/s (nobody had yet taken their price)"
    )
    assert details[2] == (
        "accepted: slowest 5 s window 30.0/s, median window 30.0/s, slowest single second 30; "
        "acceptance ratio 60% (1,500 of 2,500)"
    )
    assert details[3] == (
        "gaps between acceptances: p10 33 ms, p50 33 ms, p90 33 ms, p99 43 ms (1,499 gaps); "
        "by 10 ms band: 0-9 0%, 10-19 0%, 20-29 0%, 30-39 97%, 40-49 3%, 50-59 0%, 60+ 0%"
    )


def test_a_bid_sent_after_its_price_was_taken_was_not_winnable() -> None:
    acceptances = [(1, 5_000, 1_000), (2, 5_100, 1_050)]
    acks = [{"sent": 990}, {"sent": 1_040}]
    rejections = [
        {"amount": 5_000, "sent": 998, "code": "BID_TOO_LOW"},  # in flight: taken at 1,000
        {"amount": 5_000, "sent": 1_002, "code": "BID_TOO_LOW"},  # sent after it was taken
        {"amount": 5_100, "sent": 1_050, "code": "BID_TOO_LOW"},  # the same millisecond
        {"amount": 5_200, "sent": 1_060, "code": "AUCTION_CLOSED"},
    ]
    assert offered_bids(acks, rejections, acceptances, 0, 2_000) == {"sent": 6, "winnable": 4}
    assert offered_bids(acks, rejections, acceptances, 1_000, 2_000) == {"sent": 4, "winnable": 2}
    assert gap_distribution(acceptances, 0, 1_050) == "n/a"  # one acceptance, no gap


def test_the_result_cell_says_where_an_open_requirement_is_taken_up() -> None:
    assert verdict({"pass": True, "note": None}) == "Pass"
    assert verdict({"pass": False, "note": None}) == "**Not met**"  # a measured miss
    assert verdict({"pass": False, "note": "Phase 11 part 2"}) == "Not met - Phase 11 part 2"
    assert verdict({"pass": None, "note": "verification matrix in Phase 11 part 2"}) == (
        "Not assessed - verification matrix in Phase 11 part 2"
    )


def with_watched(ev: Evidence, **changes: Any) -> Evidence:
    return replace(ev, watched={**ev.watched, **changes})


def with_summaries(ev: Evidence, i: int, **changes: Any) -> Evidence:
    summaries = [dict(s) for s in ev.records["rx_summary"]]
    summaries[i].update(changes)
    return replace(ev, records={**ev.records, "rx_summary": summaries})


def with_metric(ev: Evidence, name: str, value: dict[str, Any]) -> Evidence:
    return replace(ev, summary={"metrics": {**ev.summary["metrics"], name: value}})


FAILURES = {
    "arbitration over 10 ms at p95 (also fails NFR-4)": (
        lambda ev: with_watched(ev, arbitration_us=[11_000] * 6000),
        {"NFR-1", "NFR-4"},
    ),
    "the slow log one execution short of the bids decided (also NFR-4)": (
        lambda ev: with_watched(ev, arbitration_us=[80] * 5999),
        {"NFR-1", "NFR-4"},
    ),
    "a bid refused before arbitration, so without an execution (also NFR-4)": (
        lambda ev: replace(
            ev,
            instances={
                **ev.instances,
                "api": {**ev.instances["api"], "bids": {**BIDS, "RATE_LIMITED": 1}},
            },
        ),
        {"NFR-1", "NFR-4"},
    ),
    "deliveries 301 ms after the send (also fails NFR-4)": (
        lambda ev: replace(
            ev,
            records={**ev.records, "rx": [{"vu": 4, "seq": 1, "t": accepted_ms(1) - 2 + 301}]},
        ),
        {"NFR-2", "NFR-4"},
    ),
    "broadcast to receipt 151 ms at p95": (
        lambda ev: with_metric(ev, "nfr3_delivery_ms", trend(151.0)),
        {"NFR-3"},
    ),
    # the same bids, two seconds' worth moved into the final minute: one window holds 18 a second
    "two quiet seconds in one window of the steady phase": (
        lambda ev: replace(
            ev,
            counts=[(at, {20: 0, 21: 0, 100: 90}.get(i, n)) for i, (at, n) in enumerate(ev.counts)],
        ),
        {"NFR-4"},
    ),
    "a socket missed one bid_placed": (lambda ev: with_summaries(ev, 7, missing=1), {"NFR-5"}),
    "a socket got one bid_placed twice": (
        lambda ev: with_summaries(ev, 7, duplicates=1),
        {"NFR-5"},
    ),
    "a socket never saw the close": (lambda ev: with_summaries(ev, 7, saw_closed=False), {"NFR-5"}),
    "every socket on one instance": (
        lambda ev: replace(
            ev,
            instances={
                "api": {**ev.instances["api"], "subscribed_users": ev.bidder_ids},
                "api-2": {**ev.instances["api-2"], "subscribed_users": []},
            },
        ),
        {"NFR-5"},
    ),
    "fewer than 100 sockets in the room at once": (
        lambda ev: with_watched(ev, presence_peak=99),
        {"NFR-5"},
    ),
    "search over 500 ms at p95": (
        lambda ev: with_metric(ev, "rest_search_ms", trend(501.0)),
        {"NFR-6"},
    ),
    "a REST read not answered 200": (
        lambda ev: with_metric(ev, "checks", {"values": {"passes": 2999, "fails": 1}}),
        {"NFR-6"},
    ),
    "persistence over 2 s at p95": (
        lambda ev: with_watched(ev, persistence_ms=[2001.0] * 100),
        {"NFR-7"},
    ),
    "a reconciliation test not run": (
        lambda ev: replace(
            ev, outcomes={k: v for k, v in ev.outcomes.items() if k not in CITED["NFR-7"][:1]}
        ),
        {"NFR-7"},
    ),
    "an acknowledged bid missing from the ledger (also NFR-4, OR-15)": (
        lambda ev: replace(
            ev, checks=[*ev.checks[:1], {**ev.checks[1], "passed": False, "value": {"missing": 1}}]
        ),
        {"NFR-28", "NFR-4", "OR-15"},
    ),
    "a concurrent-close test failed (also NFR-4)": (
        lambda ev: replace(ev, outcomes={**ev.outcomes, CITED["OR-14"][1]: "FAILED"}),
        {"NFR-28", "NFR-4", "OR-14"},
    ),
    "a soft-close boundary test failed": (
        lambda ev: replace(ev, outcomes={**ev.outcomes, CITED["OR-15"][0]: "FAILED"}),
        {"OR-15"},
    ),
}


@pytest.mark.parametrize("failure", list(FAILURES))
def test_each_missed_target_fails_its_requirements_and_no_other(failure: str) -> None:
    make, failing = FAILURES[failure]
    before, after = verdicts(passing()), verdicts(make(passing()))
    assert {k for k in before if before[k] is True and after[k] is not True} == failing


def test_results_page_has_a_row_per_requirement_and_says_no_limit_was_raised() -> None:
    ev = passing()
    host = {
        "cpu": "CPU",
        "cores": 8,
        "threads": 16,
        "ram_gib": 32.0,
        "os": "OS",
        "docker": {
            "server_version": "1",
            "operating_system": "Docker Desktop",
            "kernel": "6",
            "cpus": 16,
            "memory_gib": 15.6,
        },
        "k6": "k6 v2.3.0",
    }
    doc = document(ev, host)
    page = markdown(doc)
    rows = [line for line in page.splitlines() if line.startswith(("| NFR-", "| OR-"))]
    assert [row.split(" | ")[0][2:] for row in rows] == [r["id"] for r in doc["requirements"]]
    by_id = {row.split(" | ")[0][2:]: row for row in rows}
    assert "| Not met - Phase 11 part 2 |" in by_id["NFR-8"]
    assert "| Not met - Phase 11 part 2 |" in by_id["OR-6"]
    assert "| Not assessed - verification matrix in Phase 11 part 2 |" in by_id["OR-12"]
    assert "| Pass |" in by_id["OR-14"]
    assert "**Not met**" not in page  # every measured target met
    assert "None. Every application limit kept its production value." in page
    assert doc["date"] == "2027-01-15"
    assert doc["run"]["bids_rejected"] == {"BID_TOO_LOW": 1100}
