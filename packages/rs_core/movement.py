"""Pure cohort movement-lens statistics for Ward Watch (PRD 0003 §6.2): the robust 2x2 triage
classifier at household granularity. No DB, no network - unit-testable on synthetic series.

The lens is deliberately robust (median and median-absolute-deviation, never mean and standard
deviation): in a distressed cohort the severe cases inflate the standard deviation and drag the
mean, which would hide the very households an officer needs to visit. Each household is placed in a
2x2 of its own trajectory against the cohort's trajectory:

    +------------------+---------------+------------------+
    |                  | cohort stable | cohort declining |
    +------------------+---------------+------------------+
    | household stable | nominal       | resilient        |
    | hh declining     | idiosyncratic | systemic         |
    +------------------+---------------+------------------+

Idiosyncratic is the highest-value officer visit (one farm failing while peers hold); systemic is a
food-security escalation (the whole cohort sliding). This module computes only the lens; cohort
assembly and persistence live in the comparison-groups layer (ADR 0010), which this extends."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median
from typing import Generic, TypeVar

H = TypeVar("H")

# MAD-to-sigma scale for a normal distribution, so a robust deviation reads like a z-score.
_MAD_TO_SIGMA = 1.4826


class MovementLabel(StrEnum):
    """The 2x2 triage label (PRD 0003 §6.2)."""

    NOMINAL = "nominal"
    RESILIENT = "resilient"
    IDIOSYNCRATIC = "idiosyncratic"
    SYSTEMIC = "systemic"


@dataclass(frozen=True)
class HouseholdMovement(Generic[H]):
    """One household's place in the lens: its 2x2 label, its robust movement over the window
    (negative is declining), its robust deviation below the cohort centre (positive is worse than
    peers), and its standing percentile of recent level (0 worst, 100 best)."""

    household_id: H
    label: MovementLabel
    movement: float
    robust_deviation: float
    standing_percentile: float


@dataclass(frozen=True)
class CohortMovement(Generic[H]):
    """The cohort's robust movement centre, whether it is declining, and the per-household lens
    results in input order."""

    centre: float
    declining: bool
    households: list[HouseholdMovement[H]]


def _window_movement(values: Sequence[float]) -> float:
    """Robust change across the baseline window: the median of the recent half minus the median of
    the earlier half. Half-window medians instead of endpoints ignore single-date spikes (a cloud
    edge, one bad pixel) that would otherwise read as movement."""
    n = len(values)
    if n < 2:
        return 0.0
    half = n // 2
    return median(values[half:]) - median(values[:half])


def _recent_level(values: Sequence[float]) -> float:
    """The household's current level: the median of the recent half of the window, used for the
    standing percentile. Robust to a single bad observation."""
    n = len(values)
    if n == 0:
        return 0.0
    half = n // 2
    return median(values[half:] if n >= 2 else values)


def _mad(values: Sequence[float], centre: float) -> float:
    """Median absolute deviation about a given centre."""
    if not values:
        return 0.0
    return median([abs(v - centre) for v in values])


def _percentile_rank(value: float, population: Sequence[float]) -> float:
    """Percentile rank of `value` within `population`: the share at or below it, as 0..100. A lone
    household has no peers, so it ranks at the top (100)."""
    n = len(population)
    if n <= 1:
        return 100.0
    at_or_below = sum(1 for v in population if v <= value)
    return 100.0 * at_or_below / n


def _label(*, household_declining: bool, cohort_declining: bool) -> MovementLabel:
    if household_declining and cohort_declining:
        return MovementLabel.SYSTEMIC
    if household_declining:
        return MovementLabel.IDIOSYNCRATIC
    if cohort_declining:
        return MovementLabel.RESILIENT
    return MovementLabel.NOMINAL


def classify_cohort_movement(
    cohort: Sequence[tuple[H, Sequence[float]]],
    *,
    decline_threshold: float,
) -> CohortMovement[H]:
    """Classify every household in a cohort with the robust movement lens.

    `cohort` is a sequence of (household_id, index series over the baseline window, oldest first).
    `decline_threshold` is the positive index drop that counts as declining: a movement at or below
    `-decline_threshold` is a decline, for both a household and the cohort centre. The cohort centre
    is the MEDIAN household movement, so a few collapsing farms cannot make a healthy cohort look
    like it is declining (the robustness a standing-only lens lacks).

    Raises ValueError if `decline_threshold` is negative."""
    if decline_threshold < 0:
        raise ValueError(f"decline_threshold must be non-negative, got {decline_threshold}")
    if not cohort:
        return CohortMovement(centre=0.0, declining=False, households=[])

    movements = [_window_movement(values) for _, values in cohort]
    levels = [_recent_level(values) for _, values in cohort]
    centre = median(movements)
    mad = _mad(movements, centre)
    scale = _MAD_TO_SIGMA * mad if mad > 0 else 1.0
    cohort_declining = centre <= -decline_threshold

    households: list[HouseholdMovement[H]] = []
    for (household_id, _values), movement, level in zip(cohort, movements, levels, strict=True):
        household_declining = movement <= -decline_threshold
        households.append(
            HouseholdMovement(
                household_id=household_id,
                label=_label(
                    household_declining=household_declining, cohort_declining=cohort_declining
                ),
                movement=movement,
                robust_deviation=(centre - movement) / scale,
                standing_percentile=_percentile_rank(level, levels),
            )
        )
    return CohortMovement(centre=centre, declining=cohort_declining, households=households)
