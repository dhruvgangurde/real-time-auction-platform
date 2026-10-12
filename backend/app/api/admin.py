"""Administrator endpoints (FR-63, FR-75, FR-76, FR-80, BR-9, BR-12, NFR-14). Routing only.

Every endpoint needs the admin role (FORBIDDEN otherwise); every state change needs a reason of
1 to 500 characters, kept in its audit row with the administrator."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from app.api.deps import get_admin_service, get_user_service, require_roles
from app.schemas.auctions import AdminReason, Role, UserStatus
from app.schemas.auth import UserProfile
from app.services.admin import AdminService
from app.services.auth import CurrentUser
from app.services.users import UserService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
admin_only = require_roles("admin")


@router.post("/auctions/{auction_id}/force-close")
async def force_close(
    auction_id: UUID,
    body: AdminReason,
    admin: CurrentUser = Depends(admin_only),
    service: AdminService = Depends(get_admin_service),
) -> dict[str, Any]:
    return await service.force_close(admin, auction_id, body.reason)


@router.post("/auctions/{auction_id}/cancel")
async def cancel_auction(
    auction_id: UUID,
    body: AdminReason,
    admin: CurrentUser = Depends(admin_only),
    service: AdminService = Depends(get_admin_service),
) -> dict[str, Any]:
    return await service.cancel(admin, auction_id, body.reason)


@router.get("/users")
async def list_users(
    q: str | None = Query(default=None, max_length=200),
    role: Role | None = Query(default=None),
    status: UserStatus | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=20, ge=1, le=100),
    _: CurrentUser = Depends(admin_only),
    service: AdminService = Depends(get_admin_service),
) -> dict[str, Any]:
    return await service.list_users(q, role, status, page, page_size)


@router.post("/users/{user_id}/suspend", response_model=UserProfile)
async def suspend_user(
    user_id: UUID,
    body: AdminReason,
    admin: CurrentUser = Depends(admin_only),
    users: UserService = Depends(get_user_service),
) -> UserProfile:
    await users.suspend_user(admin.id, user_id, body.reason)
    return UserProfile.model_validate(await users.get_profile(user_id))


@router.post("/users/{user_id}/reinstate", response_model=UserProfile)
async def reinstate_user(
    user_id: UUID,
    body: AdminReason,
    admin: CurrentUser = Depends(admin_only),
    users: UserService = Depends(get_user_service),
) -> UserProfile:
    await users.reinstate_user(admin.id, user_id, body.reason)
    return UserProfile.model_validate(await users.get_profile(user_id))


@router.put("/users/{user_id}/roles/{role}", response_model=UserProfile)
async def grant_role(
    user_id: UUID,
    role: Role,
    body: AdminReason,
    admin: CurrentUser = Depends(admin_only),
    users: UserService = Depends(get_user_service),
) -> UserProfile:
    await users.change_role(user_id, role, grant=True, actor_id=admin.id, reason=body.reason)
    return UserProfile.model_validate(await users.get_profile(user_id))


@router.delete("/users/{user_id}/roles/{role}", response_model=UserProfile)
async def revoke_role(
    user_id: UUID,
    role: Role,
    body: AdminReason,
    admin: CurrentUser = Depends(admin_only),
    users: UserService = Depends(get_user_service),
) -> UserProfile:
    await users.change_role(user_id, role, grant=False, actor_id=admin.id, reason=body.reason)
    return UserProfile.model_validate(await users.get_profile(user_id))


@router.get("/audit")
async def audit_log(
    actor_id: UUID | None = Query(default=None),
    entity_type: str | None = Query(default=None, max_length=64),
    entity_id: str | None = Query(default=None, max_length=200),
    since: AwareDatetime | None = Query(default=None, alias="from"),
    until: AwareDatetime | None = Query(default=None, alias="to"),
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    _: CurrentUser = Depends(admin_only),
    service: AdminService = Depends(get_admin_service),
) -> dict[str, Any]:
    return await service.audit(
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        since=since,
        until=until,
        before_id=before_id,
        limit=limit,
    )
