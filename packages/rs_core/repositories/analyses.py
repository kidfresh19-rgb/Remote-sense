"""The analysis upsert (D3): one engine result becomes one additive, idempotent `analysis` row
keyed by its scientific identity (field, scene, index, geometry_version, formula_version).
Re-processing the same identity refreshes that row in place rather than duplicating it, so a
re-run can complete a partial write or attach a COG uri later."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Insert, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import Analysis

# The analysis identity (PLAN §5): one row per field/scene/index at a given geometry + formula
# version. Re-processing the same identity must converge, never duplicate, so the upsert keys on
# this constraint and refreshes only the computed payload + provenance listed here.
_ANALYSIS_MUTABLE = (
    "pass_date",
    "mean",
    "min_val",
    "max_val",
    "std",
    "p10",
    "p90",
    "clear_fraction",
    "resolution_m",
    "provider",
    "provider_scene_id",
    "processing_mode",
    "cog_uri",
    "confidence",
)


def _analysis_upsert_stmt(values: dict[str, object]) -> Insert:
    """Build the INSERT ... ON CONFLICT DO UPDATE for one analysis row. Factored out so the value
    mapping and the conflict target are unit-testable with no database. RETURNING `(xmax = 0)`
    reports whether this call inserted (True) or refreshed an existing row (False) - the standard
    Postgres idiom for telling the two apart in a single statement."""
    stmt = pg_insert(Analysis).values(**values)
    return stmt.on_conflict_do_update(
        constraint="uq_analysis_identity",
        set_={col: stmt.excluded[col] for col in _ANALYSIS_MUTABLE},
    ).returning(literal_column("(xmax = 0)"))


async def upsert_analysis(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    scene_id: str,
    pass_date: date,
    index_name: str,
    formula_version: str,
    geometry_version: int,
    provider: str,
    provider_scene_id: str,
    processing_mode: str,
    resolution_m: float,
    clear_fraction: float,
    mean: float | None = None,
    min_val: float | None = None,
    max_val: float | None = None,
    std: float | None = None,
    p10: float | None = None,
    p90: float | None = None,
    confidence: str | None = None,
    cog_uri: str | None = None,
) -> tuple[Analysis, bool]:
    """Persist one engine result as an `analysis` row; return (row, created). Additive and
    idempotent (CLAUDE.md invariant 5): a new scientific identity inserts, a repeat refreshes the
    same row in place rather than duplicating it. Every row carries the full provenance tuple
    (provider, provider_scene_id, processing_mode, formula_version, geometry_version)."""
    values: dict[str, object] = {
        "field_id": field_id,
        "scene_id": scene_id,
        "pass_date": pass_date,
        "index_name": index_name,
        "formula_version": formula_version,
        "geometry_version": geometry_version,
        "provider": provider,
        "provider_scene_id": provider_scene_id,
        "processing_mode": processing_mode,
        "resolution_m": resolution_m,
        "clear_fraction": clear_fraction,
        "mean": mean,
        "min_val": min_val,
        "max_val": max_val,
        "std": std,
        "p10": p10,
        "p90": p90,
        "confidence": confidence,
        "cog_uri": cog_uri,
    }
    created = bool((await session.execute(_analysis_upsert_stmt(values))).scalar_one())
    row = (
        await session.execute(
            select(Analysis).where(
                Analysis.field_id == field_id,
                Analysis.scene_id == scene_id,
                Analysis.index_name == index_name,
                Analysis.geometry_version == geometry_version,
                Analysis.formula_version == formula_version,
            )
        )
    ).scalar_one()
    return row, created
