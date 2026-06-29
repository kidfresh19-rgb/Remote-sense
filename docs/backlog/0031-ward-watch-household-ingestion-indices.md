# Backlog 0031 — Ward Watch: household ingestion and per-household indices

- Status: built 2026-06-29 (full slice on `feat/ward-watch-movement-lens`: per-plot PlotAnalysis store,
  worker ingestion reusing the AOI engine, 0026 declarations reconcile, centroid ward + NR placement)
- Type: pipeline
- Parent: PRD 0003 (`docs/prd/0003-ward-watch.md`), Phase 1
- Blocked by: 0027, 0029 (both built); the §12.1 gateway inbound contract (0026, built candidate)
- Invariants / decisions: §1.2 reflectance-first, §1.3 per-AOI SCL cloud masking with clear-pixel
  fraction stored, §1.4 resolution honesty, §1.5 provenance on every result, §1.7 raw bands transient.
  Reuse the existing analysis pipeline and AOI Studio primitives.

## Context

Once households exist with proxy geometries, the existing pipeline runs them as a new AOI source to
produce per-household index series. This is the tracer that proves Ward Watch data flows end to end
through the unchanged scientific core.

## What to build

- Read household identity and geometry (gateway is the authority), assign ward (0027) and NR.
- Materialise per-household AOIs and run the existing analysis pipeline to the index series per plot.
- Attach the §4 pixel-count quality flag (from 0028) to every per-household stat.
- Stamp full provenance (provider, scene id, processing mode, formula version, geometry version).

## Acceptance criteria

- [x] A synthetic household yields an index series with clear-pixel fraction and provenance stored.
      (`PlotAnalysis` via `ingest_household_plots`; `test_ward_watch_ingest_db.py`)
- [x] A 2-pixel plot is flagged low-quality and its stat is suppressed or marked, never shown as
      confident (PRD 0003 §4). (`low_pixel_quality` from `pixels < proxy_aoi.MIN_USABLE_PIXELS`)
- [x] No raw bands persist after derivation. (the unchanged AOI engine derives and discards; §1.7)
- [x] Runs with the mock adapter and synthetic arrays: zero network, zero real DB in unit tests.
- [x] ruff + ruff format + mypy + pytest green.

## Built

- `rs_core.models.PlotAnalysis` (plot-keyed, provenance + clear_fraction + §4 `low_pixel_quality`) +
  `Household.dominant_nr`; migration `0012` (up/down/up verified).
- `rs_core.repositories.plot_analyses.upsert_plot_analysis` (additive, idempotent) + `plot_index_series`.
- `rs_core.repositories.households.reconcile_household_declarations` (value-based; folds the 0026
  declarations: canonical id, crop mix -> `dominant_crop`, planting -> `planting_window`).
- `rs_core.repositories.regions.assign_households_by_centroid` (the centroid-containment placement 0027
  deferred to 0031: ward + Natural Region from the plot-union centroid).
- `services/worker`: `plot_persistence.plot_series_upsert_kwargs` + `tasks/ward_watch.py`
  (`ingest_household_plots[_task]`, `reconcile_ward_declarations_task`), reusing `_analyse_aoi_series`.

> Geometry note: 0031 operates on existing geometry-bearing Plot rows (from enrollment / seed); the
> declarations reconcile carries no geometry (the 0026 contract is geometry-free). The triage / rollup
> endpoints stay empty until **0032** assembles cohorts + runs the movement lens over these series.
