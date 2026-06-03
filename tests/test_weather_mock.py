"""Contract tests for the mock weather adapter and the registry switch (Tier 1). Zero network."""

from __future__ import annotations

from datetime import date

import pytest
from rs_core.config import Settings, WeatherAdapter
from rs_weather import MockWeatherAdapter, get_weather_adapter
from rs_weather.types import Location

_LOC = Location(lat=-17.83, lon=31.05)  # Harare-ish


async def test_daily_returns_inclusive_ordered_series():
    adapter = MockWeatherAdapter()
    series = await adapter.daily(_LOC, date(2024, 1, 1), date(2024, 1, 5))
    assert len(series.days) == 5  # inclusive
    assert [d.date for d in series.days] == sorted(d.date for d in series.days)  # oldest first
    assert series.provenance.provider == "mock"
    assert series.provenance.forecast is False
    for d in series.days:
        assert d.tmax_c >= d.tmin_c
        assert d.precip_mm >= 0.0


async def test_daily_is_deterministic():
    adapter = MockWeatherAdapter()
    a = await adapter.daily(_LOC, date(2024, 1, 1), date(2024, 1, 3))
    b = await adapter.daily(_LOC, date(2024, 1, 1), date(2024, 1, 3))
    assert a.days == b.days  # seeded by location + date


async def test_daily_rejects_reversed_range():
    with pytest.raises(ValueError):
        await MockWeatherAdapter().daily(_LOC, date(2024, 1, 5), date(2024, 1, 1))


async def test_forecast_returns_n_days_flagged_forecast():
    series = await MockWeatherAdapter().forecast(_LOC, days=3)
    assert len(series.days) == 3
    assert series.provenance.forecast is True


def test_registry_returns_mock_and_defers_open_meteo():
    assert isinstance(
        get_weather_adapter(Settings(weather_adapter=WeatherAdapter.MOCK)), MockWeatherAdapter
    )
    with pytest.raises(NotImplementedError):
        get_weather_adapter(Settings(weather_adapter=WeatherAdapter.OPEN_METEO))
