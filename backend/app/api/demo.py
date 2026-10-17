"""Demo mode (brief 12.3, 12.4). Every route depends on the switch: while DEMO_MODE is off each
answers 404 exactly as an unknown path does (B2 Q3), so the landing page learns whether the demo
is on from GET /api/v1/demo alone. Routing only; the rules live in DemoService."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.deps import client_ip, get_demo_service, get_limiter
from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimit, RateLimiter
from app.schemas.demo import StressStart
from app.services import live_events
from app.services.demo import DemoService
from app.services.snapshot import SnapshotCache

OVERVIEW = UUID(int=0)  # the overview's key in the per-instance demo cache


async def require_demo_mode() -> None:
    if not settings.demo_mode:
        raise AppError("NOT_FOUND", "Not Found", 404)


router = APIRouter(prefix="/api/v1/demo", tags=["demo"], dependencies=[Depends(require_demo_mode)])


@router.get("")
async def overview(
    request: Request,
    response: Response,
    ip: str = Depends(client_ip),
    limiter: RateLimiter = Depends(get_limiter),
    demo: DemoService = Depends(get_demo_service),
) -> dict[str, Any]:
    """Read once per landing view; anonymous, so counted per client address like /live and
    cached 1 s per instance (brief 12.2)."""
    anon = RateLimit.parse(settings.rate_limit_anon_read_ip)
    await limiter.hit("anon_demo", ip, anon, fail_open=True)
    cache: SnapshotCache = request.app.state.demo_cache
    response.headers["Cache-Control"] = "public, max-age=1"
    body = await cache.get(OVERVIEW, demo.overview)
    return body | {"server_time_ms": live_events.server_time_ms()}  # for the cooldown's clock


@router.post("/stress")
async def start_stress(
    response: Response,
    _: StressStart | None = None,  # no body, or {}; any field is VALIDATION_ERROR (DC-11)
    x_requested_with: str | None = Header(default=None),
    ip: str = Depends(client_ip),
    demo: DemoService = Depends(get_demo_service),
) -> dict[str, Any]:
    """Anonymous by the 2026-10-13 decision (a recorded deviation from NFR-21): there is no
    cookie, session or user to forge, and X-Requested-With keeps a plain cross-site form from
    starting a run (a cross-origin script fails its preflight: no CORS here). 202 for a new run,
    200 for the run already in progress, which the caller then watches."""
    if not x_requested_with:
        raise AppError("FORBIDDEN", "X-Requested-With header required", 403)
    started = await demo.start_stress(ip)
    response.status_code = 200 if started["joined"] else 202
    return started


@router.get("/stress/{run_id}")
async def stress_status(
    run_id: UUID,
    request: Request,
    response: Response,
    ip: str = Depends(client_ip),
    limiter: RateLimiter = Depends(get_limiter),
    demo: DemoService = Depends(get_demo_service),
) -> dict[str, Any]:
    """Polled every second while the page watches a run (brief 8.3): anonymous, counted per
    address like /live, cached 1 s per instance. Either instance may answer a poll, so a client
    keeps the highest version it has seen and ignores an older body."""
    anon = RateLimit.parse(settings.rate_limit_anon_read_ip)
    await limiter.hit("anon_demo", ip, anon, fail_open=True)
    cache: SnapshotCache = request.app.state.demo_cache
    response.headers["Cache-Control"] = "public, max-age=1"
    body = await cache.get(run_id, lambda: demo.stress_status(run_id))
    return body | {"server_time_ms": live_events.server_time_ms()}
