"""Export builders (L7, Phase 6). CSV time-series of zonal stats for analyst download. GeoTIFF
and PDF exports are parked on the raster/PDF stacks; the gateway JSON payload lives in
`payload.py`. No geometry is emitted (invariant 6)."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence

import numpy as np

from rs_sync.payload import IndexResult

_COLUMNS = (
    "canonical_field_id",
    "index_name",
    "pass_date",
    "mean",
    "min",
    "max",
    "std",
    "p10",
    "p90",
    "clear_fraction",
    "confidence",
    "resolution_m",
)


def analyses_to_csv(results: Sequence[IndexResult]) -> str:
    """A flat CSV of index results (one row per field/pass/index), for analyst download. Carries
    field identity but never geometry."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_COLUMNS)
    for r in results:
        writer.writerow(
            [
                r.canonical_field_id,
                r.index_name,
                r.pass_date.isoformat(),
                r.mean,
                r.min,
                r.max,
                r.std,
                r.p10,
                r.p90,
                r.clear_fraction,
                r.confidence,
                r.resolution_m,
            ]
        )
    return buffer.getvalue()


def index_geotiff(
    array: np.ndarray,
    *,
    transform: tuple[float, float, float, float, float, float],
    crs: str,
    provenance: Mapping[str, str] | None = None,
) -> bytes:
    """A single index raster as a self-describing GeoTIFF for analyst download: the tiled COG layout
    from `rs_analysis.write_cog`, plus provenance embedded as GDAL metadata tags (invariant 5, so a
    downloaded raster stays reproducible). Needs the `geo` extra (rasterio), in-container only.

    An analyst raster export is inherently georeferenced; the no-geometry rule (invariant 6) governs
    the additive gateway push (see `payload.py`), not analyst downloads."""
    from rs_analysis import write_cog  # lazy: the `geo` extra (rasterio), installed in-container

    tags = {f"RS_{key.upper()}": str(value) for key, value in (provenance or {}).items()}
    return write_cog(array, transform=transform, crs=crs, tags=tags or None)
