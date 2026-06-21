# Backlog 0018 — Orthophoto: tiler static preview + export endpoints

- Status: ready-for-agent
- Type: geospatial
- Parent: orthophoto download + natural color preview feature
- Blocked by: 0017 (rgb COG must exist in the store for the endpoints to serve)
- Invariants / decisions: tiler is internal (not a frozen external route - CLAUDE.md §0); free to
  add new routes. Existing `GET /tiles/{index}/{geometry_version}/{field_id}/{scene_id}/{z}/{x}/{y}.png`
  is untouched. `render_params("rgb")` already exists and returns per-channel rescale tuples for the
  composite path. `Reader.preview()` from rio-tiler provides decimated full-extent read from COG
  overviews.

## Context

`services/tiler` currently serves XYZ map tiles only. Adding two new endpoints enables:
- A static 512x512 thumbnail (used by the passes list and the AOI Studio results table).
- A direct GeoTIFF export (presigned redirect handled by the BFF; the tiler route is a passthrough
  to the raw COG bytes from the store).

The export endpoint does no geo processing - it is a pure S3 key lookup and byte stream. The static
preview endpoint uses `rio-tiler`'s `Reader.preview()` for a decimated full-field JPEG.

## What to build

### 1. `GET /static/{view}/{geometry_version}/{field_id}/{scene_id}.jpg`

`view` is any valid index name or the composites `rgb` / `fcc`.

- Validate `view` by calling `render_params(view)` - 422 if unrecognized.
- Build the COG key: `cog_key(field_id=field_id, scene_id=scene_id, index=view, geometry_version=geometry_version)`.
- If the key does not exist in the COG store, return 404.
- Open the COG via `/vsis3/` and call `Reader.preview(max_size=512)`.
- Apply the same `render_params` rescale / colormap logic that `render_tile` uses (same branch: if
  composite, per-channel rescale; if single index, colormap).
- Encode as JPEG (quality 85). Return `Content-Type: image/jpeg`.
- Add `Cache-Control: public, max-age=86400` (COG is immutable; key encodes geometry_version).

### 2. `GET /export/{view}/{geometry_version}/{field_id}/{scene_id}.tif`

- Validate `view` via `render_params(view)`.
- Build the COG key and check existence; 404 if absent.
- Return the raw COG bytes with:
  - `Content-Type: image/tiff`
  - `Content-Disposition: attachment; filename="{field_id}_{scene_id}_{view}.tif"`
- This is a byte-stream passthrough of the object from the store. No rasterio processing.
- The BFF (backlog 0019) generates a presigned URL pointing here instead of calling this route
  directly; this route exists as a fallback and for test convenience.

### Add `render_preview()` to `services/tiler/render.py`

Extract the preview rendering logic from the static endpoint handler into a dedicated function,
parallel to the existing `render_tile()`:

```python
def render_preview(
    cog_path: str,
    *,
    index: str,
    max_size: int = 512,
) -> bytes:
    """Render a full-extent decimated JPEG preview from a COG using Reader.preview()."""
```

This keeps the route handler thin and the preview logic independently testable.

## Acceptance criteria

- [ ] `GET /static/rgb/{gv}/{fid}/{sid}.jpg` returns a valid JPEG when `rgb.tif` exists; 404 when absent.
- [ ] `GET /static/ndvi/{gv}/{fid}/{sid}.jpg` returns a colormap-rendered JPEG for any supported index.
- [ ] `GET /export/rgb/{gv}/{fid}/{sid}.tif` returns raw COG bytes with correct `Content-Disposition`.
- [ ] Both endpoints return 422 for an unrecognized `view`.
- [ ] `Cache-Control: public, max-age=86400` header present on static responses.
- [ ] `render_preview()` is unit-testable with a synthetic COG (no MinIO required in tests).
- [ ] Existing tile endpoint `GET /tiles/...` is unmodified and all existing tiler tests pass.
- [ ] ruff + ruff format + mypy + pytest green.
