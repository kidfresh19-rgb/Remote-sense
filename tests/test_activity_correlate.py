"""Tests for AgriTrack activity correlation (Tier 2, T2.1). Pure: activities + an NDVI series in,
ActivityResponses out, zero network and zero DB."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from rs_activity.correlate import correlate
from rs_activity.types import ActivityLog, ActivityType

_D0 = date(2024, 1, 1)


def _act(day_offset: int, activity: ActivityType) -> ActivityLog:
    return ActivityLog("FARM-1", "FIELD-1", _D0 + timedelta(days=day_offset), activity)


def _series(values: list[float]) -> tuple[list[date], list[float]]:
    dates = [_D0 + timedelta(days=10 * i) for i in range(len(values))]
    return dates, values


def test_correlate_measures_response_after_an_activity():
    # Passes at day 0,10,20,30,40 with NDVI rising after a day-12 fertiliser application.
    dates, ndvi = _series([0.30, 0.32, 0.45, 0.55, 0.60])
    [resp] = correlate([_act(12, ActivityType.FERTILIZER)], dates, ndvi, window_days=30)
    assert resp.baseline_date == dates[1]  # nearest pass on/before day 12 = day 10
    assert resp.baseline_ndvi == pytest.approx(0.32)
    # Passes in (12, 42]: day 20, 30, 40 -> mean of 0.45, 0.55, 0.60.
    assert resp.passes_after == 3
    assert resp.response_ndvi == pytest.approx((0.45 + 0.55 + 0.60) / 3)
    assert resp.delta == pytest.approx(resp.response_ndvi - 0.32)


def test_correlate_no_baseline_before_first_pass():
    dates, ndvi = _series([0.4, 0.5, 0.6])  # first pass at day 0
    [resp] = correlate([_act(-5, ActivityType.PLANTING)], dates, ndvi)  # before any pass
    assert resp.baseline_ndvi is None
    assert resp.delta is None
    assert resp.passes_after >= 1  # still has post-activity passes


def test_correlate_no_passes_in_window():
    dates, ndvi = _series([0.4, 0.5])  # passes at day 0 and 10
    [resp] = correlate([_act(0, ActivityType.SPRAY)], dates, ndvi, window_days=5)
    assert resp.passes_after == 0
    assert resp.response_ndvi is None
    assert resp.delta is None
    assert resp.baseline_ndvi == pytest.approx(0.4)  # the day-0 pass is on/before the activity


def test_correlate_returns_one_response_per_activity_in_order():
    dates, ndvi = _series([0.3, 0.4, 0.5, 0.6])
    acts = [_act(5, ActivityType.FERTILIZER), _act(25, ActivityType.IRRIGATION)]
    responses = correlate(acts, dates, ndvi)
    assert [r.activity.activity for r in responses] == [
        ActivityType.FERTILIZER,
        ActivityType.IRRIGATION,
    ]


def test_correlate_drops_nan_passes():
    dates, ndvi = _series([0.3, float("nan"), 0.5, 0.6])
    [resp] = correlate([_act(5, ActivityType.FERTILIZER)], dates, ndvi, window_days=40)
    # The NaN pass (day 10) is ignored; baseline is the day-0 pass.
    assert resp.baseline_ndvi == pytest.approx(0.3)
    assert resp.passes_after == 2  # day 20 and 30
