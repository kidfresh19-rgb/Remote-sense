"""Pure tests for Ward Watch cohort strata (PRD 0003 §6.3, backlog 0032): the planting-window bucket
and the peer-cohort key. Zero DB, zero network - synthetic dates and strata only."""

from __future__ import annotations

from datetime import date

import pytest
from rs_core.strata import CohortKey, PlantingWindow, bucket_planting_window, cohort_key


@pytest.mark.parametrize(
    ("planting_date", "expected"),
    [
        (date(2024, 10, 25), PlantingWindow.EARLY),  # dry-planted before the main rains
        (date(2024, 11, 14), PlantingWindow.EARLY),  # day before the main window opens
        (date(2024, 11, 15), PlantingWindow.MAIN),  # main window opens
        (date(2024, 12, 31), PlantingWindow.MAIN),  # still main at year end
        (date(2025, 1, 1), PlantingWindow.LATE),  # late window opens in the new calendar year
        (date(2025, 2, 10), PlantingWindow.LATE),  # replant / late starter
    ],
)
def test_bucket_planting_window(planting_date: date, expected: PlantingWindow) -> None:
    assert bucket_planting_window(planting_date) is expected


def test_season_does_not_wrap_the_calendar_year() -> None:
    # A January date sorts AFTER a November one in the same season, not before it.
    november = bucket_planting_window(date(2024, 11, 20))
    january = bucket_planting_window(date(2025, 1, 20))
    assert november is PlantingWindow.MAIN
    assert january is PlantingWindow.LATE


def test_empty_window_starts_rejected() -> None:
    with pytest.raises(ValueError, match="window_starts"):
        bucket_planting_window(date(2024, 11, 20), window_starts=[])


def _key(**overrides) -> CohortKey:
    base = dict(
        dominant_crop="maize",
        natural_region="region iii",
        ward="ward 3",
        planting_window=PlantingWindow.MAIN,
        size_class="small_holding",
    )
    base.update(overrides)
    return cohort_key(**base)


def test_same_strata_make_the_same_cohort() -> None:
    assert _key() == _key()
    assert hash(_key()) == hash(_key())


def test_different_planting_window_is_a_different_cohort() -> None:
    # AC: two plots of the same crop in different planting windows land in different cohorts.
    assert _key(planting_window=PlantingWindow.MAIN) != _key(planting_window=PlantingWindow.LATE)


def test_different_ward_is_a_different_cohort() -> None:
    assert _key(ward="ward 3") != _key(ward="ward 7")


def test_different_size_bucket_is_a_different_cohort() -> None:
    # The comparison-groups size bucket is carried through the Ward Watch key.
    assert _key(size_class="backyard") != _key(size_class="medium")


def test_key_is_normalised_so_casing_never_splits_a_cohort() -> None:
    loud = cohort_key(
        dominant_crop="  MAIZE ",
        natural_region="Region III",
        ward="Ward 3",
        planting_window="main",
        size_class="SMALL_HOLDING",
    )
    assert loud == _key()
    assert loud.key_id() == "maize|region iii|ward 3|main|small_holding"


def test_planting_window_accepts_the_enum_value_string() -> None:
    assert (
        cohort_key(
            dominant_crop="maize",
            natural_region="region iii",
            ward="ward 3",
            planting_window="late",
        ).planting_window
        is PlantingWindow.LATE
    )


def test_blank_required_stratum_rejected() -> None:
    with pytest.raises(ValueError, match="required"):
        _key(ward="  ")


def test_unknown_planting_window_rejected() -> None:
    with pytest.raises(ValueError):
        _key(planting_window="midseason")


def test_size_class_is_optional() -> None:
    key = cohort_key(
        dominant_crop="sorghum",
        natural_region="region iv",
        ward="ward 9",
        planting_window=PlantingWindow.EARLY,
    )
    assert key.size_class is None
    assert key.key_id().endswith("|*")
