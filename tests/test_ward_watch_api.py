"""Tests for the Ward Watch read endpoints (backlog 0036 + 0039): the triage and rollup projections,
and the endpoint functions called directly with a seeded assessor (HTTP auth is covered by
test_api_auth). The DB-backed assessor is the 0031 seam and yields nothing until ingestion lands, so
the real ranking/rollup logic is exercised through injected assessments. Zero network."""

from __future__ import annotations

import pytest
from rs_core.cohorts import CohortLevel
from rs_core.movement import MovementLabel
from rs_core.rbac import Principal, Role

from services.api.workspace.ward_watch import (
    DbHouseholdAssessor,
    HouseholdAssessment,
    get_household_assessor,
    rank_triage,
    roll_up,
    ward_watch_rollups_endpoint,
    ward_watch_triage_endpoint,
)

_VIEWER = Principal(subject="officer-1", roles=frozenset({Role.VIEWER}))


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

    async def assess(self, session, *, ward):
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
        principal=_VIEWER, session=None, assessor=assessor, ward=None, cap=15
    )
    assert [r.household_id for r in rows] == ["failing", "calm"]


async def test_rollups_endpoint_projects_seeded_assessments() -> None:
    assessor = _FakeAssessor([_assess(f"s-{i}", MovementLabel.SYSTEMIC) for i in range(3)])
    out = await ward_watch_rollups_endpoint(principal=_VIEWER, session=None, assessor=assessor)
    assert out.by_province[0].systemic == 3


async def test_default_db_assessor_is_empty_until_ingestion() -> None:
    # The 0031 seam: no per-household series exist yet, so the endpoints serve empty over real data.
    assert await DbHouseholdAssessor().assess(None, ward=None) == []
    rows = await ward_watch_triage_endpoint(
        principal=_VIEWER, session=None, assessor=get_household_assessor(), ward=None, cap=15
    )
    assert rows == []


def test_rank_triage_empty() -> None:
    assert rank_triage([]) == []


def test_rank_triage_rejects_bad_cap() -> None:
    with pytest.raises(ValueError, match="cap must be at least 1"):
        rank_triage([_assess("x", MovementLabel.NOMINAL)], cap=0)
