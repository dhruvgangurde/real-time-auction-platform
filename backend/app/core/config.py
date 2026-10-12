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
    ws_max_sessions_per_user: int = 5  # one socket per session (CI-3), at most this many sessions
    ws_send_queue_size: int = 256
    ws_watch_interval_s: float = 1.0
    ws_max_messages_per_s: int = 30  # per connection, counted in memory before any Redis work
    ws_max_strikes: int = 5
    ws_strike_decay_s: float = 60.0  # one strike is forgiven per interval
    ws_presence_interval_s: float = 5.0
    # per user and message type, shared across instances; bid submission is the strictest (CI-3)
    rate_limit_ws_place_bid: str = "10/1"  # FR-44 default: 10 bid submissions per second
    rate_limit_ws_set_proxy: str = "5/1"
    rate_limit_ws_subscribe: str = "20/1"
    rate_limit_ws_unsubscribe: str = "20/1"
    rate_limit_ws_authenticate: str = "20/1"
    rate_limit_ws_ping: str = "20/1"
    # auction lifecycle (Phase 7: FR-11 to FR-18, NFR-22, NFR-24)
    rate_limit_auction_create: str = "10/3600"  # per user (NFR-22)
    antisnipe_threshold_min_s: int = 10
    antisnipe_threshold_max_s: int = 600
    antisnipe_extension_min_s: int = 10
    antisnipe_extension_max_s: int = 600
    scheduler_interval_s: float = 5.0  # beat period of the lifecycle scheduler (FR-16)
    media_root: str = "/media"  # named volume; served read-only under /media (D10)
    image_max_bytes: int = 5 * 1024 * 1024  # FR-13
    image_max_pixels: int = 40_000_000  # decompression-bomb guard (NFR-24)
    images_per_auction: int = 8
    # closing and settlement (Phase 8: FR-57 to FR-62, NFR-12)
    close_grace_s: int = 300  # past end_time + this without a terminal status: UNRESOLVED_CLOSE
    # payments (Phase 9: FR-65 to FR-69, SI-3); Razorpay test mode only (AS-5)
    razorpay_key_id: str = ""  # rzp_test_...; public, handed to the checkout
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""  # set per webhook in the Razorpay dashboard
    razorpay_api_base: str = "https://api.razorpay.com/v1"
    razorpay_timeout_s: float = 10.0
    payment_min_amount: int = 100  # paise: the gateway's minimum charge
    payment_reconcile_interval_s: float = 120.0
    payment_reconcile_after_s: int = 120  # leave fresh orders to the callback and webhook
    payment_reconcile_lookback_s: int = 7 * 86_400  # counted from the last payment activity
    payment_reconcile_batch: int = 50
    payment_webhook_max_bytes: int = 65_536
    rate_limit_payment_start: str = "10/60"  # per user
    rate_limit_payment_confirm: str = "20/60"  # per user
    rate_limit_payment_webhook: str = "600/60"  # per source address
    # notifications (FR-70 to FR-74, SI-4); EMAIL_BACKEND=smtp sends through SMTP_* (TBD-1)
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    smtp_timeout_s: float = 10.0
    email_from: str = "Auction <no-reply@auction.local>"
    email_group_window_s: int = 60  # FR-72: same type, recipient and auction combined
    email_max_attempts: int = 5
    email_lease_s: int = 300  # a claimed group is retried after this if the sender died
    email_dispatch_interval_s: float = 15.0
    email_batch: int = 200
    ending_soon_lead_s: int = 300  # FR-73 default lead interval
    ending_soon_interval_s: float = 30.0

    @property
    def origin_allowlist(self) -> frozenset[str]:
        return frozenset(o.strip() for o in self.allowed_origins.split(",") if o.strip())


settings = Settings()
