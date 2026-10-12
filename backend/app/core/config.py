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
    redis_timeout_s: float = 0.5
    # bidding (Phase 3)
    self_outbid_policy_enabled: bool = True  # FR-38: platform-wide, copied into each live state
    bid_request_ttl_s: int = 600  # how long a request_id result is kept for idempotent retries
    celery_broker_url: str = "redis://redis:6379/1"
    # identity (FR-1 to FR-9, NFR-17, NFR-22)
    jwt_secret: str = ""  # >= 32 bytes required outside APP_ENV=local
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 2_592_000  # 30 days, sliding on rotation
    cookie_secure: bool = True  # local http needs false
    app_base_url: str = "http://localhost:5173"  # frontend origin used in emailed links
    email_backend: str = "console"  # dev-only until TBD-1 is decided
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65_536
    argon2_parallelism: int = 4
    verification_token_ttl_s: int = 86_400
    reset_token_ttl_s: int = 3_600
    # "count/seconds" fixed windows (NFR-22)
    rate_limit_login_ip: str = "10/60"
    rate_limit_login_email: str = "5/300"
    rate_limit_register_ip: str = "5/3600"
    rate_limit_resend_ip: str = "5/3600"
    rate_limit_resend_email: str = "3/3600"
    rate_limit_forgot_ip: str = "5/3600"
    rate_limit_forgot_email: str = "3/3600"
    # unauthenticated token endpoints: reset hashes a password (64 MiB argon2), so cap it
    rate_limit_reset_ip: str = "10/3600"
    rate_limit_verify_ip: str = "20/3600"


settings = Settings()
