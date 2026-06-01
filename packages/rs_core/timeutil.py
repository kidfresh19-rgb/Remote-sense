"""Time handling (S-4). The system stores UTC; analysts work in Central Africa Time (CAT, UTC+2,
no DST). Analyst-entered local ranges are resolved to UTC for querying; stored UTC instants are
formatted in CAT for display. Pure, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

CAT = timezone(timedelta(hours=2), "CAT")  # Central Africa Time, UTC+2, no daylight saving


def to_cat(instant: datetime) -> datetime:
    """A stored instant in CAT for display. A naive instant is read as UTC (the storage default)."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(CAT)


def _to_utc(local: datetime) -> datetime:
    if local.tzinfo is None:
        local = local.replace(tzinfo=CAT)
    return local.astimezone(UTC)


def cat_range_to_utc(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Resolve an analyst's CAT range to the UTC range to query on. Naive inputs are read as CAT;
    tz-aware inputs are converted from whatever zone they carry."""
    return _to_utc(start), _to_utc(end)
