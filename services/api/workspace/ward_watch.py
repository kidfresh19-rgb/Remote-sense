"""Ward Watch read endpoints (PRD 0003 §7.1, §10; backlog 0036 + 0039): the officer triage queue and
the food-security rollups, both reading the same 2x2 movement labels. Internal workspace BFF, read
only - additive, not the frozen AgriTrack contract.

Both endpoints project a per-household ASSESSMENT (movement label, robust deviation, cohort level
and the honesty flags) through the pure cohort-engine cores (`rs_core.triage`, `rs_core.rollups`).
The assessment itself - assembling each household's plots into peer cohorts and running the movement
lens over their stored per-plot index series - is produced by a `HouseholdAssessor`. The DB-backed
assessor (backlog 0032) does this live via `rs_core.repositories.assess_household_cohorts`, so the
queue and rollups are non-empty over real ingested data (0031). A household with no Natural Region
assignment, ward, or assessable plot series is simply absent (honest empty, never fabricated).

# CONFIRM (0041): RBAC is the generic `view` permission for now. Ward Watch officer/ward scoping -
an officer sees only their own ward's queue, enforced server-side rather than via a client `ward`
param - lands with the 0041 role hierarchy."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from rs_core import get_settings
from rs_core.alert_hints import AlertHint
from rs_core.cohorts import CohortLevel
from rs_core.diagnosis import validate_diagnosis
from rs_core.models import Diagnosis, Plot
from rs_core.movement import MovementLabel
from rs_core.repositories import (
    HouseholdVisitPackage,
    VisitDiagnosis,
    VisitPlot,
    assess_household_cohorts,
    get_household_visit_package,
    list_diagnoses,
    record_diagnosis,
)
from rs_core.rollups import HouseholdLabel, RollupNode, roll_up_food_security
from rs_core.triage import DEFAULT_QUEUE_CAP, TriageCandidate, build_triage_queue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.workspace.deps import (
    ReadSessionDep,
    RecordDiagnosisPrincipal,
    SessionDep,
    ViewPrincipal,
)

router = APIRouter(tags=["ward-watch"])

# ⚑ CONFIRM (PRD §12.6): the administrative hierarchy above ward (district, province) is not yet
# sourced - ward-boundary procurement is an open item, and a household carries only its ward and its
# (agro-ecological) Natural Region. Rather than fabricate a province from the NR, the rollup reports
# these levels as a single "unassigned" node so the dashboard is honest that only the ward rollup is
# live today; district / province light up when the boundary layers land.
_UNASSIGNED_ADMIN = "unassigned"


@dataclass(frozen=True)
class HouseholdAssessment:
    """One household's assessed state for a triage/rollup pass - the contract a `HouseholdAssessor`
    returns and backlog 0031 populates. Identity and administrative placement plus the movement-lens
    output (label, robust deviation), the cohort level the lens used and whether it cleared quorum,
    and the pixel-quality flag (§4)."""

    household_id: str
    village: str | None
    ward: str
    district: str
    province: str
    dominant_crop: str | None
    label: MovementLabel
    robust_deviation: float
    cohort_level: CohortLevel
    cohort_meets_quorum: bool
    low_pixel_quality: bool


class HouseholdAssessor(Protocol):
    """Produces per-household assessments for a triage/rollup pass. Implemented for real in 0031
    (per-plot ingestion -> movement lens -> cohort assignment); injected so the endpoints are
    testable with seeded assessments today."""

    async def assess(
        self, session: AsyncSession, *, ward: str | None
    ) -> list[HouseholdAssessment]: ...


class DbHouseholdAssessor:
    """The default DB-backed assessor (backlog 0032). Assembles each household's plots into peer
    cohorts (dominant crop, NR assignment, ward, planting window), climbs the small-cohort fallback
    ladder, and runs the robust movement lens over the stored per-plot index series, summarising a
    household by its most severe plot. The cohort tuning (N_min, decline threshold, clear floor) is
    read from settings (⚑ CONFIRM PRD §12.2). District / province are not yet sourced, so they carry
    the honest `_UNASSIGNED_ADMIN` placeholder (see module note); ward and the cohort signal are
    real."""

    async def assess(self, session: AsyncSession, *, ward: str | None) -> list[HouseholdAssessment]:
        settings = get_settings()
        assessed = await assess_household_cohorts(
            session,
            ward=ward,
            n_min=settings.ward_cohort_n_min,
            decline_threshold=settings.ward_movement_decline_threshold,
            clear_floor=settings.ward_clear_fraction_floor,
        )
        return [
            HouseholdAssessment(
                household_id=a.household_id,
                village=a.village,
                ward=a.ward,
                district=_UNASSIGNED_ADMIN,
                province=_UNASSIGNED_ADMIN,
                dominant_crop=a.dominant_crop,
                label=a.label,
                robust_deviation=a.robust_deviation,
                cohort_level=a.cohort_level,
                cohort_meets_quorum=a.cohort_meets_quorum,
                low_pixel_quality=a.low_pixel_quality,
            )
            for a in assessed
        ]


def get_household_assessor() -> HouseholdAssessor:
    return DbHouseholdAssessor()


AssessorDep = Annotated[HouseholdAssessor, Depends(get_household_assessor)]


class TriageRowOut(BaseModel):
    """One row of the officer queue. Geometry-free; the distance-from-officer, trend sparkline and
    full crop mix are presentation joins added once the per-plot geometry/series are wired (0031).
    The cohort level, quorum flag and pixel-quality flag travel on every row for honesty (§7.1)."""

    rank: int
    household_id: str
    village: str | None
    ward: str
    dominant_crop: str | None
    label: str
    robust_deviation: float
    cohort_level: str
    cohort_meets_quorum: bool
    low_pixel_quality: bool


class RollupNodeOut(BaseModel):
    """One administrative unit's label tally, idiosyncratic and systemic reported separately."""

    name: str
    total: int
    nominal: int
    resilient: int
    idiosyncratic: int
    systemic: int
    distressed: int
    systemic_fraction: float


class FoodSecurityRollupOut(BaseModel):
    """The same households tallied per ward, district and province, each list worst-first."""

    by_ward: list[RollupNodeOut]
    by_district: list[RollupNodeOut]
    by_province: list[RollupNodeOut]


def rank_triage(
    assessments: list[HouseholdAssessment], *, cap: int = DEFAULT_QUEUE_CAP
) -> list[TriageRowOut]:
    """Project assessments through the pure triage ranking and shape the wire rows, joining the
    descriptive fields back by household id."""
    by_id = {a.household_id: a for a in assessments}
    queue = build_triage_queue(
        [
            TriageCandidate(
                household_id=a.household_id,
                label=a.label,
                robust_deviation=a.robust_deviation,
                cohort_level=a.cohort_level,
                cohort_meets_quorum=a.cohort_meets_quorum,
                low_pixel_quality=a.low_pixel_quality,
            )
            for a in assessments
        ],
        cap=cap,
    )
    rows: list[TriageRowOut] = []
    for row in queue:
        a = by_id[row.household_id]
        rows.append(
            TriageRowOut(
                rank=row.rank,
                household_id=row.household_id,
                village=a.village,
                ward=a.ward,
                dominant_crop=a.dominant_crop,
                label=row.label.value,
                robust_deviation=row.robust_deviation,
                cohort_level=row.cohort_level.value,
                cohort_meets_quorum=row.cohort_meets_quorum,
                low_pixel_quality=row.low_pixel_quality,
            )
        )
    return rows


def _node_out(node: RollupNode) -> RollupNodeOut:
    c = node.counts
    return RollupNodeOut(
        name=node.name,
        total=c.total,
        nominal=c.nominal,
        resilient=c.resilient,
        idiosyncratic=c.idiosyncratic,
        systemic=c.systemic,
        distressed=c.distressed,
        systemic_fraction=c.systemic_fraction,
    )


def roll_up(assessments: list[HouseholdAssessment]) -> FoodSecurityRollupOut:
    """Project assessments through the pure food-security rollup and shape the wire response."""
    rollup = roll_up_food_security(
        [
            HouseholdLabel(ward=a.ward, district=a.district, province=a.province, label=a.label)
            for a in assessments
        ]
    )
    return FoodSecurityRollupOut(
        by_ward=[_node_out(n) for n in rollup.by_ward],
        by_district=[_node_out(n) for n in rollup.by_district],
        by_province=[_node_out(n) for n in rollup.by_province],
    )


@router.get("/ward-watch/triage")
async def ward_watch_triage_endpoint(
    principal: ViewPrincipal,
    session: ReadSessionDep,
    assessor: AssessorDep,
    ward: str | None = None,
    cap: Annotated[int, Query(ge=1, le=100)] = DEFAULT_QUEUE_CAP,
) -> list[TriageRowOut]:
    """The capped, ranked weekly officer queue (PRD 0003 §7.1). Ordered by movement-label severity
    then robust deviation; every row carries the cohort level used and the pixel-quality flag. The
    `ward` filter is a placeholder for the 0041 server-side ward scoping (see module note)."""
    assessments = await assessor.assess(session, ward=ward)
    return rank_triage(assessments, cap=cap)


@router.get("/ward-watch/rollups")
async def ward_watch_rollups_endpoint(
    principal: ViewPrincipal,
    session: ReadSessionDep,
    assessor: AssessorDep,
) -> FoodSecurityRollupOut:
    """Food-security rollups (PRD 0003 §10): idiosyncratic and systemic counts per ward, district
    and province, read from the same labels the officer queue uses - no parallel statistics path."""
    assessments = await assessor.assess(session, ward=None)
    return roll_up(assessments)


class VisitTrendPointOut(BaseModel):
    """One stored pass on a plot's index trend, with the §4 flag for low-confidence points."""

    pass_date: str
    ndvi_mean: float | None
    clear_fraction: float
    low_pixel_quality: bool


class VisitPlotOut(BaseModel):
    """One plot for the visit screen. `geometry` (GeoJSON) plus `latest_scene_id` +
    `latest_pass_date` are the inputs the cockpit feeds the natural-colour (RGB-COG) preview, so the
    orthophoto loads the same way AOI Studio loads it (PRD §7.3)."""

    plot_id: str
    dominant_crop: str | None
    planting_window: str | None
    size_class: str | None
    area_m2: float | None
    geometry: dict[str, Any]
    latest_scene_id: str | None
    latest_pass_date: str | None
    trend: list[VisitTrendPointOut]


class VisitAssessmentOut(BaseModel):
    """The household's cohort movement assessment (the same signal the triage queue ranks on)."""

    label: str
    robust_deviation: float
    cohort_level: str
    cohort_meets_quorum: bool
    low_pixel_quality: bool


class AlertHintOut(BaseModel):
    """One index-grounded alert hint - a prioritisation hint, never a diagnosis (PRD §7.2).
    `framing` carries that caveat so the UI cannot present it as a verdict."""

    category: str
    headline: str
    signature: str
    strength: float
    framing: str


class PreviousVisitOut(BaseModel):
    """One past field diagnosis on this household (0038), the visit history newest-first."""

    diagnosis_id: str
    plot_id: str
    observed_on: str
    observed_crop: str
    condition: str
    cause: str
    recommended_action: str | None
    notes: str | None
    officer_id: str | None


class VisitPackageOut(BaseModel):
    """The physical-visit package for one household (PRD §7.3): context, per-plot imagery references
    and index trend, the movement assessment, alert hints, the officer's questions, and the
    household's recorded diagnoses (`previous_visits`, 0038). `drone_reference` is a seam."""

    household_id: str
    village: str | None
    ward: str | None
    dominant_nr: str | None
    dominant_crop: str | None
    plots: list[VisitPlotOut]
    assessment: VisitAssessmentOut | None
    alert_hints: list[AlertHintOut]
    recommended_questions: list[str]
    previous_visits: list[PreviousVisitOut]
    drone_reference: str | None


def _hint_out(hint: AlertHint) -> AlertHintOut:
    return AlertHintOut(
        category=hint.category.value,
        headline=hint.headline,
        signature=hint.signature,
        strength=hint.strength,
        framing=hint.framing,
    )


def _plot_out(plot: VisitPlot) -> VisitPlotOut:
    return VisitPlotOut(
        plot_id=plot.plot_id,
        dominant_crop=plot.dominant_crop,
        planting_window=plot.planting_window,
        size_class=plot.size_class,
        area_m2=plot.area_m2,
        geometry=plot.geometry,
        latest_scene_id=plot.latest_scene_id,
        latest_pass_date=plot.latest_pass_date.isoformat() if plot.latest_pass_date else None,
        trend=[
            VisitTrendPointOut(
                pass_date=point.pass_date.isoformat(),
                ndvi_mean=point.ndvi_mean,
                clear_fraction=point.clear_fraction,
                low_pixel_quality=point.low_pixel_quality,
            )
            for point in plot.trend
        ],
    )


def _previous_visit_out(visit: VisitDiagnosis) -> PreviousVisitOut:
    return PreviousVisitOut(
        diagnosis_id=visit.diagnosis_id,
        plot_id=visit.plot_id,
        observed_on=visit.observed_on.isoformat(),
        observed_crop=visit.observed_crop,
        condition=visit.condition,
        cause=visit.cause,
        recommended_action=visit.recommended_action,
        notes=visit.notes,
        officer_id=visit.officer_id,
    )


def _visit_out(package: HouseholdVisitPackage) -> VisitPackageOut:
    """Shape the assembled package onto the wire response."""
    assessment = package.assessment
    return VisitPackageOut(
        household_id=package.household_id,
        village=package.village,
        ward=package.ward,
        dominant_nr=package.dominant_nr,
        dominant_crop=package.dominant_crop,
        plots=[_plot_out(p) for p in package.plots],
        assessment=(
            VisitAssessmentOut(
                label=assessment.label.value,
                robust_deviation=assessment.robust_deviation,
                cohort_level=assessment.cohort_level.value,
                cohort_meets_quorum=assessment.cohort_meets_quorum,
                low_pixel_quality=assessment.low_pixel_quality,
            )
            if assessment is not None
            else None
        ),
        alert_hints=[_hint_out(h) for h in package.alert_hints],
        recommended_questions=package.recommended_questions,
        previous_visits=[_previous_visit_out(v) for v in package.previous_visits],
        drone_reference=package.drone_reference,
    )


@router.get("/ward-watch/visit/{household_id}")
async def ward_watch_visit_endpoint(
    household_id: str,
    principal: ViewPrincipal,
    session: ReadSessionDep,
) -> VisitPackageOut:
    """One household's physical-visit package (PRD 0003 §7.3): context, per-plot orthophoto refs and
    index trend, the cohort movement assessment, index-grounded alert hints (framed as hints, not
    diagnoses) and the officer's recommended questions. 404 when the household is not held."""
    settings = get_settings()
    package = await get_household_visit_package(
        session,
        household_id,
        n_min=settings.ward_cohort_n_min,
        decline_threshold=settings.ward_movement_decline_threshold,
        clear_floor=settings.ward_clear_fraction_floor,
    )
    if package is None:
        raise HTTPException(status_code=404, detail="household not found")
    return _visit_out(package)


class DiagnosisIn(BaseModel):
    """The officer's field diagnosis to record (backlog 0038). `observed_crop` / `condition` /
    `cause` are controlled vocabularies (required); `recommended_action` is an optional controlled
    value; `notes` is the only free text. `scene_id` is the pass observed against (provenance);
    `observed_on` defaults to today. The recording officer is the verified token subject, not the
    client's."""

    plot_id: str
    observed_crop: str
    condition: str
    cause: str
    recommended_action: str | None = None
    notes: str | None = None
    scene_id: str | None = None
    observed_on: date | None = None


class DiagnosisOut(BaseModel):
    """A stored field diagnosis - one labelled ground-truth point for the flywheel (0038)."""

    id: str
    plot_id: str
    observed_on: str
    observed_crop: str
    condition: str
    cause: str
    recommended_action: str | None
    notes: str | None
    scene_id: str | None
    officer_id: str | None
    created_at: str


@router.post("/ward-watch/diagnosis", status_code=201)
async def ward_watch_record_diagnosis_endpoint(
    payload: DiagnosisIn,
    principal: RecordDiagnosisPrincipal,
    session: SessionDep,
) -> DiagnosisOut:
    """Record one officer field diagnosis (PRD 0003 §0, backlog 0038): a controlled-vocab labelled
    point linked to the plot + the scene observed against. 422 on a bad controlled value or plot id,
    404 when the plot is not held; the officer is the verified token subject."""
    try:
        plot_uuid = uuid.UUID(payload.plot_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="plot_id must be a uuid") from exc
    try:
        fields = validate_diagnosis(
            observed_crop=payload.observed_crop,
            condition=payload.condition,
            cause=payload.cause,
            recommended_action=payload.recommended_action,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    plot = (await session.execute(select(Plot).where(Plot.id == plot_uuid))).scalar_one_or_none()
    if plot is None:
        raise HTTPException(status_code=404, detail="plot not found")

    diagnosis = await record_diagnosis(
        session,
        plot_id=plot_uuid,
        fields=fields,
        observed_on=payload.observed_on or date.today(),
        scene_id=payload.scene_id,
        officer_id=principal.subject,
    )
    await session.commit()
    return _diagnosis_out(diagnosis)


@router.get("/ward-watch/diagnoses")
async def ward_watch_diagnoses_endpoint(
    principal: ViewPrincipal,
    session: ReadSessionDep,
    ward: str | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[DiagnosisOut]:
    """The recorded diagnoses as a labelled set for export (PRD 0003 §0, backlog 0038), newest
    observation first; `ward` scopes to one ward. This is the flywheel's read side."""
    rows = await list_diagnoses(session, ward=ward, limit=limit)
    return [_diagnosis_out(d) for d in rows]


def _diagnosis_out(diagnosis: Diagnosis) -> DiagnosisOut:
    return DiagnosisOut(
        id=str(diagnosis.id),
        plot_id=str(diagnosis.plot_id),
        observed_on=diagnosis.observed_on.isoformat(),
        observed_crop=diagnosis.observed_crop,
        condition=diagnosis.condition,
        cause=diagnosis.cause,
        recommended_action=diagnosis.recommended_action,
        notes=diagnosis.notes,
        scene_id=diagnosis.scene_id,
        officer_id=diagnosis.officer_id,
        created_at=diagnosis.created_at.isoformat(),
    )
