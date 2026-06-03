"""The normalized shapes every weather adapter returns. The contract mirrors the imagery access
layer: a daily series for a location, in known units, with a provenance tag, so downstream agronomy
math is identical regardless of which weather provider served the data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Location:
    """A point to query weather for: a field centroid in WGS84 (lat/lon degrees)."""

    lat: float
    lon: float


@dataclass(frozen=True)
class DailyWeather:
    """One day of weather for a location. Temperatures in degrees Celsius, precipitation in mm.
    `tmean_c` is the daily mean when the provider supplies it; otherwise `mean_c` falls back to the
    midpoint of tmin/tmax (the convention growing-degree-days assume)."""

    date: date
    tmin_c: float
    tmax_c: float
    precip_mm: float
    tmean_c: float | None = None

    @property
    def mean_c(self) -> float:
        return self.tmean_c if self.tmean_c is not None else (self.tmin_c + self.tmax_c) / 2.0


@dataclass(frozen=True)
class WeatherProvenance:
    """Travels with every series so a derived figure (GDD, ET0) is reproducible and attributable
    to a provider and an access time (the weather analogue of imagery provenance, invariant 5)."""

    provider: str
    location: Location
    accessed_at: datetime
    forecast: bool = False


@dataclass(frozen=True)
class WeatherSeries:
    """A daily weather series for one location, oldest day first, with provenance."""

    location: Location
    days: list[DailyWeather]
    provenance: WeatherProvenance
