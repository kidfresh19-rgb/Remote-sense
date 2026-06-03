"""Index-COG output (D1): turn a computed index into a Cloud-Optimized GeoTIFF the tiler windows
for map previews, plus the pure raster prep that builds the array.

`index_raster` is pure NumPy (the index, clipped to its valid range, masked pixels set to NoData)
and stays in the zero-dependency core. `write_cog` needs `rasterio` (the `geo` extra, installed
only in-container), imported lazily - this module still imports on a host without the raster stack,
and only `write_cog` raises there."""

from __future__ import annotations

import numpy as np

from rs_analysis.indices import clip_to_range, get_index
from rs_analysis.scl import clear_mask

# A COG band carries reflectance-derived float values; NoData is NaN, never 0 (mirrors BandStack).
NODATA = float("nan")


def index_raster(
    reflectance: dict[str, np.ndarray],
    index_name: str,
    *,
    scl: np.ndarray | None = None,
    aoi_mask: np.ndarray | None = None,
) -> np.ndarray:
    """The index as a float32 raster ready to encode as a COG: computed from reflectance, clipped
    to the index's valid range, with masked and out-of-AOI pixels set to NaN (NoData). Same masking
    contract as `analyze_index`, so the stored stats and the rendered tile agree pixel for pixel."""
    spec = get_index(index_name)
    clipped = clip_to_range(spec.compute(reflectance), *spec.valid_range)
    out = np.asarray(clipped, dtype="float32").copy()
    if scl is not None:
        include = clear_mask(scl)
        if aoi_mask is not None:
            include = include & aoi_mask
        out[~include] = NODATA
    elif aoi_mask is not None:
        out[~aoi_mask] = NODATA
    return out


def write_cog(
    array: np.ndarray,
    *,
    transform: tuple[float, float, float, float, float, float],
    crs: str,
    nodata: float = NODATA,
) -> bytes:
    """Encode a single-band float index raster as a Cloud-Optimized GeoTIFF and return its bytes.
    Uses GDAL's COG driver, which guarantees the cloud-optimized layout (internal tiling + built
    overviews) the tiler relies on for windowed reads. A hand-assembled tiled GTiff silently
    degrades to strips when a single tile spans the whole raster, which is not a valid COG. Needs
    `rasterio` (the `geo` extra, in-container only)."""
    try:
        from rasterio.io import MemoryFile
        from rasterio.transform import Affine
    except ImportError as exc:  # pragma: no cover - the host has no raster stack
        raise RuntimeError(
            "write_cog needs the `geo` extra (rasterio), installed in-container"
        ) from exc

    data = np.asarray(array, dtype="float32")
    if data.ndim != 2:
        raise ValueError(f"index raster must be 2-D, got shape {data.shape}")
    height, width = data.shape
    profile = {
        "driver": "COG",
        "dtype": "float32",
        "count": 1,
        "height": height,
        "width": width,
        "crs": crs,
        "transform": Affine(*transform),
        "nodata": nodata,
        "compress": "deflate",
        "overview_resampling": "average",
    }
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(data, 1)
        return bytes(mem.read())
