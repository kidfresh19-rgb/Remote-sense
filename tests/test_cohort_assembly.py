"""Pure tests for Ward Watch live cohort assembly (backlog 0032): the keying that lands two planting
windows (or two Natural Regions) in two cohorts, the fallback ladder that widens a thin cohort and
stamps the level used, the movement lens reading through to a household label, and the per-household
aggregation to the most severe plot. No DB, no network."""

from __future__ import annotations

from rs_core.cohort_assembly import (
    PlotObservation,
    assemble_household_assessments,
)
from rs_core.cohorts import CohortLevel
from rs_core.movement import MovementLabel
from rs_core.strata import CohortKey, PlantingWindow

# Half-window medians: STABLE moves 0.0, DECLINING moves -0.2 (a clear decline past the 0.1
# threshold).
STABLE = (0.6, 0.6, 0.6, 0.6)
DECLINING = (0.6, 0.6, 0.4, 0.4)


def _obs(
    plot_id: str,
    household_id: str,
    *,
    series: tuple[float, ...],
    crop: str = "maize",
    nr: str = "region iii",
    ward: str = "ward 7",
    window: PlantingWindow = PlantingWindow.MAIN,
    low: bool = False,
    district: str | None = None,
) -> PlotObservation:
    return PlotObservation(
        plot_id=plot_id,
        household_id=household_id,
        key=CohortKey(dominant_crop=crop, natural_region=nr, ward=ward, planting_window=window),
        series=series,
        low_pixel_quality=low,
        district=district,
    )


def _of(assessments, household_id):
    return next(a for a in assessments if a.household_id == household_id)


def test_empty_input_yields_no_assessments() -> None:
    assert assemble_household_assessments([], n_min=5, decline_threshold=0.1) == []


def test_two_planting_windows_form_two_cohorts() -> None:
    # The subject declines among STABLE early-window peers, while the main-window peers all decline.
    # If the planting window is part of the key the subject is scored only against early peers (a
    # stable cohort) -> idiosyncratic; if the window were ignored it would join the declining main
    # peers and read systemic. Asserting idiosyncratic proves the window splits the cohort (AC).
    observations = (
        [_obs(f"e{i}", f"e{i}", series=STABLE, window=PlantingWindow.EARLY) for i in range(4)]
        + [_obs("subject", "subject", series=DECLINING, window=PlantingWindow.EARLY)]
        + [_obs(f"m{i}", f"m{i}", series=DECLINING, window=PlantingWindow.MAIN) for i in range(4)]
    )
    result = assemble_household_assessments(observations, n_min=1, decline_threshold=0.1)
    subject = _of(result, "subject")
    assert subject.label == MovementLabel.IDIOSYNCRATIC
    assert subject.cohort_level == CohortLevel.WARD_CROP_WINDOW
    assert subject.cohort_meets_quorum is True


def test_natural_region_splits_the_cohort() -> None:
    # Same shape as the window test but varying the NR: a declining plot scored only against its own
    # NR's (stable) peers reads idiosyncratic, proving the cohort keys off the NR assignment (AC).
    observations = (
        [_obs(f"a{i}", f"a{i}", series=STABLE, nr="region iii") for i in range(4)]
        + [_obs("subject", "subject", series=DECLINING, nr="region iii")]
        + [_obs(f"b{i}", f"b{i}", series=DECLINING, nr="region ii") for i in range(4)]
    )
    result = assemble_household_assessments(observations, n_min=1, decline_threshold=0.1)
    assert _of(result, "subject").label == MovementLabel.IDIOSYNCRATIC


def test_fallback_ladder_widens_thin_window_cohort_to_ward_crop() -> None:
    # The early-window cohort has 2 plots (below n_min=3); ward+crop (early + main) has 5, so the
    # ladder climbs one rung and stamps WARD_CROP as the level actually used.
    observations = [
        _obs(f"e{i}", f"e{i}", series=STABLE, window=PlantingWindow.EARLY) for i in range(2)
    ] + [_obs(f"m{i}", f"m{i}", series=STABLE, window=PlantingWindow.MAIN) for i in range(3)]
    result = assemble_household_assessments(observations, n_min=3, decline_threshold=0.1)
    early = _of(result, "e0")
    assert early.cohort_level == CohortLevel.WARD_CROP
    assert early.cohort_meets_quorum is True


def test_lone_plot_falls_back_to_widest_and_flags_thin_quorum() -> None:
    result = assemble_household_assessments(
        [_obs("solo", "solo", series=DECLINING)], n_min=5, decline_threshold=0.1
    )
    solo = _of(result, "solo")
    assert solo.cohort_level == CohortLevel.NATURAL_REGION_CROP
    assert solo.cohort_meets_quorum is False


def test_household_summarised_by_its_most_severe_plot() -> None:
    # Household H has a calm main-window plot and a failing early-window plot (idiosyncratic among
    # stable early peers). The household takes the worst plot's label and its pixel-quality flag.
    observations = [
        _obs("p_calm", "H", series=STABLE, window=PlantingWindow.MAIN, low=False),
        _obs("p_fail", "H", series=DECLINING, window=PlantingWindow.EARLY, low=True),
        *[_obs(f"m{i}", f"m{i}", series=STABLE, window=PlantingWindow.MAIN) for i in range(3)],
        *[_obs(f"e{i}", f"e{i}", series=STABLE, window=PlantingWindow.EARLY) for i in range(3)],
    ]
    result = assemble_household_assessments(observations, n_min=1, decline_threshold=0.1)
    household = _of(result, "H")
    assert household.label == MovementLabel.IDIOSYNCRATIC
    assert household.low_pixel_quality is True  # carried from the winning (failing) plot
    # one assessment per household, not one per plot
    assert sum(1 for a in result if a.household_id == "H") == 1


def test_district_rung_used_when_present() -> None:
    observations = [
        _obs("subject", "subject", series=STABLE, ward="ward 7", district="goromonzi"),
        *[
            _obs(f"d{i}", f"d{i}", series=STABLE, ward="ward 8", district="goromonzi")
            for i in range(3)
        ],
    ]
    result = assemble_household_assessments(observations, n_min=3, decline_threshold=0.1)
    assert _of(result, "subject").cohort_level == CohortLevel.DISTRICT_CROP


def test_district_rung_skipped_when_absent() -> None:
    # Same membership, no district -> the ladder skips the district rung and lands on NR+crop rather
    # than fabricating a district grouping.
    observations = [
        _obs("subject", "subject", series=STABLE, ward="ward 7"),
        *[_obs(f"d{i}", f"d{i}", series=STABLE, ward="ward 8") for i in range(3)],
    ]
    result = assemble_household_assessments(observations, n_min=3, decline_threshold=0.1)
    assert _of(result, "subject").cohort_level == CohortLevel.NATURAL_REGION_CROP
