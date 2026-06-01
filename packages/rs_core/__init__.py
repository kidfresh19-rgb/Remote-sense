"""rs_core: shared foundations for remote-sense (config, logging, telemetry, the PostGIS
data model and the pure geospatial helpers used by ingestion)."""

from rs_core.config import Settings, get_settings
from rs_core.db import Base, get_engine, get_session, get_sessionmaker
from rs_core.logging import configure_logging, get_logger
from rs_core.models import (
    Analysis,
    Farm,
    Field,
    FieldCollectionState,
    FieldGeometryVersion,
    Interpretation,
    SceneMetadata,
    SyncOutbox,
)
from rs_core.rbac import Permission, Principal, Role, permissions_for
from rs_core.repositories import (
    advance_cursor,
    ensure_collection_state,
    get_collection_state,
    get_interpretation,
    get_outbox,
    insert_interpretation,
    mark_backfill_complete,
    pipeline_health,
    processed_scene_ids,
    record_forward_fill_poll,
    record_push,
    upsert_analysis,
    upsert_scene_metadata,
)
from rs_core.telemetry import (
    configure_telemetry,
    get_tracer,
    instrument_celery,
    instrument_fastapi,
    instrument_httpx,
)
from rs_core.timeutil import CAT, cat_range_to_utc, to_cat

__all__ = [
    "Settings",
    "get_settings",
    "Base",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "configure_logging",
    "get_logger",
    "Analysis",
    "Farm",
    "Field",
    "FieldCollectionState",
    "FieldGeometryVersion",
    "Interpretation",
    "SceneMetadata",
    "SyncOutbox",
    "advance_cursor",
    "ensure_collection_state",
    "get_collection_state",
    "get_interpretation",
    "get_outbox",
    "insert_interpretation",
    "mark_backfill_complete",
    "pipeline_health",
    "processed_scene_ids",
    "record_forward_fill_poll",
    "record_push",
    "upsert_analysis",
    "upsert_scene_metadata",
    "Permission",
    "Principal",
    "Role",
    "permissions_for",
    "configure_telemetry",
    "get_tracer",
    "instrument_celery",
    "instrument_fastapi",
    "instrument_httpx",
    "CAT",
    "cat_range_to_utc",
    "to_cat",
]
