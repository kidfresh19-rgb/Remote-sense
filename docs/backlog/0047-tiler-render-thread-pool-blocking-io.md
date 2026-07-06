# Backlog 0047 — Tiler render routes block the event loop on synchronous COG reads

- Status: needs-triage
- Type: backend (tiler) — async correctness / performance
- Parent: PRD 0004 (`docs/prd/0004-as-of-date-historical-anchor.md`), Extended scope; surfaced by the
  `/code-review` of backlog 0045/0046 (2026-07-02).
- Blocked by: None.
- Invariants / decisions: no invariant touched. Render-only, no stored analysis, no provenance
  change. The fix must not alter any tile's pixels or any route's status codes — it only moves the
  existing blocking read off the event loop.

## Context

Re-filed 2026-07-06. This item was first raised by the code-review of the pass-difference (0045) and
cloud-mask (0046) render paths, filed as `docs/backlog/0047-*`, but that file only ever existed
uncommitted in the abandoned `feat/pass-visualization` worktree, so it was lost when develop was
fast-forwarded. The finding still holds against `develop`; this restores it.

The tiler's rendering routes are declared `async` but call their synchronous `rio-tiler` read +
colorize functions **directly**, so each request blocks the FastAPI event loop for the whole
duration of a (network-bound, `/vsis3/`-backed) COG window read. Under concurrent map traffic — the
exact load the tiler was split out to absorb (`services/tiler/main.py` module docstring) — one slow
read stalls every other in-flight request on the same worker.

On `develop`, the affected routes in `services/tiler/main.py` are:

- `static_preview` → `render_preview(...)` (blocking, not offloaded)
- `tile` → `render_tile(...)` (blocking, not offloaded)
- `mask_tile` → `render_mask_tile(...)` (blocking, not offloaded)
- `diff_tile` → `render_diff_tile(...)` (blocking, and the **worst case**: `render_diff_tile` in
  `services/tiler/render.py` opens and reads **two** COGs sequentially — `Reader(source_a)` then
  `Reader(source_b)` — so it holds the loop for two serial network reads per tile).

The correct pattern already lives in the same file: `export_cog` wraps its object-store calls in
`await run_in_threadpool(...)`. The render routes were simply never given the same treatment.

## What to build

- Wrap each blocking `render_*` call in `services/tiler/main.py` in `await run_in_threadpool(...)`
  (already imported), so the rio-tiler read runs on the threadpool instead of the event loop. Covers
  `render_preview`, `render_tile`, `render_mask_tile`, and `render_diff_tile`.
- For `render_diff_tile`'s two sequential reads: at minimum move the whole function off the loop via
  `run_in_threadpool` (one hop, both reads run in the worker thread). Optionally read the two COGs
  concurrently, but only if it stays inside the threadpool and does not change pixel output — the
  simple single-offload is the accepted-scope fix; concurrent reads are a nice-to-have, not required.
- Exception mapping is unchanged: `RasterStackUnavailable` → 503, `TileUnavailable` → 404. The
  `run_in_threadpool` await must sit inside the existing `try/except` so those still translate.

## Acceptance criteria

- [ ] `static_preview`, `tile`, `mask_tile`, and `diff_tile` no longer call a synchronous
  `render_*` function directly on the event loop; each goes through `run_in_threadpool`.
- [ ] Tile/preview bytes are byte-for-byte identical to before for the same inputs (no pixel or
  colormap change).
- [ ] 404 (unknown index / missing COG / outside coverage) and 503 (raster stack absent) responses
  are unchanged for every route.
- [ ] A concurrency test shows a slow render on one request no longer serializes other in-flight
  tiler requests on the same worker (prior art: existing tiler route tests).
- [ ] `ruff check`, `ruff format --check`, `mypy`, and `pytest` clean.
