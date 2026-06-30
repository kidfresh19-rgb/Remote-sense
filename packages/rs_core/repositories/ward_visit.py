"""Ward Watch physical-visit package read path (backlog 0037, PRD 0003 §7.3). Opening a household
assembles everything an officer needs for a field visit in one read: the household and its plots,
each with its index trend + history, the cohort movement assessment, index-grounded alert HINTS
(never diagnoses, `rs_core.alert_hints`), and the officer's recommended questions.

The orthophoto is referenced the way AOI Studio reads it: each plot carries its geometry plus its
latest pass's scene id + date, which are exactly the inputs the natural-colour (RGB-COG) preview
takes, so the cockpit renders it through the existing path, not a new one. Geometry travels on this
internal BFF (the cockpit map needs it); never pushed to the gateway (invariant 6 governs the
external contract).

Mirrors `repositories.comparison` / `repositories.ward_cohorts`: the DB work is here, the pure cores
(movement lens, hint engine) do the judgement. `previous_visits` reads the household's recorded
diagnoses (0038); `drone_reference` stays a seam (None) until a gateway-provided drone ref is stored
(the 0026 inbound carries it, but the reconcile does not persist it yet)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.alert_hints import AlertHint, HintInputs, assess_alert_hints, recommended_questions
from rs_core.cohorts import CohortLevel
from rs_core.models import Household, Plot
from rs_core.movement import MovementLabel
from rs_core.repositories.diagnoses import diagnoses_for_household
from rs_core.repositories.plot_analyses import plot_index_series
from rs_core.repositories.ward_cohorts import (
    DEFAULT_PLOT_INDEX,
    DEFAULT_WARD_CLEAR_FLOOR,
    DEFAULT_WARD_COHORT_N_MIN,
    DEFAULT_WARD_DECLINE_THRESHOLD,
    assess_household_cohorts,
)

# The movement lens needs at least two clear passes to read a trend (mirrors
# ward_cohorts._MIN_SERIES).
_MIN_SERIES = 2


@dataclass(frozen=True)
class VisitTrendPoint:
    """One stored pass on a plot's index trend: its date, the index mean, the per-AOI clear fraction
    and the §4 pixel-quality flag (so the chart can mark low-confidence points)."""

    pass_date: date
    ndvi_mean: float | None
    clear_fraction: float
    low_pixel_quality: bool


@dataclass(frozen=True)
class VisitPlot:
    """One plot in the visit package: its enrollment facts, its geometry (for the map and the
    orthophoto request), the latest pass's scene id + date (the natural-colour preview inputs), and
    its full index trend."""

    plot_id: str
    dominant_crop: str | None
    planting_window: str | None
    size_class: str | None
    area_m2: float | None
    geometry: dict[str, Any]
    latest_scene_id: str | None
    latest_pass_date: date | None
    trend: list[VisitTrendPoint]


@dataclass(frozen=True)
class VisitAssessment:
    """The household's cohort movement assessment (the same signal the triage queue ranks on)."""

    label: MovementLabel
    robust_deviation: float
    cohort_level: CohortLevel
    cohort_meets_quorum: bool
    low_pixel_quality: bool


@dataclass(frozen=True)
class VisitDiagnosis:
    """One past field diagnosis on this household (backlog 0038), newest first - the visit history.
    Controlled-vocab condition / cause / action plus the observed crop and any free-text note."""

    diagnosis_id: str
    plot_id: str
    observed_on: date
    observed_crop: str
    condition: str
    cause: str
    recommended_action: str | None
    notes: str | None
    officer_id: str | None


@dataclass(frozen=True)
class HouseholdVisitPackage:
    """Everything for one household's visit screen (PRD §7.3). `alert_hints` are prioritisation
    hints, never diagnoses; `previous_visits` is the household's recorded diagnoses (0038);
    `drone_reference` is a seam until a gateway-provided drone ref is stored."""

    household_id: str
    village: str | None
    ward: str | None
    dominant_nr: str | None
    dominant_crop: str | None
    plots: list[VisitPlot]
    assessment: VisitAssessment | None
    alert_hints: list[AlertHint]
    recommended_questions: list[str]
    previous_visits: list[VisitDiagnosis] = field(default_factory=list)
    drone_reference: str | None = None


async def _resolve_household(session: AsyncSession, household_id: str) -> Household | None:
    """Find the household by the id the triage queue exposes: the canonical household id, or the
    offline client uuid when the gateway has not assigned a canonical id yet."""
    conditions = [Household.canonical_household_id == household_id]
    try:
        conditions.append(Household.client_uuid == uuid.UUID(household_id))
    except ValueError:
        pass  # not a uuid - only the canonical id can match
    return (await session.execute(select(Household).where(or_(*conditions)))).scalar_one_or_none()


def _engine_series(clear_series: dict[str, list[float]], plot_id: str | None) -> list[float] | None:
    """The NDVI series the hint engine reads: the assessment's contributing plot when it has
    enough clear passes, else the plot with the most clear observations. None when nothing is long
    enough to read a trend."""
    if plot_id is not None:
        chosen = clear_series.get(plot_id, [])
        if len(chosen) >= _MIN_SERIES:
            return chosen
    longest = max(clear_series.values(), key=len, default=[])
    return longest if len(longest) >= _MIN_SERIES else None


async def _clear_series_for_plot(
    session: AsyncSession, plot_id: uuid.UUID, index_name: str, clear_floor: float
) -> list[float] | None:
    """One plot's clear series for an index (oldest first), or None below the lens minimum. Used to
    feed the contributing plot's NDMI / NDRE to the alert-hint engine (the moisture / red-edge
    signatures, PRD §7.2)."""
    rows = await plot_index_series(session, plot_id, index_name)
    series = [
        float(row.mean)
        for row in rows
        if row.mean is not None and row.clear_fraction >= clear_floor
    ]
    return series if len(series) >= _MIN_SERIES else None


async def get_household_visit_package(
    session: AsyncSession,
    household_id: str,
    *,
    index_name: str = DEFAULT_PLOT_INDEX,
    n_min: int = DEFAULT_WARD_COHORT_N_MIN,
    decline_threshold: float = DEFAULT_WARD_DECLINE_THRESHOLD,
    clear_floor: float = DEFAULT_WARD_CLEAR_FLOOR,
) -> HouseholdVisitPackage | None:
    """Assemble one household's visit package, or None when no such household is held. Loads each
    plot's stored index series (trend + the latest pass that references the orthophoto), runs the
    cohort movement lens for the assessment, then the alert-hint engine over the contributing plot's
    series (NDVI plus its NDMI / NDRE), so the moisture and red-edge signatures fire once those
    indices are ingested (the 0031 widening)."""
    household = await _resolve_household(session, household_id)
    if household is None:
        return None

    plots = (
        (await session.execute(select(Plot).where(Plot.household_id == household.id)))
        .scalars()
        .all()
    )

    visit_plots: list[VisitPlot] = []
    clear_series: dict[str, list[float]] = {}
    for plot in plots:
        rows = await plot_index_series(session, plot.id, index_name)
        trend = [
            VisitTrendPoint(
                pass_date=row.pass_date,
                ndvi_mean=row.mean,
                clear_fraction=row.clear_fraction,
                low_pixel_quality=row.low_pixel_quality,
            )
            for row in rows
        ]
        clear_series[str(plot.id)] = [
            float(row.mean)
            for row in rows
            if row.mean is not None and row.clear_fraction >= clear_floor
        ]
        latest = rows[-1] if rows else None  # plot_index_series orders oldest-first
        visit_plots.append(
            VisitPlot(
                plot_id=str(plot.id),
                dominant_crop=plot.dominant_crop,
                planting_window=plot.planting_window,
                size_class=plot.size_class,
                area_m2=plot.area_m2,
                geometry=dict(mapping(to_shape(plot.boundary))),
                latest_scene_id=latest.scene_id if latest is not None else None,
                latest_pass_date=latest.pass_date if latest is not None else None,
                trend=trend,
            )
        )

    resolved_id = household.canonical_household_id or str(household.client_uuid)
    assessment: VisitAssessment | None = None
    engine_plot_id: str | None = None
    dominant_crop = next((p.dominant_crop for p in plots if p.dominant_crop), None)
    if household.ward_name:
        cohort = await assess_household_cohorts(
            session,
            ward=household.ward_name,
            index_name=index_name,
            n_min=n_min,
            decline_threshold=decline_threshold,
            clear_floor=clear_floor,
        )
        match = next((a for a in cohort if a.household_id == resolved_id), None)
        if match is not None:
            assessment = VisitAssessment(
                label=match.label,
                robust_deviation=match.robust_deviation,
                cohort_level=match.cohort_level,
                cohort_meets_quorum=match.cohort_meets_quorum,
                low_pixel_quality=match.low_pixel_quality,
            )
            engine_plot_id = match.plot_id
            dominant_crop = match.dominant_crop

    # NDMI / NDRE for the contributing plot light up the moisture + red-edge hints (PRD §7.2); they
    # are dormant until plot ingestion stores those indices (it now does, 0031 widening).
    ndmi_series: list[float] | None = None
    ndre_series: list[float] | None = None
    if engine_plot_id is not None:
        contributing = uuid.UUID(engine_plot_id)
        ndmi_series = await _clear_series_for_plot(session, contributing, "ndmi", clear_floor)
        ndre_series = await _clear_series_for_plot(session, contributing, "ndre", clear_floor)

    inputs = HintInputs(
        ndvi=_engine_series(clear_series, engine_plot_id),
        ndmi=ndmi_series,
        ndre=ndre_series,
        movement_label=assessment.label if assessment is not None else None,
        robust_deviation=assessment.robust_deviation if assessment is not None else None,
    )
    hints = assess_alert_hints(inputs, decline_threshold=decline_threshold)

    diagnoses = await diagnoses_for_household(session, household.id)
    previous_visits = [
        VisitDiagnosis(
            diagnosis_id=str(d.id),
            plot_id=str(d.plot_id),
            observed_on=d.observed_on,
            observed_crop=d.observed_crop,
            condition=d.condition,
            cause=d.cause,
            recommended_action=d.recommended_action,
            notes=d.notes,
            officer_id=d.officer_id,
        )
        for d in diagnoses
    ]

    return HouseholdVisitPackage(
        household_id=resolved_id,
        village=household.village,
        ward=household.ward_name,
        dominant_nr=household.dominant_nr,
        dominant_crop=dominant_crop,
        plots=visit_plots,
        assessment=assessment,
        alert_hints=hints,
        recommended_questions=recommended_questions([h.category for h in hints]),
        previous_visits=previous_visits,
    )
