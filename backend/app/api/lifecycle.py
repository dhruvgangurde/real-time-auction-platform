"""Seller endpoints: create, edit, publish, cancel, images (FR-11 to FR-18, BR-1, NFR-24).
Routing only; the rules live in AuctionService."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import current_user, get_auction_service, require_roles
from app.schemas.auctions import AuctionCreate, AuctionUpdate
from app.services.auctions import AuctionService
from app.services.auth import CurrentUser

router = APIRouter(prefix="/api/v1/auctions", tags=["auctions"])


@router.post("", status_code=201)
async def create_auction(
    body: AuctionCreate,
    user: CurrentUser = Depends(require_roles("seller")),  # BR-1
    auctions: AuctionService = Depends(get_auction_service),
) -> dict[str, Any]:
    return await auctions.create(user, body.model_dump())


@router.patch("/{auction_id}")
async def update_auction(
    auction_id: UUID,
    body: AuctionUpdate,
    user: CurrentUser = Depends(current_user),
    auctions: AuctionService = Depends(get_auction_service),
) -> dict[str, Any]:
    return await auctions.update(user, auction_id, body.model_dump(exclude_unset=True))


@router.post("/{auction_id}/publish")
async def publish_auction(
    auction_id: UUID,
    user: CurrentUser = Depends(current_user),
    auctions: AuctionService = Depends(get_auction_service),
) -> dict[str, Any]:
    return await auctions.publish(user, auction_id)


@router.post("/{auction_id}/cancel")
async def cancel_auction(
    auction_id: UUID,
    user: CurrentUser = Depends(current_user),
    auctions: AuctionService = Depends(get_auction_service),
) -> dict[str, Any]:
    return await auctions.cancel(user, auction_id)


@router.post("/{auction_id}/images", status_code=201)
async def add_image(
    auction_id: UUID,
    request: Request,
    user: CurrentUser = Depends(current_user),
    auctions: AuctionService = Depends(get_auction_service),
) -> dict[str, Any]:
    """The body is the image file itself; its type is decided by decoding it."""
    return await auctions.add_image(user, auction_id, request.stream())


@router.delete("/{auction_id}/images/{image_id}", status_code=204)
async def remove_image(
    auction_id: UUID,
    image_id: int,
    user: CurrentUser = Depends(current_user),
    auctions: AuctionService = Depends(get_auction_service),
) -> Response:
    await auctions.remove_image(user, auction_id, image_id)
    return Response(status_code=204)
