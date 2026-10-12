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
   Rejections write nothing. After step h, `resolve()` lets armed maxima respond inside the same script (see Proxy maxima below).
4. API acknowledges the submitter right away with sequence, amount, request_id, end time and extension count (FR-40), then enqueues `persist_bids(auction_id)` without awaiting the broker.
5. Proxy resolution has already run inside the script; the acceptance lists any automatic bids in `automatic_bids`, each with its own sequence number and end time.
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
| `auction:{id}:bids` | stream | Outbox, three kinds. `kind=bid` (default when absent): `auction_id`, `bidder_id`, `amount`, `sequence_no`, `bid_type`, `accepted_at_ms`, plus `end_time_after` and `extension_count` when the bid extended the auction. `kind=proxy_set`: `bidder_id`, `max_amount`, `arm_seq`, `armed_at_ms`. `kind=proxy_deactivated`: `bidder_id`, `arm_seq`, `deactivated_at_ms`. |
| `auction:{id}:proxies` | hash | `bidder -> "max|arm_seq"` for every armed maximum. Read only by the scripts and the rebuild; never returned to another bidder. |
| `auction:{id}:bids:dead` | stream | Dead letter for `SEQUENCE_COLLISION` entries (also written to `audit_log`). |
| `auction:{id}:req:{bidder}:{request_id}` | string, 10 min TTL | Stored acceptance for idempotent retries. Only acceptances are stored. |
| `auction:{id}:preq:{bidder}:{request_id}` | string, 10 min TTL | Stored proxy result (same pattern, separate namespace). |

Status values mirror Postgres. Money is integer paise as decimal strings; every number the script returns is a string (cjson keeps 14 digits). `arm_seq` in the state hash is a counter incremented by every set or raise; equal maxima are won by the lower `arm_seq`, never by wall-clock time.

### Rebuild (FR-43)

Redis state is rebuilt from Postgres only when it has been lost. `init_live_state`:
1. reads the auction row and its highest-sequence bid;
2. runs `init_state.lua`: returns `EXISTS` if a hash is present (unless it is a `rebuilding` hash whose lease expired), `OUTBOX_PENDING` if the outbox is non-empty (the snapshot would miss accepted bids; the caller fails closed and the sweeper drains the outbox), else writes the hash with `status = rebuilding`, a token and a 5 s lease;
3. re-reads the ledger max; if a persister committed in between, aborts via `finalize_state.lua` and retries (at most 3 times, then `SERVICE_UNAVAILABLE`); otherwise commits the real status.
`place_bid.lua` treats `rebuilding` as not initialised, so no bid is ever arbitrated against an unverified snapshot. Two instances rebuilding at once cannot clobber each other: only the token holder can commit or abort.

Armed maxima are rebuilt from the active `proxy_bids` rows, and `arm_seq` restarts above the highest `arm_seq` ever persisted for the auction, active or not. The persister applies `proxy_set` only when its `arm_seq` is above every row for that (auction, bidder), under a transaction-scoped advisory lock, so a stale replay can never resurrect an exhausted maximum.

### Proxy maxima (FR-45 to FR-51)

`set_proxy.lua` (KEYS `state`, `outbox`, `preq`, `proxies`) sets or raises a maximum and resolves it in the same step: idempotent request record; `AUCTION_CLOSED` for any auction that is not live (FR-46); `BELOW_CURRENT_PRICE` below the minimum next bid; `BID_ABOVE_LIMIT` above 100 x start price; `PROXY_CANNOT_DECREASE` for a lower or equal maximum; then `HINCRBY arm_seq`, `HSET proxies bidder "max|arm_seq"`, `XADD kind=proxy_set`, `resolve()`. The result echoes the maximum to its owner only and says whether the maximum is still `active` after its own resolution.

Both scripts include `app/lua/_core.lua` at load time (`--#include _core`, expanded by `app/lua/__init__.py`), which holds `accept()` and `resolve()`.

**Resolution, closed form (FR-47 to FR-49), after every manual accept and every set/raise:**

1. Sort active maxima by amount descending, then `arm_seq` ascending. `top` and `second`.
2. If there is no top, or top belongs to someone else and does not exceed the current highest bid, nobody acts.
3. `competing` = the higher of the current highest bid and the runner-up maximum. `target` = `min(top.max, competing + increment)`, or the start price when nothing has been bid and nobody competes.
4. If top already leads and no runner-up exceeds the current highest bid, nothing is placed.
5. Otherwise: if the runner-up exceeds the current highest bid and its maximum is below `target`, it bids its own maximum first (the record bid); then top bids `target`. Equal maxima give `target == second.max`, so no record bid, and the earlier `arm_seq` leads at the common value.
6. Deactivation: every active maximum whose owner is not the new leader and whose amount no longer exceeds the new highest bid is deleted from the hash with `XADD kind=proxy_deactivated`. The leader's own maximum stays armed.

Automatic bids go through the same `accept()` as manual bids: same sequence numbers, same soft close, same outbox (FR-50). They may sit below a full increment above the previous bid, because FR-48 caps them at the maximum and FR-49 allows the leader to stop at its own maximum. A manual bid equal to an armed maximum wins (FR-48: a maximum acts only when it *exceeds* the highest bid).

Worked example, increment 10, start price 1000: Bob bids 1000. Dave arms 3000 (arm 1), Carol 4000 (arm 2), Alice 5000 (arm 3), each arming resolving immediately: Dave 1010; then Dave 3000 (record), Carol 3010, Dave deactivated; then Carol 4000 (record), Alice 4010, Carol deactivated. A manual 4995 by Erin makes Alice bid 5000 (her maximum, below 5005). Equal case: Alice 3000 (arm 1) leads at 1010; Bob arms 3000 (arm 2): Alice bids 3000, Bob is deactivated at once (`active: false` in his result).

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
Bidding: AUCTION_CLOSED, AUCTION_NOT_STARTED, BID_TOO_LOW, BID_ABOVE_LIMIT, SELF_OUTBID_NOT_ALLOWED, BELOW_CURRENT_PRICE, PROXY_CANNOT_DECREASE (lower or equal maximum, FR-46), SERVICE_UNAVAILABLE.
Payment: NOT_WINNER, ALREADY_PAID, INVALID_SIGNATURE.
General: VALIDATION_ERROR (also returned by the bid service for a malformed amount or request_id), INTERNAL_ERROR, SERVICE_UNAVAILABLE (readiness with a dependency down; bid path when Redis is unreachable or live state cannot be established, see the client contract in section 2).
Internal only, never sent to clients: NOT_INITIALISED (script result meaning live state is absent; the service rebuilds or maps it).
Framework HTTP errors not listed above use `HTTP_<status>` (for example HTTP_405); 404 maps to NOT_FOUND.

## 10. WebSocket close codes

Define in phase 5 and record here.

## 11. Pseudonyms

Public bidder label: `Bidder-` plus the first four hex characters of a per-auction hash of the bidder id (NFR-27).

## 12. Identity (FR-1 to FR-10)

**Accounts.** `POST /api/v1/auth/register` creates a `pending_verification` user with the bidder role and an argon2id password hash (t=3, m=64 MiB, p=4, from config). Email is normalised to lower case; a duplicate gives `EMAIL_TAKEN` (FR-1). Passwords are 10 to 128 characters and may not equal the email.

**Tokens.** Opaque tokens (verification, reset, refresh) are 32 random bytes, urlsafe; only their SHA-256 is stored. Verification tokens last 24 h, reset tokens 1 h, both single use through one `UPDATE ... WHERE used_at IS NULL`. Access tokens are HS256 JWTs (`sub`, `roles`, `sid`, `iat`, `exp`, `jti`) valid 15 minutes; decoding pins `algorithms=["HS256"]`, requires every claim and checks expiry against the service clock. The refresh token lives only in an `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` cookie, 30 days sliding, rotated on every `POST /api/v1/auth/refresh`, which also requires an `X-Requested-With` header (NFR-21).

**Login (FR-4).** Rate limits per IP and per email first. An unknown email runs a dummy argon2 verify so timing matches a wrong password; both return the same `INVALID_CREDENTIALS` body. `EMAIL_NOT_VERIFIED` and `ACCOUNT_SUSPENDED` are returned only after the password verified.

**Revocation (FR-5, FR-6).** Postgres is the source of truth (`sessions.revoked_at`, `users.status`). Redis keys `revoked:sid:{sid}` and `revoked:user:{user_id}` (TTL = access-token lifetime) make the per-request check one round trip: a user key means `ACCOUNT_SUSPENDED`, a sid key means `INVALID_TOKEN`. Logout and password reset write sid keys (reset never writes a user key, so the next login works at once); suspension writes the user key. A failed write is retried once, then handed to the `denylist_revoke` Celery task; the `sync_denylist` beat task (every 30 s) rewrites every revocation of the last 15 minutes from Postgres. If Redis is unreachable at request time the check runs against Postgres instead of being skipped.

**Rate limits (NFR-22).** Fixed windows keyed by the clock bucket (`rl:{name}:{subject}:{window_start}`), counted by one Lua `INCR`+`EXPIRE`. Defaults: login 10/60 s per IP and 5/300 s per email; register 5/h per IP; resend and forgot 5/h per IP and 3/h per email. `RATE_LIMITED` is 429 with `Retry-After`. Redis down means `SERVICE_UNAVAILABLE` on these endpoints (fail closed).

**Profile (FR-9, FR-10).** `GET`/`PATCH /api/v1/users/me` (name and avatar only; the schema forbids other fields). `GET /api/v1/users/me/bids` pages the caller's ledger rows newest first with a `created_at:id` keyset cursor.

**Suspension.** `UserService.suspend_user(admin, user, reason)`: status, revocation of every session and the `audit_log` row (`user_suspended`, with the reason) commit in one transaction (FR-78, BR-12), then the user denylist key. The admin endpoint arrives with the admin work.

**Status codes.** 401 `INVALID_TOKEN`/`TOKEN_EXPIRED` for bearer problems and `INVALID_CREDENTIALS`; 400 for a bad body token; 403 `EMAIL_NOT_VERIFIED`, `ACCOUNT_SUSPENDED`, `FORBIDDEN`; 409 `EMAIL_TAKEN`; 429 `RATE_LIMITED`.
