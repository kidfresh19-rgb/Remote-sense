"""Contract and integration tests for the Open-Meteo weather adapter. Zero network: mock transport."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from rs_core.config import Settings
from rs_weather.adapters.open_meteo import OpenMeteoWeatherAdapter
from rs_weather.types import Location

_LOC = Location(lat=-17.83, lon=31.05)


def _mock_daily_response() -> dict:
    return {
        "daily": {
            "time": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "temperature_2m_max": [25.0, 26.5, 27.0],
            "temperature_2m_min": [15.0, 14.5, 16.0],
            "precipitation_sum": [0.0, 1.2, 0.0],
        }
    }


def _transport(response: httpx.Response) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    return httpx.MockTransport(handler), requests


async def test_daily_queries_archive_and_parses():
    resp_data = _mock_daily_response()
    mock_response = httpx.Response(200, json=resp_data)
    transport, requests = _transport(mock_response)
    client = httpx.AsyncClient(transport=transport)

    settings = Settings(weather_api_url="http://mock-api.example")
    adapter = OpenMeteoWeatherAdapter(settings, client=client)

    series = await adapter.daily(_LOC, date(2024, 1, 1), date(2024, 1, 3))

    assert len(requests) == 1
    req = requests[0]
    assert req.url.path == "/v1/archive"
    assert req.url.params["latitude"] == "-17.83"
    assert req.url.params["longitude"] == "31.05"
    assert req.url.params["start_date"] == "2024-01-01"
    assert req.url.params["end_date"] == "2024-01-03"

    assert len(series.days) == 3
    assert [d.date for d in series.days] == [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    assert series.days[0].tmax_c == 25.0
    assert series.days[0].tmin_c == 15.0
    assert series.days[0].precip_mm == 0.0
    assert series.days[1].precip_mm == 1.2
    assert series.provenance.provider == "open_meteo"
    assert series.provenance.forecast is False


async def test_forecast_queries_forecast_endpoint_and_parses():
    resp_data = _mock_daily_response()
    mock_response = httpx.Response(200, json=resp_data)
    transport, requests = _transport(mock_response)
    client = httpx.AsyncClient(transport=transport)

    settings = Settings(weather_api_url="http://mock-api.example")
    adapter = OpenMeteoWeatherAdapter(settings, client=client)

    series = await adapter.forecast(_LOC, days=3)

    assert len(requests) == 1
    req = requests[0]
    assert req.url.path == "/v1/forecast"
    assert req.url.params["latitude"] == "-17.83"
    assert req.url.params["longitude"] == "31.05"
    assert req.url.params["forecast_days"] == "3"

    assert len(series.days) == 3
    assert series.provenance.forecast is True


async def test_transient_failures_are_retried():
    resp_data = _mock_daily_response()
    responses = [
        httpx.Response(500, text="internal server error"),
        httpx.Response(429, text="too many requests"),
        httpx.Response(200, json=resp_data),
    ]

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        resp = responses[calls]
        calls += 1
        return resp

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    settings = Settings(weather_api_url="http://mock-api.example")
    adapter = OpenMeteoWeatherAdapter(settings, client=client, wait_min=0.01, wait_multiplier=0.01)

    series = await adapter.daily(_LOC, date(2024, 1, 1), date(2024, 1, 3))
    assert calls == 3
    assert len(series.days) == 3


async def test_non_transient_failure_fails_immediately():
    mock_response = httpx.Response(404, text="not found")
    transport, requests = _transport(mock_response)
    client = httpx.AsyncClient(transport=transport)

    settings = Settings(weather_api_url="http://mock-api.example")
    adapter = OpenMeteoWeatherAdapter(settings, client=client, wait_min=0.01, wait_multiplier=0.01)

    with pytest.raises(httpx.HTTPStatusError):
        await adapter.daily(_LOC, date(2024, 1, 1), date(2024, 1, 3))

    assert len(requests) == 1
