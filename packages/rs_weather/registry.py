"""Weather adapter selection. The active adapter is a config switch (CLAUDE.md invariant 1), so the
weather source is fully reversible and never wired into downstream code, exactly like imagery."""

from __future__ import annotations

from rs_core.config import Settings, WeatherAdapter, get_settings

from rs_weather.adapters.mock import MockWeatherAdapter
from rs_weather.port import WeatherPort


def get_weather_adapter(settings: Settings | None = None) -> WeatherPort:
    settings = settings or get_settings()
    adapter = settings.weather_adapter

    if adapter is WeatherAdapter.MOCK:
        return MockWeatherAdapter()

    # A real provider (Open-Meteo is free and key-less) lands behind this same port, with HTTP
    # behind an injected httpx client like the CDSE STAC client (T1.1 follow-on).
    if adapter is WeatherAdapter.OPEN_METEO:
        from rs_weather.adapters.open_meteo import OpenMeteoWeatherAdapter

        return OpenMeteoWeatherAdapter(settings)

    raise ValueError(f"Unknown weather adapter: {adapter!r}")
