"""Persist analysis outputs (Phase 3, D3): map an engine `AnalysisOutput` plus scene
provenance onto the value-based `upsert_analysis` helper and write it.

The worker is the layer allowed to know both rs_analysis (the science) and rs_core (the store),
so this mapping lives here. That keeps rs_core free of an upward dependency on rs_analysis, the
same boundary the scene-metadata upsert keeps with rs_imagery (rs_core/repositories.py)."""

from __future__ import annotations

import uuid
from datetime import date

from rs_analysis import AnalysisOutput
from rs_core.models import Analysis
from rs_core.repositories import upsert_analysis
from sqlalchemy.ext.asyncio import AsyncSession


def analysis_upsert_kwargs(
    output: AnalysisOutput,
    *,
    field_id: uuid.UUID,
    scene_id: str,
    pass_date: date,
    geometry_version: int,
    provider: str,
    provider_scene_id: str,
    processing_mode: str,
    cog_uri: str | None = None,
) -> dict[str, object]:
    """Flatten an `AnalysisOutput` (which nests its stats in a `ZonalStats`) plus the scene's
    provenance into the keyword arguments `upsert_analysis` expects. Pure - no DB - so the
    mapping is unit-testable on its own."""
    stats = output.stats
    return {
        "field_id": field_id,
        "scene_id": scene_id,
        "pass_date": pass_date,
        "index_name": output.index_name,
        "formula_version": output.formula_version,
        "geometry_version": geometry_version,
        "provider": provider,
        "provider_scene_id": provider_scene_id,
        "processing_mode": processing_mode,
        "resolution_m": float(output.resolution_m),
        "clear_fraction": output.clear_fraction,
        "mean": stats.mean,
        "min_val": stats.min,
        "max_val": stats.max,
        "std": stats.std,
        "p10": stats.p10,
        "p90": stats.p90,
        "confidence": output.confidence,
        "cog_uri": cog_uri,
    }


async def persist_analysis_output(
    session: AsyncSession,
    output: AnalysisOutput,
    *,
    field_id: uuid.UUID,
    scene_id: str,
    pass_date: date,
    geometry_version: int,
    provider: str,
    provider_scene_id: str,
    processing_mode: str,
    cog_uri: str | None = None,
) -> tuple[Analysis, bool]:
    """Write one engine result to the `analysis` table additively and idempotently; return
    (row, created)."""
    return await upsert_analysis(
        session,
        **analysis_upsert_kwargs(
            output,
            field_id=field_id,
            scene_id=scene_id,
            pass_date=pass_date,
            geometry_version=geometry_version,
            provider=provider,
            provider_scene_id=provider_scene_id,
            processing_mode=processing_mode,
            cog_uri=cog_uri,
        ),
    )
