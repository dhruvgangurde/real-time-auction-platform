"""Own profile and bid history (FR-9, FR-10)."""

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_user, get_user_service
from app.schemas.auth import UserProfile
from app.schemas.users import BidHistoryItem, BidHistoryPage, ProfileUpdate
from app.services.auth import CurrentUser
from app.services.users import UserService

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/me", response_model=UserProfile)
async def get_me(
    user: CurrentUser = Depends(current_user), users: UserService = Depends(get_user_service)
) -> UserProfile:
    return UserProfile.model_validate(await users.get_profile(user.id))


@router.patch("/me", response_model=UserProfile)
async def update_me(
    body: ProfileUpdate,
    user: CurrentUser = Depends(current_user),
    users: UserService = Depends(get_user_service),
) -> UserProfile:
    profile = await users.update_profile(
        user.id,
        body.full_name,
        str(body.avatar_url) if body.avatar_url else None,
        body.clear_avatar,
    )
    return UserProfile.model_validate(profile)


@router.get("/me/bids", response_model=BidHistoryPage)
async def my_bids(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, pattern=r"^\d{1,16}:\d{1,19}$"),
    user: CurrentUser = Depends(current_user),
    users: UserService = Depends(get_user_service),
) -> BidHistoryPage:
    page = await users.list_bids(user.id, limit, cursor)
    return BidHistoryPage(
        items=[BidHistoryItem.model_validate(i) for i in page.items], next_cursor=page.next_cursor
    )
