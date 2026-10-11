from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    app = FastAPI(title="Real-Time Auction Platform", docs_url="/api/v1/docs")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    return app


app = create_app()
