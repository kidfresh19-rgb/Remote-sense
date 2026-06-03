"""MockActivityAdapter: deterministic synthetic field-activity logs with no network, so the
correlation layer is testable against the ActivityLogPort contract offline (the activity analogue
of the imagery and weather mock adapters). A plausible smallholder season: a planting near the
start, then a few fertiliser / irrigation / spray events, reproducible from the field id."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np

from rs_activity.port import ActivityLogPort
from rs_activity.types import ActivityLog, ActivityType

_PROVIDER = "mock"
_SEASON_EVENTS = (ActivityType.FERTILIZER, ActivityType.IRRIGATION, ActivityType.SPRAY)


def _seed(field_id: str) -> int:
    return int.from_bytes(hashlib.sha256(field_id.encode()).digest()[:8], "big")


class MockActivityAdapter(ActivityLogPort):
    async def logs_for_field(
        self, canonical_farm_id: str, field_id: str, start: date, end: date
    ) -> list[ActivityLog]:
        if end < start:
            raise ValueError("logs_for_field needs end >= start")
        rng = np.random.default_rng(_seed(field_id))
        logs: list[ActivityLog] = []

        planting = start + timedelta(days=int(rng.integers(0, 10)))
        logs.append(
            ActivityLog(canonical_farm_id, field_id, planting, ActivityType.PLANTING, "maize")
        )
        cursor = planting + timedelta(days=int(rng.integers(15, 25)))
        i = 0
        while cursor <= end and i < len(_SEASON_EVENTS):
            logs.append(ActivityLog(canonical_farm_id, field_id, cursor, _SEASON_EVENTS[i], None))
            cursor += timedelta(days=int(rng.integers(15, 30)))
            i += 1

        return sorted((log for log in logs if start <= log.date <= end), key=lambda log: log.date)
