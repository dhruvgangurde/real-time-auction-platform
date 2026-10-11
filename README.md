# Real-Time Auction Platform

## Start

```
cp .env.example .env
docker compose up --build
```

## Tests and lint

```
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
```

## Migrations

```
docker compose exec api alembic upgrade head
docker compose exec api alembic revision --autogenerate -m "message"
```

## Host ports

| Service | Host port | Container port |
|---|---|---|
| API | 8010 | 8000 |
| Postgres | 5442 | 5432 |
| Redis | 6389 | 6379 |

Change them with `API_HOST_PORT`, `POSTGRES_HOST_PORT` and `REDIS_HOST_PORT` in `.env`.
