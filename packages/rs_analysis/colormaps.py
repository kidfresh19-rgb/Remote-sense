"""Per-index colormaps and legend ranges. Pure data: the display range and named colormap an
index is rendered with, consumed by the tiler (Phase 4/5) and the workspace legend. No
rendering dependency here, so it stays importable in the zero-dependency analysis core.

Display ranges are intentionally narrower than each index's mathematical valid range so the
colour ramp spends its contrast on the values that actually occur over cropland, not the
theoretical extremes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorMap:
    """A named colormap plus the value range it stretches across for display."""

    index: str
    colormap: str
    vmin: float
    vmax: float


# Display ranges for Zimbabwean cropland: a colour stretch only, not agronomic classification
# (that lives in rs_interpret/thresholds.py). The tiler renders rasters with these and the
# workspace legend mirrors them (frontend/src/lib/indices.ts), so the two must stay in sync.
# Colormap names follow the matplotlib / rio-tiler convention the tiler uses.
COLORMAPS: dict[str, ColorMap] = {
    "ndvi": ColorMap("ndvi", "RdYlGn", -0.2, 0.9),
    "evi2": ColorMap("evi2", "RdYlGn", -0.2, 0.9),
    "savi": ColorMap("savi", "RdYlGn", -0.2, 0.9),
    "ndre": ColorMap("ndre", "RdYlGn", -0.1, 0.7),
    "ndmi": ColorMap("ndmi", "BrBG", -0.4, 0.6),
}


def get_colormap(index: str) -> ColorMap:
    try:
        return COLORMAPS[index.lower()]
    except KeyError as exc:
        raise KeyError(f"no colormap for index {index!r}") from exc
