from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from rs_activity.types import ActivityLog, ActivityType
from rs_core.models import Analysis, Farm, Field
from rs_interpret.grounding import aggregate_grounding_data
from rs_weather.types import DailyWeather, Location, WeatherProvenance, WeatherSeries


class FakeWeatherPort:
    def __init__(self, days: list[DailyWeather]):
        self.days = days

    async def daily(self, location: Location, start: date, end: date) -> WeatherSeries:
        return WeatherSeries(
            location=location,
            days=self.days,
            provenance=WeatherProvenance("mock", location, None),
        )

    async def forecast(self, location: Location, *, days: int) -> WeatherSeries:
        raise NotImplementedError()


class FakeActivityLogPort:
    def __init__(self, logs: list[ActivityLog]):
        self._logs = logs

    async def logs_for_field(
        self, canonical_farm_id: str, field_id: str, start: date, end: date
    ) -> list[ActivityLog]:
        return [log for log in self._logs if start <= log.date <= end]


@pytest.mark.asyncio
async def test_aggregate_grounding_data() -> None:
    # 1. Mock DB session and return values
    session = AsyncMock()

    # Target analysis row
    analysis = Analysis(
        field_id="fld-1",
        scene_id="scene-1",
        pass_date=date(2025, 6, 15),
        index_name="ndvi",
    )

    # Mock field and farm objects
    field = Field(canonical_field_id="cfld-1", crop="maize")
    farm = Farm(canonical_farm_id="cfarm-1", centroid_lat=-17.83, centroid_lon=31.05)

    # Mock database execute return value
    mock_result = MagicMock()
    mock_result.first.return_value = (field, farm)
    session.execute.return_value = mock_result

    # 2. Mock weather series
    weather_days = [
        DailyWeather(date(2025, 6, 1), 10.0, 20.0, 5.0),  # mean 15, base 10 = 5 GDD, 5mm precip
        DailyWeather(date(2025, 6, 14), 12.0, 22.0, 10.0),  # mean 17, base 10 = 7 GDD, 10mm precip
    ]
    weather_port = FakeWeatherPort(weather_days)

    # 3. Mock activity logs
    all_logs = [
        ActivityLog("cfarm-1", "cfld-1", date(2025, 6, 5), ActivityType.PLANTING, "maize"),
        ActivityLog("cfarm-1", "cfld-1", date(2025, 6, 10), ActivityType.FERTILIZER, None),
        ActivityLog("cfarm-1", "cfld-1", date(2025, 6, 12), ActivityType.OTHER, None),  # filtered out  # noqa: E501
    ]
    activity_port = FakeActivityLogPort(all_logs)

    # 4. Call helper function
    gdd, precip, logs = await aggregate_grounding_data(
        session, analysis, weather_port, activity_port
    )

    # 5. Assertions
    # (20+10)/2-10 = 5 GDD; (22+12)/2-10 = 7 GDD; 5 + 7 = 12 GDD
    assert gdd == 12.0
    assert precip == 15.0
    assert len(logs) == 2
    assert logs[0].activity == ActivityType.PLANTING
    assert logs[1].activity == ActivityType.FERTILIZER
