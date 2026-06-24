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
| 1 | Enqueue backfill on ingest (kill ~24h start latency) | DONE, merged + pushed | `58c68a0`; merged to `integration/azure-consolidation` (`94ed4a7`) |
| 2a | Cross-worker scene-metadata cache + collection-adapter cache wiring | DONE, merged + pushed | `e1e077b`, `5752d5d`; merged to integration (`be3a6c5`) |
| 2b | Eliminate redundant per-pass STAC search | DONE | `aada28d` on `perf/backfill-pass-redundancy` |
| 2c | Share one OAuth client per worker process | N/A (won't do) | see below - the windowed_cog collection path uses no OAuth |
| 3a | Fail-fast on permanent CDSE errors | DONE | `bd952ff` on `develop` |
| 3b | Reads-per-pass telemetry + bucket sizing | DONE | see below — band memo stats surfaced in `collection.field.complete` / `collection.pass.complete` |
| 4 | COG off the number-critical path | DEFERRED (measurement-gated) | |

Everything now lives on `develop`: the `integration/azure-consolidation` consolidation was merged
into `develop`, so Phases 1, 2a, 2b, 3b and 3a are all on it. `origin/develop` is at `5c7418c`; local
`develop` is ahead 4 (2b, docs, 3b, 3a), **unpushed**. 2c is N/A; Phase 4 is deferred and 3b is its
measurement gauge.

## Diagnosis (the four problems)

1. **Start latency dominated (~up to 24h).** `POST /ingest/farm` set `needs_backfill=True` but never
   enqueued backfill; ingested fields waited for the daily 02:00 scan. Fixed in Phase 1.
2. **Redundant per-pass CDSE work.** `_fan_out_backfill` searches the whole window once, then each
   `collect_pass` re-runs a one-day `adapter.search` purely to re-hydrate the scene it was already
   handed. That one-day search draws a quota token (`cdse_stac.py` `_bucket.acquire()`). Plus each
   task rebuilt the adapter, so the metadata XML was re-read across fields sharing a tile. Phase 2a
   fixed the metadata re-read; Phase 2b removed the redundant search. NOTE: the original diagnosis
   also claimed an OAuth round-trip per pass - that was wrong. `_build_collection_adapter` builds
   `WindowedCogAdapter` with no `oauth=`, so the stored pipeline's STAC search is unauthenticated
   (the CDSE STAC catalogue is public; the S3 eodata reads use S3 keys). There is no per-pass token
   to share, which is why Phase 2c is N/A.
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

## Phase 2b (DONE, `aada28d`): eliminate the redundant per-pass STAC search

The win: removed one quota token per pass (for N passes per field backfill) - no OAuth was involved
(see 2c). Shipped as the no-port-change design below; no invariant moved, numbers byte-identical.

**What shipped:**

- **Adapter scene-item cache.** `WindowedCogAdapter` takes `scene_item_cache` (namespace
  `cdse:scene_item`, TTL `RS_CDSE_SCENE_ITEM_CACHE_TTL_S`, default 30d). `search()` write-throughs
  every discovered `StacItem` (`to_json_dict`) keyed by `scene_id`; `_resolve_item(scene_id)` returns
  from the in-process `_items`, else loads from the cache (`from_json_dict`), else raises
  `LookupError`. `metadata()`/`fetch()` now `await self._resolve_item(...)`. Fail-open.
- **Known-scene fast path in `collect_field`.** New `scenes: Sequence[SceneRef] | None`. When given,
  it skips `adapter.search`; a `LookupError` (cold cache) falls back to the search it skipped (one
  search rehydrates the whole window), so the result is identical warm or cold.
- **SceneRef threaded through the task.** `plan_backfill_scenes` now returns `list[SceneRef]`; the
  fan-out serialises each (`model_dump_json`) as the optional 4th arg of `collect_pass_task`. An
  in-flight 3-arg message (acks_late redelivery) still runs - it just takes the search fallback. The
  real `sensing_datetime` is preserved (invariant 5), never rebuilt from `pass_date`. `collect_dates`
  threads it too on the interactive lane.
- **Invariant 7 held:** the band `_ReadMemo` stays per-task; only the OAuth-free STAC item and the
  Redis caches are shared.
- Tests: `test_windowed_cog_adapter.py` (cache write-through, cross-instance resolve, fail-open,
  LookupError contract preserved), `test_collection.py` (known-scene path skips search; cold-cache
  fallback), `test_tasks_db.py` updated for the new `plan_backfill_scenes` return type.

**Original design rationale (kept for the record):** `collect_field` always called `adapter.search`,
and `AccessPort` defines exactly four operations (search, metadata, fetch, preview). A clean removal
either needed a new port op (invariant-1 change, ADR) OR the no-port-change design above. The
no-port-change design was chosen:

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

## Phase 2c (N/A - won't do): share OAuth per worker process

Dropped after grounding. Two independent reasons:

1. **The collection path uses no OAuth.** `_build_collection_adapter` builds `WindowedCogAdapter`
   with no `oauth=`, so its `CdseStacClient` runs `oauth=None` - the STAC search is unauthenticated
   (the CDSE STAC catalogue is public; the S3 eodata reads use S3 keys, not a bearer token). Only the
   `server_compute` adapter (the interactive Process-API/AOI-Studio lane) builds a `CdseOAuth2Client`,
   and it already reuses it within its instance. There is no per-task token fetch in the stored
   pipeline to share, so 2c would save nothing.
2. **The literal design would be a runtime bug.** Each Celery task runs `asyncio.run(...)` on a fresh
   event loop. A `CdseOAuth2Client` built once in `worker_process_init` owns an `httpx.AsyncClient`
   whose connection pool binds to the first loop; reusing it on the next task's loop raises
   "Event loop is closed". Sharing only the token string would need a process- or Redis-level token
   store - a security-relevant refactor not justified by (1).

Phase 2b already removed the per-pass search (the cost that mattered). If the interactive lane ever
needs cross-task token reuse, that is server_compute's concern, tracked separately, not here.

## Phase 3a (DONE, `bd952ff`): fail-fast on permanent CDSE errors

The bug was bigger than wasted retries: `RasterioWindowSource._make_retrying` retried any
`RasterioIOError` 4x, **and** `CircuitBreaker` counted any exception as a failure - so ~5 permanent
errors in a row (a deleted / forbidden / LTA-offline granule) would OPEN the breaker and pause ALL
CDSE reads for 60s on an otherwise-healthy store.

**What shipped:**
- `resilience.py`: a `PermanentError` marker. `CircuitBreaker.call`/`call_sync` treat it as a health
  *success* - a definitive 404/AccessDenied means CDSE *answered*, so it is reachable: never trip on
  missing data, break any failure streak, and never deadlock a half-open probe. Re-raised for the
  caller to handle.
- `windowed_cog.py`: `_is_permanent_read_error` (conservative substring classifier - unmatched
  defaults to transient, so a recoverable read is never wrongly dropped) and `_is_permanent_s3_error`
  (boto3 `ClientError` code/status). `_read_window_once` classifies a `RasterioIOError` and re-raises
  permanent ones as `PermanentReadError` (not a `RasterioIOError`, so the retry skips it; a
  `PermanentError`, so the breaker spares it); `_open_and_read` holds the raw GDAL read. The boto3 and
  http metadata-byte paths classify the same way.
- Tests (zero-network): `test_imagery_resilience.py` (breaker does not trip on `PermanentError`, and
  the permanent error breaks the failure streak); `test_windowed_cog_adapter.py` (classifier units; a
  permanent windowed read fails after 1 attempt with the breaker spared; a transient one retries 4x
  and trips the breaker).
- Validity: no index math touched, no invariant moved, numbers byte-identical - only error handling
  changed. A permanently-missing scene still fails its pass (fast now), and is re-planned next scan;
  *skipping* such scenes permanently is a separate feature, out of 3a scope.

## Phase 3b (DONE): telemetry + bucket sizing

`_adapter_read_stats(adapter)` (mirrors `_band_memo_stats` in `analysis.py`) reads the band memo
hit/miss counts from `adapter.read_cache_stats()` defensively via `getattr` (invariant 1 — mock /
server_compute adapters have no memo and return `None`, which is fine). `CollectionSummary` carries
`band_memo_hits` / `band_memo_misses`; `_summary_dict` serialises both onto the Celery result dict;
`run_collection` stamps them on the summary after each field collection.

Two new structured log events:
- **`collection.field.complete`** (from `_run_for_field`): `field_id`, `is_backfill`, `scenes`,
  `analyses`, `locked`, `wall_clock_s`, `band_memo_hits`, `band_memo_misses`.
- **`collection.pass.complete`** (from `_collect_one_pass`): same fields plus `scene_id`,
  `pass_date`.

`misses` is the real CDSE read count (band-memo misses = S3 eodata reads). Before Phase 3b, nobody
could observe this number at runtime; now every completed task emits it. This is the gauge that
Phase 4 (`COG off the critical path`) is gated on — if `misses` per pass is already low, Phase 4
may not be worth the complexity.

For the `RS_CDSE_RATE_LIMIT_RPS` / `RS_CDSE_RATE_LIMIT_BURST` bucket sizing: once `band_memo_misses`
per pass is observable in production logs, compare against the account's actual CDSE quota (typically
~4 rps sustained, ~10-burst) and tune if needed. Tests: `test_collection.py` (`_adapter_read_stats`
returns None for mock; `CollectionSummary` carries the counts; `_summary_dict` serialises them).

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

- `integration/azure-consolidation` (pushed to `origin`): the consolidation merged Phase 1 (via
  `94ed4a7`, bundled with the two orthophoto commits) and Phase 2a (via `be3a6c5`). This is where 1 +
  2a actually live now; they are NOT on `develop`. The earlier "Phase 1 PR isolates `58c68a0`" plan
  was overtaken by the consolidation - if 1/2a ever need a clean landing on `develop`, that is a
  separate cherry-pick decision.
- `perf/backfill-start-latency`: Phase 1 (`58c68a0`) with two orthophoto commits stacked above it.
- `perf/backfill-pass-redundancy` (off develop): Phase 2a (`e1e077b`, `5752d5d`) + Phase 2b
  (`aada28d`). 3a/3b continue here. Unpushed.
- Decisions: Phases 2+3 = one PR; 2c dropped (N/A); Phase 4 deferred. Reuse the existing `celery`
  bulk queue (no new queue); the `interactive` queue isolation already exists (`celery_app.py`).
