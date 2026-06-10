"""Interpretation worker (Phase 4b, L4b): turn a field's stored analysis rows for one pass into a
drafted, unpublished agronomic read. The orchestrator takes an injected `InterpretClient`, so it
is testable against a fake client + a real session with no Anthropic SDK; the Celery task wires
the real `AnthropicInterpretClient` (lazy-imported, since the SDK is an optional extra)."""

from __future__ import annotations

import uuid

from rs_analysis import AnalysisOutput
from rs_analysis.zonal import ZonalStats
from rs_core import Analysis, Interpretation, get_interpretation, insert_interpretation
from rs_interpret import PROMPT_VERSION, InterpretClient, ground, interpret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _output_from_analysis(row: Analysis) -> AnalysisOutput:
    """Rebuild the science part of a stored analysis row as an `AnalysisOutput` so grounding can
    classify it. `count` is not persisted and is not used downstream, so it is left at 0."""
    return AnalysisOutput(
        index_name=row.index_name,
        formula_version=row.formula_version,
        resolution_m=int(row.resolution_m),
        clear_fraction=row.clear_fraction,
        confidence=row.confidence or "low",
        stats=ZonalStats(
            count=0,
            mean=row.mean,
            min=row.min_val,
            max=row.max_val,
            std=row.std,
            p10=row.p10,
            p90=row.p90,
        ),
    )


async def interpret_field_pass(
    session: AsyncSession,
    client: InterpretClient,
    *,
    field_id: uuid.UUID,
    scene_id: str,
    geometry_version: int,
    crop: str | None,
    model_id: str,
) -> Interpretation | None:
    """Draft an interpretation for one field/pass and store it unpublished. Idempotent: if a draft
    already exists for this prompt version it is returned without calling the model again (no
    wasted API call). Returns None when the pass has no stored analyses to interpret."""
    existing = await get_interpretation(
        session,
        field_id=field_id,
        scene_id=scene_id,
        geometry_version=geometry_version,
        prompt_version=PROMPT_VERSION,
    )
    if existing is not None:
        return existing

    rows = (
        (
            await session.execute(
                select(Analysis).where(
                    Analysis.field_id == field_id,
                    Analysis.scene_id == scene_id,
                    Analysis.geometry_version == geometry_version,
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    # Fetch preceding 14-day weather and 30-day activity logs context
    from rs_activity import get_activity_adapter
    from rs_interpret.grounding import aggregate_grounding_data
    from rs_weather import get_weather_adapter

    weather_port = get_weather_adapter()
    activity_port = get_activity_adapter()

    gdd, precip, logs = await aggregate_grounding_data(
        session,
        rows[0],
        weather_port,
        activity_port,
    )

    recent_activities = [
        {
            "date": log.date.isoformat(),
            "activity": str(log.activity),
            "detail": log.detail,
        }
        for log in logs
    ]

    evidence = ground(
        [_output_from_analysis(row) for row in rows],
        crop=crop,
        pass_date=rows[0].pass_date,
        gdd_accumulation=gdd,
        total_precipitation=precip,
        recent_activities=recent_activities,
    )
    draft = await interpret(evidence, client, model=model_id)
    row, _ = await insert_interpretation(
        session,
        field_id=field_id,
        scene_id=scene_id,
        pass_date=rows[0].pass_date,
        geometry_version=geometry_version,
        prompt_version=draft.prompt_version,
        crop=crop,
        narrative=draft.narrative,
        status=draft.status,
        confidence=draft.confidence,
        model=draft.model,
        gdd_accumulation=evidence.gdd_accumulation,
        total_precipitation=evidence.total_precipitation,
        recent_activities=evidence.recent_activities,
    )
    return row
