"""rs_activity: read-only access to AgriTrack field-activity logs and their correlation against the
satellite signal (improvement plan Tier 2, the differentiator). Logs flow through ActivityLogPort;
provider specifics live only in adapters, selected by config. Correlation math lives in `correlate`
and is pure, reused by the workspace overlay and the interpretation layer."""

from rs_activity.adapters import MockActivityAdapter
from rs_activity.correlate import (
    DEFAULT_RESPONSE_WINDOW_DAYS,
    ActivityResponse,
    correlate,
)
from rs_activity.port import ActivityLogPort
from rs_activity.registry import get_activity_adapter
from rs_activity.types import ActivityLog, ActivityProvenance, ActivityType

__all__ = [
    "ActivityLogPort",
    "MockActivityAdapter",
    "get_activity_adapter",
    "ActivityLog",
    "ActivityType",
    "ActivityProvenance",
    "ActivityResponse",
    "correlate",
    "DEFAULT_RESPONSE_WINDOW_DAYS",
]
