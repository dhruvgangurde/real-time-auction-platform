# Architecture

Design reference. The SRS governs if anything here conflicts with it.

## 1. Components

| Component | Role |
|---|---|
| API (FastAPI) | REST and one WebSocket endpoint, stateless apart from connections |
| Worker (Celery) | Durable bid writes, settlement (`close_auction`), payment reconciliation, notifications and email, reconciliation |
| Beat | Scheduler: activate auctions and move them to closing (`run_scheduler`, 5 s), close repair sweep and unresolved-close alert (`sweep_closing`, 30 s), outbox sweep, reconciliation, denylist resync, payment reconciliation (`reconcile_payments`, 120 s), ending soon (`notify_ending_soon`, 30 s), email (`dispatch_notification_emails`, 15 s) |
| PostgreSQL | Source of truth |
| Redis | Live auction state, Lua arbitration, pub/sub, rate limits, task queue |
| Payment gateway | Razorpay in test mode only (AS-5): orders, payments, signed checkout callback and webhook (section 13) |
| Mailpit (dev only) | SMTP sink with a web UI, in `docker-compose.override.yml`; production uses any SMTP provider (TBD-1) |

## 2. Bid flow (accepted manual bid)

1. Client sends `place_bid {auction_id, amount, request_id}` over the WebSocket (Phase 5; REST fallback uses the same service, DC-9).
2. API checks, cheapest first: the per-connection and per-user rate limits, the token and denylist (account status), the bidder role, the room subscription and degraded mode (FR-34, FR-44; section 4).
3. `BidService.place_bid` validates the input (amount is an integer 1..2^53-1, request_id is 1..64 chars of `[A-Za-z0-9_-]`), refuses the auction's own seller with `SELLER_CANNOT_BID` (seller cached per process after the first bid; decisions 2026-10-13), and calls `place_bid.lua` with keys `state`, `outbox`, `req`. In one atomic step the script:
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
7. The worker's `persist_bids` task drains the outbox into the `bids` table with `ON CONFLICT (auction_id, sequence_no) DO NOTHING`, applies the extension of every bid it inserts to `auctions.end_time` and `extension_count` (never decreasing; a replayed row's extension is not applied again, so a replay cannot undo a force-close) with an audit entry in the same transaction, then XDELs the persisted entries. Retries with backoff; after 5 retries it raises the `LEDGER_WRITE_FAILED` alert and the entries stay in the outbox. A beat sweeper (`sweep_outboxes`, every 5 s) persists every non-empty outbox as a safety net.
8. Displaced bidders get `outbid_notice` on `user:{id}:events` (email notifications are Phase 9).

Rejected bids go back to the submitter only. Nothing is written to the ledger.

### Client contract for SERVICE_UNAVAILABLE

`SERVICE_UNAVAILABLE` means the real-time layer could not establish the bid's order (FR-42). When it is caused by a timeout the outcome is unknown: the script may have run. Clients must retry with the **same `request_id`**; the stored result is returned unchanged for 10 minutes, so a retry can never place a second bid. Phase 5 (WebSocket) and Phase 10 (frontend) must implement this.

### Postgres end_time lags Redis

Soft-close extensions reach `auctions.end_time` only through the persister, so Postgres can lag Redis by up to the sweep interval (5 s) plus retries. Redis `end_time_ms` is the authoritative end time while an auction is live. The scheduler decides "end time passed" from Redis (`set_status.lua`, D9); the close task drains the outbox first and re-checks Redis (section 7), never Postgres alone (FR-58).

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
| `closed_sent:{id}` | string, 24 h TTL | `auction_closed` already broadcast, by a close or an administrator cancel (FR-62). Set with the publish in `publish_closed_once.lua`; outside the `auction:{id}:` prefix, so live-state cleanup never removes it. Losing it allows at most one more broadcast. |

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
| `UNRESOLVED_CLOSE` | an auction is past `end_time` + `close_grace_s` (300 s) without a terminal status (NFR-12); raised by `sweep_closing`, see section 7 |
| `PAYMENT_MISMATCH` | a confirmed payment's amount, currency or order does not match its settlement; not applied (section 13) |
| `PAYMENT_DUPLICATE_CHARGE` | a second captured payment for an already paid settlement; refund manually |
| `EMAIL_DELIVERY_FAILED` | an email group (or a queued verification or reset email) failed its last attempt |

## 4. Channels and real-time layer

Redis pub/sub, one hub per API instance (`app/services/fanout.py`):

- `auction:{id}:events`: room events (`bid_placed`, `timer_extended`, `auction_closed`). Subscribed only while a local socket is in that room (reference counted).
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
| submitter | `subscribed` | `auction_id`, `your_alias`, `snapshot` (same shape as `/live` for an authenticated viewer, so it carries the subscriber's own `you_lead`, `your_max`, `your_result`) |
| submitter | `unsubscribed`, `pong` | |
| submitter | `bid_accepted` | `request_id`, `auction_id`, `sequence_no`, `amount`, `end_time_ms`, `extended`, `extension_count`, `placed_at_ms` (the Redis TIME of acceptance, DC-3; equals the ledger's `created_at`; null only on a replay of a result stored before this field existed) |
| submitter | `bid_rejected` | `request_id`, `auction_id`, `code`, `message`, `current_highest?`, `min_next?` |
| submitter | `proxy_armed` | `request_id`, `auction_id`, `max_amount` (own only, FR-51), `active` |
| submitter | `proxy_rejected` | `request_id`, `auction_id`, `code`, `message`, `min_next?` |
| submitter | `error` | `code`, `message`, `request_id?` |
| room | `bid_placed` | `auction_id`, `sequence_no`, `amount`, `bidder_alias`, `bid_type`, `end_time_ms`, `extended`, `extension_count`, `placed_at_ms` (as in `bid_accepted`; automatic bids share their trigger's time), `reserve_met` (null without a reserve, otherwise whether this bid's amount reaches it; never the reserve itself, BR-6) (one per accepted bid, automatic bids included) |
| room | `timer_extended` | `auction_id`, `sequence_no`, `end_time_ms`, `extension_count`, `final` (true at the 10th extension, FR-54) |
| room | `auction_closed` | `auction_id`, `outcome` (`closed_sold`, `closed_no_sale` or `cancelled`), `final_price` (null unless sold), `reserve_met` (exactly three values: null when the auction has no reserve; true when it was met: `closed_sold`, or on cancel a committed price at or above it; false otherwise: `closed_no_sale` with a reserve, or a cancel below it. The cancel value is what the auction detail shows. The reserve amount itself is in no payload, BR-6), `final_sequence_no` (the last bid's sequence; 0 without bids). Sent once per auction (FR-62) |
| room (local) | `presence` | `auction_id`, `participants`, `sequence_no`, `end_time_ms` |
| user | `outbid_notice` | `auction_id`, `current_price`, `sequence_no` |
| user | `notification` | `id`, `notification_type`, `auction_id`, `payload`, `created_at_ms`: a stored notification just created (FR-71); not sent for `outbid`, which `outbid_notice` already delivers live |
| user (internal) | `session_revoked` | `sid` or null: the instance closes the matching sockets with 4004 |
| user (internal) | `session_replaced` | `sid`, `gen`: the instance closes that session's sockets with a lower generation with 4012 |
| local | `system_status` | `degraded` |

**Outbid recipients:** the leader before the trigger, every maximum the trigger exhausted, and every bidder whose bid in the trigger was immediately exceeded, minus the new leader; each once. Retries republish; the stored outbid notification's `dedupe_key` is `outbid:{auction}:{user}:{sequence_no}`, so a retry stores nothing twice.

### Client rules (Phase 10 must implement)

Pub/sub is at-most-once and events from different instances can arrive out of order. Apply room events in `sequence_no` order; drop anything at or below the last applied sequence (retries republish); on a gap, wait briefly, then refetch `GET /api/v1/auctions/{id}/live`. Also refetch `/live` when a `presence.sequence_no` is above the last applied sequence (a lost event in a quiet room), and after `system_status {degraded: false}`. On `bid_rejected`/`proxy_rejected` with `SERVICE_UNAVAILABLE`, retry with the same `request_id`.

- **Buffer before `subscribed`.** Room events for an auction can arrive before its `subscribed` frame (the hub joins the room before reading the snapshot). Buffer them, then apply only those above `snapshot.sequence_no`.
- **Reconnect.** Reconnect with exponential backoff, re-authenticate, resubscribe and refetch (CI-6), but never automatically after 4002 (authentication failed), 4004 (session revoked) or 4012 (replaced by a newer socket of the same session, e.g. another tab). On 4003 refresh the access token first. Keep one socket per session.
- **`auction_closed` is always applied, never dropped by the sequence rule.** Its `final_sequence_no` equals the last bid's sequence, which the client has usually applied already. Apply buffered bid events up to `final_sequence_no` first (refetch `/live` on a gap), then show the outcome; no room events follow it. If it is lost (pub/sub is at-most-once), `/live` shows the terminal status from Postgres.
- **Own status.** Leading, outbid, won and lost come from the viewer's acknowledgements, `outbid_notice`, and `you_lead` / `your_result` in `/live` and `subscribed`, never from alias equality (aliases can collide). "You" row labels in the ledger may use alias equality, because bids placed by the viewer's maximum get no acknowledgement. After a reload, `your_max` restores the proxy panel.
- **No heartbeat needed.** There is no idle timeout; the browser answers the protocol keepalive by itself. `ping` exists for clients that want an application-level round trip.

### Pseudonyms (NFR-27)

`Bidder-` + first 4 hex characters of `HMAC-SHA256(ALIAS_SECRET, "{auction_id}:{bidder_id}")`. Stable within an auction, not linkable across auctions without the secret. Room events and snapshots never carry user ids. `subscribed.your_alias` tells each user their own alias.

### REST

- `GET /api/v1/auctions/{id}/live` (public; drafts only for their seller or an admin, FR-30): status, current price, leader alias, sequence, bid count, min next, end time, extension count, participants, last 20 bids (ledger merged with unpersisted outbox entries, never above the sequence; each with `sequence_no`, `amount`, `bidder_alias`, `bid_type`, `placed_at_ms`: `created_at` once persisted, the outbox's `accepted_at_ms` before), `reserve_met` (null without a reserve; live and closing: whether the current price reaches it; every other status: the value `auction_closed` carries; never the amount, BR-6), `self_outbid_blocked` (FR-38 as this auction enforces it: the `self_outbid` flag copied into its live state at init; without live state, the platform setting it will copy at go-live; the client disables bidding for the leader only when true) and `server_time_ms`. For an authenticated viewer only, never in a room event: `you_lead` (the viewer holds the highest bid, from the user id in the live state, not alias equality), `your_max` (the viewer's own active maximum or null; never anyone else's, FR-51; null after close because maxima are deactivated) and `your_result` (`won` or `lost` once the auction sold, `lost` only for someone who bid or armed a maximum; null otherwise). Anonymous responses do not have these keys. In the live path they come from the same MULTI as the state. Live and closing auctions are read from Redis live state (state, outbox and presence in one MULTI) and rebuilt when missing (FR-26); every other status is served from Postgres with `degraded: false` and writes nothing to Redis (D2). With Redis unreachable it is served from Postgres with `degraded: true` (NFR-13).
- `POST /api/v1/auctions/{id}/bids` and `/proxy` (bidder role): the REST fallback (DC-9, UC-01 2a). Same service, same `request_id` contract, same per-user rate limit; the response is the acknowledgement and events are published after it is sent. Rejections: 409 with the arbitration code, 422, 429, 503.

Lifecycle and discovery (Phase 7; `api/lifecycle.py`, `api/discovery.py`, services `AuctionService`, `CatalogService`):

| Endpoint | Who | Notes |
|---|---|---|
| `POST /api/v1/auctions` | seller (BR-1) | draft; FR-12 per-field errors; anti-snipe 60/120 default, bounded by config; 10 per hour per user (NFR-22) |
| `PATCH /api/v1/auctions/{id}` | seller and owner (BR-1) | rules by status in section 5; audit before/after |
| `POST /api/v1/auctions/{id}/publish` | seller and owner (BR-1) | draft to scheduled; at least one image (FR-13); FR-12 re-checked |
| `POST /api/v1/auctions/{id}/cancel` | owner (a former seller can still withdraw) | draft or scheduled to cancelled (FR-18, BR-9) |
| `POST /api/v1/auctions/{id}/images` | seller and owner (BR-1) | the body is the image (any Content-Type is read; OpenAPI declares image/jpeg, image/png and image/webp binary bodies so Swagger can upload); decoded JPEG/PNG/WebP only, at most 5 MB (streamed), pixel cap, re-encoded without metadata, random name, at most 8 (D10, NFR-24) |
| `DELETE /api/v1/auctions/{id}/images/{image_id}` | seller and owner (BR-1) | an auction that left draft keeps at least one image |
| `GET /api/v1/auctions` | public | `q` (at most 200, websearch syntax, title ranked above description), `category` (descendants included), `status` (repeatable; default scheduled, live, closing; draft and cancelled refused), `min_price`/`max_price` on `coalesce(current_price, starting_price)`, `closing_before`, `page`, `page_size` (20, at most 50); `has_more` |
| `GET /api/v1/auctions/{id}` | public (drafts: owner, admin) | FR-26 details (title, description, category, images, seller name) plus the `/live` fields; `has_reserve`, `reserve_met` for everyone; `reserve_price` for the owner only (BR-6) |
| `GET /api/v1/auctions/{id}/bids?before=&limit=` | public | newest first, keyset on `sequence_no`, at most 100; unpersisted outbox bids merged for live and closing auctions; aliases; admins also get `bidder_id` (BR-10) |
| `GET /api/v1/categories` | public | nested tree |
| `POST /api/v1/categories`, `PATCH`/`DELETE /api/v1/categories/{id}` | admin | `reason` required and audited with the actor (FR-20, FR-75); no cycles; no delete while it has subcategories or auctions |
| `PUT`/`DELETE /api/v1/watchlist/{auction_id}`, `GET /api/v1/watchlist` | authenticated | add is idempotent; cancelled refused; view with price, end time and `server_time_ms` (FR-24, FR-25) |
| `GET /api/v1/seller/auctions` | seller | own auctions including drafts, with price, bid count, times and outcome (FR-19) |
| `/media/...` | public | re-encoded images, read-only (named volume `media`) |
| `GET /api/v1/auctions/{id}/settlement` | winner, seller, admin | FR-64, BR-10: status, final price, winner `{id, full_name}` (null for a no-sale), times; anyone else FORBIDDEN; NOT_FOUND before settlement; never the reserve |

Administration (Phase 8; `api/admin.py`, `AdminService`, `UserService`). Every endpoint needs the admin role (FORBIDDEN otherwise, FR-75, FR-80). Every state change takes `{"reason": "..."}` (1 to 500 characters, VALIDATION_ERROR otherwise) and writes an audit row with the administrator and the reason in the same transaction (BR-12, NFR-14).

| Endpoint | Notes |
|---|---|
| `POST /api/v1/admin/auctions/{id}/force-close` | live: ended now, then settled by the close path (section 7); closing: runs the close path; draft or scheduled AUCTION_NOT_LIVE; terminal ALREADY_CLOSED (FR-63). Returns `status`, `outcome`, `settlement` |
| `POST /api/v1/admin/auctions/{id}/cancel` | draft, scheduled or live to cancelled, no settlement (BR-9); closing or terminal ALREADY_CLOSED |
| `GET /api/v1/admin/users?q&role&status&page&page_size` | `q` matches email or name, case-insensitive, LIKE wildcards literal; page size at most 100; `has_more` (FR-76) |
| `POST /api/v1/admin/users/{id}/suspend`, `/reinstate` | never yourself (CANNOT_TARGET_SELF) or the last active admin (LAST_ADMIN; decided under an advisory lock so two admins cannot suspend each other at once); suspend revokes every session and writes the denylist (user and session keys); reinstate (NOT_SUSPENDED unless suspended) sets active and deletes the user key, so a fresh login works at once; old sessions stay revoked (FR-76) |
| `PUT`/`DELETE /api/v1/admin/users/{id}/roles/{role}` | not your own admin role (CANNOT_TARGET_SELF), never the last active admin's (LAST_ADMIN); grant or revoke, audited with before/after roles. Roles travel in the token: a grant applies at the next login; a revoke (also through the CLI) revokes every session of the user like a suspension (denylist, `session_revoked`), so the old access and refresh tokens are dead at once and the audit row records the role and `sessions_revoked` |
| `GET /api/v1/admin/audit?actor_id&entity_type&entity_id&from&to&before_id&limit` | newest first, keyset on id (`next_before_id`), at most 200 per page; each entry with before, after and reason (FR-80) |

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
| live | closing | administrator | force-close with a reason: `set_status.lua` mode `end_now` ends it at Redis TIME (FR-63) |
| draft, scheduled, live | cancelled | administrator | cancel with a reason; a live auction is flipped in Redis first (BR-9) |
| closing | closed_sold, closed_no_sale | close task | settlement (section 7) |

Every transition is a conditional UPDATE (`AuctionRepository.transition`) with an `audit_log` row (`status_changed`, before and after) in the same transaction (FR-77, FR-78); the scheduler's and the close task's rows have a NULL actor; an administrator's carry the actor and the reason (a force-close writes `auction_force_closed`).

Editing by status (FR-17, BR-4): draft and scheduled, any field, re-validated, `original_end_time` follows `end_time` (D1); live, title, description and images only while Redis `sequence == 0` (D8; Redis unavailable means `SERVICE_UNAVAILABLE`), otherwise `AUCTION_ALREADY_LIVE`; closing and terminal, `ALREADY_CLOSED`.

### Scheduler (`services/scheduler.py`, beat every 5 s)

1. **Activate:** scheduled auctions with `start_time <= now()`, claimed `FOR UPDATE SKIP LOCKED`; conditional UPDATE to live plus audit, one transaction; a start more than two intervals late logs a warning (FR-16). After the commit: `cleanup_state.lua` removes any stale non-live hash, then `init_live_state`. Bids in between find no state and are rebuilt lazily by the bid path.
2. **Repair:** live auctions without Redis state get it built.
3. **Close:** live auctions with Postgres `end_time <= now()` are candidates (Postgres lags Redis, never leads it). `set_status.lua` flips Redis live to closing only once Redis TIME has reached `end_time_ms`; then a conditional UPDATE live to closing plus audit, and `on_closing`, which enqueues `close_auction` (a failed enqueue is logged; `sweep_closing` repairs it). Missing state is rebuilt first; a pending outbox defers to the next run; Redis closing with Postgres live is completed.

## 6. Settlement status

pending_payment, paid, no_sale. Only pending_payment to paid exists (a verified confirmation, section 13). A failed payment attempt leaves pending_payment so the bidder can retry. There is no payment window in Release 1: an unpaid settlement stays payable (known issue).

## 7. Close and settlement flow

`services/closing.py` (`Closer.close`), run by the Celery task `close_auction(auction_id)` (`acks_late`, up to 8 retries, backoff 1 s to 60 s). Any number of runs, concurrent, retried or after a crash at any point, give one settlement, one terminal transition and one notification set (NFR-11, OR-14).

1. **Drain.** Persist the auction's outbox with the ledger persister, so Postgres holds every accepted bid, `end_time` and `extension_count` (FR-55, NFR-15). If Redis or Postgres fails: `DEFERRED` (retried).
2. **One transaction:**
   1. **Lock (FR-57):** `pg_try_advisory_xact_lock(key)`, key = the first 8 bytes of the auction id as a signed 64-bit integer (stable across instances and versions; one-key locks never collide with the two-key proxy locks). Not acquired: `LOCKED`, exit cleanly. Released at commit or rollback, so there is no lease to expire.
   2. Already `closed_sold`/`closed_no_sale`, or a settlement exists: `ALREADY_CLOSED`. Any status but closing: `NOT_CLOSING`.
   3. **Re-check at execution (FR-58):** the outbox is still empty (else `DEFERRED`); Redis status is `closing` and Redis TIME >= `end_time_ms`, or there is no Redis state and Postgres `end_time` has passed (else `NOT_ENDED`, nothing written); `rebuilding` defers; the Redis `sequence` is not above the ledger maximum (else `DEFERRED`: never settle on a ledger missing an accepted bid). With D9 the end-time guard never fires.
   4. **Winner (FR-59):** highest amount, equal amounts by lowest `sequence_no`. Sold when there is a winner and either no reserve or `amount >= reserve` (FR-60, BR-5).
   5. **Settlement first:** `pending_payment` with winner and final price, or `no_sale` with both NULL (never payable); `idempotency_key = settle:{auction_id}`. With the lock bypassed, `UNIQUE(settlements.auction_id)` stops a second closer here: its IntegrityError on `uq_settlements_*` is treated as `ALREADY_CLOSED`, rolled back and logged, and no second notification set is written (OR-14).
   6. Conditional UPDATE closing to `closed_sold`/`closed_no_sale` with its audit row (actor NULL, before/after with winner and price) and a `settlement_created` audit row (FR-64); every active maximum deactivated (FR-51); notification rows (FR-62) with `dedupe_key = close:{auction_id}:{type}:{user_id}`: winner `payment_due`, seller `auction_sold` or `auction_no_sale`, every other participant (distinct bidders from `bids` union maximum holders from `proxy_bids`) `auction_lost`. Payload: auction id, title, outcome, final price, `reserve_met` (never the reserve, BR-6).
3. **After the commit:** `set_status.lua` closing to the terminal status; `auction_closed` is published by the caller whose flip returned `OK` or, when there is no live state (`MISSING`), by the caller that settled, both through `publish_closed_once.lua`: `SET closed_sent:{id} 1 NX EX 86400` and `PUBLISH` in one atomic call, so it goes out at most once per marker lifetime (clients apply it idempotently using `final_sequence_no`). The payload comes from the committed auction and settlement rows; a Redis error there is logged as a warning and the close stays committed. Then `cleanup_state.lua` removes the live state. A Redis failure here leaves the rest to the repair sweep.

**Repair sweep and alert (`sweep_closing`, beat every 30 s, NFR-12).** Re-enqueues closing auctions without a settlement and settled auctions whose Redis state still says closing (a crash after the commit). Any auction in scheduled, live or closing past `end_time + close_grace_s` raises `UNRESOLVED_CLOSE` (a CRITICAL log line with `alert`).

**Force-close (FR-63).** For a live auction:
1. `set_status.lua` live to closing in mode `end_now`, which moves `end_time_ms` back to Redis TIME when later and has no end-time condition. Missing state is rebuilt first.
2. Drain.
3. One transaction with the row locked: conditional live to closing, `end_time` set to the forced instant (`original_end_time` untouched), `auction_force_closed` audit row with actor, reason and before/after.
4. `Closer.close`. `DEFERRED` or `LOCKED` hands the auction to `close_auction`.

A Redis status already `closing` at step 1 counts as flipped, so the call can be repeated after a failure between the flip and the commit. A closing auction skips steps 1 and 3's transition but still gets the audit row.

**Admin cancel (BR-9).** For a live auction: `set_status.lua` live to cancelled (bids refused from then on), drain, then one transaction: conditional live to cancelled, maxima deactivated, audit with actor and reason; after the commit `auction_closed {outcome: cancelled}` from the committed row through `Closer.broadcast_closed` (the same `closed_sent` marker as a close, so a retried cancel sends it once) and the live state is removed. A Redis status already `cancelled` counts as flipped (retry after a failure). Draft or scheduled: the transition and audit only; an auction that went live meanwhile is handled as live.

**Operator procedure: Redis sequence ahead of the ledger.** The close defers on every run and `UNRESOLVED_CLOSE` fires after the grace period (`SEQUENCE_GAP` usually fired earlier). Such an auction is never settled automatically. The operator:
1. Takes the auction id from the alert; checks the outbox and dead-letter streams (`XLEN`/`XRANGE auction:{<id>}:bids` and `:bids:dead`) and the gap: Redis `sequence` against `SELECT max(sequence_no) FROM bids WHERE auction_id = ...`.
2. If outbox entries are pending, lets `persist_bids`/`sweep_outboxes` drain them, first fixing whatever `LEDGER_WRITE_FAILED` reported. The next sweep then settles normally.
3. If bids are really lost (no outbox entries, a true gap), establishes the facts from the logs: every accepted bid has an FR-44 log line with its sequence number, amount and bidder. Restoring the missing ledger rows is a reviewed, recorded administrative act run as the owner role (the app role cannot alter the ledger), with an `audit_log` entry stating the reason.
4. Only once the ledger is complete does the auction settle, through the normal path (`close_auction`, or a force-close).

## 8. Data model (tables)

Migrations `0001` to `0010` in `backend/alembic/versions/`. Money is BIGINT paise, every timestamp is `timestamptz`, every FK is `ON DELETE RESTRICT`. Constraint names follow the naming convention in `app/models/base.py` (`pk_`, `uq_`, `ck_`, `fk_`, `ix_`).

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
| payments | uuid | One row per settlement and gateway order: UNIQUE idempotency_key (`order:{settlement_id}`), UNIQUE gateway_order_id, UNIQUE gateway_payment_id (the captured payment), partial UNIQUE settlement_id WHERE status = 'succeeded'; status created, failed, succeeded; amount > 0; `reconciled_at` (migration 0010) |
| payment_events (append-only) | bigint | Every applied terminal confirmation once: UNIQUE dedupe_key (`webhook:{event id}`, `callback:{payment id}:{outcome}`, `reconcile:{payment id}:{outcome}`); source, event, gateway payment id, outcome succeeded or failed; no payload, no contact data |
| notifications | bigint | UNIQUE dedupe_key; type CHECK (outbid, ending_soon, auction_won, auction_lost, payment_due, payment_received, from migration 0009 auction_sold and auction_no_sale, from 0010 payment_failed); email delivery state `email_status` (pending, sent, skipped, failed), `email_attempts`, `email_next_at` (lease or backoff), `emailed_at`; partial index (user_id, created_at DESC) WHERE read_at IS NULL; partial index for the email queue WHERE email_status = 'pending' |
| notification_preferences | (user_id, type) | type CHECK |
| audit_log (append-only) | bigint | `actor_id` NULL for system actions. Indexes: (entity_type, entity_id), (actor_id, created_at), created_at |

### Database roles (DC-4, FR-79)

| Role | Used by | Privileges |
|---|---|---|
| owner (`POSTGRES_USER`) | Alembic only, via `MIGRATION_DATABASE_URL` | owns every object |
| `auction_app` | api, worker, beat, via `DATABASE_URL` | SELECT, INSERT, UPDATE, DELETE on ordinary tables; SELECT, INSERT only on `bids`, `audit_log` and `payment_events`; SELECT only on `alembic_version`; USAGE on schema and sequences; no TRUNCATE, no default privileges |

## 9. Error codes (add new ones here)

Auth: EMAIL_TAKEN, INVALID_TOKEN, TOKEN_EXPIRED, INVALID_CREDENTIALS, EMAIL_NOT_VERIFIED, ACCOUNT_SUSPENDED, RATE_LIMITED, FORBIDDEN, NOT_FOUND.
Auctions: AUCTION_ALREADY_LIVE (409), AUCTION_NOT_LIVE (409, force-close of a draft or scheduled auction), CATEGORY_NOT_FOUND (422), NOT_WATCHING (404), ALREADY_CLOSED (409), NOT_DRAFT (409, publish of a scheduled auction), UNSUPPORTED_IMAGE (422), IMAGE_TOO_LARGE (413), TOO_MANY_IMAGES (409), CATEGORY_IN_USE (409), CATEGORY_CYCLE (422), SLUG_TAKEN (409).
Validation errors carry an optional `fields` object of per-field messages (FR-12).
Bidding: SELLER_CANNOT_BID (403: the auction's seller, for bids and maxima), AUCTION_CLOSED, AUCTION_NOT_STARTED, BID_TOO_LOW, BID_ABOVE_LIMIT, SELF_OUTBID_NOT_ALLOWED, BELOW_CURRENT_PRICE, PROXY_CANNOT_DECREASE (lower or equal maximum, FR-46), SERVICE_UNAVAILABLE.
Administration: NOT_SUSPENDED (409, reinstating an account that is not suspended); CANNOT_TARGET_SELF (409, an admin suspending their own account or removing their own admin role); LAST_ADMIN (409, suspending or demoting the last active administrator, from the API or the CLI); FORBIDDEN for anyone without the admin role; ALREADY_CLOSED for a force-close of a terminal auction or a cancel of a closing or terminal one.
Payment: NOT_WINNER (403), ALREADY_PAID (409), INVALID_SIGNATURE (400, checkout callback or webhook), PAYMENT_MISMATCH (409, amount or currency differs from the settlement), AMOUNT_BELOW_GATEWAY_MINIMUM (409, under 100 paise), PAYLOAD_TOO_LARGE (413, webhook body over 64 KB), SERVICE_UNAVAILABLE (503, gateway unreachable or not configured).
Notifications: NOT_FOUND for another user's notification (FR-74).
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

## 13. Payments (Phase 9: FR-65 to FR-69, NFR-25, DC-10, DP-1, SI-3)

Razorpay in test mode only: a key id that is not `rzp_test_…` is refused at startup everywhere (AS-5). The winner pays in Razorpay Checkout, so card data never reaches the platform (DC-10). `services/payment_gateway.py` holds the gateway interface (`create_order`, `find_order(receipt)`, `order_payments`, `capture`), the httpx client (no SDK) and the two signature checks; tests use an in-memory gateway. Nothing in bidding, closing or settlement creation calls the gateway (DP-1).

| Endpoint | Who | Notes |
|---|---|---|
| `POST /api/v1/auctions/{id}/payment` | the winner | FR-65: NOT_WINNER for anyone else (a no-sale has no winner), ALREADY_PAID, AMOUNT_BELOW_GATEWAY_MINIMUM. Returns `{payment_id, settlement_id, status, amount, currency, gateway, key_id, order_id}` for the checkout. The amount is the settlement's: send no body or `{}`; a body with any field (an amount, say) is VALIDATION_ERROR (422, DC-11). 10 per minute per user |
| `POST /api/v1/auctions/{id}/payment/confirm` | the winner | Body: the three ids the checkout returns (`razorpay_order_id`, `razorpay_payment_id`, `razorpay_signature`, strict patterns, nothing else). 200 `{status: paid|failed}`, 202 `{status: pending}` while the gateway cannot be read, 400 INVALID_SIGNATURE, 409 PAYMENT_MISMATCH. 20 per minute per user |
| `POST /api/v1/payments/razorpay/webhook` | the gateway | No session; admitted only by `X-Razorpay-Signature` over the raw body. 200 once processed or deliberately ignored, 400 INVALID_SIGNATURE, 413 above 64 KB, 5xx on a database or limiter failure so the gateway redelivers. 600 per minute per address |
| `GET /api/v1/auctions/{id}/settlement` | winner, seller, admin | now includes `payment {id, status, amount, currency, order_id, payment_id, updated_at_ms}` (FR-69) |

**Order (FR-66).** One payment row and one gateway order per settlement (`idempotency_key = order:{settlement_id}`, receipt = settlement id). No transaction is held across the gateway call: tx 1 returns an existing row (touching `updated_at`); then `create_order`; a duplicate-receipt refusal adopts the order found by receipt (an earlier attempt whose commit failed); tx 2 inserts with ON CONFLICT DO NOTHING (any unique key) and reads the row back, so concurrent starts share one payment row and one exposed order. Razorpay's sandbox does not refuse duplicate receipts, so such starts can leave unused extra orders at the gateway, never shown to anyone (known issue). Failed attempts are retried on the same order (B.5).

**Confirmation (FR-67, FR-68).** Three sources, any order, any number of times, concurrently:
1. Checkout callback: `hex HMAC_SHA256(key_secret, stored_order_id|payment_id)`, constant-time, checked before anything else; then `sync_order`.
2. Webhook: `hex HMAC_SHA256(webhook_secret, raw_body)` checked before parsing; `payment.captured` applies success and `payment.failed` failure from the signed payload, with no outbound call; any other event is ignored. The event id (`x-razorpay-event-id`) is the dedupe key.
3. Reconciler `reconcile_payments` (beat, 120 s, FR-69, S4): orders in created or failed of pending_payment settlements whose last activity is between 120 s and 7 days old, least recently checked first (`reconciled_at` nulls first), 50 per run; `sync_order` each.

`sync_order` reads the order's payments from the gateway: a captured one applies success; else an authorized one is captured by the platform (so the dashboard's auto-capture setting does not matter; "already captured" means read again) and applied; else the latest failed one applies failure.

Applying is one transaction on the payment row lock (`FOR UPDATE` by gateway order id):
- success: refused with PAYMENT_MISMATCH if the amount differs from the payment or the settlement, or the currency is not INR; nothing new if already succeeded (a different payment id raises PAYMENT_DUPLICATE_CHARGE); otherwise the `payment_events` row, payment created/failed to succeeded with `gateway_payment_id`, settlement pending_payment to paid (a conditional update that must change one row or the transaction rolls back), audit rows `payment_succeeded` and settlement `status_changed` (FR-64), `payment_received` to winner and seller;
- failure: nothing if the payment already succeeded or this gateway payment's failure was applied before; otherwise the `payment_events` row, payment to failed, audit `payment_failed`, `payment_failed` to the winner. failed to succeeded stays possible (late authorisation); succeeded is terminal.
A `payment_events` row is written only when an outcome is applied; a sync that changes nothing writes nothing. Exactly once rests on the row lock, with the conditional updates, `UNIQUE gateway_payment_id`, the partial unique succeeded payment per settlement and the notification `dedupe_key` as backstops.

**Client contract (Phase 10).** Open Checkout with `key_id`, `order_id` and the amount from `POST …/payment`; on the handler's success post its three ids to `/confirm`; on 202 show "confirming" and poll `GET …/settlement` (the webhook or the reconciler completes it); on `failed` offer to pay again (same order). The client never sends an amount.

## 14. Notifications (Phase 9: FR-70 to FR-74, SI-4)

Every notification is a row first (`services/notifications.py`), written by its producer with a `dedupe_key`, so a retried producer writes nothing twice:

| Producer | Types | dedupe_key |
|---|---|---|
| close (section 7) | `auction_won` and `payment_due` to the winner, `auction_sold` or `auction_no_sale` to the seller, `auction_lost` to every other participant | `close:{auction}:{type}:{user}` |
| payments (section 13) | `payment_received` to winner and seller; `payment_failed` to the winner | `pay:{settlement}:payment_received:{user}`, `pay:{payment}:failed:{gateway payment}` |
| outbid | `outbid` to the bidders a bid displaced (same recipients as `outbid_notice`), written by the task `record_outbid`, queued fire-and-forget after the bid's acknowledgement | `outbid:{auction}:{user}:{sequence_no}` |
| ending soon (beat, 30 s) | `ending_soon` to watchers of a live auction whose end is within 300 s; once per auction, even after an extension (FR-73) | `ending_soon:{auction}:{user}` |

**In-app (FR-71).** The row is the record; after the commit the producer pushes `notification` on the recipient's channel (at-most-once). Outbid rows are not pushed, since `outbid_notice` already reached the bidder live.

**API (FR-74).** `GET /api/v1/notifications?unread=&before=&limit=` (newest first, keyset on id, at most 100; `{items: [{id, type, auction_id, payload, read, created_at_ms}], next_before}`); `POST /api/v1/notifications/{id}/read` (204; another user's or unknown is NOT_FOUND); `POST /api/v1/notifications/read-all` (`{updated}`); `GET`/`PUT /api/v1/notifications/preferences` (`{email: {type: bool}}`, every type defaults to on; unknown types are refused).

**Email (FR-72, SI-4).** `dispatch_notification_emails` (beat, 15 s):
1. Claim: a `(user, type, auction)` group is due once its oldest pending row is older than 60 s; its pending rows are claimed with `FOR UPDATE SKIP LOCKED`, `email_attempts + 1` and a 5-minute lease in `email_next_at`, and the claim commits before anything is sent, so the user's own reads and writes never wait behind SMTP.
2. One email per group (repeats combined, "outbid 5 times"); preference off or an account that is not active: `skipped` (the in-app row is unaffected).
3. Sent: `sent` and `emailed_at`. Failed: retried after 1, 2, 4 and 8 minutes; after the fifth attempt `failed` and `EMAIL_DELIVERY_FAILED`. Delivery is at least once: a crash between the SMTP accept and the final update re-sends after the lease.
Emails are built from the payload only (no reserve, no maximum ever appears in one); amounts are formatted from paise with integer arithmetic. Verification and reset emails are queued to the `send_email` task (`QueuedEmailSender`, retried), so the request never waits for SMTP; their arguments are never logged. Senders: `SmtpEmailSender` (any SMTP server through `SMTP_*`; Mailpit in dev) or the dev-only console sender.

