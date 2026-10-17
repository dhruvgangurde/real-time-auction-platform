# Load test results

The run of 2026-10-17, against the Docker Compose dev stack: web (nginx) in front of api and api-2 (one uvicorn process each), worker (Celery, CELERY_CONCURRENCY processes, default 4), beat, Postgres 16, Redis 7; production limits. How each figure is measured, and why at that point, is in [README.md](README.md). Every figure here comes from [results/2026-10-17.json](results/2026-10-17.json), which `bash loadtest/run.sh` writes together with this page.

| Requirement | Target | Measured | Result | Method |
|---|---|---|---|---|
| NFR-1 | Arbitration execution within 10 ms at p95 | p95 0.128 ms; 27,691 EVALSHA entries for 17,356 accepted + 10,335 rejected bids | Pass | Server side: Redis's slow log at threshold 0 for the run, every execution of the place_bid script (by its SHA-1); assessed only when it holds one for every bid decided (both instances' FR-44 lines) |
| NFR-2 | Submission to delivery of the acceptance event to subscribed clients within 300 ms at p95 | p95 10.0 ms | Pass | Client side, k6: each bid's send time joined on its sequence number with its bid_placed receipt at every fifth socket, one clock |
| NFR-3 | Broadcast to client receipt within 150 ms at p95 | p95 6.0 ms | Pass | Client side, k6: each room event's receipt at every socket minus its server_time_ms, stamped by the publishing instance before the PUBLISH |
| NFR-4 | At least 20 accepted bids/s on one auction, within NFR-1 and NFR-2, none lost or duplicated | slowest 5 s window 37.6 bids/s | Pass | Server side, the ledger: accepted bids per 5 s window of acceptance time, from 10 s after the opening to the final minute |
| NFR-5 | At least 100 connected bidders in one room across at least 2 instances, no loss of event delivery | 110 sockets (api 55, api-2 55); 0 missing, 0 repeated deliveries | Pass | k6 sockets through the web service (round robin); subscriptions per instance from each instance's log; presence from Redis; every socket's bid_placed sequence numbers against the ledger's 1 to N |
| NFR-6 | Listings, search results and auction detail within 500 ms at p95 under AS-4 | listing p95 19.5 ms; search p95 18.3 ms; detail p95 26.5 ms | Pass | Client side, k6: request duration per endpoint through the web service, during the run |
| NFR-7 | Durable persistence within 2 s at p95 of acceptance; a failure to persist detected by reconciliation within 60 s | p95 159.9 ms; detection: tests 2/2 passed, reconciler every 30 s | Pass | Server side: Postgres polled every 100 ms, each bid row's first sight minus its acceptance time; the second clause by tests and the reconciler's schedule |
| NFR-8 | Counters exposed: accepted bids per second, rejections by reason, sockets per instance, queue depth, reconciliation gaps | not exposed: the platform has no metrics endpoint or counters | Not met - Phase 11 part 2 | Inspection |
| NFR-28 | Zero lost and zero duplicated bids under load; zero duplicate settlements under concurrent close | 6/6 ledger checks passed on 17356 bids; concurrent-close tests 2/2 passed (OR-14) | Pass | Server side, after the close: the six ledger checks of Try to break it on the lot, fed every acknowledgement k6 received; and the concurrent-close tests |
| OR-6 | Daily logical backup, kept 7 days, restore demonstrated | no scheduled backup; restore not demonstrated | Not met - Phase 11 part 2 | Inspection (operational, not a load-test measurement) |
| OR-12 | Every requirement mapped to a verification activity | not assessed by the load test | Not assessed - verification matrix in Phase 11 part 2 | Inspection of docs/testing.md and docs/requirements-map.md |
| OR-14 | Settlement uniqueness verified with concurrent close invocations, and separately with the settlement lock disabled, the database constraint alone preventing duplication | concurrent-close tests 2/2 passed | Pass | Tests on the test database: ten closers at once settle once; with the lock disabled, the UNIQUE (auction_id) constraint alone keeps one settlement |
| OR-15 | Soft close verified at the boundary both ways and across repeated extensions to the cap; ordering under sustained load with zero lost and zero duplicated bids | boundary tests 4/4 passed; ordering under load: NFR-28's checks pass | Pass | The soft-close tests; the ledger checks of this run |

## The run

- One auction, `1cf3e5ad-3cc3-4445-831d-ac1ffd82fb56`: opened 2026-10-17T17:50:00.224000+00:00, scheduled to end 2026-10-17T17:54:00.224000+00:00; 10 soft-close extensions, ended 2026-10-17T17:55:40.224000+00:00, closed_sold.
- 110 bidders, one socket each, through the web service. They bid the minimum they had last seen and held back while leading, each on its own jittered timer: every 2 s in the steady phase, every 1 s in the final minute (AS-4: bidding concentrated in the final minute).
- Bids: 27691 sent, 17356 acknowledged as accepted, 17356 in the ledger; rejected by code {'AUCTION_CLOSED': 504, 'BID_TOO_LOW': 9831}; 0 replies over 5 s; error frames none.
- REST reads during the run: 2040. Celery queue peak: 3 waiting, 16 in flight.
- k6 warnings and errors: none.

## Details

### NFR-1

- settings in force, read back from Redis: at the start slowlog-log-slower-than 0, slowlog-max-len 100000; at the end slowlog-log-slower-than 0, slowlog-max-len 100000
- p50 0.081 ms, p99 0.172 ms, max 11.9 ms
- [NFR-1] place_bid.lua x1000: p50=0.297 ms  p95=0.378 ms  p99=0.435 ms  max=0.563 ms
- tests/bidding/test_latency.py::test_arbitration_p95_under_10ms: PASSED

### NFR-2

- 381832 deliveries at 22 sockets; p50 6.0 ms, p99 26.0 ms, max 280.0 ms
- api: 11 sampled sockets, p95 11.0 ms (n 190916)
- api-2: 11 sampled sockets, p95 10.0 ms (n 190916)
- the submitter's own bid_placed: p95 11.0 ms (n 17356)

### NFR-3

- 1910480 deliveries; median 3.0 ms, p99 10.0 ms, max 277.0 ms
- the submitter's acknowledgement (send to bid_accepted): p95 6.0 ms (n 17356)

### NFR-4

- SRS: "The system shall sustain at least 20 accepted bids per second against a single auction without violating NFR-1 or NFR-2 and without any lost or duplicated bid." Window: the steady phase, from 10 s after the opening to the final minute (169 s), in 5 s windows of acceptance time; to sustain is read as every window at 20 or more.
- offered: 9,286 bids sent in the window, 54.9/s; 7,759 of them winnable when sent, 45.9/s (nobody had yet taken their price)
- accepted: slowest 5 s window 37.6/s, median window 40.8/s, slowest single second 31; acceptance ratio 74% (6,915 of 9,286)
- gaps between acceptances: p10 8 ms, p50 19 ms, p90 48 ms, p99 88 ms (6,914 gaps); by 10 ms band: 0-9 17%, 10-19 34%, 20-29 21%, 30-39 12%, 40-49 7%, 50-59 4%, 60+ 5%
- 17,356 bids accepted in the whole run
- NFR-1, NFR-2 and NFR-28 in the same run: NFR-1 pass, NFR-2 pass, NFR-28 pass

### NFR-5

- 110 sockets opened, 110 subscribed; presence peak 110
- 110 saw auction_closed; 0 of those with a final sequence number other than the ledger's 17356
- api: sockets closed by code {'1000': 55}
- api-2: sockets closed by code {'1000': 55}

### NFR-6

- listing: n 680, median 7.2 ms, max 63.8 ms
- search: n 680, median 7.1 ms, max 78.4 ms
- detail: n 680, median 12.7 ms, max 81.9 ms
- answered 200: 2040, otherwise: 0
- [NFR-6] list    n=100  p50=   5.0 ms  p95=  11.5 ms
- [NFR-6] search  n=100  p50=   8.1 ms  p95=  10.0 ms
- [NFR-6] detail  n=100  p50=   7.7 ms  p95=   9.6 ms
- tests/discovery/test_nfr6.py::test_listing_search_and_detail_p95_under_500_ms: PASSED

### NFR-7

- 17356 rows seen of 17356; p50 72.2 ms, p99 218.0 ms, max 301.0 ms
- Celery queue peak: 3 waiting, 16 in flight
- tests/bidding/test_reconciliation.py::test_gap_not_covered_by_outbox_raises_alert: PASSED
- tests/test_tasks_unit.py::test_persist_bids_alerts_after_final_retry: PASSED

### NFR-8

- this run measured those quantities itself (k6, the ledger, the logs, Redis)

### NFR-28

- Sequence numbers run from 1 to N with no gaps: pass ({'n': 17356, 'lowest': 1, 'highest': 17356, 'missing': []})
- N bids carry N distinct sequence numbers: no duplicates: pass ({'n': 17356, 'distinct': 17356})
- One winner: the highest bid, the earlier one on a tie: pass ({'winner': 'Bidder-a9aa', 'final_price': 18355000, 'sequence_no': 17356, 'expected_winner': 'Bidder-a9aa', 'expected_price': 18355000})
- One settlement: pass ({'settlements': 1})
- Closed once: pass ({'closes': 1})
- Every bid acknowledged as accepted is in the ledger: pass ({'acknowledged': 17356, 'missing': 0})
- acknowledged to k6: 17356 (17356 distinct); accepted in the instances' logs: 17356; in the ledger: 17356
- tests/closing/test_closing.py::test_ten_concurrent_closes_settle_once: PASSED
- tests/closing/test_closing.py::test_constraint_alone_keeps_one_settlement: PASSED

### OR-14

- tests/closing/test_closing.py::test_ten_concurrent_closes_settle_once: PASSED
- tests/closing/test_closing.py::test_constraint_alone_keeps_one_settlement: PASSED

### OR-15

- this run: 10 extensions, closed closed_sold
- tests/bidding/test_soft_close.py::test_bid_61s_before_end_does_not_extend: PASSED
- tests/bidding/test_soft_close.py::test_bid_59s_before_end_extends_by_extension: PASSED
- tests/bidding/test_soft_close.py::test_three_consecutive_late_bids_each_extend: PASSED
- tests/bidding/test_soft_close.py::test_extension_cap_of_ten: PASSED

## Machine

| | |
|---|---|
| CPU | Intel(R) Core(TM) i9-14900HX, 24 cores, 32 threads |
| RAM | 15.7 GiB |
| OS | Microsoft Windows 11 Home Single Language |
| Docker | 29.8.2 on Docker Desktop (containerized) (kernel 6.18.54.1-microsoft-standard-WSL2): 32 CPUs, 7.6 GiB for every container together, the load generator included |
| k6 | k6 v2.3.0 (commit/e088784614, go1.27.1, linux/amd64), the grafana/k6 image on the compose network |

## Limits raised for the test

None. Every application limit kept its production value.

## Reproduce

```
cp .env.example .env    # first run only
docker compose up -d --build
bash loadtest/run.sh    # about 10 minutes; writes this page and results/<date>.json
```
