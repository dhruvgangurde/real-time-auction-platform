"""Settings come only from environment variables (NFR-26, OR-8)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    # restricted role used by api, worker and beat (DC-4)
    database_url: str = "postgresql+asyncpg://auction_app:auction_app@postgres:5432/auction"
    # owner role used only by Alembic
    migration_database_url: str = "postgresql+asyncpg://auction:auction@postgres:5432/auction"
    app_db_user: str = "auction_app"
    app_db_password: str = "auction_app"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"


settings = Settings()
