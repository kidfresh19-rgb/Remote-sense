"""The WeatherPort: the only seam between remote-sense and any weather source (CLAUDE.md invariant
1, the same ports-and-adapters discipline as imagery and the gateway). Callers depend on this
interface; provider endpoints, auth and SDKs live only in adapters, selected by config."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from rs_weather.types import Location, WeatherSeries


class WeatherPort(ABC):
    """Two operations: historical/current `daily` weather and a short-range `forecast`. Every
    adapter normalises its output to `WeatherSeries` so the agronomy math is provider-agnostic."""

    @abstractmethod
    async def daily(self, location: Location, start: date, end: date) -> WeatherSeries:
        """Daily weather for the location over [start, end] inclusive (historical and current)."""

    @abstractmethod
    async def forecast(self, location: Location, *, days: int) -> WeatherSeries:
        """Daily forecast for the next `days` days starting today."""
