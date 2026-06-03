"""The ActivityLogPort: the only seam between remote-sense and the AgriTrack activity feed
(CLAUDE.md invariant 1, the same ports-and-adapters discipline as imagery and weather). Access is
read-only (invariant 6); provider specifics live only in adapters, selected by config."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from rs_activity.types import ActivityLog


class ActivityLogPort(ABC):
    """Read-only access to AgriTrack field-activity logs, joined on the canonical farm/field id."""

    @abstractmethod
    async def logs_for_field(
        self, canonical_farm_id: str, field_id: str, start: date, end: date
    ) -> list[ActivityLog]:
        """Activity logs for one field over [start, end] inclusive, oldest first."""
