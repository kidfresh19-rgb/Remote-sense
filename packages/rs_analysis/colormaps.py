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


# Provenance (v1): these display ranges are literature-derived (proposed 2026-06-03) and carry
# provisional engineering approval only - Mishael Gwede, owner/engineer, 2026-06-19. This is NOT an
# agronomist sign-off; agronomist review is still outstanding (tracked in
# docs/backlog/0015-agronomy-thresholds-v1-agronomist-signoff.md). A display colour stretch only,
# NOT the agronomic classification bands (those live in rs_interpret/thresholds.py). Ranges are
# tuned to the values that actually occur over Zimbabwean cropland so the ramp spends its contrast
# on the cropping window, not the mathematical extremes; the workspace legend
# (frontend/src/lib/indices.ts) mirrors these and must stay in sync. Colormap names follow the
# matplotlib / rio-tiler convention the tiler uses. Sources: per-index typical-range literature
# (EOS/Sentinel-Hub crop guidance; SAVI L=0.5 range compression; NDRE 0.1-0.6 crop band) reconciled
# to the dry-to-wet season swing of NR II-V (FAO Zimbabwe). Stops are display-only; index math and
# the stored valid_range are untouched.
COLORMAPS: dict[str, ColorMap] = {
    # NDVI: bare lowveld/dry-season soil sits ~0.1-0.2 and dense maize/tobacco saturates ~0.85; a
    # slightly negative floor keeps water and shadow legible. (test-locked stop)
    "ndvi": ColorMap("ndvi", "RdYlGn", -0.2, 0.9),
    # EVI2: soil-/saturation-damped, so it reads a touch lower than NDVI at the same biomass; floor
    # near 0 (less negative than NDVI), top ~0.8 at peak canopy.
    "evi2": ColorMap("evi2", "RdYlGn", -0.1, 0.8),
    # SAVI (L=0.5): the (1+L)=1.5 soil correction compresses the range, so peak-canopy SAVI tops
    # ~0.6-0.7, well below NDVI; ideal index for the sparse/bare semi-arid early season.
    "savi": ColorMap("savi", "RdYlGn", -0.1, 0.7),
    # NDRE: works in a narrow 0.1-0.6 band over crops and rarely reaches 0.7; capping at 0.6 keeps
    # the full ramp on the nitrogen/chlorophyll working range.
    "ndre": ColorMap("ndre", "RdYlGn", -0.1, 0.6),
    # NDMI: diverging ramp around 0 (the dry/wet hinge). Parched lowveld soil goes well negative;
    # a healthy/irrigated canopy reaches ~0.4-0.5, so -0.3..0.5 brackets the field signal.
    "ndmi": ColorMap("ndmi", "BrBG", -0.3, 0.5),
}


def get_colormap(index: str) -> ColorMap:
    try:
        return COLORMAPS[index.lower()]
    except KeyError as exc:
        raise KeyError(f"no colormap for index {index!r}") from exc
