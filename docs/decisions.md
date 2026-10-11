# Decisions

Newest first. Add an entry whenever a decision is made or changed. Format: date, decision, reason.

## 2026-10-11 Non-default host ports
API 8010, Postgres 5442, Redis 6389. Another project on this machine uses 5432 and 8000. Compose project name `auction`, no `container_name`.

## 2026-10-11 Redis runs in Docker only
No local Redis install. Docker Compose provides it.

## 2026-10-11 Windows with WSL2 Ubuntu
Repo and Claude Code live inside WSL for fast bind mounts and normal line endings.

## Earlier decisions (carried over from the SRS work)
- Semester scope: no dispute handling, no moderation queue, no i18n, no compliance sections.
- Performance targets scaled to what a laptop can demonstrate, verified with k6.
- Correctness over features. The three guarantees (no lost or duplicate bids, soft close, one settlement) define the project.
- Soft close is enforced inside the bid arbitration script, never client-side.
- Atomic Lua scripts for bid arbitration, no distributed locks on the bidding path.
- SRS v1.1 (plain-language rewrite, requirement IDs unchanged) is approved. The college-grade SRS is the technical reference.
- UML diagrams belong to the individual lab experiments, not the SRS.
- Email provider (TBD-1) and demo hosting (TBD-2) are still open.
