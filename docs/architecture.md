# Architecture

Design reference. The SRS governs if anything here conflicts with it.

## 1. Components

| Component | Role |
|---|---|
| API (FastAPI) | REST and one WebSocket endpoint, stateless apart from connections |
| Worker (Celery) | Durable bid writes, settlement, notifications, reconciliation |
| Beat | Scheduler: activate auctions, close auctions, ending-soon, reconciliation |
| PostgreSQL | Source of truth |
| Redis | Live auction state, Lua arbitration, pub/sub, rate limits, task queue |

## 2. Bid flow (accepted manual bid)

1. Client sends `place_bid {auction_id, amount, request_id}` over the WebSocket (Phase 5; REST fallback uses the same service, DC-9).
2. API checks token, account status, room subscription, rate limit (Phase 5/6, FR-34).
3. `BidService.place_bid` validates the input (amount is an integer 1..2^53-1, request_id is 1..64 chars of `[A-Za-z0-9_-]`) and calls `place_bid.lua` with keys `state`, `outbox`, `req`. In one atomic step the script:
   a. returns the stored result if `req` exists (idempotent retry);
   b. returns `NOT_INITIALISED` if the state hash is missing or still `rebuilding`;
   c. reads `now` from Redis `TIME` (DC-3);
   d. rejects `AUCTION_NOT_STARTED` (status scheduled or now < start) or `AUCTION_CLOSED` (status not live or now >= end) (FR-36);
   e. rejects `BID_TOO_LOW` below start price (no bids yet) or highest + increment, returning `current_highest` and `min_next` to the submitter only (FR-37);
   f. rejects `BID_ABOVE_LIMIT` above 100 x starting price (FR-37);
   g. rejects `SELF_OUTBID_NOT_ALLOWED` when the policy is on and the bidder already leads (FR-38);
   h. accepts: writes highest amount and bidder, increments `sequence` (FR-39);
   i. soft close: if `end - now <= threshold` and `extension_count < 10`, `end += extension`, `extension_count += 1` (FR-52 to FR-54);
   j. XADDs the bid to the outbox stream, with `end_time_after` when extended (FR-55);
   k. stores the result under `req` with a TTL and returns it.
   Rejections write nothing. Phase 4 adds proxy resolution after step h, inside the same script.
4. API acknowledges the submitter right away with sequence, amount, request_id, end time and extension count (FR-40), then enqueues `persist_bids(auction_id)` without awaiting the broker.
5. Proxy resolution runs at the same serialisation point and may place an automatic bid (Phase 4).
6. API publishes the event to `auction:{id}`; every instance fans it out to its subscribers (Phase 5).
7. The worker's `persist_bids` task drains the outbox into the `bids` table with `ON CONFLICT (auction_id, sequence_no) DO NOTHING`, applies any extension to `auctions.end_time` and `extension_count` (never decreasing) with an audit entry in the same transaction, then XDELs the persisted entries. Retries with backoff; after 5 retries it raises the `LEDGER_WRITE_FAILED` alert and the entries stay in the outbox. A beat sweeper (`sweep_outboxes`, every 5 s) persists every non-empty outbox as a safety net.
8. The displaced bidder is notified through `user:{id}` (Phase 5/9).

Rejected bids go back to the submitter only. Nothing is written to the ledger.

### Client contract for SERVICE_UNAVAILABLE

`SERVICE_UNAVAILABLE` means the real-time layer could not establish the bid's order (FR-42). When it is caused by a timeout the outcome is unknown: the script may have run. Clients must retry with the **same `request_id`**; the stored result is returned unchanged for 10 minutes, so a retry can never place a second bid. Phase 5 (WebSocket) and Phase 10 (frontend) must implement this.

### Postgres end_time lags Redis

Soft-close extensions reach `auctions.end_time` only through the persister, so Postgres can lag Redis by up to the sweep interval (5 s) plus retries. Redis `end_time_ms` is the authoritative end time while an auction is live. The Phase 7 scheduler and the Phase 8 close task must decide "end time passed" from Redis `end_time_ms` (or drain the outbox first), never from Postgres alone (FR-58).

## 3. Redis live state (per auction)

Redis runs with `appendonly yes`, `appendfsync everysec`, `maxmemory-policy noeviction`. Keys share the `{auction_id}` hash tag.

| Key | Type | Contents |
|---|---|---|
| `auction:{id}:state` | hash | `status`, `start_time_ms`, `end_time_ms`, `highest_amount`, `highest_bidder`, `sequence`, `start_price`, `min_increment`, `extension_count`, `threshold_ms`, `extension_ms`, `self_outbid` (0/1). During a rebuild also `rebuild_token`, `rebuild_deadline_ms` and `status = rebuilding`. A missing hash means "not initialised". |
| `auction:{id}:bids` | stream | Outbox of accepted bids not yet in Postgres: `auction_id`, `bidder_id`, `amount`, `sequence_no`, `bid_type`, `accepted_at_ms`, plus `end_time_after` and `extension_count` when the bid extended the auction. |
| `auction:{id}:bids:dead` | stream | Dead letter for `SEQUENCE_COLLISION` entries (also written to `audit_log`). |
| `auction:{id}:req:{bidder}:{request_id}` | string, 10 min TTL | Stored acceptance for idempotent retries. Only acceptances are stored. |

Status values mirror Postgres. Money is integer paise as decimal strings; every number the script returns is a string (cjson keeps 14 digits).

### Rebuild (FR-43)

Redis state is rebuilt from Postgres only when it has been lost. `init_live_state`:
1. reads the auction row and its highest-sequence bid;
2. runs `init_state.lua`: returns `EXISTS` if a hash is present (unless it is a `rebuilding` hash whose lease expired), `OUTBOX_PENDING` if the outbox is non-empty (the snapshot would miss accepted bids; the caller fails closed and the sweeper drains the outbox), else writes the hash with `status = rebuilding`, a token and a 5 s lease;
3. re-reads the ledger max; if a persister committed in between, aborts via `finalize_state.lua` and retries (at most 3 times, then `SERVICE_UNAVAILABLE`); otherwise commits the real status.
`place_bid.lua` treats `rebuilding` as not initialised, so no bid is ever arbitrated against an unverified snapshot. Two instances rebuilding at once cannot clobber each other: only the token holder can commit or abort.

Armed proxy maxima are held in Redis for arbitration but always also stored in Postgres (`proxy_bids`) (Phase 4).

### Reconciliation (FR-43, NFR-10)

`reconcile_live_auctions` (beat, every 30 s) compares, per live auction, the Redis `sequence` with the ledger. Alerts are CRITICAL log lines with an `alert` field:

| Alert | Meaning |
|---|---|
| `SEQUENCE_GAP` | sequence numbers missing from the ledger and not in the outbox (lost bid) |
| `SEQUENCE_REGRESSION` | ledger max is above the Redis sequence (Redis lost writes, e.g. AOF rollback) |
| `SEQUENCE_COLLISION` | persister found an outbox entry whose sequence exists in the ledger with different data; the entry is kept in `audit_log` and the dead-letter stream |
| `LEDGER_WRITE_FAILED` | persist task exhausted its retries; entries remain in the outbox |

## 4. Channels

- `auction:{id}`: room events (bid_accepted, price_update, timer_extended, auction_closed)
- `user:{id}`: personal events (outbid_notice, notifications)

## 5. Auction status

draft, scheduled, live, closing, closed_sold, closed_no_sale, cancelled.
Transitions: draft to scheduled; draft or scheduled to cancelled; scheduled to live (scheduler); live to closing (end time passed); live to cancelled (admin only); closing to closed_sold or closed_no_sale.

## 6. Settlement status

pending_payment, paid, no_sale. A failed payment attempt leaves pending_payment so the bidder can retry.

## 7. Close and settlement flow

1. Scheduler finds live auctions past end time, moves them to closing, enqueues a close task.
2. Worker takes the per-auction lock; if it cannot, it exits cleanly.
3. Worker re-checks the end time now. If a soft-close pushed it into the future, release the lock and leave the auction live.
4. Winner is the highest amount, ties broken by lowest sequence number.
5. Reserve met or no reserve: closed_sold plus one settlement (pending_payment). Otherwise closed_no_sale and no payable settlement.
6. Audit entry in the same transaction. Notifications once. `auction_closed` broadcast. Release the lock.
7. Backstop: UNIQUE(auction_id) on settlements.

## 8. Data model (tables)

Migrations `0001` to `0005` in `backend/alembic/versions/`. Money is BIGINT paise, every timestamp is `timestamptz`, every FK is `ON DELETE RESTRICT`. Constraint names follow the naming convention in `app/models/base.py` (`pk_`, `uq_`, `ck_`, `fk_`, `ix_`).

| Table | PK | Key constraints and indexes |
|---|---|---|
| users | uuid | unique index on `lower(email)`; status CHECK (pending_verification, active, suspended) |
| user_roles | (user_id, role) | role CHECK (bidder, seller, admin) |
| sessions | uuid | `refresh_token_hash` UNIQUE; `revoked_at` for immediate revocation |
| email_verification_tokens, password_reset_tokens | bigint | `token_hash` UNIQUE |
| categories | bigint | `slug` UNIQUE; self-FK `parent_id`, indexed |
| auctions | uuid | CHECKs: end > start, starting_price >= 0, min_increment > 0, reserve NULL or >= starting_price, extension_count 0..10, status in the 7 states. Trigger blocks any change to `original_end_time` (FR-14). Indexes: (status, start_time), (status, end_time), seller_id, category_id, GIN on generated `search_vector` ('simple' config, title weight A, description weight B) |
| auction_images | bigint | index on auction_id |
| bids (append-only) | bigint | UNIQUE (auction_id, sequence_no); amount > 0; bid_type manual or automatic; `end_time_after` (FR-55). Indexes: (auction_id, sequence_no DESC), (bidder_id, created_at DESC) |
| proxy_bids | bigint | partial UNIQUE (auction_id, bidder_id) WHERE is_active; max_amount > 0 |
| watchlist | (user_id, auction_id) | |
| settlements | uuid | UNIQUE auction_id; UNIQUE idempotency_key; CHECK no_sale iff winner_id and final_price are NULL |
| payments | uuid | UNIQUE gateway_ref; UNIQUE idempotency_key; partial UNIQUE settlement_id WHERE status = 'succeeded' (failed attempts can be retried) |
| notifications | bigint | UNIQUE dedupe_key; type CHECK; partial index (user_id, created_at DESC) WHERE read_at IS NULL |
| notification_preferences | (user_id, type) | type CHECK |
| audit_log (append-only) | bigint | `actor_id` NULL for system actions. Indexes: (entity_type, entity_id), (actor_id, created_at), created_at |

### Database roles (DC-4, FR-79)

| Role | Used by | Privileges |
|---|---|---|
| owner (`POSTGRES_USER`) | Alembic only, via `MIGRATION_DATABASE_URL` | owns every object |
| `auction_app` | api, worker, beat, via `DATABASE_URL` | SELECT, INSERT, UPDATE, DELETE on ordinary tables; SELECT, INSERT only on `bids` and `audit_log`; SELECT only on `alembic_version`; USAGE on schema and sequences; no TRUNCATE, no default privileges |

## 9. Error codes (add new ones here)

Auth: EMAIL_TAKEN, INVALID_TOKEN, TOKEN_EXPIRED, INVALID_CREDENTIALS, EMAIL_NOT_VERIFIED, ACCOUNT_SUSPENDED, RATE_LIMITED, FORBIDDEN, NOT_FOUND.
Auctions: AUCTION_ALREADY_LIVE, CATEGORY_NOT_FOUND, NOT_WATCHING, ALREADY_CLOSED.
Bidding: AUCTION_CLOSED, AUCTION_NOT_STARTED, BID_TOO_LOW, BID_ABOVE_LIMIT, SELF_OUTBID_NOT_ALLOWED, BELOW_CURRENT_PRICE, SERVICE_UNAVAILABLE.
Payment: NOT_WINNER, ALREADY_PAID, INVALID_SIGNATURE.
General: VALIDATION_ERROR (also returned by the bid service for a malformed amount or request_id), INTERNAL_ERROR, SERVICE_UNAVAILABLE (readiness with a dependency down; bid path when Redis is unreachable or live state cannot be established, see the client contract in section 2).
Internal only, never sent to clients: NOT_INITIALISED (script result meaning live state is absent; the service rebuilds or maps it).
Framework HTTP errors not listed above use `HTTP_<status>` (for example HTTP_405); 404 maps to NOT_FOUND.

## 10. WebSocket close codes

Define in phase 5 and record here.

## 11. Pseudonyms

Public bidder label: `Bidder-` plus the first four hex characters of a per-auction hash of the bidder id (NFR-27).
