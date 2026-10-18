"""Reads for the restore drill (OR-6): every table's rows, the latest verified stress run and the
image keys, on whichever database the session points at (live or restored)."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, table, text

from app.models.auction import AuctionImage
from app.models.demo import DemoRun
from app.repositories.base import BaseRepository


class OpsRepository(BaseRepository):
    async def table_counts(self) -> dict[str, int]:
        """Rows in every table of the public schema, by name."""
        names = await self.session.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        )
        counts = {}
        for name in names.all():
            # the name comes from the catalogue, quoted by SQLAlchemy as an identifier
            counts[name] = int(
                await self.session.scalar(select(func.count()).select_from(table(name)))
            )
        return counts

    async def latest_verified_run(
        self,
    ) -> tuple[UUID, list[list[Any]], list[dict[str, Any]]] | None:
        """(lot, acknowledgements, recorded checks) of the newest verified stress run."""
        row = (
            await self.session.execute(
                select(DemoRun.auction_id, DemoRun.acks, DemoRun.checks)
                .where(
                    DemoRun.kind == "stress",
                    DemoRun.state == "verified",
                    DemoRun.auction_id.is_not(None),
                )
                .order_by(DemoRun.finished_at.desc().nulls_last())
                .limit(1)
            )
        ).first()
        if row is None or row.auction_id is None:
            return None
        return row.auction_id, row.acks or [], row.checks or []

    async def image_keys(self) -> list[str]:
        return list(
            (
                await self.session.scalars(
                    select(AuctionImage.object_key).order_by(AuctionImage.id)
                )
            ).all()
        )
