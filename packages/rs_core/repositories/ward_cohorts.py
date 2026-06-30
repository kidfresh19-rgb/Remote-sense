"""Ward Watch live cohort read path (backlog 0032): assemble each household's plots into peer
cohorts and run the movement lens, so the officer triage queue and food-security rollups read real
per-household signals instead of an empty list.

Mirrors `repositories.comparison`: this module does only the DB work - load the households in scope,
their cohort-keyed plots, and each plot's stored index series - then hands plain observations to the
pure assembler (`rs_core.cohort_assembly`). Cohort membership is computed live; the cohort key is
composed of persisted columns (`Plot.dominant_crop` / `Plot.planting_window` and the household's
Natural Region + ward), so there is no separate cohort table - it extends the ADR 0010 peer
cohorts (which materialise live behind a cache seam) rather than forking them. The cohort always
keys on the Natural Region ASSIGNMENT, never a gateway region string (invariant 6, ADR 0010)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.cohort_assembly import (
    CohortAssessment,
    PlotObservation,
    assemble_household_assessments,
)
from rs_core.cohorts import CohortLevel
from rs_core.logging import get_logger
from rs_core.models import Household, Plot, PlotAnalysis
from rs_core.movement import MovementLabel
from rs_core.strata import cohort_key

log = get_logger("rs_core.repositories.ward_cohorts")

# Defaults mirror the authoritative Settings knobs (rs_core.config, ⚑ CONFIRM PRD §12.2); the read
# path passes the configured values, these let the function be called standalone in tests.
DEFAULT_WARD_COHORT_N_MIN = 20
DEFAULT_WARD_DECLINE_THRESHOLD = 0.05
DEFAULT_WARD_CLEAR_FLOOR = 0.5
DEFAULT_PLOT_INDEX = "ndvi"
# The movement lens needs at least two clear passes to read a trend; below this a plot is not
# assessed at all rather than shown as a falsely confident "nominal".
_MIN_SERIES = 2


@dataclass(frozen=True)
class HouseholdCohortAssessment:
    """A household's assessed triage signal plus the display fields the read endpoints join on: the
    pure `CohortAssessment` enriched with the household's village, ward and Natural Region."""

    household_id: str
    plot_id: str
    village: str | None
    ward: str
    dominant_nr: str
    dominant_crop: str
    label: MovementLabel
    robust_deviation: float
    cohort_level: CohortLevel
    cohort_meets_quorum: bool
    low_pixel_quality: bool


@dataclass
class _Series:
    """One plot's clear index series (oldest first) and the pixel-quality flag of its latest
    pass."""

    values: list[float] = field(default_factory=list)
    low_pixel_quality: bool = False


async def _plot_series(
    session: AsyncSession,
    plot_ids: list[uuid.UUID],
    index_name: str,
    clear_floor: float,
) -> dict[uuid.UUID, _Series]:
    """Each plot's clear index series in one query (no N+1). A pass joins the series only when it
    has a mean and clears `clear_floor`; rows are read oldest-first, so the running
    `low_pixel_quality` ends on the latest included pass - the current read whose honesty the
    officer queue surfaces (§4). Low-pixel passes are kept (and flagged), never dropped, so a tiny
    backyard plot still gets a series rather than vanishing."""
    rows = (
        await session.execute(
            select(
                PlotAnalysis.plot_id,
                PlotAnalysis.mean,
                PlotAnalysis.clear_fraction,
                PlotAnalysis.low_pixel_quality,
            )
            .where(PlotAnalysis.plot_id.in_(plot_ids), PlotAnalysis.index_name == index_name)
            .order_by(PlotAnalysis.plot_id, PlotAnalysis.pass_date)
        )
    ).all()
    grouped: dict[uuid.UUID, _Series] = {}
    for plot_id, mean, clear_fraction, low_pixel in rows:
        if mean is None or clear_fraction < clear_floor:
            continue
        series = grouped.setdefault(plot_id, _Series())
        series.values.append(float(mean))
        series.low_pixel_quality = bool(low_pixel)
    return grouped


async def _cached_household_assessments(
    session: AsyncSession, *, ward: str | None, index_name: str
) -> list[HouseholdCohortAssessment] | None:
    """The materialization seam (mirrors `comparison._cached_cluster_stats`, the ~200-farm seam
    carried unchanged): v1 has no cache table, so this always misses and live assembly runs. When a
    materialized cohort cache lands this is the single place it is read - the call site never
    changes."""
    return None


async def assess_household_cohorts(
    session: AsyncSession,
    *,
    ward: str | None = None,
    index_name: str = DEFAULT_PLOT_INDEX,
    n_min: int = DEFAULT_WARD_COHORT_N_MIN,
    decline_threshold: float = DEFAULT_WARD_DECLINE_THRESHOLD,
    clear_floor: float = DEFAULT_WARD_CLEAR_FLOOR,
) -> list[HouseholdCohortAssessment]:
    """Assess every in-scope household live (backlog 0032). Checks the (always-empty in v1) cache,
    then falls back to live compute - the seam where a materialized cache slots in without touching
    callers. `n_min`, `decline_threshold` and `clear_floor` are configurable, never hard-coded into
    the assembly."""
    cached = await _cached_household_assessments(session, ward=ward, index_name=index_name)
    if cached is not None:
        return cached
    return await _compute_household_cohorts(
        session,
        ward=ward,
        index_name=index_name,
        n_min=n_min,
        decline_threshold=decline_threshold,
        clear_floor=clear_floor,
    )


async def _compute_household_cohorts(
    session: AsyncSession,
    *,
    ward: str | None,
    index_name: str,
    n_min: int,
    decline_threshold: float,
    clear_floor: float,
) -> list[HouseholdCohortAssessment]:
    """Live compute: load households in scope and their cohort-keyed plot series, build plain plot
    observations, and run the pure cohort assembler. A household with no Natural Region assignment
    or ward, or no plot with a declared crop + planting window and at least two clear passes, has
    nothing to cohort on and is simply absent from the result (honest empty, never fabricated)."""
    household_stmt = select(Household)
    if ward is not None:
        # Placeholder ward filter; 0041 replaces it with server-side officer scoping
        # (see the ward_watch module note).
        household_stmt = household_stmt.where(
            func.lower(Household.ward_name) == ward.strip().lower()
        )
    households = (await session.execute(household_stmt)).scalars().all()
    in_scope = {h.id: h for h in households if h.dominant_nr and h.ward_name}
    if not in_scope:
        return []

    plots = (
        (await session.execute(select(Plot).where(Plot.household_id.in_(list(in_scope.keys())))))
        .scalars()
        .all()
    )
    keyable = [p for p in plots if p.dominant_crop and p.planting_window]
    if not keyable:
        return []

    series_by_plot = await _plot_series(session, [p.id for p in keyable], index_name, clear_floor)

    observations: list[PlotObservation] = []
    for plot in keyable:
        series = series_by_plot.get(plot.id)
        if series is None or len(series.values) < _MIN_SERIES:
            continue
        household = in_scope[plot.household_id]
        crop, window = plot.dominant_crop, plot.planting_window
        nr, ward_name = household.dominant_nr, household.ward_name
        if not (crop and window and nr and ward_name):
            continue  # defensive: the scope/keyable filters already guarantee these
        try:
            key = cohort_key(
                dominant_crop=crop,
                natural_region=nr,
                ward=ward_name,
                planting_window=window,
                size_class=plot.size_class,
            )
        except ValueError as exc:
            log.warning("ward_watch.cohort.unkeyable_plot", plot_id=str(plot.id), detail=str(exc))
            continue
        household_id = household.canonical_household_id or str(household.client_uuid)
        observations.append(
            PlotObservation(
                plot_id=str(plot.id),
                household_id=household_id,
                key=key,
                series=tuple(series.values),
                low_pixel_quality=series.low_pixel_quality,
            )
        )

    assessments = assemble_household_assessments(
        observations, n_min=n_min, decline_threshold=decline_threshold
    )
    return _enrich(assessments, in_scope)


def _enrich(
    assessments: list[CohortAssessment], in_scope: dict[uuid.UUID, Household]
) -> list[HouseholdCohortAssessment]:
    """Join the pure assessments back to their household's display fields (village, ward, NR)."""
    by_id: dict[str, Household] = {
        (h.canonical_household_id or str(h.client_uuid)): h for h in in_scope.values()
    }
    enriched: list[HouseholdCohortAssessment] = []
    for assessment in assessments:
        household = by_id[assessment.household_id]
        # in_scope guarantees both are set; assert narrows for the typed return.
        assert household.ward_name is not None and household.dominant_nr is not None
        enriched.append(
            HouseholdCohortAssessment(
                household_id=assessment.household_id,
                plot_id=assessment.plot_id,
                village=household.village,
                ward=household.ward_name,
                dominant_nr=household.dominant_nr,
                dominant_crop=assessment.dominant_crop,
                label=assessment.label,
                robust_deviation=assessment.robust_deviation,
                cohort_level=assessment.cohort_level,
                cohort_meets_quorum=assessment.cohort_meets_quorum,
                low_pixel_quality=assessment.low_pixel_quality,
            )
        )
    return enriched
