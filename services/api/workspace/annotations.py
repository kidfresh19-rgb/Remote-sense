"""Annotation endpoints: analyst field notes - list, create (geometry version resolved
server-side from the field, author from the verified token subject), and delete."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, StringConstraints
from rs_core import delete_annotation, insert_annotation, list_annotations
from rs_core.models import Field
from sqlalchemy import select

from services.api.workspace.deps import AnnotatePrincipal, SessionDep, ViewPrincipal

router = APIRouter(tags=["workspace"])


class AnnotationOut(BaseModel):
    id: str
    field_id: str
    geometry_version: int
    pass_date: date | None
    body: str
    author: str | None
    created_at: datetime


class AnnotationCreate(BaseModel):
    """An analyst's new note. The geometry version is resolved server-side from the field (never
    trusted from the client, invariant 5); the author is the verified token subject."""

    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    pass_date: date | None = None


def _annotation_out(row: Any) -> AnnotationOut:
    return AnnotationOut(
        id=str(row.id),
        field_id=str(row.field_id),
        geometry_version=row.geometry_version,
        pass_date=row.pass_date,
        body=row.body,
        author=row.author,
        created_at=row.created_at,
    )


@router.get("/fields/{field_id}/annotations")
async def list_annotations_endpoint(
    field_id: uuid.UUID, principal: ViewPrincipal, session: SessionDep
) -> list[AnnotationOut]:
    rows = await list_annotations(session, field_id=field_id)
    return [_annotation_out(row) for row in rows]


@router.post("/fields/{field_id}/annotations", status_code=status.HTTP_201_CREATED)
async def create_annotation_endpoint(
    field_id: uuid.UUID,
    payload: AnnotationCreate,
    principal: AnnotatePrincipal,
    session: SessionDep,
) -> AnnotationOut:
    # Pin the note to the field's current geometry version, read authoritatively here so a client
    # cannot misattribute it (invariant 5). A missing field is a 404, not a dangling note.
    geometry_version = (
        await session.execute(select(Field.geometry_version).where(Field.id == field_id))
    ).scalar_one_or_none()
    if geometry_version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")
    row = await insert_annotation(
        session,
        field_id=field_id,
        geometry_version=geometry_version,
        pass_date=payload.pass_date,
        body=payload.body,
        author=principal.subject,
    )
    return _annotation_out(row)


@router.delete(
    "/fields/{field_id}/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_annotation_endpoint(
    field_id: uuid.UUID,
    annotation_id: uuid.UUID,
    principal: AnnotatePrincipal,
    session: SessionDep,
) -> None:
    removed = await delete_annotation(session, annotation_id=annotation_id, field_id=field_id)
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "annotation not found")
