"""Pure tests for the Ward Watch movement lens (PRD 0003 §6.2): the robust 2x2 triage classifier.
Zero DB, zero network - synthetic cohort series only."""

from __future__ import annotations

from statistics import mean

import pytest
from rs_core.movement import (
    CohortMovement,
    HouseholdMovement,
    MovementLabel,
    classify_cohort_movement,
)

_THRESHOLD = 0.1


def _flat(level: float, n: int = 4) -> list[float]:
    return [level] * n


def _decline(start: float, end: float) -> list[float]:
    # earlier half at `start`, recent half at `end`; movement = end - start.
    return [start, start, end, end]


def _by_id(result: CohortMovement[str], household_id: str) -> HouseholdMovement[str]:
    return next(h for h in result.households if h.household_id == household_id)


def test_single_failing_household_in_stable_cohort_is_idiosyncratic() -> None:
    cohort: list[tuple[str, list[float]]] = [(f"stable-{i}", _flat(0.5)) for i in range(10)]
    cohort.append(("failing", _decline(0.5, 0.15)))
    result = classify_cohort_movement(cohort, decline_threshold=_THRESHOLD)
    assert not result.declining
    assert _by_id(result, "failing").label is MovementLabel.IDIOSYNCRATIC
    assert _by_id(result, "stable-0").label is MovementLabel.NOMINAL


def test_uniformly_declining_cohort_is_systemic() -> None:
    cohort: list[tuple[str, list[float]]] = [(f"hh-{i}", _decline(0.6, 0.3)) for i in range(8)]
    result = classify_cohort_movement(cohort, decline_threshold=_THRESHOLD)
    assert result.declining
    labels = {h.label for h in result.households}
    assert labels == {MovementLabel.SYSTEMIC}


def test_household_holding_in_declining_cohort_is_resilient() -> None:
    cohort: list[tuple[str, list[float]]] = [(f"hh-{i}", _decline(0.6, 0.3)) for i in range(6)]
    cohort.append(("holding", _flat(0.6)))
    result = classify_cohort_movement(cohort, decline_threshold=_THRESHOLD)
    assert result.declining
    assert _by_id(result, "holding").label is MovementLabel.RESILIENT


def test_one_outlier_does_not_flip_a_healthy_household_label() -> None:
    # Five stable households plus one collapsing outlier.
    cohort: list[tuple[str, list[float]]] = [(f"stable-{i}", _flat(0.5)) for i in range(5)]
    cohort.append(("collapse", _decline(0.9, 0.0)))
    result = classify_cohort_movement(cohort, decline_threshold=_THRESHOLD)

    # A mean-of-movements baseline WOULD read the cohort as declining and mislabel the healthy
    # households; the robust median centre does not.
    movements = [h.movement for h in result.households]
    assert mean(movements) <= -_THRESHOLD  # the outlier drags the mean below the threshold
    assert not result.declining  # the robust median centre keeps the cohort stable
    assert _by_id(result, "stable-0").label is MovementLabel.NOMINAL
    assert _by_id(result, "collapse").label is MovementLabel.IDIOSYNCRATIC


def test_failing_household_has_low_standing_and_high_deviation() -> None:
    cohort: list[tuple[str, list[float]]] = [(f"stable-{i}", _flat(0.5)) for i in range(10)]
    cohort.append(("failing", _decline(0.5, 0.15)))
    result = classify_cohort_movement(cohort, decline_threshold=_THRESHOLD)
    failing = _by_id(result, "failing")
    assert failing.standing_percentile < 50.0
    assert failing.robust_deviation > 0.0


def test_empty_cohort_is_handled() -> None:
    result: CohortMovement[str] = classify_cohort_movement([], decline_threshold=_THRESHOLD)
    assert result.households == []
    assert not result.declining


def test_negative_threshold_rejected() -> None:
    with pytest.raises(ValueError):
        classify_cohort_movement([("h", _flat(0.5))], decline_threshold=-0.1)
