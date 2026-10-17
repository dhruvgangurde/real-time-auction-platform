# Real-Time Auction Platform

## Start

```
cp .env.example .env
docker compose up --build
```

## Tests and lint

```
docker compose exec api pytest
docker compose exec api pytest tests/db
docker compose exec api ruff check .
docker compose exec api ruff format --check .
```

`docker compose ps` shows `migrate` as `Exited (0)`. That is expected: it is a one-shot service that applies migrations before api, worker and beat start.

## Migrations

Migrations run as the database owner (`MIGRATION_DATABASE_URL`). The app runs as the restricted `auction_app` role (`DATABASE_URL`).

```
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1
docker compose exec api alembic check
```

New migrations are hand-written in `backend/alembic/versions/` and must grant privileges on any new table to the app role explicitly.

## Host ports

| Service | Host port | Container port |
|---|---|---|
| API | 8010 | 8000 |
| Postgres | 5442 | 5432 |
| Redis | 6389 | 6379 |
| Mailpit web UI (dev only) | 8025 | 8025 |

Change them with `API_HOST_PORT`, `POSTGRES_HOST_PORT` and `REDIS_HOST_PORT` in `.env`.

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
