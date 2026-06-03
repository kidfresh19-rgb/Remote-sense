"""Correlate AgriTrack field activities against the NDVI timeline: for each activity, the baseline
NDVI on or before it and the vegetation response over the passes within a window after it. This is
the differentiator, joining what happened on the ground to what the satellite saw.

Pure: activities + an NDVI series in, `ActivityResponse`s out. No DB, no network. The series is the
same per-field pass series the alerts and phenology layers consume (primitive date/value pairs, so
rs_activity stays independent of rs_core/rs_analysis types)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from rs_activity.types import ActivityLog

DEFAULT_RESPONSE_WINDOW_DAYS = 30


@dataclass(frozen=True)
class ActivityResponse:
    """An activity paired with the vegetation response that followed it. `baseline_ndvi` is the
    nearest pass on/before the activity; `response_ndvi` is the mean NDVI of the passes in
    (activity, activity + window]; `delta` is their difference (None if either is missing)."""

    activity: ActivityLog
    baseline_date: date | None
    baseline_ndvi: float | None
    response_date: date | None
    response_ndvi: float | None
    delta: float | None
    passes_after: int


def correlate(
    activities: Sequence[ActivityLog],
    dates: Sequence[date],
    ndvi: Sequence[float],
    *,
    window_days: int = DEFAULT_RESPONSE_WINDOW_DAYS,
) -> list[ActivityResponse]:
    """One `ActivityResponse` per activity (in input order). Activities with no usable baseline or
    no post-activity pass still return, with None fields, so the timeline overlay shows every marker
    and annotates the ones it can measure."""
    series = sorted(
        (d, float(v))
        for d, v in zip(dates, ndvi, strict=True)
        if v is not None and not math.isnan(float(v))
    )
    out: list[ActivityResponse] = []
    for act in activities:
        before = [(d, v) for d, v in series if d <= act.date]
        after = [
            (d, v) for d, v in series if act.date < d <= act.date + timedelta(days=window_days)
        ]
        baseline_date, baseline_ndvi = before[-1] if before else (None, None)
        if after:
            response_date = after[-1][0]
            response_ndvi = sum(v for _, v in after) / len(after)
        else:
            response_date = None
            response_ndvi = None
        delta = (
            response_ndvi - baseline_ndvi
            if response_ndvi is not None and baseline_ndvi is not None
            else None
        )
        out.append(
            ActivityResponse(
                activity=act,
                baseline_date=baseline_date,
                baseline_ndvi=baseline_ndvi,
                response_date=response_date,
                response_ndvi=response_ndvi,
                delta=delta,
                passes_after=len(after),
            )
        )
    return out
