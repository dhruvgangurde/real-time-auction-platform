"""Liveness and readiness (OR-10)."""

from fastapi import APIRouter

from app.core.errors import AppError
from app.services import health

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict[str, str]:
    failing = await health.readiness()
    if failing:
        raise AppError("SERVICE_UNAVAILABLE", f"{failing} unreachable", 503)
    return {"status": "ok"}
