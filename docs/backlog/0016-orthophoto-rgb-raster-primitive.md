# Backlog 0016 — Orthophoto: RGB raster primitive

- Status: ready-for-agent
- Type: geospatial
- Parent: orthophoto download + natural color preview feature
- Blocked by: none - can start immediately
- Invariants / decisions: CLAUDE.md §1.2 reflectance-first (B02/B03/B04 from BOA reflectance, offset
  applied before any composite); §1.7 raw bands transient (this function operates on already-derived
  reflectance arrays, not raw DN); `write_cog()` in `rs_analysis/cog.py` already handles 3-D arrays.

## Context

Adding natural color previews and GeoTIFF downloads requires a `rgb` COG alongside the per-index COGs
that the collection pipeline already writes. The only missing primitive is a function that assembles
the three reflectance bands (B04/B03/B02) into a normalized `(3, H, W) float32` array suitable for
passing directly to the existing `write_cog()`.

All three bands are 10 m native resolution - no upsampling is needed and resolution invariant 4 is
not implicated.

## What to build

Add `rgb_raster()` to `packages/rs_analysis/cog.py`:

```python
def rgb_raster(
    reflectance: dict[str, np.ndarray],
    *,
    aoi_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Assemble B04 (R), B03 (G), B02 (B) surface reflectance into a (3, H, W) float32 array.

    Pixels outside aoi_mask are set to NaN (written as nodata by write_cog).
    Values are clipped to [0.0, 1.0] - reflectance above 1.0 is instrument noise.
    Raises KeyError if any of B02, B03, B04 is absent from the reflectance dict.
    """
```

Requirements:
- Band stacking order: `[B04, B03, B02]` (standard RGB). Do NOT reorder to BGR.
- Clip pixel values to `[0.0, 1.0]` with `np.clip` before returning.
- Where `aoi_mask` is provided (`True` = inside AOI), set pixels where `aoi_mask == False` to
  `np.nan`. This matches the nodata convention `write_cog` uses (`NODATA = np.nan`).
- Return dtype: `float32`. Cast with `.astype(np.float32, copy=False)` if bands are float64.
- Raise `KeyError` with a descriptive message if B02, B03, or B04 is absent - never silently
  return a partial composite.
- No new imports beyond `numpy` (already present in the module).

## Acceptance criteria

- [ ] `rgb_raster({"B04": r, "B03": g, "B02": b})` returns shape `(3, H, W)` in `[0.0, 1.0]`.
- [ ] Pixels where `aoi_mask == False` are `np.nan` in all three channels.
- [ ] A reflectance dict missing any band raises `KeyError`.
- [ ] Values above 1.0 or below 0.0 are clipped, not preserved.
- [ ] Return dtype is `float32`.
- [ ] The resulting array round-trips through `write_cog()` without error: `write_cog(rgb_raster(...), transform=..., crs=...)` produces valid bytes.
- [ ] Unit tests: happy path; masking; clipping of out-of-range values; missing band error.
- [ ] ruff + ruff format + mypy + pytest green.
