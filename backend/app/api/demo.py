"""Demo mode (brief 12.3, 12.4). Every route depends on the switch: while DEMO_MODE is off each
answers 404 exactly as an unknown path does (B2 Q3), so the landing page learns whether the demo
is on from GET /api/v1/demo alone. Routing only; the rules live in DemoService."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import client_ip, get_demo_service, get_limiter
from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimit, RateLimiter
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
    return await cache.get(OVERVIEW, demo.overview)
