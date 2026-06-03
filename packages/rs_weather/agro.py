"""Agronomy math over a weather series: growing-degree-days, reference evapotranspiration (ET0),
and rainfall accumulation. Pure functions (no network, no provider), so they are unit-tested with
synthetic series and reused by every adapter and by the alerts/interpretation layers.

ET0 uses the FAO-56 Hargreaves equation, which needs only tmin/tmax (and latitude + day-of-year for
the extraterrestrial radiation), so it works from the same minimal daily series the mock and any
real provider supply, with no extra inputs."""

from __future__ import annotations

import math
from collections.abc import Sequence

from rs_weather.types import DailyWeather

# FAO-56 base temperature for many cereals; callers override per crop. Maize is commonly 10 C.
DEFAULT_GDD_BASE_C = 10.0
_GSC = 0.0820  # solar constant, MJ m^-2 min^-1 (FAO-56)
_MJ_TO_MM = 0.408  # MJ m^-2 day^-1 -> mm day^-1 equivalent evaporation (FAO-56)


def growing_degree_days(
    tmin_c: float,
    tmax_c: float,
    *,
    base_c: float = DEFAULT_GDD_BASE_C,
    upper_c: float | None = None,
) -> float:
    """GDD for one day: the daily mean temperature minus `base_c`, floored at 0. With an `upper_c`
    cap (the modified method), tmin and tmax are clamped to `upper_c` before averaging, so heat
    above the crop's upper threshold does not over-count."""
    lo, hi = tmin_c, tmax_c
    if upper_c is not None:
        lo = min(lo, upper_c)
        hi = min(hi, upper_c)
    return max(0.0, (lo + hi) / 2.0 - base_c)


def accumulate_gdd(
    series: Sequence[DailyWeather],
    *,
    base_c: float = DEFAULT_GDD_BASE_C,
    upper_c: float | None = None,
) -> float:
    """Total GDD over a series (e.g. emergence to date), the standard heat-unit driver of crop
    development that EOSDA and similar tools track."""
    return sum(
        growing_degree_days(d.tmin_c, d.tmax_c, base_c=base_c, upper_c=upper_c) for d in series
    )


def total_precip_mm(series: Sequence[DailyWeather]) -> float:
    """Cumulative rainfall over a series, in mm."""
    return sum(d.precip_mm for d in series)


def extraterrestrial_radiation(latitude_deg: float, day_of_year: int) -> float:
    """Extraterrestrial radiation Ra (MJ m^-2 day^-1) for a latitude and day of year, per FAO-56.
    Drives the Hargreaves ET0. The sunset-hour-angle term is clamped so polar day/night does not
    produce a domain error (irrelevant for Zimbabwe, but kept correct)."""
    phi = math.radians(latitude_deg)
    j = day_of_year
    dr = 1.0 + 0.033 * math.cos(2.0 * math.pi / 365.0 * j)  # inverse Earth-Sun distance
    decl = 0.409 * math.sin(2.0 * math.pi / 365.0 * j - 1.39)  # solar declination
    sunset = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(decl))))
    return (
        (24.0 * 60.0 / math.pi)
        * _GSC
        * dr
        * (
            sunset * math.sin(phi) * math.sin(decl)
            + math.cos(phi) * math.cos(decl) * math.sin(sunset)
        )
    )


def hargreaves_et0(
    tmin_c: float,
    tmax_c: float,
    *,
    latitude_deg: float,
    day_of_year: int,
    tmean_c: float | None = None,
) -> float:
    """Reference evapotranspiration ET0 (mm day^-1) by the FAO-56 Hargreaves equation:
    `0.0023 (Tmean + 17.8) sqrt(Tmax - Tmin) Ra`, with Ra converted from MJ to mm. A negative
    diurnal range (bad input) is treated as 0."""
    tmean = tmean_c if tmean_c is not None else (tmin_c + tmax_c) / 2.0
    ra_mm = _MJ_TO_MM * extraterrestrial_radiation(latitude_deg, day_of_year)
    diurnal = max(0.0, tmax_c - tmin_c)
    return 0.0023 * (tmean + 17.8) * math.sqrt(diurnal) * ra_mm


def accumulate_et0(series: Sequence[DailyWeather], *, latitude_deg: float) -> float:
    """Total reference ET0 (mm) over a series: the sum of each day's FAO-56 Hargreaves ET0, with
    that day's day-of-year driving its extraterrestrial radiation. Paired with `total_precip_mm` it
    feeds the water-deficit alert."""
    return sum(
        hargreaves_et0(
            d.tmin_c,
            d.tmax_c,
            latitude_deg=latitude_deg,
            day_of_year=d.date.timetuple().tm_yday,
            tmean_c=d.tmean_c,
        )
        for d in series
    )
