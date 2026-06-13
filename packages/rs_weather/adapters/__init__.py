"""Adapters implementing WeatherPort. mock (testing/offline); a real provider (e.g. Open-Meteo)
lands behind the same port. The active one is selected by config in the registry."""

from rs_weather.adapters.mock import MockWeatherAdapter
from rs_weather.adapters.open_meteo import OpenMeteoWeatherAdapter

__all__ = ["MockWeatherAdapter", "OpenMeteoWeatherAdapter"]
