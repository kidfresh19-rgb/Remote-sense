# Backlog 0017 — Orthophoto: pipeline RGB COG emission

- Status: ready-for-agent
- Type: pipeline + geospatial
- Parent: orthophoto download + natural color preview feature
- Blocked by: 0016 (rgb_raster primitive)
- Invariants / decisions: §1.7 raw bands transient - emit COG from in-memory reflectance; never
  re-fetch from CDSE just to write the RGB COG. §1.4 resolution honesty - B02/B03/B04 are all 10 m;
  COG is stored at 10 m. §1.5 provenance - COG key encodes `geometry_version` so boundary changes
  produce distinct objects. COG key scheme: `cog/v{geometry_version}/{field_id}/{scene_id}/rgb.tif`.
  The `write_cog` + `S3CogStore.put` pattern already used for per-index COGs.

## Context

The collection pipeline (`services/worker/tasks/analysis.py`) already writes one COG per index
(e.g. `ndvi.tif`, `savi.tif`). B02, B03, and B04 - the three RGB bands - are already fetched and
in memory as part of normal collection because indices like EVI2 and NDVI use them. The RGB COG is
therefore free in terms of CDSE quota: it is written from the arrays already in memory, not a
separate fetch.

The sole correctness risk is ordering: the COG write must not block the zonal-stats return, and the
write must use the same `geometry_version` as the index COGs written for the same pass (not the
field's current version if they diverge).

## What to build

In the field collect pipeline (the code path that calls `analyze_index` + `cog_store.put` for each
index), add RGB COG emission after all index stats have been computed and returned:

1. After all `AnalysisOutput` objects are collected and ready to persist, call
   `rgb_raster(reflectance, aoi_mask=aoi_mask)` on the in-memory reflectance dict.
2. Pass the result to `write_cog(rgb_array, transform=transform, crs=crs)` to get COG bytes.
3. Build the key: `cog_key(field_id=field_id, scene_id=scene_id, index="rgb", geometry_version=geometry_version)`.
4. Call `cog_store.put(key, cog_bytes)` - same store used for index COGs.
5. **Do not block the result row persistence on the COG write.** Write index COG bytes and persist
   zonal stats first. Emit the RGB COG last (or fire it into a background coroutine / after the DB
   commit). The DB record is the durable artifact; the RGB COG is a derived visual asset.

The `geometry_version` used for the RGB key MUST be the same value used for the index COGs written
in the same pass. This is always the case if retrieved from the same field record before the task
begins; document the lookup explicitly so a future refactor cannot silently use a stale value.

Do not emit the RGB COG if B02, B03, or B04 is absent from the reflectance dict (some indices do not
require all three). Log a warning and continue; never fail the collect task over a missing visual.

## Acceptance criteria

- [ ] After a field collect task completes, `cog/v{geometry_version}/{field_id}/{scene_id}/rgb.tif`
  exists in the COG store.
- [ ] The RGB COG uses the same `geometry_version` as the index COGs for that pass.
- [ ] A failure in the RGB COG write (network, store unavailable) does not fail the collect task or
  roll back the zonal stats.
- [ ] If B02, B03, or B04 is missing from the reflectance dict, a warning is logged and the task
  continues cleanly.
- [ ] Zonal stats are persisted before the RGB COG write is attempted (ordering).
- [ ] Integration test with the mock adapter confirms `rgb.tif` is present in the mock store after
  `collect_pass` runs.
- [ ] ruff + ruff format + mypy + pytest green.
