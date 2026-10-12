"""The signed-in user's notifications and email preferences (FR-74). Routing only."""

from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import current_user, get_notification_service
from app.schemas.notifications import PreferencesUpdate
from app.services.auth import CurrentUser
from app.services.notifications import NotificationService

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("")
async def my_notifications(
    unread: bool = Query(default=False),
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(current_user),
    notifications: NotificationService = Depends(get_notification_service),
) -> dict[str, Any]:
    return await notifications.page(user, unread=unread, before=before, limit=limit)


@router.post("/read-all")
async def mark_all_read(
    user: CurrentUser = Depends(current_user),
    notifications: NotificationService = Depends(get_notification_service),
) -> dict[str, int]:
    return await notifications.mark_all_read(user)


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: int,
    user: CurrentUser = Depends(current_user),
    notifications: NotificationService = Depends(get_notification_service),
) -> Response:
    await notifications.mark_read(user, notification_id)
    return Response(status_code=204)


@router.get("/preferences")
async def preferences(
    user: CurrentUser = Depends(current_user),
    notifications: NotificationService = Depends(get_notification_service),
) -> dict[str, dict[str, bool]]:
    return await notifications.preferences(user)


@router.put("/preferences")
async def set_preferences(
    body: PreferencesUpdate,
    user: CurrentUser = Depends(current_user),
    notifications: NotificationService = Depends(get_notification_service),
) -> dict[str, dict[str, bool]]:
    return await notifications.set_preferences(user, dict(body.email))
