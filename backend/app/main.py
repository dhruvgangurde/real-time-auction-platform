from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auctions import router as auctions_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.users import router as users_router
from app.api.ws import router as ws_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware
from app.core.redis import create_redis
from app.core.security import check_startup_config


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
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(auctions_router)
    app.include_router(ws_router)
    return app


app = create_app()
