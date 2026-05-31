"""rs_imagery: the imagery access layer. All satellite data flows through AccessPort;
endpoint specifics live only in adapters. The active adapter is a config switch."""

from rs_imagery.port import AccessPort
from rs_imagery.registry import get_access_adapter
from rs_imagery.types import (
    AOI,
    BandStack,
    BBox,
    NormalizedResult,
    PreviewTile,
    ProcessingMode,
    Provenance,
    SceneMetadata,
    SceneRef,
    TimeRange,
)

__all__ = [
    "AccessPort",
    "get_access_adapter",
    "AOI",
    "BandStack",
    "BBox",
    "NormalizedResult",
    "PreviewTile",
    "ProcessingMode",
    "Provenance",
    "SceneMetadata",
    "SceneRef",
    "TimeRange",
]
