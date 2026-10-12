"""Discovery endpoints: listing and search, detail, bid history, categories, watchlist, seller
dashboard (FR-19 to FR-27, FR-30, FR-75). Routing only; the rules live in CatalogService."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime

from app.api.deps import current_user, get_catalog_service, optional_user, require_roles
from app.schemas.auctions import AdminReason, CategoryCreate, CategoryUpdate, ListedStatus
from app.services.auth import CurrentUser
from app.services.bidding import MAX_AMOUNT
from app.services.catalog import CatalogService

auctions = APIRouter(prefix="/api/v1/auctions", tags=["auctions"])
categories = APIRouter(prefix="/api/v1/categories", tags=["categories"])
watchlist = APIRouter(prefix="/api/v1/watchlist", tags=["watchlist"])
seller = APIRouter(prefix="/api/v1/seller", tags=["seller"])


@auctions.get("")
async def list_auctions(
    q: str | None = Query(default=None, max_length=200),  # FR-22
    category: int | None = Query(default=None, ge=1),
    status: list[ListedStatus] | None = Query(default=None),
    min_price: int | None = Query(default=None, ge=0, le=MAX_AMOUNT),
    max_price: int | None = Query(default=None, ge=0, le=MAX_AMOUNT),
    closing_before: AwareDatetime | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=20, ge=1, le=50),
    catalog: CatalogService = Depends(get_catalog_service),
) -> dict[str, Any]:
    return await catalog.list_auctions(
        query=q,
        category_id=category,
        statuses=list(status) if status else None,
        min_price=min_price,
        max_price=max_price,
        closing_before=closing_before,
        page=page,
        page_size=page_size,
    )


@auctions.get("/{auction_id}")
async def auction_detail(
    auction_id: UUID,
    viewer: CurrentUser | None = Depends(optional_user),
    catalog: CatalogService = Depends(get_catalog_service),
) -> dict[str, Any]:
    return await catalog.auction_detail(viewer, auction_id)


@auctions.get("/{auction_id}/bids")
async def bid_history(
    auction_id: UUID,
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    viewer: CurrentUser | None = Depends(optional_user),
    catalog: CatalogService = Depends(get_catalog_service),
) -> dict[str, Any]:
    return await catalog.bid_history(viewer, auction_id, before, limit)


@categories.get("")
async def category_tree(
    catalog: CatalogService = Depends(get_catalog_service),
) -> list[dict[str, Any]]:
    return await catalog.category_tree()


@categories.post("", status_code=201)
async def create_category(
    body: CategoryCreate,
    admin: CurrentUser = Depends(require_roles("admin")),
    catalog: CatalogService = Depends(get_catalog_service),
) -> dict[str, Any]:
    return await catalog.create_category(admin, body.name, body.slug, body.parent_id, body.reason)


@categories.patch("/{category_id}")
async def update_category(
    category_id: int,
    body: CategoryUpdate,
    admin: CurrentUser = Depends(require_roles("admin")),
    catalog: CatalogService = Depends(get_catalog_service),
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    return await catalog.update_category(admin, category_id, changes, body.reason)


@categories.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: int,
    body: AdminReason,
    admin: CurrentUser = Depends(require_roles("admin")),
    catalog: CatalogService = Depends(get_catalog_service),
) -> Response:
    await catalog.delete_category(admin, category_id, body.reason)
    return Response(status_code=204)


@watchlist.get("")
async def my_watchlist(
    user: CurrentUser = Depends(current_user),
    catalog: CatalogService = Depends(get_catalog_service),
) -> dict[str, Any]:
    return await catalog.watchlist(user)


@watchlist.put("/{auction_id}", status_code=204)
async def watch(
    auction_id: UUID,
    user: CurrentUser = Depends(current_user),
    catalog: CatalogService = Depends(get_catalog_service),
) -> Response:
    await catalog.watch(user, auction_id)
    return Response(status_code=204)


@watchlist.delete("/{auction_id}", status_code=204)
async def unwatch(
    auction_id: UUID,
    user: CurrentUser = Depends(current_user),
    catalog: CatalogService = Depends(get_catalog_service),
) -> Response:
    await catalog.unwatch(user, auction_id)
    return Response(status_code=204)


@seller.get("/auctions")
async def seller_auctions(
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=20, ge=1, le=50),
    user: CurrentUser = Depends(require_roles("seller")),
    catalog: CatalogService = Depends(get_catalog_service),
) -> dict[str, Any]:
    return await catalog.seller_auctions(user, page, page_size)
