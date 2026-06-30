"""Pure live cohort assembly + movement lens for Ward Watch (PRD 0003 §6.2/6.3, backlog 0032). This
is the slice that makes the officer triage queue and the food-security rollups non-empty: it ties
the three pre-built pure cores together over a household's plot series.

For each plot it climbs the small-cohort fallback ladder (`rs_core.cohorts`) to the narrowest peer
cohort that clears quorum, runs the robust 2x2 movement lens (`rs_core.movement`) over that cohort,
and reads the plot's label + robust deviation from the result. The peer cohort is keyed by the
`strata.CohortKey` (dominant crop, Natural Region assignment, ward, planting window), so the same
crop in two planting windows lands in two cohorts (backlog 0032 AC). A household is then summarised
by its most severe plot, because the per-household triage queue and rollups want one signal per
household and the officer acts on the worst plot.

No DB, no network - the DB read path (`repositories.ward_cohorts`) loads the observations and hands
them here, exactly the way the comparison engine splits `comparison` (pure) from
`repositories.comparison` (DB). Membership is computed live; the cohort definition is just the key,
composed entirely of persisted columns, so there is no separate cohort table to keep in sync."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rs_core.cohorts import COHORT_LADDER, CohortLevel, CohortRung, select_cohort_level
from rs_core.movement import HouseholdMovement, MovementLabel, classify_cohort_movement
from rs_core.strata import CohortKey


@dataclass(frozen=True)
class PlotObservation:
    """One plot's input to the cohort engine: its identity and household, the peer-cohort key it
    sits in (`strata.CohortKey`), its index series over the baseline window (oldest first), and the
    §4 pixel-quality flag. `district` backs the district rung of the fallback ladder; None when no
    administrative district layer is loaded, so that rung is skipped rather than faked."""

    plot_id: str
    household_id: str
    key: CohortKey
    series: tuple[float, ...]
    low_pixel_quality: bool
    district: str | None = None


@dataclass(frozen=True)
class CohortAssessment:
    """One household's triage signal: the movement label and robust deviation of its most severe
    plot, the cohort level that plot's lens actually used and whether the cohort cleared quorum, and
    that plot's pixel-quality flag. `plot_id` identifies that contributing plot (the visit package
    reads its series for the alert hints); `dominant_crop` is its cohort crop."""

    household_id: str
    plot_id: str
    dominant_crop: str
    label: MovementLabel
    robust_deviation: float
    cohort_level: CohortLevel
    cohort_meets_quorum: bool
    low_pixel_quality: bool


# Triage severity, worst first - mirrors `rs_core.triage._SEVERITY_ORDER` (both encode PRD 0003
# §7.1). When a household has several plots, the one with the most severe label (then the largest
# robust deviation) is the signal the officer acts on, so it drives the household's assessment.
_SEVERITY_ORDER: tuple[MovementLabel, ...] = (
    MovementLabel.IDIOSYNCRATIC,
    MovementLabel.SYSTEMIC,
    MovementLabel.RESILIENT,
    MovementLabel.NOMINAL,
)
_SEVERITY_RANK: dict[MovementLabel, int] = {label: i for i, label in enumerate(_SEVERITY_ORDER)}


# One plot's lens result at its selected level: (observation, label, robust deviation, level used,
# whether that cohort cleared quorum).
_PlotResult = tuple[PlotObservation, MovementLabel, float, CohortLevel, bool]


def _level_key(level: CohortLevel, obs: PlotObservation) -> tuple[str, ...] | None:
    """The grouping key for `obs` at a ladder level, or None when the level is unavailable (the
    district rung with no district). Each rung widens by dropping a stratum: the window, then the
    ward; the district rung groups by district instead of ward (PRD 0003 §6.3)."""
    k = obs.key
    if level is CohortLevel.WARD_CROP_WINDOW:
        return (k.dominant_crop, k.natural_region, k.ward, k.planting_window.value)
    if level is CohortLevel.WARD_CROP:
        return (k.dominant_crop, k.natural_region, k.ward)
    if level is CohortLevel.DISTRICT_CROP:
        return (k.dominant_crop, obs.district) if obs.district else None
    if level is CohortLevel.NATURAL_REGION_CROP:
        return (k.dominant_crop, k.natural_region)
    return None


def _is_more_severe(candidate: _PlotResult, current: _PlotResult) -> bool:
    """Whether `candidate` outranks `current` for its household: a more severe label, or an equal
    label with a larger robust deviation (the same order `build_triage_queue` ranks by)."""
    _, c_label, c_dev, _, _ = candidate
    _, k_label, k_dev, _, _ = current
    return (_SEVERITY_RANK[c_label], -c_dev) < (_SEVERITY_RANK[k_label], -k_dev)


def assemble_household_assessments(
    observations: Sequence[PlotObservation],
    *,
    n_min: int,
    decline_threshold: float,
) -> list[CohortAssessment]:
    """Assess every household from its plot observations (backlog 0032).

    For each plot the fallback ladder picks the narrowest cohort level (`rs_core.cohorts`) whose
    membership clears `n_min`, the movement lens (`rs_core.movement`) classifies that cohort, and
    the plot takes its label + robust deviation from the result; the cohort a plot is scored against
    is the full membership at the selected level, independent of which level any other plot picked.
    Each household is summarised by its most severe plot (PRD §7.1 severity). Returns one
    `CohortAssessment` per household that has at least one assessable plot, in first-seen household
    order. `n_min` and `decline_threshold` are the (configurable) ladder quorum and the index drop
    that counts as declining."""
    if not observations:
        return []

    # Index plots by their key at every ladder level, so a rung's size is a dict lookup and the
    # cohort the lens runs on is the full membership at that level.
    level_members: dict[CohortLevel, dict[tuple[str, ...], list[PlotObservation]]] = {
        level: {} for level in COHORT_LADDER
    }
    for obs in observations:
        for level in COHORT_LADDER:
            lk = _level_key(level, obs)
            if lk is not None:
                level_members[level].setdefault(lk, []).append(obs)

    # Classify each cohort at most once, lazily, keyed by (level, level_key).
    classified: dict[tuple[CohortLevel, tuple[str, ...]], dict[str, HouseholdMovement[str]]] = {}

    def _movement_for(
        level: CohortLevel, level_key: tuple[str, ...], plot_id: str
    ) -> HouseholdMovement[str]:
        cache_key = (level, level_key)
        result = classified.get(cache_key)
        if result is None:
            cohort = [(o.plot_id, o.series) for o in level_members[level][level_key]]
            movement = classify_cohort_movement(cohort, decline_threshold=decline_threshold)
            result = {hm.household_id: hm for hm in movement.households}
            classified[cache_key] = result
        return result[plot_id]

    # Per plot: pick its level via the ladder, then read its lens result at that level.
    plot_results: list[_PlotResult] = []
    for obs in observations:
        rungs: list[CohortRung] = []
        for level in COHORT_LADDER:
            lk = _level_key(level, obs)
            if lk is not None:
                rungs.append(CohortRung(level=level, size=len(level_members[level][lk])))
        # Every plot keys on its own Natural Region, so the NR rung is always present and rungs is
        # non-empty.
        selection = select_cohort_level(rungs, n_min=n_min)
        sel_key = _level_key(selection.level, obs)
        assert sel_key is not None  # the selected level came from a rung we built above
        hm = _movement_for(selection.level, sel_key, obs.plot_id)
        plot_results.append(
            (obs, hm.label, hm.robust_deviation, selection.level, selection.meets_quorum)
        )

    # Aggregate plots to one signal per household: the most severe plot.
    by_household: dict[str, _PlotResult] = {}
    order: list[str] = []
    for record in plot_results:
        household_id = record[0].household_id
        current = by_household.get(household_id)
        if current is None:
            order.append(household_id)
            by_household[household_id] = record
        elif _is_more_severe(record, current):
            by_household[household_id] = record

    assessments: list[CohortAssessment] = []
    for household_id in order:
        obs, label, deviation, level, meets_quorum = by_household[household_id]
        assessments.append(
            CohortAssessment(
                household_id=household_id,
                plot_id=obs.plot_id,
                dominant_crop=obs.key.dominant_crop,
                label=label,
                robust_deviation=deviation,
                cohort_level=level,
                cohort_meets_quorum=meets_quorum,
                low_pixel_quality=obs.low_pixel_quality,
            )
        )
    return assessments
