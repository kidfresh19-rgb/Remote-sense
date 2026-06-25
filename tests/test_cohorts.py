"""Pure tests for the Ward Watch cohort fallback ladder (PRD 0003 §6.3). Zero DB, zero network."""

from __future__ import annotations

import pytest
from rs_core.cohorts import (
    CohortLevel,
    CohortRung,
    select_cohort_level,
)

_N_MIN = 20


def test_small_preferred_cohort_widens_to_clear_quorum() -> None:
    rungs = [
        CohortRung(CohortLevel.WARD_CROP_WINDOW, 3),
        CohortRung(CohortLevel.WARD_CROP, 50),
        CohortRung(CohortLevel.DISTRICT_CROP, 400),
    ]
    chosen = select_cohort_level(rungs, n_min=_N_MIN)
    assert chosen.level is CohortLevel.WARD_CROP
    assert chosen.meets_quorum


def test_large_preferred_cohort_stays_at_narrowest() -> None:
    rungs = [
        CohortRung(CohortLevel.WARD_CROP_WINDOW, 100),
        CohortRung(CohortLevel.WARD_CROP, 400),
    ]
    chosen = select_cohort_level(rungs, n_min=_N_MIN)
    assert chosen.level is CohortLevel.WARD_CROP_WINDOW
    assert chosen.meets_quorum


def test_rung_order_does_not_matter() -> None:
    rungs = [
        CohortRung(CohortLevel.NATURAL_REGION_CROP, 900),
        CohortRung(CohortLevel.WARD_CROP, 50),
        CohortRung(CohortLevel.WARD_CROP_WINDOW, 3),
    ]
    chosen = select_cohort_level(rungs, n_min=_N_MIN)
    assert chosen.level is CohortLevel.WARD_CROP  # the narrowest rung that clears 20


def test_no_rung_clears_quorum_uses_widest_and_flags() -> None:
    rungs = [
        CohortRung(CohortLevel.WARD_CROP_WINDOW, 2),
        CohortRung(CohortLevel.WARD_CROP, 5),
        CohortRung(CohortLevel.DISTRICT_CROP, 9),
    ]
    chosen = select_cohort_level(rungs, n_min=_N_MIN)
    assert chosen.level is CohortLevel.DISTRICT_CROP  # widest available
    assert not chosen.meets_quorum


def test_invalid_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        select_cohort_level([], n_min=_N_MIN)
    with pytest.raises(ValueError):
        select_cohort_level([CohortRung(CohortLevel.WARD_CROP, 50)], n_min=0)
