"""Tests for land-surface phenology (Tier 1, T1.4). Pure: an NDVI time series in, a Phenology out,
zero network and zero DB."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from rs_analysis.phenology import phenology

_D0 = date(2024, 1, 1)


def _weekly(values: list[float]) -> tuple[list[date], list[float]]:
    dates = [_D0 + timedelta(days=7 * i) for i in range(len(values))]
    return dates, values


def test_full_season_has_peak_greenup_and_senescence():
    # A bell-shaped season: rise 0.2 -> 0.8, then fall back to 0.2.
    dates, ndvi = _weekly([0.20, 0.35, 0.55, 0.80, 0.55, 0.35, 0.20])
    p = phenology(dates, ndvi)
    assert p.peak_value == pytest.approx(0.80)
    assert p.peak_date == dates[3]
    assert p.baseline == pytest.approx(0.20)
    assert p.amplitude == pytest.approx(0.60)
    # Half-amplitude threshold 0.5: greenup is the first pass >= 0.5 after a lower one (wk2),
    # senescence the first pass <= 0.5 after the peak (wk5).
    assert p.start_of_season == dates[2]
    assert p.end_of_season == dates[5]
    assert p.length_days == (dates[5] - dates[2]).days
    assert p.time_integrated > 0.0


def test_incomplete_season_rising_only_has_no_senescence():
    dates, ndvi = _weekly([0.20, 0.40, 0.60, 0.80])  # ends at the peak
    p = phenology(dates, ndvi)
    assert p.start_of_season is not None
    assert p.end_of_season is None
    assert p.length_days is None


def test_series_starting_green_has_no_observed_greenup():
    dates, ndvi = _weekly([0.80, 0.75, 0.55, 0.30])  # already green, then declines
    p = phenology(dates, ndvi)
    assert p.start_of_season is None  # greenup was not observed
    assert p.end_of_season is not None  # senescence is


def test_nan_values_are_dropped():
    dates, ndvi = _weekly([0.20, float("nan"), 0.55, 0.80, float("nan"), 0.30])
    p = phenology(dates, ndvi)
    assert p.peak_value == pytest.approx(0.80)


def test_too_few_observations_raises():
    dates, ndvi = _weekly([0.3, 0.6])
    with pytest.raises(ValueError, match="at least 3"):
        phenology(dates, ndvi)
