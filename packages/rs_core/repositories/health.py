"""The coverage + failure summary behind the pipeline-health dashboard (R-4)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import Field, SyncOutbox


async def pipeline_health(session: AsyncSession) -> dict[str, int]:
    """A coverage + failure summary for the pipeline-health dashboard (R-4): total fields, fields
    still awaiting backfill, and dead-lettered gateway pushes awaiting retry."""
    fields = (await session.execute(select(func.count()).select_from(Field))).scalar_one()
    awaiting_backfill = (
        await session.execute(
            select(func.count()).select_from(Field).where(Field.needs_backfill.is_(True))
        )
    ).scalar_one()
    dead_letters = (
        await session.execute(
            select(func.count()).select_from(SyncOutbox).where(SyncOutbox.status == "dead_letter")
        )
    ).scalar_one()
    return {
        "fields": fields,
        "awaiting_backfill": awaiting_backfill,
        "dead_letters": dead_letters,
    }
