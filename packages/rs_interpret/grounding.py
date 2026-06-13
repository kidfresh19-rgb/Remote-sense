"""Grounding: turn the stored analysis outputs for one field/pass into a structured, deterministic
evidence block. The interpretation model sees ONLY this - the numbers plus their band
classification - so it can explain but never invent (risk #6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from rs_analysis import AnalysisOutput, confidence_for

from rs_interpret.thresholds import classify

if TYPE_CHECKING:
    from rs_activity.port import ActivityLogPort
    from rs_activity.types import ActivityLog
    from rs_core.models import Analysis
    from rs_weather.port import WeatherPort
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class IndexReading:
    """One index's field-mean value and its band classification (None when no clear pixels)."""

    index: str
    mean: float | None
    band: str | None
    note: str | None


@dataclass(frozen=True)
class Evidence:
    """Everything the model is allowed to reason from for one field/pass."""

    crop: str | None
    pass_date: date | None
    clear_fraction: float
    confidence: str
    readings: list[IndexReading]
    gdd_accumulation: float | None = None
    total_precipitation: float | None = None
    recent_activities: list[dict[str, Any]] | None = None


def _reading(output: AnalysisOutput, crop: str | None) -> IndexReading:
    mean = output.stats.mean
    if mean is None:
        return IndexReading(index=output.index_name, mean=None, band=None, note="no clear pixels")
    band = classify(output.index_name, mean, crop)
    return IndexReading(index=output.index_name, mean=mean, band=band.label, note=band.note)


def ground(
    outputs: Sequence[AnalysisOutput],
    *,
    crop: str | None = None,
    pass_date: date | None = None,
    gdd_accumulation: float | None = None,
    total_precipitation: float | None = None,
    recent_activities: list[dict[str, Any]] | None = None,
) -> Evidence:
    """Build the evidence block from one field/pass's per-index outputs. The pass-level clear
    fraction is the most conservative across indices (a cloudy 20 m index drags the whole pass's
    confidence down), and the confidence label is derived from it via the engine's thresholds."""
    if not outputs:
        raise ValueError("ground() needs at least one analysis output")
    clear = min(output.clear_fraction for output in outputs)
    return Evidence(
        crop=crop,
        pass_date=pass_date,
        clear_fraction=clear,
        confidence=confidence_for(clear),
        readings=[_reading(output, crop) for output in outputs],
        gdd_accumulation=gdd_accumulation,
        total_precipitation=total_precipitation,
        recent_activities=recent_activities,
    )


async def aggregate_grounding_data(
    session: AsyncSession,
    analysis: Analysis,
    weather_port: WeatherPort,
    activity_log_port: ActivityLogPort,
) -> tuple[float | None, float | None, list[ActivityLog]]:
    """Fetch preceding 14-day weather metrics (GDD accumulation, total precipitation) and preceding
    30-day activity logs (planting, fertilisation, spray, irrigation)."""
    from datetime import timedelta

    from rs_core.models import Farm, Field
    from rs_weather.agro import accumulate_gdd, total_precip_mm
    from rs_weather.types import Location
    from sqlalchemy import select

    # 1. Fetch Field and Farm details
    stmt = (
        select(Field, Farm)
        .join(Farm, Field.farm_id == Farm.id)
        .where(Field.id == analysis.field_id)
    )
    result = await session.execute(stmt)
    row = result.first()
    if not row:
        raise ValueError(f"Field not found for id {analysis.field_id}")
    field, farm = row

    # 2. Weather query: preceding 14 days (excluding pass date itself)
    start_weather = analysis.pass_date - timedelta(days=14)
    end_weather = analysis.pass_date - timedelta(days=1)
    location = Location(lat=farm.centroid_lat, lon=farm.centroid_lon)

    try:
        weather_series = await weather_port.daily(location, start_weather, end_weather)
        gdd = accumulate_gdd(weather_series.days)
        precip = total_precip_mm(weather_series.days)
    except Exception:
        gdd = None
        precip = None

    # 3. Activity logs query: preceding 30 days
    start_activity = analysis.pass_date - timedelta(days=30)
    end_activity = analysis.pass_date

    try:
        logs = await activity_log_port.logs_for_field(
            canonical_farm_id=farm.canonical_farm_id,
            field_id=field.canonical_field_id,
            start=start_activity,
            end=end_activity,
        )
        target_activities = {"planting", "fertilizer", "spray", "irrigation"}
        filtered_logs = [log for log in logs if log.activity in target_activities]
    except Exception:
        filtered_logs = []

    return gdd, precip, filtered_logs
