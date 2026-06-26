"""Analyst workspace BFF (L6): RBAC-gated (view) read endpoints backing the React workspace -
farms, fields (with geometry for the map), per-field/index time series, scene passes, and
interpretations. Read-only. Geometry IS shown to the internal analyst here, which is distinct
from the geometry-free *outbound* push (invariant 6 governs the gateway direction only).

One submodule per resource (farms, publish, fields, interpretations, annotations, analyse, plus
the shared deps), each contributing a router, aggregated here into the single `router` that
main.py includes. Every public name is re-exported so callers keep importing from
services.api.workspace."""

from fastapi import APIRouter

from services.api.workspace.analyse import (
    AOIAnalysisRequest,
    AOISeriesRequest,
    FarmSeriesRequest,
    analyse_aoi_endpoint,
    analyse_aoi_series_endpoint,
    analyse_farm_series_endpoint,
    aoi_job_endpoint,
)
from services.api.workspace.analyse import (
    router as analyse_router,
)
from services.api.workspace.annotations import (
    AnnotationCreate,
    AnnotationOut,
    create_annotation_endpoint,
    delete_annotation_endpoint,
    list_annotations_endpoint,
)
from services.api.workspace.annotations import (
    router as annotations_router,
)
from services.api.workspace.deps import (
    AnnotatePrincipal,
    PublishPrincipal,
    RunAnalysisPrincipal,
    SessionDep,
    ViewPrincipal,
)
from services.api.workspace.farms import (
    FarmOut,
    list_farms,
    list_farms_endpoint,
)
from services.api.workspace.farms import (
    router as farms_router,
)
from services.api.workspace.fields import (
    AsOfResolution,
    AuditRecordOut,
    CollectDatesRequest,
    FieldOut,
    ResolvedPass,
    SceneOut,
    TimeseriesPoint,
    choose_nearer_pass,
    field_as_of,
    field_as_of_endpoint,
    field_audit,
    field_audit_endpoint,
    field_collect_dates_endpoint,
    field_collect_endpoint,
    field_scenes,
    field_scenes_endpoint,
    field_timeseries,
    field_timeseries_endpoint,
    list_fields,
    list_fields_endpoint,
)
from services.api.workspace.fields import (
    router as fields_router,
)
from services.api.workspace.interpretations import (
    InterpretationOut,
    InterpretationReview,
    ReviewQueueItem,
    field_interpretations,
    field_interpretations_endpoint,
    review_interpretation_endpoint,
    review_queue,
    review_queue_endpoint,
)
from services.api.workspace.interpretations import (
    router as interpretations_router,
)
from services.api.workspace.publish import (
    PublishEnqueuedOut,
    PublishStatusOut,
    publish_farm_endpoint,
    publish_status_endpoint,
)
from services.api.workspace.publish import (
    router as publish_router,
)
from services.api.workspace.regions import (
    RegionBoundaryFeatureCollection,
    RegionDrawRequest,
    RegionLayerOut,
    RegionOut,
    RegionUploadOut,
    draw_region_endpoint,
    list_region_layers_endpoint,
    region_layer_boundaries_endpoint,
    upload_region_layer_endpoint,
)
from services.api.workspace.regions import (
    router as regions_router,
)
from services.api.workspace.ward_watch import (
    FoodSecurityRollupOut,
    HouseholdAssessment,
    RollupNodeOut,
    TriageRowOut,
    rank_triage,
    roll_up,
    ward_watch_rollups_endpoint,
    ward_watch_triage_endpoint,
)
from services.api.workspace.ward_watch import (
    router as ward_watch_router,
)

# Sub-routers in the original registration order, so the OpenAPI path listing stays familiar.
router = APIRouter()
router.include_router(farms_router)
router.include_router(publish_router)
router.include_router(fields_router)
router.include_router(interpretations_router)
router.include_router(annotations_router)
router.include_router(analyse_router)
router.include_router(regions_router)
router.include_router(ward_watch_router)

__all__ = [
    "AOIAnalysisRequest",
    "AOISeriesRequest",
    "FarmSeriesRequest",
    "AnnotatePrincipal",
    "AnnotationCreate",
    "AnnotationOut",
    "AsOfResolution",
    "AuditRecordOut",
    "CollectDatesRequest",
    "FarmOut",
    "FieldOut",
    "FoodSecurityRollupOut",
    "HouseholdAssessment",
    "InterpretationOut",
    "InterpretationReview",
    "PublishEnqueuedOut",
    "PublishPrincipal",
    "PublishStatusOut",
    "RegionBoundaryFeatureCollection",
    "RegionDrawRequest",
    "RegionLayerOut",
    "RegionOut",
    "RegionUploadOut",
    "ResolvedPass",
    "ReviewQueueItem",
    "RollupNodeOut",
    "RunAnalysisPrincipal",
    "SceneOut",
    "SessionDep",
    "TimeseriesPoint",
    "TriageRowOut",
    "ViewPrincipal",
    "analyse_aoi_endpoint",
    "analyse_aoi_series_endpoint",
    "analyse_farm_series_endpoint",
    "aoi_job_endpoint",
    "choose_nearer_pass",
    "create_annotation_endpoint",
    "delete_annotation_endpoint",
    "draw_region_endpoint",
    "field_as_of",
    "field_as_of_endpoint",
    "field_audit",
    "field_audit_endpoint",
    "field_collect_dates_endpoint",
    "field_collect_endpoint",
    "field_interpretations",
    "field_interpretations_endpoint",
    "field_scenes",
    "field_scenes_endpoint",
    "field_timeseries",
    "field_timeseries_endpoint",
    "list_annotations_endpoint",
    "list_farms",
    "list_farms_endpoint",
    "list_fields",
    "list_fields_endpoint",
    "list_region_layers_endpoint",
    "region_layer_boundaries_endpoint",
    "publish_farm_endpoint",
    "publish_status_endpoint",
    "rank_triage",
    "review_interpretation_endpoint",
    "review_queue",
    "review_queue_endpoint",
    "roll_up",
    "router",
    "upload_region_layer_endpoint",
    "ward_watch_rollups_endpoint",
    "ward_watch_triage_endpoint",
]
