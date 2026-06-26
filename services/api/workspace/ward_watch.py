"""Ward Watch read endpoints (PRD 0003 §7.1, §10; backlog 0036 + 0039): the officer triage queue and
the food-security rollups, both reading the same 2x2 movement labels. Internal workspace BFF, read
only - additive, not the frozen AgriTrack contract.

Both endpoints project a per-household ASSESSMENT (movement label, robust deviation, cohort level
and the honesty flags) through the pure cohort-engine cores (`rs_core.triage`, `rs_core.rollups`).
The
assessment itself - running the movement lens over each household's per-plot index series within its
cohort - is produced by a `HouseholdAssessor`. The DB-backed assessor is the seam backlog 0031 fills
once per-household ingestion lands (it is gated on the 0027 gateway inbound contract); until then it
yields nothing, so these endpoints return an empty queue / empty rollup against real data. The wire
contract, RBAC and projection are real now, which is what the dashboards (0040) build against.

# CONFIRM (0041): RBAC is the generic `view` permission for now. Ward Watch officer/ward scoping -
an officer sees only their own ward's queue, enforced server-side rather than via a client `ward`
param - lands with the 0041 role hierarchy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from rs_core.cohorts import CohortLevel
from rs_core.movement import MovementLabel
from rs_core.rollups import HouseholdLabel, RollupNode, roll_up_food_security
from rs_core.triage import DEFAULT_QUEUE_CAP, TriageCandidate, build_triage_queue
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.workspace.deps import ReadSessionDep, ViewPrincipal

router = APIRouter(tags=["ward-watch"])


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
    """The default DB-backed assessor. ⚑ CONFIRM (0031): assembling each household's per-plot index
    series, running the movement lens within its cohort, and stamping the pixel-quality flag is the
    ingestion slice 0031, gated on the 0027 gateway inbound contract. Until it lands there are no
    per-household series to classify, so this returns no assessments and the endpoints serve an
    empty queue / rollup over real data rather than a fabricated one."""

    async def assess(self, session: AsyncSession, *, ward: str | None) -> list[HouseholdAssessment]:
        return []


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
