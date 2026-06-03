"""Contract tests for the mock activity adapter and the registry switch (Tier 2, T2.1). Zero
network."""

from __future__ import annotations

from datetime import date

import pytest
from rs_activity import MockActivityAdapter, get_activity_adapter
from rs_activity.types import ActivityType
from rs_core.config import ActivityAdapter, Settings


async def test_mock_logs_are_within_range_and_ordered():
    logs = await MockActivityAdapter().logs_for_field(
        "FARM-1", "FIELD-1", date(2024, 1, 1), date(2024, 6, 1)
    )
    assert logs, "the mock should produce a plausible season of activities"
    assert [log.date for log in logs] == sorted(log.date for log in logs)  # oldest first
    assert all(date(2024, 1, 1) <= log.date <= date(2024, 6, 1) for log in logs)
    assert logs[0].activity == ActivityType.PLANTING
    assert all(log.field_id == "FIELD-1" for log in logs)


async def test_mock_is_deterministic_per_field():
    a = await MockActivityAdapter().logs_for_field(
        "F", "FIELD-1", date(2024, 1, 1), date(2024, 6, 1)
    )
    b = await MockActivityAdapter().logs_for_field(
        "F", "FIELD-1", date(2024, 1, 1), date(2024, 6, 1)
    )
    assert a == b


async def test_mock_rejects_reversed_range():
    with pytest.raises(ValueError):
        await MockActivityAdapter().logs_for_field(
            "F", "FIELD-1", date(2024, 6, 1), date(2024, 1, 1)
        )


def test_registry_returns_mock_and_defers_gateway():
    assert isinstance(
        get_activity_adapter(Settings(activity_adapter=ActivityAdapter.MOCK)), MockActivityAdapter
    )
    with pytest.raises(NotImplementedError):
        get_activity_adapter(Settings(activity_adapter=ActivityAdapter.GATEWAY))
