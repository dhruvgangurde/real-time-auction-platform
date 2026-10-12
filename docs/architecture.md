# Architecture

Design reference. The SRS governs if anything here conflicts with it.

## 1. Components

| Component | Role |
|---|---|
| API (FastAPI) | REST and one WebSocket endpoint, stateless apart from connections |
| Worker (Celery) | Durable bid writes, settlement, notifications, reconciliation |
| Beat | Scheduler: activate auctions and move them to closing (`run_scheduler`, 5 s), outbox sweep, reconciliation, denylist resync; ending-soon in Phase 9 |
| PostgreSQL | Source of truth |
| Redis | Live auction state, Lua arbitration, pub/sub, rate limits, task queue |

## 2. Bid flow (accepted manual bid)

1. Client sends `place_bid {auction_id, amount, request_id}` over the WebSocket (Phase 5; REST fallback uses the same service, DC-9).
2. API checks, cheapest first: the per-connection and per-user rate limits, the token and denylist (account status), the bidder role, the room subscription and degraded mode (FR-34, FR-44; section 4).
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
6. API publishes `bid_placed` (one per bid, automatic included), `timer_extended` and `outbid_notice` after the acknowledgement; every instance's hub fans them out to its local subscribers (section 4).
7. The worker's `persist_bids` task drains the outbox into the `bids` table with `ON CONFLICT (auction_id, sequence_no) DO NOTHING`, applies any extension to `auctions.end_time` and `extension_count` (never decreasing) with an audit entry in the same transaction, then XDELs the persisted entries. Retries with backoff; after 5 retries it raises the `LEDGER_WRITE_FAILED` alert and the entries stay in the outbox. A beat sweeper (`sweep_outboxes`, every 5 s) persists every non-empty outbox as a safety net.
8. Displaced bidders get `outbid_notice` on `user:{id}:events` (email notifications are Phase 9).

Rejected bids go back to the submitter only. Nothing is written to the ledger.

### Client contract for SERVICE_UNAVAILABLE

`SERVICE_UNAVAILABLE` means the real-time layer could not establish the bid's order (FR-42). When it is caused by a timeout the outcome is unknown: the script may have run. Clients must retry with the **same `request_id`**; the stored result is returned unchanged for 10 minutes, so a retry can never place a second bid. Phase 5 (WebSocket) and Phase 10 (frontend) must implement this.

### Postgres end_time lags Redis

Soft-close extensions reach `auctions.end_time` only through the persister, so Postgres can lag Redis by up to the sweep interval (5 s) plus retries. Redis `end_time_ms` is the authoritative end time while an auction is live. The Phase 7 scheduler and the Phase 8 close task must decide "end time passed" from Redis `end_time_ms` (or drain the outbox first), never from Postgres alone (FR-58).

## 3. Redis live state (per auction)

Redis runs with `appendonly yes`, `appendfsync everysec`, `maxmemory-policy noeviction`. Keys share the `{auction_id}` hash tag.

Only **live and closing** auctions have live state (D2). `init_live_state` refuses any other status (`AuctionNotLive`), and `/live` and `subscribe` serve every other status from Postgres without writing to Redis. Each reconciliation run calls `remove_stale_state`, which deletes the state, proxies and empty outbox of auctions Postgres no longer calls live or closing; `cleanup_state.lua` re-checks atomically and keeps any hash whose own status is live, closing or rebuilding, or whose outbox is non-empty (activation or a rebuild may be racing the Postgres read).

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

`reconcile_live_auctions` (beat, every 30 s) compares, per live auction, the Redis `sequence` with the ledger (outbox entries of kind `bid` count as pending; proxy entries carry no sequence), then removes stale live state (section 3). Each auction is checked on its own: an error for one raises `RECONCILE_FAILED` and the run continues. Alerts are CRITICAL log lines with an `alert` field:

| Alert | Meaning |
|---|---|
| `SEQUENCE_GAP` | sequence numbers missing from the ledger and not in the outbox (lost bid) |
| `SEQUENCE_REGRESSION` | ledger max is above the Redis sequence (Redis lost writes, e.g. AOF rollback) |
| `SEQUENCE_COLLISION` | persister found an outbox entry whose sequence exists in the ledger with different data; the entry is kept in `audit_log` and the dead-letter stream |
| `LEDGER_WRITE_FAILED` | persist task exhausted its retries; entries remain in the outbox |
| `RECONCILE_FAILED` | reconciliation of one auction raised an error (logged with its traceback); the other auctions were still checked |

## 4. Channels and real-time layer

Redis pub/sub, one hub per API instance (`app/services/fanout.py`):

- `auction:{id}:events`: room events (`bid_placed`, `timer_extended`; `auction_closed` in Phase 8). Subscribed only while a local socket is in that room (reference counted).
- `user:{id}:events`: personal events (`outbid_notice`, `session_revoked`, `session_replaced`). Subscribed only while the user has a local socket.

Each socket has a bounded send queue drained by its own writer; a full queue closes that socket with 4008 so a slow client never stalls the hub. If the hub loses Redis it sends `system_status {degraded: true}` to its sockets, reconnects with backoff, resubscribes until no referenced channel is missing (including any referenced while SUBSCRIBE was in flight) and only then sends `degraded: false`; while degraded it rejects bids with `SERVICE_UNAVAILABLE`. Presence (FR-32): every 5 s each instance refreshes `auction:{id}:presence` (sorted set of server-generated connection ids with expiry) and sends `presence` to its local sockets in the room.

**One socket per session (CI-3, D3).** Registering a socket runs `ws_claim.lua` on `ws:user:{id}:sessions` (sorted set of session ids, at most `ws_max_sessions_per_user` = 5), `ws:sid:{sid}` (generation of the session's current socket) and `ws:conn_seq` (global generation counter). The new socket gets the highest generation; older sockets of the same session close with 4012, locally at once and on other instances through `session_replaced {sid, gen}` on the user channel. A sixth session is refused with 4010; a held session may always reconnect. `ws_release.lua` frees the slot only if the closing socket still owns it. There is no idle timeout (D4): dead peers are dropped by the uvicorn keepalive (ping every 20 s, 20 s timeout), and the presence loop keeps a live socket's session counted.

### WebSocket endpoint `/api/v1/ws`

1. A present `Origin` must be in `ALLOWED_ORIGINS` (checked before accept, HTTP 403). A missing Origin (non-browser client) is allowed; a token is still required.
2. The first message must be `authenticate {token}` within 5 s. Tokens are never read from the URL (CI-4). A later `authenticate` with a refreshed token of the same session extends the connection; another session's token closes 4002.
3. Every frame counts against a per-connection cap kept in memory (`ws_max_messages_per_s` = 30, no I/O). Frames are validated (pydantic discriminated union, `extra=forbid`, DC-11) and limited to 4 KB (uvicorn caps frames at 64 KB). Each message type then has a per-user limit in Redis, shared across instances and with the REST fallback: `place_bid` 10/s (FR-44), `set_proxy` 5/s, `subscribe`, `unsubscribe`, `authenticate` and `ping` 20/s each, so bid submission is the strictest (CI-3). With the limiter unreachable, bids and `subscribe` fail closed (D11); `authenticate`, `unsubscribe` and `ping` proceed, bounded by the in-memory cap.
4. Strikes (D12): invalid frames, `RATE_LIMITED`, `FORBIDDEN` and `NOT_SUBSCRIBED` each count one, `SERVICE_UNAVAILABLE` never; one strike is forgiven per 60 s; 5 strikes close 4009.
5. `subscribe` re-checks the denylist (FR-6). `place_bid`/`set_proxy` re-check the denylist (FR-6), require the bidder role, a subscription to the room (FR-34) and a healthy hub, then call `BidService`. The acknowledgement is queued first, then events are published (FR-40). The socket queue is FIFO, so a submitter always sees its ack before its own `bid_placed`. Revocation closes 4004, expiry 4003.
6. Errors: an unexpected error in one message is logged with its traceback and answered (`bid_rejected`/`proxy_rejected` `SERVICE_UNAVAILABLE` for a bid not yet acknowledged, `error INTERNAL_ERROR` otherwise); the socket stays open. An unexpected error in a connection task closes 1011.
7. Logging: per connection a server-generated `conn_id` plus the request id (`X-Request-ID`, a correlation id only); per message `bid_request_id`; connect, auth, subscribe and close with the real code and reason. One FR-44 line (`bid` or `proxy`) per bid decision, including refusals before arbitration, with `automatic_sequence_nos`. Never tokens or maxima.

### Message catalogue

Every outbound event carries `server_time_ms`. Clients derive countdowns from `end_time_ms` and server time only (UI-4).

| Direction | Type | Fields |
|---|---|---|
| client | `authenticate` | `token` |
| client | `subscribe` / `unsubscribe` | `auction_id` |
| client | `place_bid` | `auction_id`, `amount`, `request_id` |
| client | `set_proxy` | `auction_id`, `max_amount`, `request_id` |
| client | `ping` | |
| submitter | `authenticated` | `user_id`, `expires_at_ms` |
| submitter | `subscribed` | `auction_id`, `your_alias`, `snapshot` (same shape as `/live`) |
| submitter | `unsubscribed`, `pong` | |
| submitter | `bid_accepted` | `request_id`, `auction_id`, `sequence_no`, `amount`, `end_time_ms`, `extended`, `extension_count` |
| submitter | `bid_rejected` | `request_id`, `auction_id`, `code`, `message`, `current_highest?`, `min_next?` |
| submitter | `proxy_armed` | `request_id`, `auction_id`, `max_amount` (own only, FR-51), `active` |
| submitter | `proxy_rejected` | `request_id`, `auction_id`, `code`, `message`, `min_next?` |
| submitter | `error` | `code`, `message`, `request_id?` |
| room | `bid_placed` | `auction_id`, `sequence_no`, `amount`, `bidder_alias`, `bid_type`, `end_time_ms`, `extended`, `extension_count` (one per accepted bid, automatic bids included) |
| room | `timer_extended` | `auction_id`, `sequence_no`, `end_time_ms`, `extension_count`, `final` (true at the 10th extension, FR-54) |
| room (local) | `presence` | `auction_id`, `participants`, `sequence_no`, `end_time_ms` |
| user | `outbid_notice` | `auction_id`, `current_price`, `sequence_no` |
| user (internal) | `session_revoked` | `sid` or null: the instance closes the matching sockets with 4004 |
| user (internal) | `session_replaced` | `sid`, `gen`: the instance closes that session's sockets with a lower generation with 4012 |
| local | `system_status` | `degraded` |

**Outbid recipients:** the leader before the trigger, every maximum the trigger exhausted, and every bidder whose bid in the trigger was immediately exceeded, minus the new leader; each once. Retries republish, so Phase 9's notification `dedupe_key` must include (auction_id, user_id, sequence_no).

### Client rules (Phase 10 must implement)

Pub/sub is at-most-once and events from different instances can arrive out of order. Apply room events in `sequence_no` order; drop anything at or below the last applied sequence (retries republish); on a gap, wait briefly, then refetch `GET /api/v1/auctions/{id}/live`. Also refetch `/live` when a `presence.sequence_no` is above the last applied sequence (a lost event in a quiet room), and after `system_status {degraded: false}`. On `bid_rejected`/`proxy_rejected` with `SERVICE_UNAVAILABLE`, retry with the same `request_id`.

- **Buffer before `subscribed`.** Room events for an auction can arrive before its `subscribed` frame (the hub joins the room before reading the snapshot). Buffer them, then apply only those above `snapshot.sequence_no`.
- **Reconnect.** Reconnect with exponential backoff, re-authenticate, resubscribe and refetch (CI-6), but never automatically after 4002 (authentication failed), 4004 (session revoked) or 4012 (replaced by a newer socket of the same session, e.g. another tab). On 4003 refresh the access token first. Keep one socket per session.
- **No heartbeat needed.** There is no idle timeout; the browser answers the protocol keepalive by itself. `ping` exists for clients that want an application-level round trip.

### Pseudonyms (NFR-27)

`Bidder-` + first 4 hex characters of `HMAC-SHA256(ALIAS_SECRET, "{auction_id}:{bidder_id}")`. Stable within an auction, not linkable across auctions without the secret. Room events and snapshots never carry user ids. `subscribed.your_alias` tells each user their own alias.

### REST

- `GET /api/v1/auctions/{id}/live` (public; drafts only for their seller or an admin, FR-30): status, current price, leader alias, sequence, bid count, min next, end time, extension count, participants, last 20 bids (ledger merged with unpersisted outbox entries, never above the sequence) and `server_time_ms`. Live and closing auctions are read from Redis live state (state, outbox and presence in one MULTI) and rebuilt when missing (FR-26); every other status is served from Postgres with `degraded: false` and writes nothing to Redis (D2). With Redis unreachable it is served from Postgres with `degraded: true` (NFR-13).
- `POST /api/v1/auctions/{id}/bids` and `/proxy` (bidder role): the REST fallback (DC-9, UC-01 2a). Same service, same `request_id` contract, same per-user rate limit; the response is the acknowledgement and events are published after it is sent. Rejections: 409 with the arbitration code, 422, 429, 503.

Lifecycle and discovery (Phase 7; `api/lifecycle.py`, `api/discovery.py`, services `AuctionService`, `CatalogService`):

| Endpoint | Who | Notes |
|---|---|---|
| `POST /api/v1/auctions` | seller (BR-1) | draft; FR-12 per-field errors; anti-snipe 60/120 default, bounded by config; 10 per hour per user (NFR-22) |
| `PATCH /api/v1/auctions/{id}` | seller and owner (BR-1) | rules by status in section 5; audit before/after |
| `POST /api/v1/auctions/{id}/publish` | seller and owner (BR-1) | draft to scheduled; at least one image (FR-13); FR-12 re-checked |
| `POST /api/v1/auctions/{id}/cancel` | owner (a former seller can still withdraw) | draft or scheduled to cancelled (FR-18, BR-9) |
| `POST /api/v1/auctions/{id}/images` | seller and owner (BR-1) | the body is the image (any Content-Type); decoded JPEG/PNG/WebP only, at most 5 MB (streamed), pixel cap, re-encoded without metadata, random name, at most 8 (D10, NFR-24) |
| `DELETE /api/v1/auctions/{id}/images/{image_id}` | seller and owner (BR-1) | an auction that left draft keeps at least one image |
| `GET /api/v1/auctions` | public | `q` (at most 200, websearch syntax, title ranked above description), `category` (descendants included), `status` (repeatable; default scheduled, live, closing; draft and cancelled refused), `min_price`/`max_price` on `coalesce(current_price, starting_price)`, `closing_before`, `page`, `page_size` (20, at most 50); `has_more` |
| `GET /api/v1/auctions/{id}` | public (drafts: owner, admin) | FR-26 details (title, description, category, images, seller name) plus the `/live` fields; `has_reserve`, `reserve_met` for everyone; `reserve_price` for the owner only (BR-6) |
| `GET /api/v1/auctions/{id}/bids?before=&limit=` | public | newest first, keyset on `sequence_no`, at most 100; unpersisted outbox bids merged for live and closing auctions; aliases; admins also get `bidder_id` (BR-10) |
| `GET /api/v1/categories` | public | nested tree |
| `POST /api/v1/categories`, `PATCH`/`DELETE /api/v1/categories/{id}` | admin | `reason` required and audited with the actor (FR-20, FR-75); no cycles; no delete while it has subcategories or auctions |
| `PUT`/`DELETE /api/v1/watchlist/{auction_id}`, `GET /api/v1/watchlist` | authenticated | add is idempotent; cancelled refused; view with price, end time and `server_time_ms` (FR-24, FR-25) |
| `GET /api/v1/seller/auctions` | seller | own auctions including drafts, with price, bid count, times and outcome (FR-19) |
| `/media/...` | public | re-encoded images, read-only (named volume `media`) |

Live and closing items in lists, the watchlist and the dashboard take price, bid count and end time from Redis (one pipeline), falling back to `auctions.current_price`/`bid_count` (D7).

## 5. Auction status

draft, scheduled, live, closing, closed_sold, closed_no_sale, cancelled.

| From | To | Who | How |
|---|---|---|---|
| (new) | draft | seller | `POST /api/v1/auctions` |
| draft | scheduled | seller and owner | publish (needs an image, FR-12 re-checked) |
| draft, scheduled | cancelled | owner | cancel (FR-18, BR-9) |
| scheduled | live | scheduler | start time passed (FR-16); live state installed after the commit (FR-15) |
| live | closing | scheduler | `set_status.lua` sees Redis TIME >= `end_time_ms` (D9, FR-58) |
| live | cancelled | administrator | Phase 8 (force-cancel with a reason) |
| closing | closed_sold, closed_no_sale | settlement | Phase 8 |

Every transition is a conditional UPDATE (`AuctionRepository.transition`) with an `audit_log` row (`status_changed`, before and after) in the same transaction (FR-77, FR-78); the scheduler's rows have a NULL actor.

Editing by status (FR-17, BR-4): draft and scheduled, any field, re-validated, `original_end_time` follows `end_time` (D1); live, title, description and images only while Redis `sequence == 0` (D8; Redis unavailable means `SERVICE_UNAVAILABLE`), otherwise `AUCTION_ALREADY_LIVE`; closing and terminal, `ALREADY_CLOSED`.

### Scheduler (`services/scheduler.py`, beat every 5 s)

1. **Activate:** scheduled auctions with `start_time <= now()`, claimed `FOR UPDATE SKIP LOCKED`; conditional UPDATE to live plus audit, one transaction; a start more than two intervals late logs a warning (FR-16). After the commit: `cleanup_state.lua` removes any stale non-live hash, then `init_live_state`. Bids in between find no state and are rebuilt lazily by the bid path.
2. **Repair:** live auctions without Redis state get it built.
3. **Close:** live auctions with Postgres `end_time <= now()` are candidates (Postgres lags Redis, never leads it). `set_status.lua` flips Redis live to closing only once Redis TIME has reached `end_time_ms`; then a conditional UPDATE live to closing plus audit, and `on_closing` (logs only until Phase 8). Missing state is rebuilt first; a pending outbox defers to the next run; Redis closing with Postgres live is completed.

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

Migrations `0001` to `0008` in `backend/alembic/versions/`. Money is BIGINT paise, every timestamp is `timestamptz`, every FK is `ON DELETE RESTRICT`. Constraint names follow the naming convention in `app/models/base.py` (`pk_`, `uq_`, `ck_`, `fk_`, `ix_`).

| Table | PK | Key constraints and indexes |
|---|---|---|
| users | uuid | unique index on `lower(email)`; status CHECK (pending_verification, active, suspended) |
| user_roles | (user_id, role) | role CHECK (bidder, seller, admin) |
| sessions | uuid | `refresh_token_hash` UNIQUE; `revoked_at` for immediate revocation |
| email_verification_tokens, password_reset_tokens | bigint | `token_hash` UNIQUE |
| categories | bigint | `slug` UNIQUE; self-FK `parent_id`, indexed |
| auctions | uuid | CHECKs: end > start, starting_price > 0 (migration 0006, FR-37), min_increment > 0, reserve NULL or >= starting_price, extension_count 0..10, status in the 7 states, current_price NULL or > 0, bid_count >= 0. `current_price`/`bid_count` kept by the persister (D7). Trigger blocks any change to `original_end_time` once the auction has left draft and scheduled (FR-14, D1). Indexes: (status, start_time), (status, end_time), seller_id, category_id, created_at, GIN on generated `search_vector` ('simple' config, title weight A, description weight B) |
| auction_images | bigint | index on auction_id; `object_key` is the server-chosen storage key (D10) |
| bids (append-only) | bigint | UNIQUE (auction_id, sequence_no); amount > 0; bid_type manual or automatic; `end_time_after` (FR-55). Indexes: (auction_id, sequence_no DESC), (bidder_id, created_at DESC) |
| proxy_bids | bigint | partial UNIQUE (auction_id, bidder_id) WHERE is_active; max_amount > 0 |
| watchlist | (user_id, auction_id) | index on auction_id (watchers of an auction, FR-25) |
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
Auctions: AUCTION_ALREADY_LIVE (409), CATEGORY_NOT_FOUND (422), NOT_WATCHING (404), ALREADY_CLOSED (409), NOT_DRAFT (409, publish of a scheduled auction), UNSUPPORTED_IMAGE (422), IMAGE_TOO_LARGE (413), TOO_MANY_IMAGES (409), CATEGORY_IN_USE (409), CATEGORY_CYCLE (422), SLUG_TAKEN (409).
Validation errors carry an optional `fields` object of per-field messages (FR-12).
Bidding: AUCTION_CLOSED, AUCTION_NOT_STARTED, BID_TOO_LOW, BID_ABOVE_LIMIT, SELF_OUTBID_NOT_ALLOWED, BELOW_CURRENT_PRICE, PROXY_CANNOT_DECREASE (lower or equal maximum, FR-46), SERVICE_UNAVAILABLE.
Payment: NOT_WINNER, ALREADY_PAID, INVALID_SIGNATURE.
Real-time: NOT_SUBSCRIBED, TOO_MANY_SUBSCRIPTIONS, MESSAGE_TOO_LARGE, UNKNOWN_TYPE (RATE_LIMITED, SERVICE_UNAVAILABLE and INTERNAL_ERROR are also sent as `error` frames).
General: VALIDATION_ERROR (also returned by the bid service for a malformed amount or request_id), INTERNAL_ERROR, SERVICE_UNAVAILABLE (readiness with a dependency down; bid path when Redis is unreachable or live state cannot be established, see the client contract in section 2).
Internal only, never sent to clients: NOT_INITIALISED (script result meaning live state is absent; the service rebuilds or maps it).
Framework HTTP errors not listed above use `HTTP_<status>` (for example HTTP_405); 404 maps to NOT_FOUND.

## 10. WebSocket close codes

| Code | Meaning |
|---|---|
| 1000 | normal close |
| 1001 | server going away |
| 1008 | Origin refused (sent before accept, surfaces as HTTP 403) |
| 1009 | frame above the 64 KB protocol cap |
| 1011 | internal error: a connection task failed unexpectedly (logged at ERROR) |
| 4001 | authentication required: no `authenticate` as the first message within the timeout |
| 4002 | authentication failed, or re-authentication with a token of another user or session; do not auto-reconnect |
| 4003 | access token expired without a fresh `authenticate` (also when a bid or subscribe finds it expired) |
| 4004 | session revoked (logout, password reset, suspension, or a denylist hit on a bid or subscribe); do not auto-reconnect |
| 4008 | slow consumer: send queue overflowed |
| 4009 | policy violation: too many strikes |
| 4010 | the user already holds the maximum number of sessions (5) across instances |
| 4012 | replaced: a newer socket of the same session took over (CI-3); do not auto-reconnect |

4011 (idle timeout) is retired (D4).

## 11. Pseudonyms

Public bidder label: `Bidder-` plus the first four hex characters of a per-auction hash of the bidder id (NFR-27).

## 12. Identity (FR-1 to FR-10)

**Accounts.** `POST /api/v1/auth/register` creates a `pending_verification` user with the bidder role and an argon2id password hash (t=3, m=64 MiB, p=4, from config). Email is normalised to lower case; a duplicate gives `EMAIL_TAKEN` (FR-1). Passwords are 10 to 128 characters and may not equal the email.

**Tokens.** Opaque tokens (verification, reset, refresh) are 32 random bytes, urlsafe; only their SHA-256 is stored. Verification tokens last 24 h, reset tokens 1 h, both single use through one `UPDATE ... WHERE used_at IS NULL`. Access tokens are HS256 JWTs (`sub`, `roles`, `sid`, `iat`, `exp`, `jti`) valid 15 minutes; decoding pins `algorithms=["HS256"]`, requires every claim and checks expiry against the service clock. The refresh token lives only in an `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` cookie, 30 days sliding, rotated on every `POST /api/v1/auth/refresh`, which also requires an `X-Requested-With` header (NFR-21).

**Login (FR-4).** Rate limits per IP and per email first. An unknown email runs a dummy argon2 verify so timing matches a wrong password; both return the same `INVALID_CREDENTIALS` body. `EMAIL_NOT_VERIFIED` and `ACCOUNT_SUSPENDED` are returned only after the password verified.

**Revocation (FR-5, FR-6).** Postgres is the source of truth (`sessions.revoked_at`, `users.status`). Redis keys `revoked:sid:{sid}` and `revoked:user:{user_id}` (TTL = access-token lifetime) make the per-request check one round trip: a user key means `ACCOUNT_SUSPENDED`, a sid key means `INVALID_TOKEN`. Logout and password reset write sid keys (reset never writes a user key, so the next login works at once); suspension writes the user key. A failed write is retried once, then handed to the `denylist_revoke` Celery task; the `sync_denylist` beat task (every 30 s) rewrites every revocation of the last 15 minutes from Postgres. If Redis is unreachable at request time the check runs against Postgres instead of being skipped.

**Rate limits (NFR-22).** Fixed windows keyed by the clock bucket (`rl:{name}:{subject}:{window_start}`), counted by one Lua `INCR`+`EXPIRE`. Defaults: login 10/60 s per IP and 5/300 s per email; register 5/h per IP; resend and forgot 5/h per IP and 3/h per email; reset-password 10/h per IP; verify-email 20/h per IP. Reset checks the token with a plain SELECT before hashing the new password, so an invalid token never costs an argon2 hash; the atomic consume still decides. `RATE_LIMITED` is 429 with `Retry-After`. Redis down means `SERVICE_UNAVAILABLE` on these endpoints (fail closed).

**Profile (FR-9, FR-10).** `GET`/`PATCH /api/v1/users/me` (name and avatar only; the schema forbids other fields). `GET /api/v1/users/me/bids` pages the caller's ledger rows newest first with a `created_at:id` keyset cursor.

**Suspension.** `UserService.suspend_user(admin, user, reason)`: status, revocation of every session and the `audit_log` row (`user_suspended`, with the reason) commit in one transaction (FR-78, BR-12), then the user denylist key. The admin endpoint arrives with the admin work.

**Status codes.** 401 `INVALID_TOKEN`/`TOKEN_EXPIRED` for bearer problems and `INVALID_CREDENTIALS`; 400 for a bad body token; 403 `EMAIL_NOT_VERIFIED`, `ACCOUNT_SUSPENDED`, `FORBIDDEN`; 409 `EMAIL_TAKEN`; 429 `RATE_LIMITED`.
