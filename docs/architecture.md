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

1. Client sends `place_bid {auction_id, amount, request_id}` over the WebSocket.
2. API checks token, account status, room subscription, rate limit.
3. API calls the Lua script. In one atomic step it: reads Redis `TIME`, checks status and end time, checks increment and 100x limit, checks self-outbid policy, writes highest bid and bidder, increments the sequence, applies soft close.
4. API acknowledges the submitter right away with sequence, amount, request_id, new end time if extended.
5. Proxy resolution runs at the same serialisation point and may place an automatic bid.
6. API publishes the event to `auction:{id}`; every instance fans it out to its subscribers.
7. A worker task appends the bid to the `bids` table (unique on auction_id + sequence_no), retries on failure, alerts if retries are exhausted.
8. The displaced bidder is notified through `user:{id}`.

Rejected bids go back to the submitter only. Nothing is written to the ledger.

## 3. Redis live state (per auction)

Hash `auction:{id}:state`: `status`, `end_time_ms`, `highest_amount`, `highest_bidder`, `sequence`, `min_increment`, `start_price`, `extension_count`, `threshold_s`, `extension_s`, `self_outbid_policy`.
Armed proxy maxima are held in Redis for arbitration but always also stored in Postgres (`proxy_bids`).
Redis state is rebuildable from Postgres (auction row plus bid ledger) and is rebuilt before an auction is shown live.

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
General: VALIDATION_ERROR, INTERNAL_ERROR, SERVICE_UNAVAILABLE (also used by readiness when a dependency is down).
Framework HTTP errors not listed above use `HTTP_<status>` (for example HTTP_405); 404 maps to NOT_FOUND.

## 10. WebSocket close codes

Define in phase 5 and record here.

## 11. Pseudonyms

Public bidder label: `Bidder-` plus the first four hex characters of a per-auction hash of the bidder id (NFR-27).
