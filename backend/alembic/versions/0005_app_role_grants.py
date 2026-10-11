"""restricted application role and explicit grants (DC-4, FR-79, NFR-26)

The app role gets SELECT/INSERT/UPDATE/DELETE on ordinary tables, only SELECT/INSERT on the
append-only tables, SELECT only on alembic_version, USAGE on sequences, and no TRUNCATE.
Every future table migration must grant explicitly; there are no default privileges.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READ_WRITE_TABLES = (
    "users",
    "user_roles",
    "sessions",
    "email_verification_tokens",
    "password_reset_tokens",
    "categories",
    "auctions",
    "auction_images",
    "proxy_bids",
    "watchlist",
    "settlements",
    "payments",
    "notifications",
    "notification_preferences",
)
APPEND_ONLY_TABLES = ("bids", "audit_log")


def _set_role_params(with_password: bool) -> None:
    # values travel as bound parameters, then format(%I/%L) quotes them inside the DO block
    op.execute(
        sa.text(
            "SELECT set_config('auction.app_user', :u, false), "
            "set_config('auction.app_password', :p, false)"
        ).bindparams(u=settings.app_db_user, p=settings.app_db_password if with_password else "")
    )


def _array(names: tuple[str, ...]) -> str:
    # fixed identifiers from this file, never user input
    return "ARRAY[" + ", ".join(f"'{n}'" for n in names) + "]"


def upgrade() -> None:
    _set_role_params(with_password=True)
    op.execute(
        f"""
        DO $$
        DECLARE
            r text := current_setting('auction.app_user');
            p text := current_setting('auction.app_password');
            t text;
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', r, p);
            END IF;
            -- keep the password in sync with the environment on every upgrade
            EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', r, p);

            REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
            REVOKE CREATE ON SCHEMA public FROM PUBLIC;
            EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', r);

            FOREACH t IN ARRAY {_array(READ_WRITE_TABLES)} LOOP
                EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO %I', t, r);
            END LOOP;
            -- append-only: no UPDATE, DELETE or TRUNCATE (DC-4, FR-79, BR-13)
            FOREACH t IN ARRAY {_array(APPEND_ONLY_TABLES)} LOOP
                EXECUTE format('GRANT SELECT, INSERT ON %I TO %I', t, r);
            END LOOP;
            -- the app role must never modify migration state
            EXECUTE format('GRANT SELECT ON alembic_version TO %I', r);
            EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO %I', r);
        END;
        $$
        """
    )
    op.execute("SELECT set_config('auction.app_password', '', false)")


def downgrade() -> None:
    _set_role_params(with_password=False)
    op.execute(
        """
        DO $$
        DECLARE
            r text := current_setting('auction.app_user');
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', r);
                EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', r);
                EXECUTE format('REVOKE USAGE ON SCHEMA public FROM %I', r);
            END IF;
        END;
        $$
        """
    )
