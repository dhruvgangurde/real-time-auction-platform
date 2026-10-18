# Load test results

The run of 2026-10-18, against the Docker Compose dev stack: web (nginx) in front of api and api-2 (one uvicorn process each), worker (Celery, CELERY_CONCURRENCY processes, default 4), beat, Postgres 16, Redis 7; production limits. How each figure is measured, and why at that point, is in [README.md](README.md). Every figure here comes from [results/2026-10-18.json](results/2026-10-18.json), which `bash loadtest/run.sh` writes together with this page.

| Requirement | Target | Measured | Result | Method |
|---|---|---|---|---|
| NFR-1 | Arbitration execution within 10 ms at p95 | p95 0.121 ms; 27,651 EVALSHA entries for 17,532 accepted + 10,119 rejected bids | Pass | Server side: Redis's slow log at threshold 0 for the run, every execution of the place_bid script (by its SHA-1); assessed only when it holds one for every bid decided (both instances' FR-44 lines) |
| NFR-2 | Submission to delivery of the acceptance event to subscribed clients within 300 ms at p95 | p95 10.0 ms | Pass | Client side, k6: each bid's send time joined on its sequence number with its bid_placed receipt at every fifth socket, one clock |
| NFR-3 | Broadcast to client receipt within 150 ms at p95 | p95 7.0 ms | Pass | Client side, k6: each room event's receipt at every socket minus its server_time_ms, stamped by the publishing instance before the PUBLISH |
| NFR-4 | At least 20 accepted bids/s on one auction, within NFR-1 and NFR-2, none lost or duplicated | slowest 5 s window 39.6 bids/s | Pass | Server side, the ledger: accepted bids per 5 s window of acceptance time, from 10 s after the opening to the final minute |
| NFR-5 | At least 100 connected bidders in one room across at least 2 instances, no loss of event delivery | 110 sockets (api 55, api-2 55); 0 missing, 0 repeated deliveries | Pass | k6 sockets through the web service (round robin); subscriptions per instance from each instance's log; presence from Redis; every socket's bid_placed sequence numbers against the ledger's 1 to N |
| NFR-6 | Listings, search results and auction detail within 500 ms at p95 under AS-4 | listing p95 19.2 ms; search p95 21.9 ms; detail p95 30.1 ms | Pass | Client side, k6: request duration per endpoint through the web service, during the run |
| NFR-7 | Durable persistence within 2 s at p95 of acceptance; a failure to persist detected by reconciliation within 60 s | p95 275.1 ms; detection: tests 2/2 passed, reconciler every 30 s | Pass | Server side: Postgres polled every 100 ms, each bid row's first sight minus its acceptance time; the second clause by tests and the reconciler's schedule |
| NFR-8 | Counters exposed: accepted bids per second, rejections by reason, sockets per instance, queue depth, reconciliation gaps | counted 17,532 accepted (ledger 17,532) and 10,119 rejected by reason (as the FR-44 lines); sockets peak api 55, api-2 55 (as subscribed) | Pass | The platform's own counters (GET /api/v1/admin/overview, services/metrics.py), read by the watcher each second, held against the run's own figures: the ledger, both instances' FR-44 lines, the subscriptions per instance |
| NFR-28 | Zero lost and zero duplicated bids under load; zero duplicate settlements under concurrent close | 6/6 ledger checks passed on 17532 bids; concurrent-close tests 2/2 passed (OR-14) | Pass | Server side, after the close: the six ledger checks of Try to break it on the lot, fed every acknowledgement k6 received; and the concurrent-close tests |
| OR-6 | Daily logical backup, kept 7 days, restore demonstrated | restore drill of 2026-10-18: pass; 19 tables' rows, the six ledger checks (6/6) and 474 media files as before | Pass | Demonstration: the restore drill (backend/scripts/restore-drill.sh), its evidence in ops/evidence/; inspection: the backup service of docker-compose.yml, its retention tested (tests/ops/test_backup_script.py) |
| OR-12 | Every requirement mapped to a verification activity | no verification matrix found | Not assessed | Inspection: docs/verification.md, generated and checked by frontend/scripts/verification.mjs (npm run verification:check) |
| OR-14 | Settlement uniqueness verified with concurrent close invocations, and separately with the settlement lock disabled, the database constraint alone preventing duplication | concurrent-close tests 2/2 passed | Pass | Tests on the test database: ten closers at once settle once; with the lock disabled, the UNIQUE (auction_id) constraint alone keeps one settlement |
| OR-15 | Soft close verified at the boundary both ways and across repeated extensions to the cap; ordering under sustained load with zero lost and zero duplicated bids | boundary tests 4/4 passed; ordering under load: NFR-28's checks pass | Pass | The soft-close tests; the ledger checks of this run |

## Compared with the run of 2026-10-17

| Requirement | p95, 2026-10-17 | p95, this run |
|---|---|---|
| NFR-1 | 0.128 ms | 0.121 ms |
| NFR-2 | 10.0 ms | 10.0 ms |

## The run

- One auction, `d520ad8e-ff41-4924-a589-54e73919e2c3`: opened 2026-10-18T15:57:18.115000+00:00, scheduled to end 2026-10-18T16:01:18.115000+00:00; 10 soft-close extensions, ended 2026-10-18T16:02:58.115000+00:00, closed_sold.
- 110 bidders, one socket each, through the web service. They bid the minimum they had last seen and held back while leading, each on its own jittered timer: every 2 s in the steady phase, every 1 s in the final minute (AS-4: bidding concentrated in the final minute).
- Bids: 27651 sent, 17532 acknowledged as accepted, 17532 in the ledger; rejected by code {'AUCTION_CLOSED': 507, 'BID_TOO_LOW': 9612}; 0 replies over 5 s; error frames none.
- REST reads during the run: 2041. Celery queue peak: 2 waiting, 16 in flight.
- k6 warnings and errors: none.

## Details

### NFR-1

- settings in force, read back from Redis: at the start slowlog-log-slower-than 0, slowlog-max-len 100000; at the end slowlog-log-slower-than 0, slowlog-max-len 100000
- p50 0.076 ms, p99 0.181 ms, max 6.2 ms, the max at +300 s after the opening
- [NFR-1] place_bid.lua x1000: p50=0.274 ms  p95=0.357 ms  p99=0.481 ms  max=0.988 ms
- tests/bidding/test_latency.py::test_arbitration_p95_under_10ms: PASSED

### NFR-2

- 385704 deliveries at 22 sockets; p50 5.0 ms, p99 15.0 ms, max 143.0 ms, the max at +206 s after the opening
- api: 15 sampled sockets, p95 11.0 ms (n 262980)
- api-2: 7 sampled sockets, p95 10.0 ms (n 122724)
- the submitter's own bid_placed: p95 11.0 ms (n 17532)

### NFR-3

- 1929840 deliveries; median 3.0 ms, p99 10.0 ms, max 141.0 ms
- the submitter's acknowledgement (send to bid_accepted): p95 6.0 ms (n 17532)
- the max: when not recorded (k6 keeps only the trend's summary)

### NFR-4

- SRS: "The system shall sustain at least 20 accepted bids per second against a single auction without violating NFR-1 or NFR-2 and without any lost or duplicated bid." Window: the steady phase, from 10 s after the opening to the final minute (169 s), in 5 s windows of acceptance time; to sustain is read as every window at 20 or more.
- offered: 9,272 bids sent in the window, 54.9/s; 7,907 of them winnable when sent, 46.8/s (nobody had yet taken their price)
- accepted: slowest 5 s window 39.6/s, median window 42.4/s, slowest single second 32; acceptance ratio 77% (7,123 of 9,272)
- gaps between acceptances: p10 7 ms, p50 18 ms, p90 48 ms, p99 86 ms (7,122 gaps); by 10 ms band: 0-9 20%, 10-19 33%, 20-29 19%, 30-39 12%, 40-49 7%, 50-59 4%, 60+ 5%
- 17,532 bids accepted in the whole run
- NFR-1, NFR-2 and NFR-28 in the same run: NFR-1 pass, NFR-2 pass, NFR-28 pass

### NFR-5

- 110 sockets opened, 110 subscribed; presence peak 110
- 110 saw auction_closed; 0 of those with a final sequence number other than the ledger's 17532
- api: sockets closed by code {'1000': 55}
- api-2: sockets closed by code {'1000': 55}

### NFR-6

- listing: n 681, median 5.9 ms, max 69.8 ms
- search: n 680, median 6.8 ms, max 64.7 ms
- detail: n 680, median 11.1 ms, max 81.7 ms
- answered 200: 2041, otherwise: 0
- [NFR-6] list    n=100  p50=   4.4 ms  p95=   5.2 ms
- [NFR-6] search  n=100  p50=   7.0 ms  p95=   8.7 ms
- [NFR-6] detail  n=100  p50=   6.6 ms  p95=   7.0 ms
- tests/discovery/test_nfr6.py::test_listing_search_and_detail_p95_under_500_ms: PASSED

### NFR-7

- 17532 rows seen of 17532; p50 77.8 ms, p99 384.2 ms, max 489.1 ms, the max at +278 s after the opening (the 100 ms poll adds up to 100 ms to each figure)
- Celery queue peak: 2 waiting, 16 in flight
- tests/bidding/test_reconciliation.py::test_gap_not_covered_by_outbox_raises_alert: PASSED
- tests/test_tasks_unit.py::test_persist_bids_alerts_after_final_retry: PASSED

### NFR-8

- rejected, counted: {'AUCTION_CLOSED': 507, 'BID_TOO_LOW': 9612}; in the FR-44 lines: {'AUCTION_CLOSED': 507, 'BID_TOO_LOW': 9612}
- sockets peak per instance, counted: {'api': 55, 'api-2': 55}; subscribed: {'api': 55, 'api-2': 55}
- queue depth peak, counted: {'celery_waiting': 1, 'celery_unacked': 10, 'outbox_backlog': 2}
- every bid in the window is this lot's: nothing else bids on the dev stack during a run

### NFR-28

- Sequence numbers run from 1 to N with no gaps: pass ({'n': 17532, 'lowest': 1, 'highest': 17532, 'missing': []})
- N bids carry N distinct sequence numbers: no duplicates: pass ({'n': 17532, 'distinct': 17532})
- One winner: the highest bid, the earlier one on a tie: pass ({'winner': 'Bidder-f6a9', 'final_price': 18531000, 'sequence_no': 17532, 'expected_winner': 'Bidder-f6a9', 'expected_price': 18531000})
- One settlement: pass ({'settlements': 1})
- Closed once: pass ({'closes': 1})
- Every bid acknowledged as accepted is in the ledger: pass ({'acknowledged': 17532, 'missing': 0})
- acknowledged to k6: 17532 (17532 distinct); accepted in the instances' logs: 17532; in the ledger: 17532
- tests/closing/test_closing.py::test_ten_concurrent_closes_settle_once: PASSED
- tests/closing/test_closing.py::test_constraint_alone_keeps_one_settlement: PASSED

### OR-6

- ops/evidence/restore-drill-2026-10-18.md

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
