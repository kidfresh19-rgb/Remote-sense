"""The per-field collection cursor: backfill completion plus the forward-only forward-fill
watermark (D5), and the processed-scene dedup set a resumed or retried run plans against."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import Analysis, FieldCollectionState


def advance_cursor(current: date | None, candidate: date | None) -> date | None:
    """The forward-fill date watermark only ever moves forward (D5). Return the later of the
    stored cursor and a candidate pass date: a missing value yields the other, and an earlier
    candidate is ignored so a late-arriving older scene can never rewind the cursor. The single
    home for this rule, used by the cursor-persistence helpers below and by the worker when it
    plans the next search start."""
    if candidate is None:
        return current
    if current is None:
        return candidate
    return max(current, candidate)


async def ensure_collection_state(
    session: AsyncSession, *, field_id: uuid.UUID, geometry_version: int
) -> FieldCollectionState:
    """Get-or-create the collection cursor for a field at a geometry version. Concurrency-safe: a
    racing creator hits ON CONFLICT DO NOTHING, then both readers see the one row."""
    await session.execute(
        pg_insert(FieldCollectionState)
        .values(field_id=field_id, geometry_version=geometry_version, backfill_complete=False)
        .on_conflict_do_nothing(constraint="uq_collection_state_field_geom")
    )
    return (
        await session.execute(
            select(FieldCollectionState).where(
                FieldCollectionState.field_id == field_id,
                FieldCollectionState.geometry_version == geometry_version,
            )
        )
    ).scalar_one()


async def get_collection_state(
    session: AsyncSession, *, field_id: uuid.UUID, geometry_version: int
) -> FieldCollectionState | None:
    """The collection cursor for a field at a geometry version, or None if collection has not
    started for it yet."""
    return (
        await session.execute(
            select(FieldCollectionState).where(
                FieldCollectionState.field_id == field_id,
                FieldCollectionState.geometry_version == geometry_version,
            )
        )
    ).scalar_one_or_none()


async def record_forward_fill_poll(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    geometry_version: int,
    polled_at: datetime,
    cursor_date: date | None = None,
    last_scene_id: str | None = None,
) -> FieldCollectionState:
    """Record a forward-fill poll: stamp `last_poll_at`, and advance the date watermark only
    forward (never backward, via `advance_cursor`). Creates the cursor row if absent."""
    state = await ensure_collection_state(
        session, field_id=field_id, geometry_version=geometry_version
    )
    state.last_poll_at = polled_at
    advanced = advance_cursor(state.cursor_date, cursor_date)
    if advanced != state.cursor_date:
        state.cursor_date = advanced
        if last_scene_id is not None:
            state.last_scene_id = last_scene_id
    await session.flush()
    return state


async def mark_backfill_complete(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    geometry_version: int,
    completed_at: datetime,
    cursor_date: date | None = None,
) -> FieldCollectionState:
    """Flag the historical backfill done for this field+geometry version and stamp the completion
    time; optionally seed the forward-fill watermark with the latest backfilled pass date."""
    state = await ensure_collection_state(
        session, field_id=field_id, geometry_version=geometry_version
    )
    state.backfill_complete = True
    state.backfill_completed_at = completed_at
    # The backfill is itself the most recent archive poll, so the first forward-fill is due one
    # cadence later rather than immediately.
    state.last_poll_at = completed_at
    advanced = advance_cursor(state.cursor_date, cursor_date)
    if advanced != state.cursor_date:
        state.cursor_date = advanced
    await session.flush()
    return state


async def processed_scene_ids(
    session: AsyncSession, *, field_id: uuid.UUID, geometry_version: int
) -> frozenset[str]:
    """The scene ids already analysed for this field at this geometry version - the dedup set the
    pipeline hands to plan_scenes so a resumed or retried run skips finished scenes (R-1) and
    fills only the gaps. Derived from the analysis rows themselves, so it can never drift from
    what was actually stored."""
    rows = await session.execute(
        select(Analysis.scene_id)
        .where(
            Analysis.field_id == field_id,
            Analysis.geometry_version == geometry_version,
        )
        .distinct()
    )
    return frozenset(rows.scalars().all())
