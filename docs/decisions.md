# Decisions

Newest first. Add an entry whenever a decision is made or changed. Format: date, decision, reason.

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
