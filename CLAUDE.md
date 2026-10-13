# Real-Time Auction Platform — Project Rules

This file is read at the start of every session. Follow it for all work in this repo.

## 1. Session protocol (do this every time)

1. Read `docs/currentstate.md` and `docs/roadmap.md` before doing anything.
2. Work only on the current phase listed in `docs/currentstate.md`. Do not start later phases.
3. Before writing code, state the plan in a few lines. For anything touching bidding, settlement or payments, show the plan and wait for approval.
4. After each task: run the tests, then update `docs/currentstate.md` (what was done, what is next, known issues).
5. If a decision is made or changed, add an entry to `docs/decisions.md`.
6. If a requirement is unclear, check `docs/` (SRS, use case model). If still unclear, ask. Do not guess on correctness-critical behaviour.
7. Tick roadmap items only when their acceptance criteria are met and tests pass.

## 2. Source documents

- `docs/` holds the SRS and use case model. Requirement IDs (FR-n, NFR-n, BR-n, DC-n, OR-n) are fixed. Cite them in code comments and tests where relevant.
- `docs/architecture.md` is the design reference. `docs/requirements-map.md` says which requirements belong to which phase. `docs/testing.md` lists the required tests.
- Where a diagram and a requirement statement conflict, the requirement statement wins.

## 3. Stack

- Backend: Python 3.12, FastAPI (async REST + one WebSocket endpoint), SQLAlchemy 2.x async + asyncpg, Alembic, redis-py asyncio, Celery + celery beat, pydantic v2 / pydantic-settings, uv for dependencies.
- Data: PostgreSQL 16 (only durable store), Redis 7 (live state, Lua arbitration, pub/sub, rate limits, Celery broker).
- Frontend: React + TypeScript (strict), Vite. Built in phase 10 only.
- Payments: Razorpay, sandbox mode only.
- Local run: Docker Compose. Load test: k6.

## 4. Ports (host side)

| Service | Host port | Container port |
|---|---|---|
| Web (nginx: SPA, `/api`, WebSocket, `/media`) | 8011 | 80 |
| API, first instance only (dev only: Swagger, curl) | 8010 | 8000 |
| Postgres | 5442 | 5432 |
| Redis | 6389 | 6379 |
| Mailpit web UI (dev only) | 8025 | 8025 |

Ports 5432 and 8000 are used by another project on this machine. Never bind them. Mailpit (dev SMTP sink) exists only in `docker-compose.override.yml`; its UI port is `MAILPIT_UI_HOST_PORT`. Containers talk to each other on the container ports. API, Postgres and Redis host mappings exist only in `docker-compose.override.yml` (the API mapping reaches the first instance only). Host ports are configurable through `WEB_HOST_PORT`, `API_HOST_PORT`, `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`. Compose project name is `auction`; never use `container_name`.

## 5. Repo layout

```
backend/app/
  main.py
  core/          config, logging, errors, db, redis
  api/           routers only, no business logic
  services/      all business logic
  repositories/  all database queries (the only place queries are built)
  models/        SQLAlchemy models
  schemas/       pydantic request/response schemas
  workers/       Celery app and tasks
  lua/           Redis Lua scripts
backend/tests/
frontend/
loadtest/
docs/
```

Layering rule: `api -> services -> repositories`. Services must be unit-testable with repositories mocked. Controllers never contain logic or queries.

## 6. Non-negotiable rules

These come from the SRS. Breaking one is a bug, even if the tests pass.

1. **PostgreSQL is the only source of truth.** Redis is rebuildable. No data may live only in Redis beyond the short interval needed to persist it. Redis state for any auction must be rebuildable from Postgres. (DC-1, FR-43)
2. **One atomic Redis Lua script decides every bid.** It checks status and end time, validates the increment, writes the new highest bid and bidder, increments the sequence, and applies soft close, all in one step. No distributed locks on the bidding path. (DC-2, FR-35)
3. **Time comes from Redis `TIME` inside the script.** Never from the app instance clock, never from the client. (DC-3)
4. **Soft close is inside the same script.** Never a client countdown, never a separate step. (FR-52)
5. **Bidding fails closed.** If Redis is down, reject bids with a temporary-unavailability error. Never accept a bid whose order cannot be established. (FR-42, NFR-9)
6. **Acknowledge the submitter before the durable write and before the broadcast.** (FR-40)
7. **Settlement:** per-auction lock plus `UNIQUE(settlement.auction_id)`. The lock is the primary mechanism, the constraint is the backstop. Re-check the end time at execution, not at scheduling. (FR-57, 58, 61)
8. **Everything retryable is idempotent, enforced by DB uniqueness:** settlement creation, payment callbacks, notification dispatch. (DC-5)
9. **Money is integer paise (BIGINT) everywhere.** No floats in any layer. All timestamps are UTC (`timestamptz`). (DC-6, OR-1)
10. **`bids` and `audit_log` are append-only,** enforced by withholding UPDATE and DELETE from the application's DB role, not just by code. (DC-4)
11. **Audit entries are written in the same DB transaction as the change they describe.** (FR-78)
12. **A proxy maximum is never exposed** to anyone but its owner, in any API, event, log line or error. (BR-7, FR-51)
13. **Validate every request body and WebSocket message** against a declared schema with explicit limits before it reaches business logic. (DC-11)
14. **No raw card data** touches our servers. (DC-10)
15. **Parameterised queries only.** No SQL by string interpolation. (NFR-20)
16. **Secrets only via environment variables.** Never committed. (NFR-26)

## 7. Conventions

- Errors: one envelope everywhere, `{"error": {"code": "STABLE_CODE", "message": "..."}}`. Codes are stable strings. New codes go in `docs/architecture.md`.
- Logging: structured JSON, with a correlation ID on every request and WebSocket event. Every bid submission logs correlation ID, auction, user, amount, decision and sequence number. (FR-44, NFR-32)
- API is versioned under `/api/v1`. Schema changes go through Alembic migrations only, reversible, committed to git. Never edit the database by hand.
- Type hints on all Python code. TypeScript strict mode on the frontend. ruff for lint and format.
- Keep it simple. No extra abstractions, frameworks or features beyond what the SRS needs. Prefer a small working solution over an elaborate one.
- Never add words like "final", "complete", "corrected" or version tags to titles or headings in docs, diagrams or UI.

## 8. Definition of done (for any task)

- The code runs from a clean `docker compose up --build`.
- New behaviour has tests, and the full test suite passes.
- Relevant requirement IDs are cited in tests.
- No rule in section 6 is violated.
- `docs/currentstate.md` is updated. `docs/decisions.md` is updated if a decision was made.
- Nothing is committed that should not be (secrets, `.env`, caches).

## 9. Never do

- Never start a later roadmap phase without being told.
- Never use `time.time()` or `datetime.now()` in the bid path.
- Never use a Redis lock, `SETNX` mutex or `WATCH/MULTI` for bid arbitration. Lua only.
- Never read-then-write the highest bid from application code.
- Never store a bid, proxy maximum or settlement only in Redis.
- Never write a migration that weakens a uniqueness constraint or append-only protection.
- Never skip or delete a failing test to get green. Fix the cause or ask.
- Never leave a TODO that hides a correctness gap. Log it in `docs/currentstate.md` under Known issues.

## 10. Commands

Keep this section current as commands come into existence.

```
cp .env.example .env                           # first run only
docker compose up --build                      # start everything
docker compose exec api pytest                 # run all tests
docker compose exec api pytest tests/db        # DB integration tests (own auction_test DB)
docker compose exec api pytest tests/bidding   # arbitration integration tests (Redis db 15 + auction_test)
docker compose exec api pytest tests/bidding/test_latency.py -s   # NFR-1 p50/p95/p99
docker compose exec api pytest tests/bidding/test_proxy_property.py --hypothesis-show-statistics   # OR-13 property test
docker compose exec api pytest tests/auth tests/test_security_unit.py   # identity tests (fake clock, no sleeps)
docker compose exec api pytest tests/realtime  # WebSocket tests on two in-process uvicorn instances
docker compose exec api pytest tests/closing   # close task: lock, backstop, re-check, outcomes, sweep
docker compose exec api pytest tests/admin     # admin endpoints and settlement read
docker compose exec api pytest tests/payments  # payments: order, callback, webhook, reconcile, races (no network)
docker compose exec api pytest tests/notifications   # notification API, producers, email dispatch
docker compose exec api pytest tests/lifecycle tests/scheduler tests/discovery   # Phase 7 tests
docker compose exec api pytest tests/discovery/test_nfr6.py -s   # NFR-6 p50/p95 with 2,000 auctions, query plans
docker compose exec api python -m app.cli grant-role <email> seller   # roles only via CLI (D6); revoke-role too
docker compose exec api python -m app.cli seed-categories   # default category tree (idempotent)
docker compose exec api python -m app.cli reinstate <email> --reason "..."   # recovery (e.g. no active admin)
docker compose exec worker celery -A app.workers.celery_app inspect registered   # worker tasks
docker compose exec api ruff check .           # lint
docker compose exec api ruff format --check .  # format check
docker compose exec api alembic upgrade head   # apply migrations (owner role, MIGRATION_DATABASE_URL)
docker compose exec api alembic check          # models vs migrations drift check
curl localhost:8010/api/v1/health/ready        # readiness check (first api instance, dev)
curl localhost:8011/api/v1/health/ready        # readiness through the web service (either instance)
docker compose exec web nginx -t              # web service config check
docker compose logs api api-2                 # both api instances (which one took a socket)
```
