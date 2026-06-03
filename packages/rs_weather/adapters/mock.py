"""MockWeatherAdapter: deterministic synthetic weather with no network and no credentials, so the
whole agronomy stack is testable against the WeatherPort contract from day one (the same role the
imagery MockAdapter plays). Values are plausible for the Zimbabwe highveld and reproducible from the
location and date."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import numpy as np

from rs_weather.port import WeatherPort
from rs_weather.types import DailyWeather, Location, WeatherProvenance, WeatherSeries

_PROVIDER = "mock"


def _seed(location: Location, day: date) -> int:
    key = f"{location.lat:.4f},{location.lon:.4f},{day.isoformat()}"
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")


def _day(location: Location, day: date) -> DailyWeather:
    rng = np.random.default_rng(_seed(location, day))
    tmin = float(rng.uniform(10.0, 18.0))
    tmax = tmin + float(rng.uniform(6.0, 14.0))
    precip = round(float(rng.uniform(0.0, 22.0)), 1) if rng.random() < 0.3 else 0.0
    return DailyWeather(date=day, tmin_c=round(tmin, 1), tmax_c=round(tmax, 1), precip_mm=precip)


class MockWeatherAdapter(WeatherPort):
    async def daily(self, location: Location, start: date, end: date) -> WeatherSeries:
        if end < start:
            raise ValueError("daily() needs end >= start")
        days: list[DailyWeather] = []
        cursor = start
        while cursor <= end:
            days.append(_day(location, cursor))
            cursor += timedelta(days=1)
        return WeatherSeries(
            location=location,
            days=days,
            provenance=WeatherProvenance(
                provider=_PROVIDER, location=location, accessed_at=datetime.now(UTC)
            ),
        )

    async def forecast(self, location: Location, *, days: int) -> WeatherSeries:
        if days <= 0:
            raise ValueError("forecast() needs days > 0")
        today = datetime.now(UTC).date()
        series = await self.daily(location, today, today + timedelta(days=days - 1))
        return WeatherSeries(
            location=location,
            days=series.days,
            provenance=WeatherProvenance(
                provider=_PROVIDER, location=location, accessed_at=datetime.now(UTC), forecast=True
            ),
        )
