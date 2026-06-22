# Backlog 0024 — Orthophoto: retroactive RGB COG backfill (gated)

- Status: needs-triage
- Type: pipeline
- Parent: orthophoto download + natural color preview feature
- Blocked by: queue isolation work (fix/worker-queue-isolation or equivalent) must land first.
  Do NOT start this slice until the dedicated low-priority worker queue is in place and verified.
- Invariants / decisions: §1.7 raw bands transient - existing passes have no B02/B03/B04 on disk;
  re-fetch is required via `windowed_cog` adapter. CDSE quota budget is the binding constraint.
  COG must be written at the `geometry_version` the existing index COGs for each pass were stored
  under, NOT the field's current geometry_version (key mismatch would orphan the RGB COG silently).

## Context

Backlog 0017 ensures new passes get an RGB COG. Existing passes (collected before 0017 lands) have
index COGs (`ndvi.tif`, etc.) but no `rgb.tif`. The passes list and AOI Studio will show
"Preview pending" for these passes until either:
  a) the backfill runs and writes the missing `rgb.tif`, or
  b) the analyst re-collects the pass.

The backfill is CDSE-bound: raw bands were discarded per invariant 7, so each pass requires a fresh
`windowed_cog` fetch of B02, B03, B04 from CDSE. On a large farm estate this is a significant quota
draw. Running it on the default queue reproduces the `collect_pass` starvation problem the queue
isolation work is solving.

The UI degrades gracefully ("Preview pending") so there is no functional urgency. Do not rush this
slice to get thumbnails faster at the cost of breaking the collection pipeline.

## What to build

### 1. Backfill task (`services/worker/tasks/backfill_rgb.py`)

```python
@shared_task(queue="low_priority", bind=True, max_retries=5)
def backfill_rgb_cog(self, field_id: str, scene_id: str, geometry_version: int) -> None:
    """
    Fetch B02/B03/B04 via windowed_cog, write rgb.tif at the geometry_version the existing
    index COGs were stored under, and put to the COG store.

    Retry with exponential backoff on CDSE 429/503. The task is idempotent: if
    aoi/v{geometry_version}/{field_id}/{scene_id}/rgb.tif already exists, return immediately.
    """
```

Idempotency check: call `cog_store.exists(cog_key(..., index="rgb", ...))` before fetching.

Rate: enqueue with a `countdown` derived from a per-field throttle, or use `apply_async` with
`rate_limit="X/m"` - whichever the queue isolation work establishes for the low-priority lane.

### 2. Backfill management command

A one-time admin script or management command that enqueues `backfill_rgb_cog` for all
`(field_id, scene_id, geometry_version)` tuples that have at least one index COG but no `rgb.tif`:

```sql
-- source of truth: join analysis rows to get the geometry_version per (field_id, scene_id)
SELECT DISTINCT a.field_id, a.provider_scene_id, a.geometry_version
FROM analyses a
WHERE NOT EXISTS (
  SELECT 1 FROM cog_store_index
  WHERE field_id = a.field_id
    AND scene_id = a.provider_scene_id
    AND index = 'rgb'
    AND geometry_version = a.geometry_version
)
```

(Adjust query to however the cog_store key existence is determined - either a side-table or a
MinIO list prefix call.)

### 3. geometry_version correctness (critical)

The `geometry_version` passed to `backfill_rgb_cog` MUST come from the analysis row, not from the
current `Field.geometry_version`. If the field boundary changed after a pass was collected, the
existing index COGs are at the old `geometry_version`. Writing `rgb.tif` at the new version creates
a key the tiler never reads, orphaning it silently.

Source of truth: `SELECT geometry_version FROM analyses WHERE field_id = ? AND provider_scene_id = ? LIMIT 1`.

## Acceptance criteria

- [ ] `backfill_rgb_cog` is idempotent: re-running for a pass that already has `rgb.tif` is a no-op.
- [ ] The task runs on the low-priority queue, not the default queue.
- [ ] A CDSE 429 triggers retry with exponential backoff; exhausted retries log and do not crash.
- [ ] The `geometry_version` on the written COG key matches the `geometry_version` stored on the
  analysis row, not the field's current version.
- [ ] After the backfill script runs on a test dataset, `rgb.tif` is present for all passes that
  previously had only index COGs.
- [ ] Queue isolation (the prerequisite) is confirmed green before this slice is started.
- [ ] ruff + ruff format + mypy + pytest green.

## Follow-on note

Once this slice is complete, the "Preview pending" state in the UI (backlog 0022 and 0023) should
gradually resolve as the backfill processes, without any UI changes needed.
