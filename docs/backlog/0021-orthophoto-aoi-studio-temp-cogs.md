# Backlog 0021 — Orthophoto: AOI Studio temporary index COGs

- Status: ready-for-agent
- Type: pipeline + backend
- Parent: orthophoto download + natural color preview feature
- Blocked by: 0016 (rgb_raster primitive); 0020 (lifecycle rules should be configured in the same
  infra pass)
- Invariants / decisions: §1.7 raw bands transient - AOI Studio arrays are already ephemeral; the
  temp COG is a short-lived derived artifact, not raw band persistence. §0 split-ownership sync -
  AOI Studio results have no gateway identity and are never pushed; temp COGs are likewise never
  persisted in the analysis tables. ADR 0011 Phase 1 = single-index per AOI job; emit only the
  indices the job actually computed (at most 1 per pass). COG write MUST NOT block result return.
  Off the interactive lane: use the dedicated worker queue, not the default queue. No CDSE re-fetch;
  COG is written from in-memory arrays.

## Context

AOI Studio computes zonal stats for a custom AOI across many passes. Analysts need to download the
index GeoTIFF for a given pass - the spatial raster, not just the scalar stats. Because AOI Studio
arrays are discarded after the task, the COG must be written from the in-memory arrays before they
are released.

Per ADR 0011 Phase 1, each AOI job computes a single index. This means at most 1 COG per pass per
job, not 5. The storage budget is small: a single 10 m field COG at typical field sizes is 50-200 KB.

The temp COG key scheme:
```
aoi_tmp/{job_id}/{pass_date}/{index}.tif
```

A 24-hour lifecycle rule on `aoi_tmp/` enforces expiry. The rule is already set up by backlog 0020.

## What to build

### 1. `aoi_tmp_cog_key()` in `packages/rs_core/storage.py`

```python
def aoi_tmp_cog_key(job_id: str, pass_date: str, index: str) -> str:
    return f"aoi_tmp/{job_id}/{pass_date}/{index}.tif"
```

### 2. COG emission in the AOI task (`services/worker/tasks/analysis.py` or equivalent)

After `analyze_index()` returns and zonal stats are appended to the result list for a pass:

1. Call `rgb_raster(reflectance, aoi_mask=aoi_mask)` if `index == "rgb"`, else use the index
   array directly from the analysis output path.
2. Call `write_cog(array, transform=transform, crs=crs)`.
3. Write to `aoi_tmp_cog_key(job_id=job_id, pass_date=pass_date, index=index)` via `cog_store.put`.
4. **Ordering:** the `put` must happen after the zonal stats for that pass are assembled, but before
   the in-memory arrays for that pass are released. Do not delay until the end of the whole job.
5. **Non-blocking on result return:** the COG write is a local side-effect within the pass loop.
   Do not await or join it on the result-return path if the task becomes async.
6. **Failure isolation:** a `cog_store.put` error for one pass must log a warning and continue;
   it must not fail the entire AOI job or roll back any stats.

### 3. `GET /analyse/aoi/jobs/{job_id}/passes/{pass_date}/download` (workspace BFF)

Query parameters:
- `index: str` (required)

Handler logic:
1. Verify the job belongs to the requesting user (reuse existing job-ownership check).
2. Build key: `aoi_tmp_cog_key(job_id, pass_date, index)`.
3. If the key exists, return 302 presigned redirect (same `presigned_url()` method from 0019).
4. If not (COG write failed or expired), return 404 with `{"detail": "COG not available or expired"}`.

## Acceptance criteria

- [ ] After an AOI job completes, `aoi_tmp/{job_id}/{pass_date}/{index}.tif` exists in the store
  for each pass where the index was computed.
- [ ] A `cog_store.put` failure logs a warning and the job still completes with full stats.
- [ ] Zonal stats are returned to the result before the COG write (ordering verified in test).
- [ ] `GET /analyse/aoi/jobs/{job_id}/passes/{pass_date}/download?index=ndvi` returns 302 when the
  COG is present and 404 when absent.
- [ ] Only the indices actually computed by the job are emitted (1 per pass for Phase 1); no
  speculative emission of all 5 indices.
- [ ] The MinIO 24-hour lifecycle rule on `aoi_tmp/` is in place (configured by backlog 0020).
- [ ] Test with mock adapter: no CDSE call is made during COG emission (arrays already in memory).
- [ ] ruff + ruff format + mypy + pytest green.
