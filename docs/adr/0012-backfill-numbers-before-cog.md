# 0012: Commit backfill numbers before encoding COGs

Status: proposed (deferred, measurement-gated). NOT implemented. This ADR records the decision shape
so the work can start the moment production telemetry justifies it; until then it is intentionally
not built.

## Context

This is Phase 4 of the backfill-performance effort (`docs/plan/backfill-performance.md`). Phases 1,
2a, 2b, 3a and 3b shipped: a freshly onboarded field now enqueues collection on ingest (no 24h wait),
per-pass CDSE reads were cut (scene-metadata and scene-item caches, the redundant per-pass STAC search
removed), and permanent CDSE errors fail fast without tripping the breaker. All of it kept the
computed numbers byte-identical.

One latency source is left. In `run_collection` (`services/worker/tasks/collection.py`) each pass
persists its analysis rows and then, inside the same transaction, encodes and uploads up to 7 COGs (5
index rasters plus the RGB and false-colour composites) to object storage before the transaction
commits. So the numbers a farmer waits on are gated behind raster encode plus MinIO/S3 I/O, several
seconds per pass, even though the zonal statistics are already computed in memory.

Phase 3b added the gauge that tells us whether this matters in practice: `band_memo_misses` and
`wall_clock_s` are now emitted on `collection.field.complete` / `collection.pass.complete`. Phase 4 is
deliberately gated on that evidence rather than a guess.

## Decision (proposed)

When pursued, take the lightweight intra-task reorder, not a second task:

1. In `run_collection`, persist the scene metadata and per-index zonal-stat rows and **commit first**,
   stamping each row's `cog_uri` from `cog_key(...)` (deterministic, so the key is known before the
   object exists).
2. **Then** encode and upload the COGs from the already-in-memory `result.rasters` (no re-fetch, same
   task, after the commit). A failed or slow upload no longer holds the numbers back; it is retried or
   left for the next pass, and the tiler already tolerates a missing COG (it renders nothing rather
   than erroring).

The numbers appear as soon as the stats commit; the COGs follow within the same task, lagging by the
encode plus upload time.

## Why this needs an ADR

It changes a commit-ordering contract. Today a committed analysis row implies its COG exists (the row
and the object are written in one transaction). After this change a row can exist for a short window
before its COG is uploaded. Anything that assumes "row present therefore COG present" (the tiler,
audit/provenance tooling, the gateway push) must be confirmed to tolerate the gap. The tiler already
does. The provenance invariant (5) is preserved because `cog_uri` is stamped deterministically before
upload, so a stored row still names the exact raster it will produce. No section-1 invariant moves;
the ADR exists for the ordering-contract change and the cross-component check, not for an invariant.

## The gate (when to implement)

Implement only if, after Phases 1 to 3b are running in production, the 3b telemetry shows numbers
still populate too slowly: i.e. `wall_clock_s` per pass is dominated by COG I/O rather than by CDSE
reads (`band_memo_misses` already low). If reads still dominate, COG reordering buys little and the
work stays deferred. The decision is evidence-driven on purpose.

## Alternatives considered

- **A separate Celery task for COG rendering.** Rejected for now: it must either re-fetch reflectance
  (a new CDSE read, against the whole point) or carry the in-memory rasters across a task boundary
  (serialising large arrays through the broker). The intra-task reorder above gets the same
  numbers-first benefit with neither cost.
- **Do nothing (status quo).** The default until the gate above is met. Encoding stays on the
  number-critical path; acceptable if reads, not I/O, dominate wall-clock.

## Consequences

- Numbers populate faster (no wait on encode plus upload) once implemented.
- COGs lag committed numbers by minutes at most; the tiler degrades gracefully meanwhile.
- A new, narrow window exists where a row's COG is not yet uploaded; documented and bounded to the
  same task.
- Until implemented, none of the above applies: this ADR is a placeholder for a justified-by-data
  decision, not a live change.
