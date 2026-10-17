# Load test (Phase 11)

The SRS states its performance targets "at the 95th percentile under the load model of AS-4" (section 5.1):

> AS-4 Peak load for Release 1.0 is modelled as a single popular auction attracting up to 100 concurrently connected bidders, with bidding activity concentrated in the final minute. This is the load model against which all performance targets in Section 5.1 are stated and verified.

> Verification: NFR-1 to NFR-7 shall be verified by a scripted load test (k6 or Locust) executed against the Docker Compose stack on a single development machine before the final demonstration, with results recorded in the summary table of Appendix B.6.

Below, each requirement the load-test phase covers (`docs/requirements-map.md`, Phase 11), with its exact wording, where it is measured and why that point is the one the wording names. No target is added or changed: the measurement windows and sampling are stated where the wording leaves them open.

**Clocks.** k6, the api instances, the tools container, Postgres and Redis all run as containers in one Docker VM and read the same kernel clock, so a time taken in one container can be compared with a time taken in another. Each cross-container measurement below says which clocks it uses.

## Where each requirement is measured

### NFR-1
> The bid accept-or-reject decision, measured as the execution time of arbitration within the real-time layer, shall complete within 10 ms at the 95th percentile.

- **Measured:** server side, by Redis itself. For the run's duration the Redis slow log records every command (threshold 0), and the tools container keeps every `EVALSHA` of the `place_bid` script (matched by its SHA-1). Each entry's duration is the script's execution time inside Redis.
- **Why there:** arbitration is that one Lua script (DC-2, FR-35); its execution inside Redis is "the execution time of arbitration within the real-time layer", with no client or network time added.
- **Also:** `tests/bidding/test_latency.py` measures the same script 1,000 times in isolation.

### NFR-2
> End-to-end bid latency, measured from receipt of the client's bid submission to delivery of the corresponding acceptance event to subscribed clients, shall be within 300 ms at the 95th percentile. A delay approaching one second would change auction outcomes in the final seconds, whereas 300 ms preserves the perception of immediacy.

- **Measured:** client side, in k6.
  - Every bidder records the time it sends each bid and, once accepted, the bid's sequence number.
  - A sample of subscribed bidders, every tenth socket, which the load balancer spreads over both instances, records when each `bid_placed` arrives.
  - After the run the two records are joined on the sequence number: one latency per (accepted bid, sampled subscriber).
- **Clocks:** both times come from the one k6 process, so a single clock.
- **Why there:**
  - It ends where the wording ends: delivery of the acceptance event to subscribed clients, on both instances.
  - It starts at the client's send, a fraction of a millisecond before the server's receipt on the same host network. So it slightly overstates the requirement's interval, never understates it.

### NFR-3
> WebSocket event delivery latency, measured from broadcast to client receipt on the local network, shall be within 150 ms at the 95th percentile. The submitting bidder's own acknowledgement shall be sent as soon as arbitration returns, without waiting for the broadcast or the durable write, so that the submitter's perceived latency is bounded by NFR-1 plus network transit.

- **Measured:** client side, in k6, for every `bid_placed` at every subscribed socket: receipt time minus the event's `server_time_ms`. The publishing instance stamps that just before the PUBLISH (`services/live_events.py` `event()`).
- **Clocks:** k6's clock against the publishing api container's clock.
- **Why there:** from broadcast (the publish) to client receipt, over the compose network.
- **The second sentence:** the submitter's acknowledgement time (send to `bid_accepted`) is also measured in k6. The ordering it requires, acknowledgement before publish and durable write (FR-40), is covered by the existing bidding and WebSocket tests.

### NFR-4
> The system shall sustain at least 20 accepted bids per second against a single auction without violating NFR-1 or NFR-2 and without any lost or duplicated bid.

- **Measured:** server side, from the ledger. Accepted bids on the one load-test auction are counted per 5-second window of their acceptance time (`bids.created_at`, which the persister sets from the Redis acceptance time) during the steady phase.
- **Pass criterion:**
  - every window holds at least 100 (20 a second);
  - and, in the same run, NFR-1, NFR-2 and NFR-28 pass.
- **Why there:** the ledger is the record of accepted bids, and per-window counts show the rate is sustained rather than averaged over a burst. 5 s is the window chosen for "sustain"; the minimum per second is reported too.

### NFR-5
> A single auction room shall support at least 100 concurrently connected bidders distributed across at least two application instances, without loss of event delivery.

- **Load:** 110 bidder sockets in one room, through the web service, which balances them round robin over `api` and `api-2`.
- **Measured:**
  - **Distribution:** subscriptions per instance, from each instance's own `ws subscribed` log lines for the auction.
  - **Concurrency:** the room's presence count, sampled by the tools container from Redis every second (peak).
  - **No loss:** every socket's received sequence numbers, compared after the close with the ledger's 1 to N. Missing and repeated deliveries are counted across all sockets.
- **Why there:** bidders connected to one room are counted, the distribution is counted per instance, and "without loss" is every event delivered to every socket exactly once.

### NFR-6
> REST endpoints serving auction listings, search results and auction detail shall respond within 500 ms at the 95th percentile under the load model of AS-4.

- **Measured:** client side, in k6, while the room is under load. These are requested through the web service at a constant rate:
  - `GET /api/v1/auctions` (listing)
  - `GET /api/v1/auctions?q=` (search)
  - `GET /api/v1/auctions/{id}` (detail)

  Request duration at the 95th percentile is taken per endpoint.
- **Why there:** the three named endpoints, timed as a client sees them, during the AS-4 load.
- **Also:** `tests/discovery/test_nfr6.py` (2,000 auctions, query plans).

### NFR-7
> Durable persistence of an accepted bid shall complete within 2 seconds at the 95th percentile of its acceptance, and any failure to persist shall be detected by reconciliation within 60 seconds.

- **Measured:** server side.
  - The tools container polls Postgres every 100 ms for the auction's bid rows.
  - Each row's first-seen time minus its acceptance time (`bids.created_at`, from Redis `TIME`) gives one persistence time.
  - The 95th percentile is reported.
- **Clocks:** the tools container's clock against Redis's clock.
- **Why there:**
  - A row is durable once it is committed and visible.
  - First-seen is at most one polling interval after the commit, so the measure slightly overstates the requirement's interval, never understates it.
- **The second clause** is not exercised under load: no failure is injected. It rests on two things:
  - existing tests: `tests/bidding/test_reconciliation.py` (a gap the outbox does not cover raises `SEQUENCE_GAP`) and `tests/test_tasks_unit.py` (a final persist failure raises `LEDGER_WRITE_FAILED`);
  - the reconciler's 30 s schedule (`workers/celery_app.py`), within the 60 s.

### NFR-8
> The system shall expose counters for bids accepted per second, bid rejection rate by reason, WebSocket connection count per instance, background queue depth and reconciliation gap count, so that the targets above can be measured rather than asserted.

- **Measured:** inspection.
- **Not met: the platform exposes no counters.** There is no metrics endpoint, and nothing in `backend/app` publishes these figures.
- The load test measures each of these quantities itself (k6 counters, the ledger, the instances' logs, Redis). That shows the targets, but it does not satisfy NFR-8, which asks the system to expose them.

### NFR-28
> Reliability. Under load testing the count of lost accepted bids and the count of duplicated bids shall both be zero, and the count of duplicate settlements under concurrent-close testing shall be zero. These are pass/fail criteria, not statistical targets.

- **Lost and duplicated bids:** checked server side after the close, by the six ledger checks of "Try to break it" (`services/demo_checks.py`, the same SQL) on the load-test auction.
  - Check 6 is fed every acknowledgement the k6 clients received: an acknowledged bid missing from the ledger is a lost accepted bid.
  - Checks 1 and 2 find a gap or a repeated sequence number.
- **Duplicate settlements:** the existing concurrent-close tests, run and reported.
  - `tests/closing/test_closing.py::test_ten_concurrent_closes_settle_once`: ten closers at once, one settlement.
  - `::test_constraint_alone_keeps_one_settlement`: the lock disabled, the constraint alone; this is OR-14.
- **Why there:** the ledger and the settlements table are where a lost or repeated bid, or a second settlement, would show.

### OR-6
> A daily logical backup of the database shall be taken, retained for seven days, with a restore procedure documented and demonstrated at least once before the final review.

- **Measured:** not a load-test measurement. It is operational, verified by demonstration.
- **Not met:**
  - the stack takes no scheduled backup;
  - a manual `pg_dump` procedure exists in the local runbook, and a backup was taken once (2026-10-17);
  - no restore has been demonstrated.

### OR-12
> Every requirement in this document shall be mapped to at least one verification activity -- inspection, demonstration, test or analysis -- in the project test plan.

- **Measured:** inspection of the test plan (`docs/testing.md`, with `docs/requirements-map.md`). This is not something a load test measures, and it is not assessed here.

### OR-15
> Soft-close threshold behaviour shall be verified at the boundary in both directions and across repeated extensions up to the cap of FR-54, and bid ordering correctness shall be verified under sustained concurrent load with zero lost and zero duplicated bids.

- **The first half:** existing tests in `tests/bidding/test_soft_close.py`, run and reported.
  - a bid 61 s before the end does not extend;
  - a bid 59 s before does;
  - three consecutive late bids each extend;
  - the cap of ten.
- **The second half:** the load test's NFR-28 checks, ordering under sustained concurrent load, on the same run.
- The concurrent-close tests are listed under NFR-28 (and OR-14).
