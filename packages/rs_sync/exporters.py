"""Export builders (L7, Phase 6). CSV time-series of zonal stats for analyst download. GeoTIFF
and PDF exports are parked on the raster/PDF stacks; the gateway JSON payload lives in
`payload.py`. No geometry is emitted (invariant 6)."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

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
