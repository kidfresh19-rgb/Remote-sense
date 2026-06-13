"""Analyst field notes: plain additive inserts with no identity constraint (many notes may pin
to the same field/pass), listed newest first, deleted scoped to their field."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import Annotation


async def insert_annotation(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    geometry_version: int,
    pass_date: date | None,
    body: str,
    author: str | None,
) -> Annotation:
    """Append a field note. Unlike interpretations these carry no identity constraint - an analyst
    may pin many notes to the same field/pass - so this is a plain additive insert. The flush pulls
    back the server-assigned `created_at` so the caller can return the full row."""
    row = Annotation(
        field_id=field_id,
        geometry_version=geometry_version,
        pass_date=pass_date,
        body=body,
        author=author,
    )
    session.add(row)
    await session.flush()
    return row


async def list_annotations(session: AsyncSession, *, field_id: uuid.UUID) -> list[Annotation]:
    """Every note pinned to a field, newest first (id as a stable tiebreaker for notes that share
    a created_at)."""
    return list(
        (
            await session.execute(
                select(Annotation)
                .where(Annotation.field_id == field_id)
                .order_by(Annotation.created_at.desc(), Annotation.id)
            )
        )
        .scalars()
        .all()
    )


async def delete_annotation(
    session: AsyncSession, *, annotation_id: uuid.UUID, field_id: uuid.UUID
) -> bool:
    """Delete one note, scoped to its field so a stale id from another field can never match.
    Returns whether a row was removed (False -> 404 at the API)."""
    result = await session.execute(
        delete(Annotation).where(Annotation.id == annotation_id, Annotation.field_id == field_id)
    )
    # session.execute is typed as the base Result; a DELETE always yields a CursorResult.
    return bool(cast("CursorResult[Any]", result).rowcount)
