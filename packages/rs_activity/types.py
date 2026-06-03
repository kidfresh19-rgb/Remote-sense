"""AgriTrack field-activity logs (improvement plan Tier 2). These are the farmer app's records of
what happened on the ground (planting, fertiliser, irrigation, spraying, harvest, scouting). They
are **read-only** here: split-ownership (CLAUDE.md invariant 6) means AgriTrack owns this data and
remote-sense only joins it, on the canonical farm/field id, to correlate interventions against the
satellite signal. remote-sense never writes a field-activity log."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class ActivityType(StrEnum):
    PLANTING = "planting"
    FERTILIZER = "fertilizer"
    IRRIGATION = "irrigation"
    SPRAY = "spray"
    HARVEST = "harvest"
    SCOUTING = "scouting"
    OTHER = "other"


@dataclass(frozen=True)
class ActivityLog:
    """One field-activity entry. `field_id` is None for a farm-level activity. Dates are local
    calendar dates (the convention activities are recorded in), correlated against pass dates."""

    canonical_farm_id: str
    field_id: str | None
    date: date
    activity: ActivityType
    detail: str | None = None


@dataclass(frozen=True)
class ActivityProvenance:
    """Travels with a fetched batch so a correlation is attributable to a source and access time."""

    provider: str
    accessed_at: datetime
