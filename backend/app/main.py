from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.users import router as users_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware
from app.core.security import check_startup_config


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    check_startup_config()
    app = FastAPI(title="Real-Time Auction Platform", docs_url="/api/v1/docs")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    return app


app = create_app()
