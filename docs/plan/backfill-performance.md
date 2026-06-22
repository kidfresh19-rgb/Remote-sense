# Backfill performance: populate analysis numbers sooner (without compromising validity)

Status doc for the multi-phase work that makes backfilled analysis numbers appear in minutes
instead of next-day, and lowers the per-pass CDSE read cost, with byte-identical values. Resume
from here.

## Goal and headline metric

- **Goal:** numbers appear in minutes, not next-day; fewer CDSE reads per pass; no change to any
  computed value.
- **Headline metric:** time from `POST /ingest/farm` to the first persisted `analysis` row.
  Secondaries: CDSE reads per pass (band-memo misses) and full single-field backfill wall-clock.
- **Hard constraint (validity):** reflectance offset / quantification stay read per scene from
  metadata (invariant 2, never hard-coded); per-AOI SCL masking stays (invariant 3); no fabricated
  numbers. The CDSE quota bucket (~4 rps, `packages/rs_imagery/resilience.py`) is the global
  ceiling (ADR 0011), so "faster" means *fewer reads per pass*, not more workers.

## Status

| Phase | What | Status | Where |
|-------|------|--------|-------|
| 1 | Enqueue backfill on ingest (kill ~24h start latency) | DONE | `58c68a0` on `perf/backfill-start-latency` |
| 2a | Cross-worker scene-metadata cache + collection-adapter cache wiring | DONE | `e1e077b`, `5752d5d` on `perf/backfill-pass-redundancy` |
| 2b | Eliminate redundant per-pass STAC search | TODO | design below |
| 2c | Share one OAuth client per worker process | TODO | |
| 3a | Fail-fast on permanent CDSE errors | TODO | |
| 3b | Reads-per-pass telemetry + quota-bucket sizing | TODO | |
| 4 | COG off the number-critical path | DEFERRED (measurement-gated) | |

Nothing pushed yet. Phases 2b to 3b continue on `perf/backfill-pass-redundancy` (one PR).

## Diagnosis (the four problems)

1. **Start latency dominated (~up to 24h).** `POST /ingest/farm` set `needs_backfill=True` but never
   enqueued backfill; ingested fields waited for the daily 02:00 scan. Fixed in Phase 1.
2. **Redundant per-pass CDSE work.** `_fan_out_backfill` searches the whole window once, then each
   `collect_pass` re-runs a one-day `adapter.search` purely to re-hydrate the scene it was already
   handed. That one-day search draws a quota token (`cdse_stac.py` `_bucket.acquire()`) and an OAuth
   round-trip. Plus each task rebuilt the adapter, so the metadata XML was re-read across fields
   sharing a tile. Phase 2a fixed the metadata re-read; Phase 2b/2c address the search + OAuth.
3. **COG-on-critical-path.** `run_collection` persists analysis rows then writes 7 COGs before the
   transaction commits, so the numbers wait on raster encode + MinIO I/O. Deferred to Phase 4.
4. **CDSE quota is the global ceiling.** More workers do not help; only fewer reads/pass and latency
   hiding do (ADR 0011).

## Phase 1 (DONE)

`services/api/ingestion/service.py`: `_enqueue_field_backfills(report)` fires a best-effort
`backfill_field.apply_async(countdown=10)` for every created / derived / re-geometried field after a
non-`unchanged` ingest. `POST /ingest/farm` stays EXTERNAL-FROZEN (fires after the report is built,
broker errors logged not raised). `countdown=10` clears the `get_session` commit race; the daily
scan is the self-healing backstop. Tests in `test_ingestion_logic.py` (no-DB) +
`test_ingestion_db.py` (endpoint wiring). Note: the faster-scan-cadence idea was moved OUT of Phase 1
to Phase 2 (raising cadence before the cooldown guard would re-fan in-flight backfills).

## Phase 2a (DONE)

`WindowedCogAdapter.metadata()` now checks a Redis `cdse:scene_meta` cache (keyed by the immutable
`scene_id`, TTL `RS_CDSE_SCENE_META_CACHE_TTL_S`, default 30d) before the product-XML read, and
writes the parsed `SceneMetadata` on a miss. Wired via `_build_collection_adapter(settings)` in
`services/worker/tasks/collection.py`, which attaches the cache to the windowed_cog adapter at all
four collection entrypoints and closes it at task end (mirrors `analysis.py`'s ADR 0011
build/close). Fields sharing a Sentinel-2 tile read each scene's metadata once across all workers
instead of once per field-pass.

- **Invariant 2 holds:** the cached value was itself read per scene from metadata, never hard-coded;
  a reprocessed scene gets a new id (new key). Fail-open: a Redis hiccup degrades to a live read.
- **Side fix (`e1e077b`):** widened the `CogStore.put` Protocol to accept `content_type`, matching
  its sole implementation `S3CogStore.put` and the `render_natural_color_task` caller. This clears a
  pre-existing mypy break already on develop (from the orthophoto work).
- Tests: `test_windowed_cog_adapter.py` (cache hit skips XML read across tasks; fails open),
  `test_collection.py` (builder attaches the cache for windowed_cog, passes other adapters through).

## Phase 2b (TODO): eliminate the redundant per-pass STAC search

The win: remove one quota token + one OAuth round-trip per pass (for N passes per field backfill).

**Constraint discovered during grounding:** `collect_field` (`services/worker/collection.py`) always
calls `adapter.search`, and the `AccessPort` (`packages/rs_imagery/port.py`) defines exactly four
operations (search, metadata, fetch, preview). Cleanly removing the per-pass search either needs a
new port operation (an invariant-1 change, so an ADR) OR a no-port-change design. A no-port-change
design exists and is preferred:

- **Adapter scene-item cache.** Add an optional `scene_item_cache: RedisJsonCache` (namespace
  `cdse:scene_item`, long TTL, immutable). `search()` writes each discovered `StacItem`
  (`to_json_dict`) keyed by `scene_id`. Add an async `_resolve_item(scene_id)` that returns from
  `_items`, else loads from the cache (`StacItem.from_json_dict`) into `_items`, else raises
  `LookupError`. `metadata()`/`fetch()` call `await self._resolve_item(...)` instead of the sync
  `self._item(...)`. (Additive; same `LookupError` when no cache.)
- **Known-scene fast path in `collect_field`.** Add `scenes: Sequence[SceneRef] | None = None`. When
  provided, skip `adapter.search` and use those refs directly; on any unresolved scene, fall back to
  search (robust against cache eviction).
- **Thread the SceneRef (neutral type) through the task.** `SceneRef` is a Pydantic model
  (`model_dump_json` / `model_validate_json`), so no vendor type leaks past the adapter. Carry it
  from `_fan_out_backfill` (which already has the refs from its window search, with the real
  `sensing_datetime`) to `collect_pass`. Preserve the real `sensing_datetime` for provenance
  (invariant 5) - do NOT rebuild a SceneRef from `pass_date` alone.
- **Backward compatibility:** keep `collect_pass_task(field_id, scene_id, pass_date)` resilient to
  in-flight messages (acks_late redelivery). If the SceneRef is unavailable, the search fallback
  runs, so an old-format task still works.
- **Invariant 7 guardrail:** the band-window `_ReadMemo` stays per-task (per source instance). Only
  the OAuth client and the Redis caches are shared. Never process-scope the band memo.
- **Validity:** byte-identical numbers - same scenes, same StacItems (cache vs re-search), same
  fetch. The existing search-cache hit path already repopulates `_items` from cached StacItem JSON.

## Phase 2c (TODO): share OAuth per worker process

`WindowedCogAdapter` and `CdseStacClient` already accept `oauth=`. Build one `CdseOAuth2Client` in
`worker_process_init` (`services/worker/celery_app.py`) and thread it into `_build_collection_adapter`
so the window/forward-fill searches reuse the token instead of re-fetching per task. (OAuth is used
only by the STAC client; the S3/XML reads use S3 creds.) After 2b removes per-pass searches, this
helps the per-field window search and forward-fill polls. Keep the band memo per-task (invariant 7).

## Phase 3a (TODO): fail-fast on permanent CDSE errors

`RasterioWindowSource._make_retrying` retries any `RasterioIOError` 4x with backoff. Classify: retry
transient (timeout / 5xx / 429), fail fast on permanent (404 / NoSuchKey / AccessDenied / LTA-offline
granule). Same for `_read_bytes_quota_guarded` (the boto3 path already has adaptive retry; add the
permanent-error short-circuit). Zero-network testable with a fake source that raises classified
errors.

## Phase 3b (TODO): telemetry + bucket sizing

Surface `adapter.read_cache_stats()` (band-memo misses = real CDSE reads) in the collection task
completion logs, the way `aoi.series.complete` does for previews, so the read budget is observable
before/after. Verify `RS_CDSE_RATE_LIMIT_RPS` / `RS_CDSE_RATE_LIMIT_BURST` match the account budget
so throughput is not capped below ~300/min.

## Phase 4 (DEFERRED, measurement-gated): COG off the number-critical path

Do not ship with 1 to 3. After 1 to 3, measure the headline metric; only pursue if numbers still
populate too slowly. Recommended lightweight form: reorder `run_collection` to persist analysis rows
and commit FIRST, then encode + upload COGs from the already-in-memory `result.rasters` (no
re-fetch, same task). `cog_key(...)` is deterministic so rows can be stamped pre-upload, and the
tiler already tolerates a missing COG. Likely no invariant moves (intra-task ordering); if pursued,
write `docs/adr/0012-backfill-numbers-before-cog.md`.

## Verification (every slice)

- Gates: `ruff check`, `ruff format --check`, `mypy`, `pytest`, 85% coverage. `tests/contract/` must
  stay green (frozen boundary untouched).
- The DB-gated `*_db.py` suites `drop_all`/`create_all` on `RS_TEST_DATABASE_URL` (defaults to the
  live DB), so run them only against an isolated `remote_sense_test` DB. Phase 2 was verified with
  the no-DB suites locally; the DB-gated tests run in CI.
- Numbers unchanged: the validation matrix and adapter-parity tests must pass with no value changes.
  Phases 1 to 3 touch scheduling / caching / resilience, never the math.

## Branch / commit map

- `perf/backfill-start-latency`: Phase 1 (`58c68a0`), with two unrelated orthophoto commits stacked
  above it by separate work. Phase 1 PR should isolate `58c68a0`.
- `perf/backfill-pass-redundancy` (off develop): Phase 2a (`e1e077b` storage fix, `5752d5d`
  scene-meta cache). Phases 2b to 3b continue here.
- Decisions: Phase 1 = its own small PR first; Phases 2+3 = one PR; Phase 4 deferred. Reuse the
  existing `celery` bulk queue (no new queue).
