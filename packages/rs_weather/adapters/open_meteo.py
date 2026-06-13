"""OpenMeteoWeatherAdapter: a real-world provider implementing WeatherPort (ADR 0004).
Communicates with Open-Meteo's free endpoints (archive-api for historical, api for forecasts),
resilient against transient network and 429/5xx errors using tenacity retries."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
from rs_core.config import Settings
from rs_core.logging import get_logger
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from rs_weather.port import WeatherPort
from rs_weather.types import DailyWeather, Location, WeatherProvenance, WeatherSeries

log = get_logger("rs_weather.open_meteo")

_PROVIDER = "open_meteo"


def _is_transient(exc: BaseException) -> bool:
    """Retry on transient network errors or HTTP status 429 (rate-limit) and 5xx (server error)."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


class OpenMeteoWeatherAdapter(WeatherPort):
    """Retrieves real-world daily weather and forecasts from the Open-Meteo API."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 4,
        wait_min: float = 1.0,
        wait_max: float = 20.0,
        wait_multiplier: float = 1.0,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._retrying = AsyncRetrying(
            retry=retry_if_exception(_is_transient),
            wait=wait_exponential(multiplier=wait_multiplier, min=wait_min, max=wait_max),
            stop=stop_after_attempt(max_attempts),
            reraise=True,
        )

        base = settings.weather_api_url.rstrip("/") if settings.weather_api_url else ""
        if base:
            self._archive_url = f"{base}/v1/archive"
            self._forecast_url = f"{base}/v1/forecast"
        else:
            self._archive_url = "https://archive-api.open-meteo.com/v1/archive"
            self._forecast_url = "https://api.open-meteo.com/v1/forecast"

    async def close(self) -> None:
        """Close the underlying HTTP client if we created and own it."""
        if self._owns_client:
            await self._client.aclose()

    async def daily(self, location: Location, start: date, end: date) -> WeatherSeries:
        if end < start:
            raise ValueError("daily() needs end >= start")

        params = {
            "latitude": location.lat,
            "longitude": location.lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": "auto",
        }

        async def _call():
            resp = await self._client.get(self._archive_url, params=params)
            resp.raise_for_status()
            return resp.json()

        try:
            async for attempt in self._retrying:
                with attempt:
                    data = await _call()
        except Exception as e:
            log.error(
                "open_meteo.daily_failed", location=location, start=start, end=end, error=str(e)
            )
            raise

        return self._parse_response(location, data, forecast=False)

    async def forecast(self, location: Location, *, days: int) -> WeatherSeries:
        if days <= 0:
            raise ValueError("forecast() needs days > 0")

        params = {
            "latitude": location.lat,
            "longitude": location.lon,
            "forecast_days": days,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": "auto",
        }

        async def _call():
            resp = await self._client.get(self._forecast_url, params=params)
            resp.raise_for_status()
            return resp.json()

        try:
            async for attempt in self._retrying:
                with attempt:
                    data = await _call()
        except Exception as e:
            log.error("open_meteo.forecast_failed", location=location, days=days, error=str(e))
            raise

        return self._parse_response(location, data, forecast=True)

    def _parse_response(self, location: Location, data: dict, *, forecast: bool) -> WeatherSeries:
        daily_data = data.get("daily") or {}
        times = daily_data.get("time") or []
        tmaxs = daily_data.get("temperature_2m_max") or []
        tmins = daily_data.get("temperature_2m_min") or []
        precips = daily_data.get("precipitation_sum") or []

        days: list[DailyWeather] = []
        for i in range(len(times)):
            day_val = date.fromisoformat(times[i])
            tmax = float(tmaxs[i]) if tmaxs[i] is not None else 0.0
            tmin = float(tmins[i]) if tmins[i] is not None else 0.0
            precip = float(precips[i]) if precips[i] is not None else 0.0
            days.append(DailyWeather(date=day_val, tmin_c=tmin, tmax_c=tmax, precip_mm=precip))

        return WeatherSeries(
            location=location,
            days=days,
            provenance=WeatherProvenance(
                provider=_PROVIDER,
                location=location,
                accessed_at=datetime.now(UTC),
                forecast=forecast,
            ),
        )
