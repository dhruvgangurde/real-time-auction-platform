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
    # real-time layer (FR-28 to FR-32, CI-3 to CI-7, NFR-27)
    allowed_origins: str = "http://localhost:5173"  # comma separated; WebSocket Origin allowlist
    alias_secret: str = ""  # >= 32 bytes required outside APP_ENV=local (NFR-27 pseudonyms)
    ws_auth_timeout_s: float = 5.0
    ws_max_message_bytes: int = 4096
    ws_max_subscriptions: int = 20
    ws_max_connections_per_user: int = 5
    ws_send_queue_size: int = 256
    ws_idle_timeout_s: float = 60.0
    ws_watch_interval_s: float = 1.0
    ws_max_strikes: int = 5
    ws_presence_interval_s: float = 5.0
    rate_limit_ws_place_bid: str = "10/1"  # FR-44 default: 10 bid submissions per second
    rate_limit_ws_set_proxy: str = "5/1"
    rate_limit_ws_subscribe: str = "20/10"

    @property
    def origin_allowlist(self) -> frozenset[str]:
        return frozenset(o.strip() for o in self.allowed_origins.split(",") if o.strip())


settings = Settings()
