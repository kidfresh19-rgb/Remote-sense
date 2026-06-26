# Code review - Phase 4 landing (S4.1, S4.7, S4.2/S4.4, S4.6)

Date: 2026-06-12. Reviewer pass over `git diff 09dd56e..HEAD` (the day's 10 commits closing the
runnable Phase 4 backlog). High-effort recall review: catch every real bug a careful reviewer
would catch in one sitting. Verdict below, then the angles worked and what each cleared.

## Verdict

**No confirmed or plausible correctness bugs. Cleared to merge.** Two non-blocking observations
recorded at the end (one pre-existing, one an inherent design tradeoff); neither is introduced
by this diff and neither blocks the land.

Quality gates at the reviewed HEAD: 457 passed / 4 skipped, coverage 88.44% (floor 85), ruff +
format + mypy clean, the OpenAPI contract diff gate green, and a security review with no
findings (the one non-ASCII-key edge it surfaced was fixed pre-merge in `5d0576a`).

## What was checked, by angle

### Line-by-line (correctness)

- **Upsert created-vs-refreshed signal** (`repositories/analyses.py`). `(xmax = 0)` is illegal
  in RETURNING through a partitioned parent, replaced by `(created_at = now())`. Verified sound:
  `created_at` is written only by the insert default (`func.now()` = `transaction_timestamp()`),
  the refresh SET list never touches it, and `now()` is constant within a transaction. A
  freshly inserted row therefore reports True, a pre-existing row False. The one edge case
  (re-upserting an identity inside the same transaction that created it would report True) is
  documented in code and is unreachable: the pipeline writes each identity once per run in its
  own transaction.
- **Partition date math** (`partitions.py` `add_months`/`month_floor`/`plan_partition_months`).
  Floor division and modulo handle negative month offsets correctly (Python `//`/`%` floor
  toward negative infinity, so `total % 12` stays 0-11). Year-wrap at December verified
  (Dec + 1 -> Jan next year). The window loop terminates and is contiguous/ascending.
- **Ingest auth** (`ingestion/service.py` `require_ingest_key`). Deny-by-default under
  enforcement (missing or empty key -> 401, unconfigured key -> fail-closed 500); log-only
  passthrough when off. Bytes-vs-bytes `compare_digest` so a non-ASCII (latin-1) header can
  never raise TypeError -> 500. Pinned both modes in `tests/contract/test_ingest_auth.py`.

### Removed-behavior auditor

- **Four single-column indexes -> one composite** `(field_id, index_name, pass_date)`. Walked
  every query path against the new index set: `field_timeseries` (field_id+index_name, order
  pass_date) is an exact match; `field_scenes`, `field_as_of`, `field_audit`,
  `processed_scene_ids`, the farm-level analytics joins, `publish_utils`, and `interpret` all
  lead with `field_id` (the composite prefix) or are served by `uq_analysis_identity`. No hot
  path filters by `scene_id` alone (its FK keeps no index by design; `scene_metadata` rows are
  never deleted, so there is no reverse scan to serve). `retention`'s `WHERE cog_uri IS NOT
  NULL` was always a full scan and remains one (weekly batch). No path lost a usable index.
- **`get_session` -> `get_read_session` on the read endpoints.** `get_read_session` never
  commits. Confirmed every switched endpoint is genuinely read-only: `list_fields`,
  `field_timeseries`/`scenes`/`as-of`/`audit`, `list_farms` (-> `get_farm_analytics_summary`,
  SELECT + in-memory `classify`), and `mobile_data` (-> `farm_satellite_results` ->
  `fetch_farm_results_with_farm_averages` + `published_narratives_for_farm`, both SELECT-only).
  No lazy write or flush side effect depends on the dropped commit.

### Cross-file tracer

- **Composite PK `(id, pass_date)`.** `retention.py`'s `session.get(Analysis, (id, pass_date))`
  matches the PK in mapper order (id first, pass_date second). Nothing keys off `analysis.id`
  alone across files.
- **Celery beat <-> task names.** `maintenance.ensure_analysis_partitions` and
  `collection.scan_and_enqueue` in the beat schedule match their `@celery.task(name=...)`
  registrations exactly. A mismatch would silently never fire; they match.
- **`run(args, payload)` signature change** (`loadtest/__main__.py`). Exactly one caller
  (`main`), updated. Tests exercise `parse_headers` and the op factories, not `run`.
- **TestClient suites.** The only ones are the contract/auth suites (which override both
  `get_session` and `get_read_session`, or assert 401-before-DB) and the tiler suite (no DB
  dependency at all). No suite drives a read-routed endpoint through an un-overridden read
  session.
- **`require_ingest_key` scope.** Added only to `POST /ingest/farm`; the `POST /api/v1/mobile/
  sync` path into `ingest_farm` keeps its own `require_agritrack_key`. No double gate, no gap.

### Reuse / simplification / efficiency / altitude

- **Month-math duplication** (`partitions.py` vs migration `0008`) is deliberate and correct:
  an alembic migration must stay frozen against future code movement, so it inlines its own
  copy rather than importing app code that can change underneath it. Noted, not flagged.
- **`require_ingest_key` vs `require_agritrack_key`** are near-duplicates. A future refactor
  could lift one parameterized shared-key dependency into `services/api/auth.py`. Left as is:
  the two gate different routes with different enforcement semantics (mobile is always-on;
  ingest is behind the migration flag), and collapsing them now would entangle the frozen
  mobile gate with an in-flight migration. Revisit once `RS_INGEST_REQUIRE_KEY` flips to true.
- **`get_read_*` lru_cache layering** is correct: when no replica is configured,
  `get_read_engine()` returns the primary engine object itself, so the default deployment runs
  one pool, not two.

## Non-blocking observations (not introduced by this diff)

1. **Upsert does a second round trip** (`repositories/analyses.py`): `INSERT ... ON CONFLICT
   ... RETURNING (created_at = now())` to get the bool, then a separate `SELECT` for the row.
   `RETURNING *` could fold both into one round trip. This is the pre-existing shape (the old
   `(xmax = 0)` code re-selected the same way); this diff only changed the boolean expression,
   not the round-trip count. Worth a follow-up if the per-pass write rate ever becomes a
   bottleneck; the S4.7 load test did not flag it.
2. **Partition-wide index probing for unbounded-date reads.** A `field_id`-only query with no
   `pass_date` bound (timeseries, audit, scenes) probes every monthly partition's composite
   index rather than one index on a single table. This is the inherent cost of the S4.1 design
   and the reason it scales to the R16 target (each probe is a cheap index seek returning few
   rows). Documented in `docs/plan/S4.1-partition-analysis.md`; acceptable by design.
