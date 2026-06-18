"""Persistence helpers that span requests/tasks rather than belonging to one endpoint.

One submodule per aggregate: scenes, analyses, collection_state, interpretations, annotations,
sync, health, analytics. Every public name is re-exported here so callers keep importing from
`rs_core.repositories`; the load-bearing rules (first-write-wins scene metadata, the additive
idempotent analysis upsert, the forward-only cursor) are documented where they live.

Kept value-based (no rs_imagery / rs_analysis import) so rs_core stays free of an upward
dependency on the imagery or analysis layers; the worker maps an AccessPort SceneMetadata or an
engine AnalysisOutput onto these arguments at call time.
"""

from rs_core.repositories.analyses import (
    _ANALYSIS_MUTABLE,
    _analysis_upsert_stmt,
    upsert_analysis,
)
from rs_core.repositories.analytics import (
    FarmAnalyticsAnomalies,
    FarmAnalyticsSummary,
    FarmAnalyticsTimeSeriesPoint,
    FarmAnomaly,
    get_farm_analytics_anomalies,
    get_farm_analytics_summary,
    get_farm_analytics_timeseries,
)
from rs_core.repositories.annotations import (
    delete_annotation,
    insert_annotation,
    list_annotations,
)
from rs_core.repositories.collection_state import (
    advance_cursor,
    ensure_collection_state,
    get_collection_state,
    mark_backfill_complete,
    processed_scene_ids,
    record_forward_fill_poll,
)
from rs_core.repositories.health import pipeline_health
from rs_core.repositories.interpretations import (
    get_interpretation,
    get_interpretation_by_id,
    insert_interpretation,
    list_review_queue,
    published_narratives_for_farm,
    review_interpretation,
)
from rs_core.repositories.regions import (
    create_drawn_region,
    create_uploaded_layer,
    get_assignments_for_farm,
    get_layer_by_identity,
    list_layers,
    natural_region_polygons,
    recompute_farm_region_assignments,
    seed_natural_regions,
)
from rs_core.repositories.scenes import upsert_scene_metadata
from rs_core.repositories.sync import (
    get_latest_outbox_for_farm,
    get_outbox,
    record_push,
)

__all__ = [
    # The two underscore names are part of the tested surface: the upsert statement builder
    # and its mutable-column set are unit-tested with no database (tests/test_repositories.py).
    "_ANALYSIS_MUTABLE",
    "_analysis_upsert_stmt",
    "FarmAnalyticsAnomalies",
    "FarmAnalyticsSummary",
    "FarmAnalyticsTimeSeriesPoint",
    "FarmAnomaly",
    "advance_cursor",
    "create_drawn_region",
    "create_uploaded_layer",
    "delete_annotation",
    "ensure_collection_state",
    "get_assignments_for_farm",
    "get_collection_state",
    "get_layer_by_identity",
    "get_farm_analytics_anomalies",
    "get_farm_analytics_summary",
    "get_farm_analytics_timeseries",
    "get_interpretation",
    "get_interpretation_by_id",
    "get_latest_outbox_for_farm",
    "get_outbox",
    "insert_annotation",
    "insert_interpretation",
    "list_annotations",
    "list_layers",
    "list_review_queue",
    "mark_backfill_complete",
    "natural_region_polygons",
    "pipeline_health",
    "processed_scene_ids",
    "published_narratives_for_farm",
    "recompute_farm_region_assignments",
    "record_forward_fill_poll",
    "record_push",
    "review_interpretation",
    "seed_natural_regions",
    "upsert_analysis",
    "upsert_scene_metadata",
]
