from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from app.api import discovery
from app.api.admin import router as admin_router
from app.api.auctions import router as auctions_router
from app.api.auth import router as auth_router
from app.api.demo import router as demo_router
from app.api.health import router as health_router
from app.api.lifecycle import router as lifecycle_router
from app.api.notifications import router as notifications_router
from app.api.payments import router as payments_router
from app.api.users import router as users_router
from app.api.ws import router as ws_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware
from app.core.redis import create_redis
from app.core.security import check_startup_config
from app.core.storage import MEDIA_URL_PREFIX
from app.services.snapshot import SnapshotCache


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """One fan-out hub per instance (CI-7). Tests set app.state.redis_url before startup."""
    from app.services.fanout import Hub

    url = getattr(app.state, "redis_url", settings.redis_url)
    hub = Hub(url, create_redis(url))
    await hub.start()
    app.state.hub = hub
    yield
    await hub.stop()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    check_startup_config()
    app = FastAPI(title="Real-Time Auction Platform", docs_url="/api/v1/docs", lifespan=lifespan)
    app.state.snapshot_cache = SnapshotCache()  # anonymous /live, per instance (brief 12.2)
    app.state.demo_cache = SnapshotCache()  # GET /demo and run status, per instance (B2)
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(lifecycle_router)
    app.include_router(discovery.auctions)
    app.include_router(auctions_router)
    app.include_router(discovery.categories)
    app.include_router(discovery.watchlist)
    app.include_router(discovery.seller)
    app.include_router(admin_router)
    app.include_router(payments_router)
    app.include_router(notifications_router)
    app.include_router(ws_router)
    app.include_router(demo_router)
    # re-encoded auction images, read-only (GET/HEAD only), never user-chosen names (NFR-24)
    app.mount(
        MEDIA_URL_PREFIX,
        StaticFiles(directory=settings.media_root, check_dir=False),
        name="media",
    )
    return app


app = create_app()
