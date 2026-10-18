# Real-Time Auction Platform

A web platform for live English auctions: sellers list items, bidders bid in a live room over a WebSocket (by hand, or up to a maximum the platform bids for them), the auction extends when a bid lands in its last seconds, and the winner pays through Razorpay in test mode.

**The guarantee:** every accepted bid is recorded once, in the order it was decided, and every auction settles exactly once.

## How it works

- One atomic Redis Lua script decides every bid: it checks the auction, the increment and the time (Redis's own clock), writes the new highest bid with the next sequence number, and applies the soft close, all in one step.
- The bidder is answered first; the bid reaches Postgres, the only source of truth, through an outbox a Celery worker drains, and the room hears of it through Redis publish/subscribe on every API instance.
- A per-auction lock plus a unique constraint settle each auction once; the ledger and the audit log are append-only at the database role.
- Bidding fails closed when Redis is unreachable; Redis's state can always be rebuilt from Postgres.

The design is in [docs/architecture.md](docs/architecture.md), and every decision with its reason in [docs/decisions.md](docs/decisions.md).

## Stack

Python 3.12, FastAPI (REST and one WebSocket endpoint), SQLAlchemy 2 async with asyncpg, Alembic, Celery with beat, PostgreSQL 16, Redis 7; React with TypeScript (strict) and Vite; nginx in front of two API instances; Razorpay in test mode; Docker Compose; k6 for the load test; pytest, Vitest and Playwright.

## Run it locally

```
cp .env.example .env
docker compose up --build
cd frontend && npm ci && npm run build    # the web app, served on 8011
```

Open http://localhost:8011. `docker compose ps` shows `migrate` as `Exited (0)`: it applies the migrations once, before the API and the workers start.

| Service | Host port |
|---|---|
| Web app, the API and the WebSocket through nginx | 8011 |
| API, first instance only (development) | 8010 |
| Postgres | 5442 |
| Redis | 6389 |
| Mailpit, the development mail inbox | 8025 |

Change them with `WEB_HOST_PORT`, `API_HOST_PORT`, `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT` and `MAILPIT_UI_HOST_PORT` in `.env`.

## Demo accounts

For a local demo on the development stack:

1. Set `DEMO_ACCOUNTS_PASSWORD` in `.env` (10 to 128 characters; `.env` is never committed). Every seeded account signs in with it. Left empty, each run makes new passwords and prints them once.
2. Run the seed. It creates four accounts (a seller, two bidders and an administrator) and six lots.

   ```
   docker compose up -d
   docker compose exec api python -m app.cli seed-demo
   ```

3. Read the credentials file the seed writes to a local folder outside the repository (`DEMO_PRIVATE_DIR` in `.env`). It lists every account with its email and password, the six lots with their state and end time, and the app and Mailpit links.

"Try to break it" on the home page sends 50 simulated bidders at a fresh lot and then checks the ledger in Postgres; it runs with demo mode on: `DEMO_MODE=true docker compose --profile demo up -d`.

## Payment test details (Razorpay test mode)

Verified on this platform, in Razorpay's test window:

- Netbanking: enter any mobile number, choose Netbanking and a bank (Bank of Baroda - Retail Banking was used), then Success on the bank's test page.
- UPI: the VPA `success@razorpay`.

Test cards, copied from Razorpay's test card documentation, https://razorpay.com/docs/payments/payments/test-card-details/ (read 2026-10-18). Use any future expiry date and any CVV; an OTP of 4 to 10 digits succeeds, fewer than 4 digits fails. Test mode only. Cards have not been tried on this platform.

| Network | Card number | Type |
|---|---|---|
| Visa | 4100 2800 0000 1007 | Debit, consumer |
| Mastercard | 5555 5100 0008 1006 | Credit, business |
| Mastercard | 5180 2872 0009 1001 | Prepaid, consumer |
| RuPay | 6527 6589 0000 1005 | Credit, consumer |
| Diners | 3608 280009 1007 | Credit, consumer |
| Amex | 3402 560004 01007 | Credit, consumer |

International cards on the same page: Mastercard 5555 5555 5555 4444, 5105 1051 0510 5100 and 5104 0600 0000 0008; Visa 4012 8888 8888 1881. The failure scenarios (declined, insufficient funds, timed out and others) have their own numbers there.

## Tests

```
docker compose exec api pytest                  # backend: unit and integration, own test database
docker compose exec api ruff check .            # backend lint (and ruff format --check .)
cd frontend && npm test                         # Vitest, and the verification matrix check
cd frontend && npm run lint && npm run typecheck
```

The browser tests run against 8011 with the real security headers, after a build, on the isolated stack: its own database (`auction_e2e`), Redis databases and media volume, so a run never adds accounts or lots to your development data, with raised rate limits (many accounts from one address). `docker compose up -d` brings the development stack back.

```
docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.isolated.yml -f docker-compose.e2e.yml up -d
cd frontend && npm run build && npm run e2e
```

The load test (about 10 minutes, on the isolated data at production limits, then the development stack again; edit nothing under `backend/` while it runs, the API reloads):

```
bash loadtest/run.sh
```

## Measured

From the load-test run of 2026-10-18 on one laptop (110 simulated bidders in one auction, through nginx to two API instances, production limits). Every figure, its method and the machine are in [loadtest/RESULTS.md](loadtest/RESULTS.md); how each is measured is in [loadtest/README.md](loadtest/README.md).

| Requirement | Target | Measured |
|---|---|---|
| NFR-1 deciding a bid, 95th percentile | 10 ms or less | 0.121 ms, every one of 27,651 bids timed |
| NFR-2 bid sent to bid shown to the room, 95th percentile | 300 ms or less | 10.0 ms |
| NFR-3 room event delivered, 95th percentile | 150 ms or less | 7.0 ms |
| NFR-4 accepted bids a second on one auction, slowest 5 seconds | at least 20 | 39.6 |
| NFR-5 bidders in one room across two servers | at least 100 | 110, none missed or repeated an event |
| NFR-6 listing, search, detail, 95th percentile | 500 ms or less | 19.2, 21.9, 30.1 ms |
| NFR-7 bid written to Postgres, 95th percentile | 2 s or less | 275.1 ms |
| NFR-8 counters | exposed | the platform's own counts matched the ledger exactly |
| NFR-28 lost or duplicated bids | none | none in 17,532 accepted bids |

## Verification

[docs/verification.md](docs/verification.md) maps every requirement of the specification to the tests, load-test rows, inspections and demonstrations that verify it, and lists the ones without any as gaps, with the reason. `npm test` fails when it falls out of date.

## Backup and restore

The `backup` service takes one database dump and one archive of the uploaded images per day while the stack runs, keeps seven days, and writes them to `BACKUP_DIR`, a folder outside the repository. `bash backend/scripts/restore-drill.sh` takes a backup with the same code, restores it into a scratch database and folder, and checks it: every table's rows, the six ledger checks on a real run, every image byte for byte. Its evidence: [ops/evidence/](ops/evidence/).

## Migrations

Migrations run as the database owner (`MIGRATION_DATABASE_URL`); the application runs as the restricted `auction_app` role (`DATABASE_URL`), which may not update or delete bids or audit entries.

```
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1
docker compose exec api alembic check
```

New migrations are hand-written in `backend/alembic/versions/` and must grant privileges on any new table to the application role explicitly.
