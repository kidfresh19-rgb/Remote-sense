"""rs_sync (L7): outbound sync. Build export formats and push selected results to the gateway,
additively, robustly, and idempotently. The gateway push goes through `GatewayPort` only
(CLAUDE.md §1.1); results are keyed to the canonical farm id and **geometry is never returned**
(§1.6). GeoTIFF/PDF exports are parked on the raster/PDF stacks; CSV + the JSON gateway payload
are in place. PDF export is parked on the PDF stack; CSV, GeoTIFF (in-container, the `geo` extra),
and the JSON gateway payload are in place. The gateway wire format is confirmed (ADR 0006): the
AgriTrack adapter delivers to /integrations/satellite/results; a generic HTTP push and a recording
sink remain behind the port."""

from rs_sync.adapters import HttpGatewayPort, RecordingGatewayPort
from rs_sync.agritrack import (
    AgriTrackGatewayPort,
    SatelliteResult,
    SubPlotEntry,
    to_satellite_results,
)
from rs_sync.exporters import analyses_to_csv, index_geotiff
from rs_sync.inbound import (
    GATEWAY_INBOUND_CONTRACT_VERSION,
    DeclarationsQuery,
    DeclaredCrop,
    DroneReference,
    HouseholdDeclaration,
    HouseholdDeclarationBatch,
    PlantingDeclaration,
    PlotDeclaration,
    synthetic_declarations,
)
from rs_sync.payload import (
    PAYLOAD_VERSION,
    AnalysisRow,
    GatewayPayload,
    IndexResult,
    PublishedNarrative,
    build_payload,
    compute_idempotency_key,
    narrative_signature,
)
from rs_sync.port import GatewayPort, PushResult

__all__ = [
    "HttpGatewayPort",
    "RecordingGatewayPort",
    "AgriTrackGatewayPort",
    "SatelliteResult",
    "SubPlotEntry",
    "to_satellite_results",
    "analyses_to_csv",
    "index_geotiff",
    "PAYLOAD_VERSION",
    "AnalysisRow",
    "GatewayPayload",
    "IndexResult",
    "PublishedNarrative",
    "build_payload",
    "compute_idempotency_key",
    "narrative_signature",
    "GatewayPort",
    "PushResult",
    "GATEWAY_INBOUND_CONTRACT_VERSION",
    "DeclaredCrop",
    "DeclarationsQuery",
    "DroneReference",
    "HouseholdDeclaration",
    "HouseholdDeclarationBatch",
    "PlantingDeclaration",
    "PlotDeclaration",
    "synthetic_declarations",
]
