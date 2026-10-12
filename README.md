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
