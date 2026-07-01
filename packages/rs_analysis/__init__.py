"""rs_analysis: the scientific core (Phase 2). Reflectance conversion, per-AOI SCL masking,
the spectral index suite, range clipping, the mixed-resolution policy and zonal statistics.
Pure library: no DB, no services, testable with synthetic arrays. COG output lands with the
windowed_cog adapter (needs rasterio) in a later step.

See PLAN.md §5 for the locked specification and CLAUDE.md §1 for the invariants
(reflectance-first, per-AOI masking, resolution honesty)."""

from rs_analysis.bands import BAND_RESOLUTION_M, coarsest_resolution_m
from rs_analysis.cog import cloud_mask_raster, index_raster, rgb_raster, write_cog
from rs_analysis.colormaps import COLORMAPS, ColorMap, get_colormap
from rs_analysis.engine import (
    AnalysisOutput,
    ResolutionError,
    analyze_from_dn,
    analyze_index,
    confidence_for,
)
from rs_analysis.indices import INDICES, IndexSpec, clip_to_range, get_index
from rs_analysis.phenology import Phenology, phenology
from rs_analysis.reflectance import stack_to_reflectance, to_reflectance
from rs_analysis.scl import CLEAR_CLASSES, SCL, clear_fraction, clear_mask
from rs_analysis.zonal import ZonalStats, zonal_stats
from rs_analysis.zones import (
    NODATA_ZONE,
    ZoneResult,
    kmeans,
    productivity_zones,
    zone_polygons,
)

__all__ = [
    "BAND_RESOLUTION_M",
    "coarsest_resolution_m",
    "cloud_mask_raster",
    "index_raster",
    "rgb_raster",
    "write_cog",
    "COLORMAPS",
    "ColorMap",
    "get_colormap",
    "AnalysisOutput",
    "ResolutionError",
    "analyze_from_dn",
    "analyze_index",
    "confidence_for",
    "INDICES",
    "IndexSpec",
    "clip_to_range",
    "get_index",
    "Phenology",
    "phenology",
    "to_reflectance",
    "stack_to_reflectance",
    "CLEAR_CLASSES",
    "SCL",
    "clear_fraction",
    "clear_mask",
    "ZonalStats",
    "zonal_stats",
    "NODATA_ZONE",
    "ZoneResult",
    "kmeans",
    "productivity_zones",
    "zone_polygons",
]
