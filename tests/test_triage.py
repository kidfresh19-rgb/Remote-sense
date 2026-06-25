"""Pure tests for the Ward Watch officer triage queue (PRD 0003 §7.1, backlog 0036): severity-then-
deviation ranking, a hard cap, and the honesty flags on every row. Zero DB, zero network."""

from __future__ import annotations

import pytest
from rs_core.cohorts import CohortLevel
from rs_core.movement import MovementLabel
from rs_core.triage import TriageCandidate, build_triage_queue


def _cand(
    hid: str,
    label: MovementLabel,
    deviation: float,
    *,
    level: CohortLevel = CohortLevel.WARD_CROP_WINDOW,
    quorum: bool = True,
    low_pixels: bool = False,
) -> TriageCandidate[str]:
    return TriageCandidate(
        household_id=hid,
        label=label,
        robust_deviation=deviation,
        cohort_level=level,
        cohort_meets_quorum=quorum,
        low_pixel_quality=low_pixels,
    )


def test_idiosyncratic_outranks_nominal() -> None:
    queue = build_triage_queue(
        [
            _cand("calm", MovementLabel.NOMINAL, 0.0),
            _cand("failing", MovementLabel.IDIOSYNCRATIC, 2.0),
        ]
    )
    assert [r.household_id for r in queue] == ["failing", "calm"]
    assert queue[0].rank == 1
    assert queue[1].rank == 2


def test_full_severity_order() -> None:
    queue = build_triage_queue(
        [
            _cand("n", MovementLabel.NOMINAL, 5.0),  # high deviation must not lift a nominal row
            _cand("r", MovementLabel.RESILIENT, 5.0),
            _cand("s", MovementLabel.SYSTEMIC, 0.1),
            _cand("i", MovementLabel.IDIOSYNCRATIC, 0.1),
        ]
    )
    assert [r.household_id for r in queue] == ["i", "s", "r", "n"]


def test_within_a_label_worst_deviation_first() -> None:
    queue = build_triage_queue(
        [
            _cand("mild", MovementLabel.IDIOSYNCRATIC, 1.0),
            _cand("severe", MovementLabel.IDIOSYNCRATIC, 3.0),
            _cand("moderate", MovementLabel.IDIOSYNCRATIC, 2.0),
        ]
    )
    assert [r.household_id for r in queue] == ["severe", "moderate", "mild"]


def test_queue_is_capped() -> None:
    candidates = [_cand(f"hh-{i}", MovementLabel.IDIOSYNCRATIC, float(i)) for i in range(40)]
    queue = build_triage_queue(candidates, cap=15)
    assert len(queue) == 15
    assert [r.rank for r in queue] == list(range(1, 16))
    # The cap keeps the worst, so the lowest-deviation households fall off.
    assert queue[0].robust_deviation == 39.0


def test_rows_carry_cohort_level_and_pixel_quality() -> None:
    queue = build_triage_queue(
        [
            _cand(
                "thin",
                MovementLabel.IDIOSYNCRATIC,
                2.0,
                level=CohortLevel.NATURAL_REGION_CROP,
                quorum=False,
                low_pixels=True,
            )
        ]
    )
    row = queue[0]
    assert row.cohort_level is CohortLevel.NATURAL_REGION_CROP
    assert row.cohort_meets_quorum is False
    assert row.low_pixel_quality is True


def test_ties_keep_input_order() -> None:
    queue = build_triage_queue(
        [
            _cand("first", MovementLabel.SYSTEMIC, 1.0),
            _cand("second", MovementLabel.SYSTEMIC, 1.0),
        ]
    )
    assert [r.household_id for r in queue] == ["first", "second"]


def test_empty_input_is_empty_queue() -> None:
    assert build_triage_queue([]) == []


def test_cap_below_one_rejected() -> None:
    with pytest.raises(ValueError, match="cap must be at least 1"):
        build_triage_queue([_cand("x", MovementLabel.NOMINAL, 0.0)], cap=0)
