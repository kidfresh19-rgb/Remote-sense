# Backlog 0020 — Orthophoto: BFF natural color preview endpoint

- Status: ready-for-agent
- Type: backend
- Parent: orthophoto download + natural color preview feature
- Blocked by: 0018 (tiler static endpoint); conceptually independent of 0019
- Invariants / decisions: CDSE Process API is a shared, quota-bounded resource (CLAUDE.md §2,
  CONTEXT.md "CDSE quota budget"). The preview must be cached aggressively to avoid redundant CDSE
  hits. No beat task for TTL: use a MinIO/S3 lifecycle rule (less code, zero queue load). Geometry
  hashing MUST use WKB (not coordinate sorting) to prevent hash collisions between distinct polygons
  sharing the same vertex set.

## Context

For registered fields the natural color preview is already a stored `rgb.tif` COG (backlog 0017),
served by the tiler (backlog 0018). This endpoint covers a different case: a **custom AOI** drawn or
uploaded in AOI Studio whose geometry has no stored COG. The backend must ask CDSE's Process API for
a True Color render of that scene + geometry and return the image.

CDSE is the CDSE quota bucket's scarcest resource. Calling it on every thumbnail request would burn
the quota budget. The cache key is `aoi_preview/{scene_id}/{wkb_hash}.jpg` in MinIO.

## What to build

### 1. Geometry hashing utility (`packages/rs_core/geo.py` or a new `packages/rs_core/hashing.py`)

```python
def geometry_cache_key(geojson_geometry: dict) -> str:
    """
    Canonical, collision-safe hash of a GeoJSON geometry for use as a cache key.

    Steps:
    1. Parse to shapely geometry.
    2. Normalize: apply shapely.normalize() to canonical vertex order + ring winding.
    3. Round coordinates to 6 decimal places (sub-pixel precision at 10 m; collapses
       serialization jitter without losing distinct geometries).
    4. Hash the WKB bytes with SHA-256; return the hex digest.

    Two distinct polygons with the same vertex set but different winding order hash
    identically (intended: they represent the same region). Two polygons with any
    differing vertex hash differently (collision-safe).
    """
```

WKB encodes vertex order, so this approach is collision-safe by construction. Do NOT sort
coordinates or decompose to a list before hashing.

### 2. `aoi_tmp_cog_key()` function (`packages/rs_core/storage.py`)

```python
def aoi_preview_key(scene_id: str, geometry_hash: str) -> str:
    return f"aoi_preview/{scene_id}/{geometry_hash}.jpg"
```

### 3. `POST /analyse/aoi/natural-color` (workspace BFF `services/api/workspace/analyse.py`)

Request body (Pydantic v2):
```python
class NaturalColorRequest(BaseModel):
    scene_id: str
    geometry: dict  # GeoJSON geometry
```

Handler logic:
1. Compute `wkb_hash = geometry_cache_key(body.geometry)`.
2. Build cache key `aoi_preview_key(body.scene_id, wkb_hash)`.
3. If the key exists in the COG store, return the presigned URL (or stream the bytes) directly.
4. Otherwise call `cdse_process_api.true_color(scene_id=body.scene_id, geometry=body.geometry)`
   through the existing CDSE adapter (respects the shared token bucket - no direct HTTP call here).
5. Store the resulting JPEG bytes at the cache key via `cog_store.put(key, jpeg_bytes)`.
6. Return the image as `Content-Type: image/jpeg`.

Return type: `200 image/jpeg` (inline, not a redirect - thumbnail is small, streaming is fine).

### 4. MinIO lifecycle rule (infrastructure / devops task)

Add a lifecycle rule on the `aoi_preview/` prefix with a 7-day expiry. This replaces any beat task
for TTL enforcement. Document the rule in `docker-compose.yml` or in a MinIO init script alongside
the existing bucket setup. Do not add a Celery task.

Similarly ensure the `aoi_tmp/` prefix (AOI Studio temp COGs, backlog 0021) has a lifecycle rule
with a 24-hour expiry (configure both at the same time to avoid a second infra PR).

## Acceptance criteria

- [ ] `POST /analyse/aoi/natural-color` returns a JPEG on first call and on cache hit.
- [ ] A second call with the same `scene_id` + geometry does NOT hit CDSE (cache hit confirmed by
  mock asserting CDSE adapter was called exactly once across two requests).
- [ ] Two geometries with the same vertex set but opposite winding order produce the same cache key
  (shapely.normalize collapses them).
- [ ] Two distinct geometries always produce different cache keys (no collision).
- [ ] The MinIO `aoi_preview/` lifecycle rule expires objects after 7 days. Verified by checking
  the rule is present in the compose init, not by waiting 7 days.
- [ ] MinIO `aoi_tmp/` lifecycle rule also configured (24 h) in the same commit.
- [ ] No direct HTTP call to CDSE in the endpoint handler - goes through the access port adapter.
- [ ] ruff + ruff format + mypy + pytest green.
