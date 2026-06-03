"""Tests for the pure agronomy math (Tier 1): growing-degree-days, rainfall accumulation, and the
FAO-56 Hargreaves ET0 (with its extraterrestrial-radiation term). Zero network."""

from __future__ import annotations

from datetime import date

import pytest
from rs_weather.agro import (
    accumulate_et0,
    accumulate_gdd,
    extraterrestrial_radiation,
    growing_degree_days,
    hargreaves_et0,
    total_precip_mm,
)
from rs_weather.types import DailyWeather


def test_gdd_subtracts_base_from_mean():
    assert growing_degree_days(10.0, 20.0, base_c=10.0) == pytest.approx(5.0)


def test_gdd_floors_at_zero_for_cold_days():
    assert growing_degree_days(5.0, 8.0, base_c=10.0) == 0.0


def test_gdd_upper_cap_clamps_heat():
    # Without a cap: mean 30, GDD 20. With a 30 C cap, tmax is clamped to 30 -> mean 25, GDD 15.
    assert growing_degree_days(20.0, 40.0, base_c=10.0) == pytest.approx(20.0)
    assert growing_degree_days(20.0, 40.0, base_c=10.0, upper_c=30.0) == pytest.approx(15.0)


def test_accumulate_gdd_and_precip():
    series = [
        DailyWeather(date(2024, 1, 1), tmin_c=12.0, tmax_c=24.0, precip_mm=0.0),  # GDD 8
        DailyWeather(date(2024, 1, 2), tmin_c=14.0, tmax_c=26.0, precip_mm=5.5),  # GDD 10
    ]
    assert accumulate_gdd(series, base_c=10.0) == pytest.approx(18.0)
    assert total_precip_mm(series) == pytest.approx(5.5)


def test_extraterrestrial_radiation_equator_equinox():
    # At the equator near an equinox Ra is near its maximum, ~37-38 MJ m^-2 day^-1 (FAO-56).
    ra = extraterrestrial_radiation(0.0, 80)
    assert 36.0 < ra < 39.0


def test_extraterrestrial_radiation_southern_seasonality():
    # Zimbabwe (lat ~ -18): more incoming radiation in January (southern summer) than July (winter).
    summer = extraterrestrial_radiation(-18.0, 15)
    winter = extraterrestrial_radiation(-18.0, 196)
    assert summer > winter


def test_hargreaves_zero_diurnal_range_is_zero():
    assert hargreaves_et0(20.0, 20.0, latitude_deg=-18.0, day_of_year=15) == 0.0


def test_hargreaves_is_plausible_for_a_summer_day():
    # A warm highveld summer day should yield a few mm/day of reference ET.
    et0 = hargreaves_et0(16.0, 30.0, latitude_deg=-18.0, day_of_year=15)
    assert 2.0 < et0 < 12.0


def test_accumulate_et0_sums_daily_hargreaves():
    series = [
        DailyWeather(date(2024, 1, 1), tmin_c=16.0, tmax_c=30.0, precip_mm=0.0),
        DailyWeather(date(2024, 1, 2), tmin_c=16.0, tmax_c=30.0, precip_mm=0.0),
    ]
    total = accumulate_et0(series, latitude_deg=-18.0)
    one_day = hargreaves_et0(16.0, 30.0, latitude_deg=-18.0, day_of_year=1)
    two_day = hargreaves_et0(16.0, 30.0, latitude_deg=-18.0, day_of_year=2)
    assert total == pytest.approx(one_day + two_day)
