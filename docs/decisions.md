# Decisions

Newest first. Add an entry whenever a decision is made or changed. Format: date, decision, reason.

## 2026-10-12 Phase 6 real-time layer choices
- **One hub per instance, reference-counted pub/sub.** An instance subscribes to a room or user channel only while it has a local socket there, so fan-out cost follows local interest (NFR-30). Each socket has a bounded queue; overflow closes it with 4008 instead of slowing the hub (NFR-5).
- **Acknowledge, then publish.** The ack goes on the submitter's own FIFO queue before any publish, so the submitter sees its ack before its own `bid_placed` (FR-40, NFR-3). The REST fallback publishes in a background task after the response.
- **Sequence numbers on every room event, at-most-once delivery accepted.** Pub/sub can lose or reorder events across instances; clients order by `sequence_no`, drop duplicates, and refetch `/live` on a gap, on `presence.sequence_no` ahead of them, or after recovery from degraded mode. Retries republish on purpose: a retry after a timeout publishes what the first attempt never did.
- **Presence carries `sequence_no` and `end_time_ms`** so a quiet room detects a lost `bid_placed` within one presence interval (FR-32).
- **Degraded mode** is per instance: lost pub/sub means `system_status {degraded: true}`, bids rejected with `SERVICE_UNAVAILABLE`, and `degraded: false` only after every referenced channel is resubscribed. The pub/sub client has no transparent retries so a dropped connection surfaces.
- **Outbid recipients** are the previous leader, every exhausted maximum, and every bidder whose bid in the same trigger was immediately exceeded (e.g. a manual bid answered by a proxy), minus the new leader, each once. The last group extends the agreed rule; without it a bidder outbid by a proxy response gets no notice (UC-01 post-condition).
- **Lua returns `previous_leader` and `exhausted`** under `_internal` for addressing notices only; the service strips them and payloads are built field by field. No decision, sequence or outbox entry changed.
- **Pseudonyms** are `Bidder-` + 4 hex of HMAC-SHA256 with `ALIAS_SECRET` (NFR-27); collisions within one auction are possible (65,536 values) and accepted as display-only.
- **Origin allowlist** applies when an Origin is present; a missing Origin (non-browser client) is allowed because a token is still required.
- **Room membership is required for WS bids** (FR-34, `NOT_SUBSCRIBED`); the REST fallback cannot hold a subscription and skips only that check. Both require the bidder role (FR-7).
- **Session revocation reaches sockets two ways:** `session_revoked` on the user channel closes them on every instance, and every bid re-runs the denylist check, so a lost broadcast still denies the next action (FR-6).
- **`/live` falls back to Postgres** with `degraded: true` when Redis is unreachable, so viewing stays available read-only (NFR-13); its end time may then lag (see the FR-58 note).
- **Tests run two real uvicorn servers inside pytest** with independent hubs on one Redis, driven by real WebSocket clients; `create_app()` gained a lifespan and dependencies come from overridable providers.
- **`auction_test` is created once per pytest session.** The `test_db` fixture is registered in two conftests; recreating it twice killed earlier connections.

## 2026-10-12 Identity before WebSockets (roadmap phases 5 and 6 swapped)
The WebSocket layer authenticates its first message with the access token (CI-4, FR-5) and needs `current_user`, the denylist check and the session model, so Identity is built first. Roadmap Phase 5 is now Identity and Phase 6 the real-time layer.

## 2026-10-12 Identity choices
- **`EMAIL_TAKEN` on duplicate registration** (FR-1 says so), even though it reveals that an address is registered. Non-enumeration is applied where the SRS requires it: login (FR-4), resend-verification and forgot-password (FR-8).
- **argon2id** via argon2-cffi with t=3, m=64 MiB, p=4 from config; hashing and verification run in a worker thread. An unknown email verifies against a real throwaway hash so timing matches a wrong password.
- **Password rules:** minimum 10 (FR-2), maximum 128, not equal to the email, new users get the bidder role (the SRS is silent on the last three).
- **Access tokens are HS256 JWTs, 15 min**, with `sub`, `roles`, `sid`, `iat`, `exp`, `jti`; decode pins the algorithm and requires every claim. Expiry is checked against the injected service clock, not the JWT library's wall clock, so tests control time. Roles in the token are up to 15 minutes stale; role changes are out of scope until the admin work.
- **Refresh tokens** are opaque, hashed at rest, cookie-only (`HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`), 30 days sliding, rotated on use (NFR-17). Refresh requires `X-Requested-With` as CSRF protection (NFR-21). Logout requires a valid access token.
- **Denylist with Postgres fallback.** Per-sid keys for logout and reset, a per-user key only for suspension (a user key after reset would block the user's next login for 15 min). Writes retry once, then go to a Celery task, and the `sync_denylist` beat task rewrites recent revocations from Postgres every 30 s; a failure of both write and enqueue raises the `DENYLIST_WRITE_FAILED` alert. On a Redis error at request time the check runs against Postgres and is never skipped (FR-6).
- **Rate limits are fixed windows keyed by the clock bucket**, so a limit resets when the clock enters the next window and tests advance a fake clock instead of sleeping. They fail closed (`SERVICE_UNAVAILABLE`) when Redis is down: a limiter that stops counting is no brute-force protection, and bidding already fails closed without Redis.
- **Console email backend is dev-only** (TBD-1 open): it logs the whole message including links with raw tokens, and `check_startup_config()` refuses it outside `APP_ENV=local`, as it refuses a `JWT_SECRET` under 32 bytes.
- **Unauthenticated token endpoints are rate limited per IP** (reset 10/h, verify 20/h), and reset validates the token with a SELECT before the 64 MiB argon2 hash, so bogus tokens cannot be used to burn memory and CPU. The single-use `UPDATE ... WHERE used_at IS NULL` still runs after hashing and decides races.
- **Bid history uses a keyset cursor** (`created_at_ms:id`), newest first (FR-10), on the existing `(bidder_id, created_at DESC)` index.
- **No `X-Forwarded-For` handling yet.** Rate limits use the direct peer address; a reverse-proxy deployment must add trusted-proxy handling first.

## 2026-10-12 Phase 4 proxy bidding choices
- **`arm_seq` is a counter at the serialisation point, not a timestamp.** Every set or raise does `HINCRBY arm_seq` and stores `max|arm_seq`; equal maxima are won by the lower `arm_seq` (FR-49). Redis TIME can tie at microsecond resolution and a rebuild cannot reproduce it; a counter is exact, ordered by the same atomic step that orders bids, and rebuilds from Postgres (restarting above the highest `arm_seq` ever persisted). **A raise takes a new `arm_seq`:** the tie goes to whoever committed to that amount first, so raising resets priority.
- **Automatic bids may be sub-increment** (FR-48 caps at the maximum, FR-49 lets the leader stop at its own maximum). A maximum of 300 facing a manual 295 at increment 10 bids 300. The SRS wins over the earlier "below the next minimum bid places nothing" wording.
- **Exhaustion rule, one rule only:** after every resolution, a maximum whose owner is not the leader and that no longer exceeds the highest bid is deactivated (UC-02 5a). The leader's own maximum stays armed even when it sits at its own maximum. Phase 9 uses the `proxy_deactivated` outbox entry for the outbid notification.
- **A manual bid equal to an armed maximum wins** (FR-48: a maximum acts only when it *exceeds* the highest bid). The proxy places nothing and is deactivated. The eBay-style "earlier commitment wins" would need the manual bid rejected at arbitration with a code the SRS does not define.
- **Lowering or setting an equal maximum is rejected** with `PROXY_CANNOT_DECREASE`. FR-46 forbids lowering; the SRS is silent on equal, and an equal value changes nothing a bidder could want, so it is treated as a client error rather than a silent no-op.
- **Any non-live auction gives `AUCTION_CLOSED` for a maximum** (FR-46, UC-02 2b), including a scheduled one, unlike manual bids which distinguish `AUCTION_NOT_STARTED` (FR-36).
- **Closed-form resolution, at most two automatic bids per trigger.** The runner-up's bid at its own maximum is recorded first when it is a valid bid below the winner's target, then the winner bids once at `min(own max, competing + increment)`. Never a per-increment loop. With equal maxima the runner-up's record bid would equal the winner's price and is skipped, so ledger amounts stay strictly increasing.
- **Privacy under FR-49 (second-price property).** The final price inherently reveals the runner-up's level, and the record bid at the runner-up's maximum discloses nothing beyond that. BR-7 protects the leading maximum, which is never bid at; `test_proxy_privacy.py` asserts that value appears nowhere except its owner's own result. Proxy log lines carry no amount; `audit_log` holds no proxy amounts.
- **Shared Lua core via a load-time include.** `place_bid.lua` and `set_proxy.lua` contain `--#include _core`, expanded by `app/lua/__init__.py` from `app/lua/_core.lua` (state loading, `accept()`, `resolve()`). One source of truth, and `SCRIPT LOAD` still sees one script per entry point.
- **Proxy rows in Postgres.** `proxy_bids.arm_seq` (migration 0007). The persister applies `proxy_set` only when its `arm_seq` is above every row ever persisted for that (auction, bidder), active or not, under `pg_advisory_xact_lock(hashtext(auction), hashtext(bidder))`, so a stale replay held by a concurrent persister can never resurrect a deactivated maximum; `proxy_deactivated` flips only the row with its own `arm_seq`. No `audit_log` rows for proxy changes: FR-77 does not list them and they must not carry amounts.
- **`hypothesis` property test with `derandomize=True`:** 200 random op sequences through the real scripts, compared step by step with a pure-Python reference model (`tests/bidding/proxy_model.py`). The fixed seed keeps CI deterministic; the table-driven cases (hand-computed, 49 cases across increments 1, 50 and 1000) are the known-correct oracle (OR-13).

## 2026-10-12 Phase 3 bid arbitration choices
- **Outbox stream.** Accepted bids are appended by the arbitration script to a per-auction Redis stream in the same atomic step. The persister deletes entries only after the Postgres commit, so a bid is never acknowledged without being either in the outbox or in the ledger (NFR-10). This replaces "fire a task and hope": a lost Celery message or a crashed worker only delays persistence.
- **Redis AOF `everysec` and `noeviction`.** AOF shrinks the window of acknowledged-but-unpersisted bids that a Redis crash can lose to about one second. `noeviction` means live state and outbox entries are never silently dropped under memory pressure: writes fail and the bid path fails closed instead (FR-42, BR-14). Scripts start with `#!lua` so Redis refuses the whole script under OOM rather than failing midway through its writes.
- **Request dedupe scoped by bidder.** The idempotency key is `auction:{id}:req:{bidder}:{request_id}`, not just `request_id`, so two bidders who happen to reuse a client request id can never receive each other's result. Only acceptances are stored (10 min TTL); rejections write nothing.
- **Extension rule.** FR-53 says "extend the end time by the configured extension amount, evaluated against the then-current end time", so the new end is `end_time + extension`, not `now + extension`. It is unambiguous; recorded here because the user asked for an explicit choice.
- **Acknowledge before persist.** `place_bid` returns as soon as the script has decided (FR-40). The Celery enqueue runs in the event loop's executor and is not awaited; an enqueue failure is logged and the beat sweeper persists the bid anyway.
- **Self-outbid policy is a platform environment flag** (`SELF_OUTBID_POLICY_ENABLED`, default true), copied into each auction's live state at init. FR-38 says "where the policy is enabled" but gives nobody the power to set it per auction; a column can be added if the product later wants it.
- **`starting_price > 0`** (migration 0006). FR-37's limit is 100 x the starting price; with a zero start price every bid would be `BID_ABOVE_LIMIT`. Tightening the CHECK keeps FR-37 literal.
- **Script numbers are strings.** Lua numbers are doubles and cjson keeps 14 significant digits, so the script receives amounts as decimal strings, writes integers back with `%.0f`, and returns every number as a string. The service validates amounts are integers <= 2^53-1 before calling Redis.
- **Rebuild uses a `rebuilding` status with a token and lease.** `init_state.lua` writes the snapshot as `rebuilding`; `place_bid.lua` treats that as not initialised; the service re-checks the ledger max and only then commits via `finalize_state.lua` (token-guarded). A crashed rebuild is taken over after its 5 s lease. Two concurrent rebuilds cannot clobber each other.
- **Rebuild refuses while the outbox is non-empty.** A snapshot taken then would miss accepted bids. The caller fails closed with `SERVICE_UNAVAILABLE`; the sweeper drains the outbox within seconds.
- **`SEQUENCE_COLLISION` keeps the bid.** If an outbox entry's sequence already exists in the ledger with different data (Redis reissued a number after losing writes), the entry is written to `audit_log` (action `sequence_collision`) in the persister's transaction and copied to `auction:{id}:bids:dead` before deletion. Nothing is lost; an operator decides.
- **Reconciliation checks every number in 1..sequence**, not just the ledger max against the Redis sequence, and also alerts `SEQUENCE_REGRESSION` when the ledger is ahead of Redis. Read order (sequence, outbox, ledger) means a concurrent persister can never cause a false gap.
- **Postgres `auctions.end_time` lags Redis** by up to the sweep interval, because extensions reach Postgres only through the persister. Redis `end_time_ms` is authoritative while live. Phase 7 and Phase 8 must not decide "ended" from Postgres alone (FR-58).
- **Client contract.** `SERVICE_UNAVAILABLE` after a timeout means the outcome is unknown; clients retry with the same `request_id`. Phase 5 and Phase 10 must implement this.
- **Tests use Redis DB 15** and the existing `auction_test` database, with committed rows (the persister, scripts and rebuild use their own connections). The concurrency test has 20 bidders on 20 separate connection pools race to 500 accepted bids (about 5,000 submissions), each bidding exactly the reported minimum next amount.

## 2026-10-12 Phase 2 schema choices
- **Primary keys.** UUID (`gen_random_uuid()`) for users, auctions, settlements, payments and sessions, because these IDs appear in URLs and events and should not be guessable or reveal volume. BIGINT identity for high-volume or internal tables: bids, audit_log, notifications, auction_images, proxy_bids, categories and the token tables.
- **Payments allow retries.** `payments.settlement_id` is not unique. A partial unique index allows at most one `succeeded` payment per settlement, so a failed attempt can be retried. This differs from the SRS ER model (B.2), which says a settlement "has at most one Payment". FR-67 and DC-5 are met by the unique `gateway_ref` and `idempotency_key`, and the requirement statements win over the diagram.
- **`bids.end_time_after`** stores the auction end time after a bid that triggered a soft-close extension, so FR-55's "persist the updated end time alongside the bid" holds without touching `original_end_time`.
- **`original_end_time` immutability** is enforced by a `BEFORE UPDATE OF original_end_time` trigger that raises `check_violation`, not only by code (FR-14).
- **Sessions table** stores hashed refresh tokens with `revoked_at`, so logout and suspension revoke immediately (FR-6).
- **`notifications.dedupe_key`** is UNIQUE so a retried dispatch cannot create a second notification (DC-5).
- **Owner/app role split.** Alembic runs as the owner via `MIGRATION_DATABASE_URL`. api, worker and beat run as `auction_app` via `DATABASE_URL`. Migration `0005` creates the role if missing, always re-applies its password from `APP_DB_PASSWORD`, and grants per table. The app role gets SELECT only on `alembic_version`, so it can never change migration state. There are no default privileges, so every future migration that adds a table must grant on it explicitly. Downgrade revokes all grants but keeps the role, because roles are cluster-wide and shared with the test databases.
- **`migrate` compose service.** A one-shot service runs `alembic upgrade head` before api, worker and beat start (`service_completed_successfully`). Without it a clean `docker compose up --build` would leave readiness at 503, because the app role only exists after migration `0005`.
- **Full-text search** uses the `'simple'` text search config (no stemming), because titles are product names and listings may mix languages.
- **`alembic check`** reports no drift on the generated `search_vector` column, the `lower(email)` index or the partial indexes, so nothing is excluded from autogenerate comparison.
- **`proxy_bids.armed_at` semantics are not decided yet.** Whether raising a maximum resets `armed_at` matters for FR-49's equal-maxima rule and is decided in Phase 4.
- **DB integration tests** use their own databases: `auction_test`, created and migrated once per session, and `auction_migtest`, created and dropped inside the migration-cycle test. Dev data and shared test tables are never wiped.

## 2026-10-11 Phase 1 foundation choices
- JSON logging uses the stdlib `logging` module with a `ContextVar` for the correlation ID. No logging library.
- Correlation ID middleware is a pure ASGI callable, not `BaseHTTPMiddleware`, so Phase 5 can reuse it for WebSocket events (NFR-32). On websocket scopes it only sets the context variable and logs; no response header.
- Readiness failures reuse the error code `SERVICE_UNAVAILABLE` with the failing dependency named in the message. Framework HTTP errors other than 404 use `HTTP_<status>`.
- Compose reads host port variables from `.env`, so `cp .env.example .env` is the first step from a clean checkout.
- The image installs the uv virtualenv at `/opt/venv` so the `./backend:/app` bind mount does not hide it. uvicorn `--reload` uses `WATCHFILES_FORCE_POLLING` because inotify does not cross the Windows bind mount.
- Celery beat writes its schedule file to `/tmp` inside the container, not the mounted repo.
- Dev dependencies (pytest, ruff) are installed in the image so tests and lint run with `docker compose exec api`.

## 2026-10-11 Native Windows with Docker Desktop
Repo lives at `C:\dev\auction` (moved out of OneDrive) and Claude Code runs on native Windows, not WSL. `.gitattributes` forces LF so line endings stay normal inside containers.

## 2026-10-11 Non-default host ports
API 8010, Postgres 5442, Redis 6389. Another project on this machine uses 5432 and 8000. Compose project name `auction`, no `container_name`.

## 2026-10-11 Redis runs in Docker only
No local Redis install. Docker Compose provides it.

## Earlier decisions (carried over from the SRS work)
- Semester scope: no dispute handling, no moderation queue, no i18n, no compliance sections.
- Performance targets scaled to what a laptop can demonstrate, verified with k6.
- Correctness over features. The three guarantees (no lost or duplicate bids, soft close, one settlement) define the project.
- Soft close is enforced inside the bid arbitration script, never client-side.
- Atomic Lua scripts for bid arbitration, no distributed locks on the bidding path.
- SRS v1.1 (plain-language rewrite, requirement IDs unchanged) is approved. The college-grade SRS is the technical reference.
- UML diagrams belong to the individual lab experiments, not the SRS.
- Email provider (TBD-1) and demo hosting (TBD-2) are still open.
