"""Analyst workspace BFF (L6): RBAC-gated (view) read endpoints backing the React workspace -
farms, fields (with geometry for the map), per-field/index time series, scene passes, and
interpretations. Read-only. Geometry IS shown to the internal analyst here, which is distinct from
the geometry-free *outbound* push (invariant 6 governs the gateway direction only)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from geoalchemy2.shape import to_shape
from pydantic import BaseModel, StringConstraints
from rs_core import (
    Permission,
    Principal,
    delete_annotation,
    get_farm_analytics_summary,
    get_latest_outbox_for_farm,
    get_settings,
    insert_annotation,
    list_annotations,
    list_review_queue,
    review_interpretation,
)
from rs_core.db import get_session
from rs_core.models import Analysis, Farm, Field, Interpretation
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.auth import require
from services.worker.publish import gateway_config_error, gateway_is_dry_run

router = APIRouter(tags=["workspace"])

ViewPrincipal = Annotated[Principal, Depends(require(Permission.VIEW))]
AnnotatePrincipal = Annotated[Principal, Depends(require(Permission.ANNOTATE))]
PublishPrincipal = Annotated[Principal, Depends(require(Permission.PUBLISH))]
RunAnalysisPrincipal = Annotated[Principal, Depends(require(Permission.RUN_ANALYSIS))]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class FarmOut(BaseModel):
    canonical_farm_id: str
    name: str | None
    region: str | None
    overall_health: str | None = None
    overall_health_score: float | None = None
    latest_pass_date: date | None = None
    total_fields: int | None = None
    total_area_hectares: float | None = None
    crops: list[str] | None = None


class FieldOut(BaseModel):
    field_id: str
    canonical_field_id: str | None
    name: str | None
    crop: str | None
    geometry_version: int
    geometry: dict[str, Any]


class TimeseriesPoint(BaseModel):
    pass_date: date
    mean: float | None
    min: float | None
    max: float | None
    std: float | None
    p10: float | None
    p90: float | None
    clear_fraction: float
    confidence: str | None


class SceneOut(BaseModel):
    scene_id: str
    pass_date: date


class InterpretationOut(BaseModel):
    id: str
    pass_date: date
    status: str
    confidence: str
    narrative: str
    published: bool
    needs_review: bool
    reviewed_by: str | None
    reviewed_at: datetime | None
    gdd_accumulation: float | None = None
    total_precipitation: float | None = None
    recent_activities: list[dict[str, Any]] | None = None


class InterpretationReview(BaseModel):
    """An agronomist's review action on a drafted read (risk #6): publish or withhold it, and
    optionally correct the narrative. `status`/`confidence` are grounded in the numbers and are not
    accepted here. `narrative=None` keeps the existing text; an empty string is rejected."""

    publish: bool
    narrative: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
        | None
    ) = None


class ReviewQueueItem(BaseModel):
    """One row of the cross-field review surface: a drafted/published read plus the canonical ids
    needed to open its field. Geometry-free (this is a triage list, not a map view)."""

    id: str
    field_id: str
    canonical_field_id: str | None
    canonical_farm_id: str
    field_name: str | None
    crop: str | None
    pass_date: date
    status: str
    confidence: str
    narrative: str
    published: bool
    needs_review: bool
    reviewed_by: str | None
    reviewed_at: datetime | None


class AuditRecordOut(BaseModel):
    """The provenance tuple (CLAUDE.md invariant 5) of one stored analysis, surfaced so an analyst
    can see exactly which scene, formula, geometry version, and processing mode produced a value.
    Read-only and geometry-free; this is internal reproducibility metadata, not the gateway push."""

    pass_date: date
    index_name: str
    scene_id: str
    provider: str
    provider_scene_id: str
    processing_mode: str
    formula_version: str
    geometry_version: int
    resolution_m: float
    clear_fraction: float
    confidence: str | None
    cog_uri: str | None
    created_at: datetime


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


class AOIAnalysisRequest(BaseModel):
    geometry: dict[str, Any]
    index: str


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


async def list_farms(session: AsyncSession) -> list[FarmOut]:
    farms = (await session.execute(select(Farm).order_by(Farm.canonical_farm_id))).scalars().all()
    out = []
    for f in farms:
        summary = await get_farm_analytics_summary(session, f.canonical_farm_id)
        if summary:
            out.append(
                FarmOut(
                    canonical_farm_id=f.canonical_farm_id,
                    name=f.name,
                    region=f.region,
                    overall_health=summary["overall_health"],
                    overall_health_score=summary["overall_health_score"],
                    latest_pass_date=summary["latest_pass_date"],
                    total_fields=summary["total_fields"],
                    total_area_hectares=summary["total_area_hectares"],
                    crops=summary["crops"],
                )
            )
        else:
            out.append(FarmOut(canonical_farm_id=f.canonical_farm_id, name=f.name, region=f.region))
    return out


async def list_fields(session: AsyncSession, canonical_farm_id: str) -> list[FieldOut]:
    fields = (
        (
            await session.execute(
                select(Field)
                .join(Farm, Field.farm_id == Farm.id)
                .where(Farm.canonical_farm_id == canonical_farm_id)
                .order_by(Field.canonical_field_id)
            )
        )
        .scalars()
        .all()
    )
    return [
        FieldOut(
            field_id=str(f.id),
            canonical_field_id=f.canonical_field_id,
            name=f.name,
            crop=f.crop,
            geometry_version=f.geometry_version,
            geometry=mapping(to_shape(f.boundary)),
        )
        for f in fields
    ]


async def field_timeseries(
    session: AsyncSession, field_id: uuid.UUID, index: str
) -> list[TimeseriesPoint]:
    rows = (
        (
            await session.execute(
                select(Analysis)
                .where(Analysis.field_id == field_id, Analysis.index_name == index)
                .order_by(Analysis.pass_date)
            )
        )
        .scalars()
        .all()
    )
    return [
        TimeseriesPoint(
            pass_date=a.pass_date,
            mean=a.mean,
            min=a.min_val,
            max=a.max_val,
            std=a.std,
            p10=a.p10,
            p90=a.p90,
            clear_fraction=a.clear_fraction,
            confidence=a.confidence,
        )
        for a in rows
    ]


async def field_scenes(session: AsyncSession, field_id: uuid.UUID) -> list[SceneOut]:
    from sqlalchemy import func

    # Partition by pass_date to select the scene with the highest clear_fraction on that date
    subq = (
        select(
            Analysis.scene_id,
            Analysis.pass_date,
            func.row_number().over(
                partition_by=Analysis.pass_date,
                order_by=Analysis.clear_fraction.desc()
            ).label("rn")
        )
        .where(Analysis.field_id == field_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(subq.c.scene_id, subq.c.pass_date)
            .where(subq.c.rn == 1)
            .order_by(subq.c.pass_date)
        )
    ).all()
    return [SceneOut(scene_id=scene_id, pass_date=pass_date) for scene_id, pass_date in rows]


async def field_interpretations(
    session: AsyncSession, field_id: uuid.UUID
) -> list[InterpretationOut]:
    rows = (
        (
            await session.execute(
                select(Interpretation)
                .where(Interpretation.field_id == field_id)
                .order_by(Interpretation.pass_date)
            )
        )
        .scalars()
        .all()
    )
    return [_interpretation_out(i) for i in rows]


def _interpretation_out(i: Interpretation) -> InterpretationOut:
    return InterpretationOut(
        id=str(i.id),
        pass_date=i.pass_date,
        status=i.status,
        confidence=i.confidence,
        narrative=i.narrative,
        published=i.published,
        needs_review=i.needs_review,
        reviewed_by=i.reviewed_by,
        reviewed_at=i.reviewed_at,
        gdd_accumulation=i.gdd_accumulation,
        total_precipitation=i.total_precipitation,
        recent_activities=i.recent_activities,
    )


async def review_queue(
    session: AsyncSession, *, needs_review: bool | None
) -> list[ReviewQueueItem]:
    """The cross-field agronomist review list, shaped from the geometry-free repo query."""
    rows = await list_review_queue(session, needs_review=needs_review)
    return [
        ReviewQueueItem(
            id=str(r.id),
            field_id=str(r.field_id),
            canonical_field_id=r.canonical_field_id,
            canonical_farm_id=r.canonical_farm_id,
            field_name=r.field_name,
            crop=r.crop,
            pass_date=r.pass_date,
            status=r.status,
            confidence=r.confidence,
            narrative=r.narrative,
            published=r.published,
            needs_review=r.needs_review,
            reviewed_by=r.reviewed_by,
            reviewed_at=r.reviewed_at,
        )
        for r in rows
    ]


async def field_audit(session: AsyncSession, field_id: uuid.UUID) -> list[AuditRecordOut]:
    """The provenance log for a field: every stored analysis with the tuple that reproduces it.
    Ordered newest-processed first, then newest pass, with index and scene as stable tiebreakers
    (rows from one run share created_at, so the extra keys keep the order deterministic)."""
    rows = (
        (
            await session.execute(
                select(Analysis)
                .where(Analysis.field_id == field_id)
                .order_by(
                    Analysis.created_at.desc(),
                    Analysis.pass_date.desc(),
                    Analysis.index_name,
                    Analysis.scene_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        AuditRecordOut(
            pass_date=a.pass_date,
            index_name=a.index_name,
            scene_id=a.scene_id,
            provider=a.provider,
            provider_scene_id=a.provider_scene_id,
            processing_mode=a.processing_mode,
            formula_version=a.formula_version,
            geometry_version=a.geometry_version,
            resolution_m=a.resolution_m,
            clear_fraction=a.clear_fraction,
            confidence=a.confidence,
            cog_uri=a.cog_uri,
            created_at=a.created_at,
        )
        for a in rows
    ]


@router.get("/farms")
async def list_farms_endpoint(principal: ViewPrincipal, session: SessionDep) -> list[FarmOut]:
    return await list_farms(session)


class PublishEnqueuedOut(BaseModel):
    status: str
    canonical_farm_id: str
    by: str
    gateway: str  # active adapter: recording | http | agritrack
    dry_run: bool  # True when the active gateway records without sending (recording sink)


@router.post("/farms/{canonical_farm_id}/publish", status_code=status.HTTP_202_ACCEPTED)
async def publish_farm_endpoint(
    canonical_farm_id: str,
    principal: PublishPrincipal,
    session: SessionDep,
) -> PublishEnqueuedOut:
    """Enqueue a gateway push for one farm so its results are sent now instead of waiting for the
    next scheduled sync. Reuses the existing ``publish_farm_task`` Celery path, which is idempotent
    (R-2). Requires ``publish``.

    A gateway configured for real delivery (``agritrack``/``http``) but missing its URL or key fails
    fast with 503 here, so the operator sees the misconfiguration immediately rather than enqueuing
    a task that cannot deliver and leaves the workspace polling forever."""
    from services.worker.tasks import publish_farm_task

    farm_exists = (
        await session.execute(select(Farm).where(Farm.canonical_farm_id == canonical_farm_id))
    ).scalar_one_or_none()
    if farm_exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "farm not found")
    settings = get_settings()
    config_error = gateway_config_error(settings)
    if config_error is not None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, config_error)
    publish_farm_task.delay(canonical_farm_id)
    return PublishEnqueuedOut(
        status="enqueued",
        canonical_farm_id=canonical_farm_id,
        by=principal.subject,
        gateway=settings.gateway_adapter.value,
        dry_run=gateway_is_dry_run(settings),
    )


class PublishStatusOut(BaseModel):
    canonical_farm_id: str
    status: str  # pending | published | dead_letter
    result_count: int
    pushed_at: datetime | None
    last_error: str | None
    gateway: str  # active adapter: recording | http | agritrack
    dry_run: bool  # True when the active gateway records without sending (recording sink)


@router.get("/farms/{canonical_farm_id}/publish/status")
async def publish_status_endpoint(
    canonical_farm_id: str,
    principal: PublishPrincipal,
    session: SessionDep,
) -> PublishStatusOut:
    """The latest gateway push outcome for a farm. The frontend polls this after enqueuing a push
    to confirm delivery (published), surface errors (dead_letter), or show in-progress (pending).
    Returns 404 if no push has ever been recorded for this farm."""
    row = await get_latest_outbox_for_farm(session, canonical_farm_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no push recorded for this farm")
    settings = get_settings()
    return PublishStatusOut(
        canonical_farm_id=canonical_farm_id,
        status=row.status,
        result_count=row.result_count,
        pushed_at=row.pushed_at,
        last_error=row.last_error,
        gateway=settings.gateway_adapter.value,
        dry_run=gateway_is_dry_run(settings),
    )


@router.get("/farms/{canonical_farm_id}/fields")
async def list_fields_endpoint(
    canonical_farm_id: str, principal: ViewPrincipal, session: SessionDep
) -> list[FieldOut]:
    return await list_fields(session, canonical_farm_id)


@router.get("/fields/{field_id}/timeseries")
async def field_timeseries_endpoint(
    field_id: uuid.UUID, index: str, principal: ViewPrincipal, session: SessionDep
) -> list[TimeseriesPoint]:
    return await field_timeseries(session, field_id, index)


@router.get("/fields/{field_id}/scenes")
async def field_scenes_endpoint(
    field_id: uuid.UUID, principal: ViewPrincipal, session: SessionDep
) -> list[SceneOut]:
    return await field_scenes(session, field_id)


@router.get("/fields/{field_id}/interpretations")
async def field_interpretations_endpoint(
    field_id: uuid.UUID, principal: ViewPrincipal, session: SessionDep
) -> list[InterpretationOut]:
    return await field_interpretations(session, field_id)


@router.patch("/fields/{field_id}/interpretations/{interpretation_id}")
async def review_interpretation_endpoint(
    field_id: uuid.UUID,
    interpretation_id: uuid.UUID,
    payload: InterpretationReview,
    principal: PublishPrincipal,
    session: SessionDep,
) -> InterpretationOut:
    """Review a drafted agronomic read: publish/withhold it and optionally correct its narrative.
    The only path that can publish one (risk #6, never auto-published). Gated on `publish`; the
    reviewer is the verified token subject, never client input. 404 if the read is not on this
    field. status/confidence stay immutable - they are grounded in the numbers, not the model."""
    row = await review_interpretation(
        session,
        interpretation_id=interpretation_id,
        field_id=field_id,
        reviewer=principal.subject,
        publish=payload.publish,
        narrative=payload.narrative,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interpretation not found")
    return _interpretation_out(row)


@router.get("/interpretations/review-queue")
async def review_queue_endpoint(
    principal: PublishPrincipal,
    session: SessionDep,
    needs_review: bool | None = None,
) -> list[ReviewQueueItem]:
    """The cross-field review backlog for an agronomist (gated on `publish`): every read with the
    canonical ids to open its field. `?needs_review=true` narrows to the unreviewed ones."""
    return await review_queue(session, needs_review=needs_review)


@router.get("/fields/{field_id}/audit")
async def field_audit_endpoint(
    field_id: uuid.UUID, principal: ViewPrincipal, session: SessionDep
) -> list[AuditRecordOut]:
    return await field_audit(session, field_id)


@router.post("/fields/{field_id}/collect", status_code=status.HTTP_202_ACCEPTED)
async def field_collect_endpoint(
    field_id: uuid.UUID,
    principal: RunAnalysisPrincipal,
    session: SessionDep,
) -> dict[str, str]:
    """Enqueue an on-demand backfill for one field so its history is collected now instead of
    waiting for the nightly scan. This only exposes the existing pipeline entrypoint:
    `backfill_field` is idempotent and gap-filling (it replans only outstanding passes), so repeated
    clicks are safe and converge the field to complete. A missing field is a 404, not a dangling
    task. Requires `run_analysis`."""
    from services.worker.tasks import backfill_field

    field_exists = (
        await session.execute(select(Field.id).where(Field.id == field_id))
    ).scalar_one_or_none()
    if field_exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")
    backfill_field.delay(str(field_id))
    return {"status": "enqueued", "field_id": str(field_id), "by": principal.subject}


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


@router.post("/analyse/aoi")
async def analyse_aoi_endpoint(
    payload: AOIAnalysisRequest,
    principal: RunAnalysisPrincipal,
) -> dict[str, Any]:
    """Ad-hoc preview analysis over a custom AOI: returns the most recent usable pass's index stats
    for the drawn geometry, computed on the worker (geo extra) through the production engine.
    Nothing is persisted - no field is created, so it cannot collide with gateway-owned identity
    (invariant 6 governs only the outbound push). Requires `run_analysis`.

    The work runs on the worker (it needs rasterio + CDSE), so we enqueue and wait for the result
    off the event loop; a bad index name is rejected up front as a 422 rather than a worker failure.
    """
    from celery.exceptions import TimeoutError as CeleryTimeoutError
    from rs_analysis import get_index

    from services.worker.tasks import analyse_aoi_task

    try:
        get_index(payload.index)
    except KeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    async_result = analyse_aoi_task.delay(payload.geometry, payload.index)
    try:
        return await run_in_threadpool(async_result.get, timeout=75)
    except CeleryTimeoutError as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "AOI analysis timed out; the imagery service is slow right now. Try again.",
        ) from exc
    except Exception as exc:  # the worker task raised (e.g. no imagery / fetch error)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"AOI analysis failed: {exc}") from exc
