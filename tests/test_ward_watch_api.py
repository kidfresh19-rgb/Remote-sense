"""Tests for the Ward Watch read endpoints (backlog 0036 + 0039): the triage and rollup projections,
and the endpoint functions called directly with a seeded assessor (HTTP auth is covered by
test_api_auth). The real cohort-assembly assessor needs PostGIS and is tested in
test_ward_watch_cohorts_db; here the ranking/rollup projection logic is exercised through injected
assessments. Zero network."""

from __future__ import annotations

import pytest
from rs_core.cohorts import CohortLevel
from rs_core.movement import MovementLabel
from rs_core.rbac import Principal, Role

from services.api.workspace.ward_watch import (
    HouseholdAssessment,
    narrow_officer_scope,
    rank_triage,
    resolve_triage_ward_scope,
    roll_up,
    ward_watch_rollups_endpoint,
    ward_watch_triage_endpoint,
)

# A district agronomist holds VIEW_TRIAGE_QUEUE + VIEW_FOOD_SECURITY_ROLLUP and is cross-ward, so
# the projection tests reach both read endpoints without an officer-scoping DB lookup (covered in
# the DB suite). Direct endpoint calls bypass the HTTP permission gate; the role drives scoping.
_SUPERVISOR = Principal(subject="agronomist-1", roles=frozenset({Role.DISTRICT_AGRONOMIST}))


def _assess(
    hid: str,
    label: MovementLabel,
    deviation: float = 1.0,
    *,
    ward: str = "ward 3",
    district: str = "goromonzi",
    province: str = "mash east",
    level: CohortLevel = CohortLevel.WARD_CROP_WINDOW,
    quorum: bool = True,
    low_pixels: bool = False,
) -> HouseholdAssessment:
    return HouseholdAssessment(
        household_id=hid,
        village="chivhu",
        ward=ward,
        district=district,
        province=province,
        dominant_crop="maize",
        label=label,
        robust_deviation=deviation,
        cohort_level=level,
        cohort_meets_quorum=quorum,
        low_pixel_quality=low_pixels,
    )


class _FakeAssessor:
    def __init__(self, assessments: list[HouseholdAssessment]) -> None:
        self._assessments = assessments
        self.seen_wards: object = "unset"

    async def assess(self, session, *, wards):
        self.seen_wards = wards
        return list(self._assessments)


def test_rank_triage_orders_and_shapes_rows() -> None:
    rows = rank_triage(
        [
            _assess("calm", MovementLabel.NOMINAL, 0.0),
            _assess(
                "failing",
                MovementLabel.IDIOSYNCRATIC,
                2.0,
                level=CohortLevel.DISTRICT_CROP,
                quorum=False,
                low_pixels=True,
            ),
        ]
    )
    assert [r.household_id for r in rows] == ["failing", "calm"]
    top = rows[0]
    assert top.rank == 1
    assert top.label == "idiosyncratic"
    assert top.dominant_crop == "maize"
    assert top.village == "chivhu"
    assert top.cohort_level == "district_crop"
    assert top.cohort_meets_quorum is False
    assert top.low_pixel_quality is True


def test_rank_triage_caps() -> None:
    assessments = [_assess(f"hh-{i}", MovementLabel.IDIOSYNCRATIC, float(i)) for i in range(30)]
    rows = rank_triage(assessments, cap=10)
    assert len(rows) == 10
    assert rows[0].robust_deviation == 29.0


def test_roll_up_reports_idiosyncratic_and_systemic_separately() -> None:
    assessments = [
        _assess(f"sys-{i}", MovementLabel.SYSTEMIC, ward="ward 3", district="goromonzi")
        for i in range(5)
    ]
    assessments.append(
        _assess("idio", MovementLabel.IDIOSYNCRATIC, ward="ward 7", district="marondera")
    )
    out = roll_up(assessments)
    province = next(n for n in out.by_province if n.name == "mash east")
    assert province.systemic == 5
    assert province.idiosyncratic == 1
    assert province.total == 6
    assert out.by_ward[0].name == "ward 3"  # worst-first


async def test_triage_endpoint_projects_seeded_assessments() -> None:
    assessor = _FakeAssessor(
        [
            _assess("calm", MovementLabel.NOMINAL, 0.0),
            _assess("failing", MovementLabel.IDIOSYNCRATIC, 2.0),
        ]
    )
    rows = await ward_watch_triage_endpoint(
        principal=_SUPERVISOR, session=None, assessor=assessor, ward=None, cap=15
    )
    assert [r.household_id for r in rows] == ["failing", "calm"]


async def test_rollups_endpoint_projects_seeded_assessments() -> None:
    assessor = _FakeAssessor([_assess(f"s-{i}", MovementLabel.SYSTEMIC) for i in range(3)])
    out = await ward_watch_rollups_endpoint(principal=_SUPERVISOR, session=None, assessor=assessor)
    assert out.by_province[0].systemic == 3


async def test_triage_endpoint_passes_supervisor_ward_filter_to_assessor() -> None:
    # A supervisor's optional `ward` reaches the assessor as a single-ward scope, never widened.
    assessor = _FakeAssessor([_assess("x", MovementLabel.NOMINAL)])
    await ward_watch_triage_endpoint(
        principal=_SUPERVISOR, session=None, assessor=assessor, ward="Ward 3", cap=15
    )
    assert assessor.seen_wards == ["Ward 3"]


async def test_triage_endpoint_supervisor_without_ward_reads_all_wards() -> None:
    assessor = _FakeAssessor([_assess("x", MovementLabel.NOMINAL)])
    await ward_watch_triage_endpoint(
        principal=_SUPERVISOR, session=None, assessor=assessor, ward=None, cap=15
    )
    assert assessor.seen_wards is None  # None = no ward filter, all wards


def test_narrow_officer_scope_defaults_to_all_officer_wards() -> None:
    assert narrow_officer_scope(["Ward 7", "Ward 8"], None) == ["Ward 7", "Ward 8"]


def test_narrow_officer_scope_can_only_narrow_within_own_wards() -> None:
    assert narrow_officer_scope(["Ward 7", "Ward 8"], "ward 7") == ["Ward 7"]
    # A ward the officer does not hold cannot be widened into - an empty scope.
    assert narrow_officer_scope(["Ward 7"], "Ward 9") == []


async def test_resolve_ward_scope_supervisor_keeps_optional_cross_ward_filter() -> None:
    # The supervisor branch never touches the DB (no officer_wards lookup), so session is unused.
    assert await resolve_triage_ward_scope(None, _SUPERVISOR, None) is None
    assert await resolve_triage_ward_scope(None, _SUPERVISOR, "Ward 7") == ["Ward 7"]


def test_rank_triage_empty() -> None:
    assert rank_triage([]) == []


def test_rank_triage_rejects_bad_cap() -> None:
    with pytest.raises(ValueError, match="cap must be at least 1"):
        rank_triage([_assess("x", MovementLabel.NOMINAL)], cap=0)
