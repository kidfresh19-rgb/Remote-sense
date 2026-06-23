# Plan — Orthophoto download: hardening and follow-ups

Status: plan only (no execution authorized yet). Owner decisions captured below.
Date: 2026-06-23. Supersedes the design exploration in the earlier claude.ai chat.

## 0. Where the feature stands (shipped on `develop`)

The "orthophoto download next to CSV" feature is **already implemented and merged to `develop`**.
Both decisions from the earlier planning chat were honored: both formats (JPEG + GeoTIFF) and a
single, analyst-selected pass.

| Commit | What landed |
|--------|-------------|
| `47c4d9f` | RGB natural-color preview + COG download pipeline (backlog 0016-0023) |
| `895d40c` | `/analyse/aoi/natural-color` serves RGB reflectance COG **and** JPEG from one band read |
| `7268bb2` | `AOIResultsTable.tsx` Image + GeoTIFF buttons + natural-colour filmstrip pass picker |

Key anchors:
- Endpoint: `services/api/workspace/analyse.py:159` (`format=jpeg|cog`, cache-hit proxy, cache-miss render).
- Render task: `services/worker/tasks/analysis.py:795` (`render_natural_color_task`), split helpers
  `_render_rgb_cog:170` and `_jpeg_from_cog:184`.
- RGB primitive: `packages/rs_analysis/cog.py:47` (`rgb_raster`, already supports `aoi_mask`).
- Storage keys: `packages/rs_core/storage.py:24` (`aoi_preview_key`, `.jpg`) and `:30`
  (`aoi_rgb_cog_key`, `.tif`), both under the `aoi_preview/` 7-day-TTL prefix.
- Frontend: `frontend/src/components/aoi/AOIResultsTable.tsx` `downloadOrthophoto:68`, buttons `:233`,
  filmstrip `:436`.
- Tests: `tests/test_aoi_download.py` (auth, cache-hit JPEG/COG, cache-miss render both formats,
  synthetic render smoke).

What is genuinely good and must be preserved through any change:
- Correct band order (R=B04, G=B03, B=B02).
- The COG stores **unstretched float32 reflectance**; the 0-0.3 display stretch is applied **only**
  for the JPEG (`_jpeg_from_cog`). Radiometric integrity is intact (invariant 2).
- The JPEG is rendered **from** the COG, so the two artifacts cannot diverge.
- The filmstrip eagerly pre-warms the render, so a GeoTIFF click is usually a cache hit.

The original gap (custom AOIs could not download an orthophoto) is **closed**. Registered fields
already had `GET /fields/{id}/scenes/{scene}/download?index=rgb` (`services/api/workspace/fields.py:371`);
custom AOIs now have parity.

## 1. Owner decisions (recorded)

- **GeoTIFF extent: clip to the drawn polygon** (not the bounding box). Pixels outside the AOI
  polygon become NoData. Rationale: a non-rectangular AOI currently ships neighbours' land in the
  download; clipping makes the file true to what the analyst selected and matches how zonal stats
  treat the AOI.
- **Execution: none yet.** This document is the artifact; build is deferred until the owner picks a
  slice.

## 2. Improvements (prioritized)

### P1 - Frontend error feedback (defect)

Problem: `downloadOrthophoto` (`AOIResultsTable.tsx:68-97`) swallows every failure. `if (!resp.ok)
return;` (`:84`) discards 502/504 silently, and there is no `catch`, so a network throw stops the
spinner with no file and no message. The analyst sees a button spin, then nothing.

Fix sketch:
- Wrap the fetch in `try/catch`; on `!resp.ok` read the error body and on `catch` surface a toast
  via whatever toast/notification utility the workspace already uses (grep `frontend/src` for the
  existing toast hook before adding one; do not introduce a new dependency).
- Keep the `finally { setOrthoFormat(null) }` reset.
- Message copy: distinguish the cold-render timeout ("Still preparing this image, try again in a
  moment.") from a hard failure ("Could not generate the orthophoto."). No em-dashes in copy.

Acceptance: a forced 504 and a forced network error each raise a visible toast; the spinner always
clears. Lint + typecheck green.

### P1 - Verify DN==0 -> NoData on the RGB COG (invariant 2)

Problem: `rgb_raster` (`cog.py:47`) stacks reflectance bands directly. Invariant 2 says `DN == 0` is
NoData. Confirm that zero-DN pixels are already NaN by the time `fetched.data.bands` reaches the
render (the reflectance conversion in `rs_analysis` should do this), and that `write_cog`'s
`nodata=NODATA` (`cog.py:105`) round-trips them as nodata in the GeoTIFF.

Fix sketch: add a `qa-engineer` test feeding a synthetic band with a `DN==0` patch through the
render and asserting those pixels are nodata in the decoded COG. If the conversion does **not** zero
them, add the zero-mask in the rgb path (cheap), but verify first - do not double-mask.

Acceptance: test proves zero-DN pixels are nodata in the downloaded GeoTIFF.

### P2 - Clip the GeoTIFF to the AOI polygon (decided: clip)

Problem: `render_natural_color_task._run` calls `_render_rgb_cog(fetched.data.bands, ...)` with no
`aoi_mask` (`analysis.py:828`), so the COG covers the fetched bounding-box window. The index COG path
has the same characteristic (it relies on `clear_fraction` from the adapter and does not pass a mask
to `analyze_index`, `analysis.py:238`), so there is no existing surfaced mask to reuse.

Fix sketch (self-contained in the task):
1. Build a boolean mask matching the fetched window: rasterize the AOI geometry (reproject from
   `EPSG:4326` to `fetched.data.crs`) against `fetched.data.transform` and the band shape, e.g.
   `rasterio.features.geometry_mask(..., invert=True)`. True = inside polygon.
2. Pass it through: `_render_rgb_cog(bands, transform, crs, aoi_mask=mask)` ->
   `rgb_raster(bands, aoi_mask=mask)` (the primitive already sets outside-polygon pixels to NODATA,
   `cog.py:62-63`). Add the `aoi_mask` param to `_render_rgb_cog`.
3. The JPEG derives from the masked COG, so the thumbnail shows the clipped shape too (acceptable;
   the masked area renders as nodata/black after stretch). Confirm this looks right in QGIS and the
   filmstrip before shipping.

Note / optional broader consistency: the cleaner long-term move is to surface the `aoi_mask` the
`windowed_cog` adapter already computes during fetch (`adapters/windowed_cog.py:358`) on the
`FetchResult`, so both the index COGs and the rgb COG clip identically. That is a larger change
touching the port contract; keep it as a separate ADR-gated follow-up, not part of this slice.

Acceptance: a triangular AOI produces a GeoTIFF whose pixels outside the triangle are nodata;
rectangular AOIs are visually unchanged. Adapter-parity and synthetic render tests green.

### P3 - Retroactive RGB COG backfill (backlog 0024, now unblocked)

The blocker named in `docs/backlog/0024-orthophoto-retroactive-rgb-cog-backfill.md` (a dedicated
non-default worker queue so the backfill cannot starve `collect_pass`) has landed: see the
`task_queues` / `task_routes` config in `services/worker/celery_app.py:46` and the `interactive`
queue used by `collect_pass` (`services/worker/tasks/collection.py:602`). 0024 can now be built per
its own acceptance criteria. The critical correctness rule there still stands: write `rgb.tif` at
the `geometry_version` from the analysis row, not the field's current version, or the COG is
orphaned. This is a `pipeline-engineer` slice.

### P4 - All-passes download (deferred by earlier decision)

The earlier chat chose single-pass (Option 1) and deferred whole-series download. If demand
materializes, add a "download all passes" action next to CSV that returns a zip of per-pass GeoTIFFs
(or a contact-sheet PDF). This multiplies CDSE/Celery load, so it should reuse the cache (only render
passes whose COG is missing) and run off the interactive queue. Separate ticket, not scheduled.

### P5 - Cold-start UX polish

First GeoTIFF on a never-viewed pass blocks up to 90s on `task.get(timeout=90)` holding a Starlette
threadpool thread (`analyse.py:210`). The filmstrip pre-warm mitigates this. If it bites in practice,
switch the cold path to 202 + poll (mirroring `analyse_aoi_series_endpoint`, `analyse.py:242`) or show
a "preparing your GeoTIFF" state. Low priority.

## 3. Housekeeping

The branch `feat/orthophoto-aoi-download` (commits `2fb5a4c`, `025060e`) is now fully duplicated on
`develop` as `7268bb2` / `895d40c`. It is redundant. Delete it locally and on the `github` mirror to
remove drift (the dual-remote drift is already tracked in memory). Confirm `git branch --merged
develop` shows its content is reachable before deleting.

## 4. Suggested rollout order (when execution is authorized)

1. P1 fast lane: error toast + DN==0 verification test (Build + Verify, no product risk).
2. P2 polygon clipping (geospatial-engineer + qa-engineer parity test; manual QGIS check on a
   non-rectangular AOI).
3. P3 backlog 0024 (pipeline-engineer), groomed via `/triage` first since its status is
   `needs-triage`.
4. P4 / P5 only if demand or a real performance complaint appears.

Each slice follows the Section 6 pipeline: small Conventional Commits, ruff + ruff format + mypy +
pytest green, `/code-review` before merge, push to both remotes.
