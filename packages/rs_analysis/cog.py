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

# COG internal tile size (a multiple of 16). 256 keeps the tiler's windowed reads granular; GDAL's
# COG driver pads the last tile, so even a raster smaller than this stays genuinely tiled.
COG_BLOCK_SIZE = 256


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


def cloud_mask_raster(cloud_mask: np.ndarray) -> np.ndarray:
    """A single-band float32 raster for the cloud-honesty overlay (backlog 0046): 1.0 where an
    in-AOI pixel was excluded by per-AOI SCL masking (invariant 3), NaN (NoData) everywhere else.

    `cloud_mask` is the boolean the adapter already computed at fetch time (`inside & ~keep`, where
    `keep = clear_mask(scl) & inside`); this only reshapes it into the NaN-NoData COG convention the
    tiler reads, it does not recompute cloud detection. Encoding the masked pixels as the raster's
    *footprint* (finite where masked, NoData elsewhere) means the tiler renders the hatch exactly
    over the unreliable part of the field with no geometry needed at tile time - the same way the
    index COGs already carry their valid region as their footprint."""
    out = np.full(cloud_mask.shape, NODATA, dtype="float32")
    out[np.asarray(cloud_mask, dtype=bool)] = 1.0
    return out


def rgb_raster(
    bands: dict[str, np.ndarray],
    *,
    aoi_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Build a (3, H, W) float32 RGB array for a 3-band COG (band order: Red=B04, Green=B03,
    Blue=B02). Pixels outside `aoi_mask` are set to NaN. No value clipping: the tiler's per-channel
    rescale controls the visual stretch at display time."""
    missing = sorted(b for b in ("B02", "B03", "B04") if b not in bands)
    if missing:
        raise ValueError(f"rgb_raster requires B02, B03, and B04; missing: {missing}")
    r = np.asarray(bands["B04"], dtype="float32")
    g = np.asarray(bands["B03"], dtype="float32")
    b = np.asarray(bands["B02"], dtype="float32")
    rgb = np.stack([r, g, b], axis=0)
    if aoi_mask is not None:
        rgb[:, ~aoi_mask] = NODATA
    return rgb


def write_cog(
    array: np.ndarray,
    *,
    transform: tuple[float, float, float, float, float, float],
    crs: str,
    nodata: float = NODATA,
    tags: dict[str, str] | None = None,
) -> bytes:
    """Encode a single-band or multi-band float index raster as a Cloud-Optimized GeoTIFF and
    return its bytes. Uses GDAL's COG driver, which guarantees the cloud-optimized layout
    (internal tiling + built overviews) the tiler relies on for windowed reads. `tags` are written
    as GDAL metadata. Needs `rasterio` (the `geo` extra, in-container only)."""
    try:
        from rasterio.io import MemoryFile
        from rasterio.transform import Affine
    except ImportError as exc:  # pragma: no cover - the host has no raster stack
        raise RuntimeError(
            "write_cog needs the `geo` extra (rasterio), installed in-container"
        ) from exc

    data = np.asarray(array, dtype="float32")
    if data.ndim == 2:
        count = 1
        height, width = data.shape
    elif data.ndim == 3:
        count = data.shape[0]
        _, height, width = data.shape
    else:
        raise ValueError(f"index raster must be 2-D or 3-D, got shape {data.shape}")

    profile = {
        "driver": "COG",
        "dtype": "float32",
        "count": count,
        "height": height,
        "width": width,
        "crs": crs,
        "transform": Affine(*transform),
        "nodata": nodata,
        "blocksize": COG_BLOCK_SIZE,
        "compress": "deflate",
        "overview_resampling": "average",
    }
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            if data.ndim == 2:
                dst.write(data, 1)
            else:
                for b in range(count):
                    dst.write(data[b], b + 1)
            if tags:
                dst.update_tags(**tags)
        return bytes(mem.read())
