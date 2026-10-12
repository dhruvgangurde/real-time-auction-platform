# Decisions

Newest first. Add an entry whenever a decision is made or changed. Format: date, decision, reason.

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
