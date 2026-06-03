"""rs_weather: the weather access layer (improvement plan Tier 1). All weather data flows through
WeatherPort; provider specifics live only in adapters, selected by config. Pure agronomy math
(growing-degree-days, Hargreaves ET0, rainfall accumulation) lives in `agro` and is reused across
adapters and the alerts/interpretation layers."""

from rs_weather.adapters import MockWeatherAdapter
from rs_weather.agro import (
    accumulate_et0,
    accumulate_gdd,
    extraterrestrial_radiation,
    growing_degree_days,
    hargreaves_et0,
    total_precip_mm,
)
from rs_weather.port import WeatherPort
from rs_weather.registry import get_weather_adapter
from rs_weather.types import DailyWeather, Location, WeatherProvenance, WeatherSeries

__all__ = [
    "WeatherPort",
    "MockWeatherAdapter",
    "get_weather_adapter",
    "DailyWeather",
    "Location",
    "WeatherProvenance",
    "WeatherSeries",
    "growing_degree_days",
    "accumulate_gdd",
    "accumulate_et0",
    "total_precip_mm",
    "extraterrestrial_radiation",
    "hargreaves_et0",
]
