"""The per-plot analysis upsert (backlog 0031): one engine pass becomes one additive, idempotent
`plot_analysis` row keyed by its identity (plot, index, pass date, formula version). Re-processing
the same identity refreshes that row in place rather than duplicating it. Mirrors the field-level
`analyses.upsert_analysis`, but keyed to a plot (Ward Watch identity, invariant 6) and carries the
§4 pixel-quality flag. Value-based - no rs_analysis import - so rs_core stays free of an upward
dependency on the analysis layer; the worker maps an engine pass dict onto these arguments."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import Insert, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import PlotAnalysis

# Everything outside the identity (plot, index, pass_date, formula_version) refreshes on a repeat.
_PLOT_ANALYSIS_MUTABLE = (
    "scene_id",
    "mean",
    "min_val",
    "max_val",
    "p10",
    "p90",
    "clear_fraction",
    "resolution_m",
    "pixels",
    "low_pixel_quality",
    "provider",
    "provider_scene_id",
    "processing_mode",
    "confidence",
)


def _plot_analysis_upsert_stmt(values: dict[str, object]) -> Insert:
    """INSERT ... ON CONFLICT DO UPDATE for one plot_analysis row, factored out so the value mapping
    and the conflict target are unit-testable with no database. RETURNING `(created_at = now())`
    reports whether this call inserted (True) or refreshed an existing row (False): created_at is
    written only by the insert default and never by the refresh SET list, so equality with the
    transaction timestamp marks a fresh row (the same idiom `analyses.upsert_analysis` uses)."""
    stmt = pg_insert(PlotAnalysis).values(**values)
    return stmt.on_conflict_do_update(
        constraint="uq_plot_analysis_identity",
        set_={col: stmt.excluded[col] for col in _PLOT_ANALYSIS_MUTABLE},
    ).returning(literal_column("(created_at = now())"))


async def upsert_plot_analysis(
    session: AsyncSession,
    *,
    plot_id: uuid.UUID,
    scene_id: str,
    pass_date: date,
    index_name: str,
    formula_version: str,
    provider: str,
    provider_scene_id: str,
    processing_mode: str,
    resolution_m: float,
    clear_fraction: float,
    pixels: int,
    low_pixel_quality: bool,
    mean: float | None = None,
    min_val: float | None = None,
    max_val: float | None = None,
    p10: float | None = None,
    p90: float | None = None,
    confidence: str | None = None,
) -> tuple[PlotAnalysis, bool]:
    """Persist one engine pass as a `plot_analysis` row; return (row, created). Additive and
    idempotent (invariant 5): a new identity inserts, a repeat refreshes the same row in place
    rather than duplicating it. `low_pixel_quality` is the §4 honesty flag (PRD 0003 §4)."""
    values: dict[str, object] = {
        "plot_id": plot_id,
        "scene_id": scene_id,
        "pass_date": pass_date,
        "index_name": index_name,
        "formula_version": formula_version,
        "provider": provider,
        "provider_scene_id": provider_scene_id,
        "processing_mode": processing_mode,
        "resolution_m": resolution_m,
        "clear_fraction": clear_fraction,
        "pixels": pixels,
        "low_pixel_quality": low_pixel_quality,
        "mean": mean,
        "min_val": min_val,
        "max_val": max_val,
        "p10": p10,
        "p90": p90,
        "confidence": confidence,
    }
    created = bool((await session.execute(_plot_analysis_upsert_stmt(values))).scalar_one())
    row = (
        await session.execute(
            select(PlotAnalysis).where(
                PlotAnalysis.plot_id == plot_id,
                PlotAnalysis.index_name == index_name,
                PlotAnalysis.pass_date == pass_date,
                PlotAnalysis.formula_version == formula_version,
            )
        )
    ).scalar_one()
    return row, created


async def plot_index_series(
    session: AsyncSession, plot_id: uuid.UUID, index_name: str
) -> Sequence[PlotAnalysis]:
    """Every stored pass for one plot + index, oldest first - the per-plot time series the cohort
    movement lens reads (0032)."""
    result = await session.execute(
        select(PlotAnalysis)
        .where(PlotAnalysis.plot_id == plot_id, PlotAnalysis.index_name == index_name)
        .order_by(PlotAnalysis.pass_date)
    )
    return result.scalars().all()
