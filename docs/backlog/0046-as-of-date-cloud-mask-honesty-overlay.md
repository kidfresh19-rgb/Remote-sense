# Backlog 0046 — Individual-pass visualization: cloud-mask / clear-pixel honesty overlay

- Status: done (landed on develop `bccbdbe` tiler + `60a5476` frontend, pushed both remotes 2026-07-06)
- Type: backend (tiler) + frontend
- Parent: PRD 0004 (`docs/prd/0004-as-of-date-historical-anchor.md`), Extended scope, story 25
- Blocked by: None. Recommended pickup order: before 0045 - ships the "here's what's unreliable"
  signal ahead of the "here's what changed" signal, consistent with invariant 3's existing posture of
  surfacing `clear_fraction` alongside every result rather than after the fact.
- Invariants / decisions: DECIDED 2026-07-01 (owner confirmed) - masked pixels render as a
  **semi-transparent hatch**, not a solid fill and not full transparency, so the underlying imagery
  stays visible underneath and reads as "uncertain," not "hidden" or "missing." Must reuse invariant
  3's already-computed per-AOI SCL mask - this surfaces an existing artifact, it does not recompute
  cloud masking.

## Context

`clear_fraction` is already shown as a percentage badge everywhere a pass appears (`SceneRow`, the
`IndexTimeseriesChart` tooltip, the `AsOfPicker` resolution label), but a "42% clear" badge gives no
sense of *which part* of the field is unreliable. The `windowed_cog` adapter already computes an AOI
mask during fetch (`packages/rs_imagery/adapters/windowed_cog.py:358`, noted as reusable in
`docs/plan/orthophoto-download-improvements.md` P2) - this surfaces that existing computation as a
map layer instead of adding a new masking pass.

## What to build

- A toggleable overlay for the active pass showing which pixels were cloud/SCL-masked, rendered as a
  semi-transparent hatch on top of whatever base layer (index / rgb / fcc) is currently showing.
- A new tiler static/tile route (or an extension of an existing one) serving the boolean mask as a
  rendered overlay tile.
- Before implementing, confirm whether the mask can be recomputed cheaply from the already-stored SCL
  band at render time, or whether it needs to be persisted alongside the COG - prefer recompute-at-
  render if the cost is acceptable, since persisting an additional raw-derived artifact per scene
  should not be taken on without checking invariant 7 (raw bands are transient; do not grow storage
  unbounded).
- The overlay tracks the active pass and geometry version exactly - it must never show a stale mask
  left over from a different pass.

## Acceptance criteria

- [ ] A toggle shows/hides a hatch overlay marking cloud/SCL-masked pixels for the active pass.
- [ ] The overlay reuses the existing per-AOI mask rather than recomputing cloud detection from
  scratch.
- [ ] The hatch pattern is semi-transparent - the base layer remains visible underneath the masked
  area.
- [ ] The overlay updates when the active pass or geometry version changes and never shows a mask
  from a different pass.
- [ ] No new raw-band persistence is introduced (invariant 7) - the implementation either derives the
  mask from an already-stored artifact or recomputes it on demand, and the PR states which.
- [ ] `ruff check` + `ruff format --check` + `mypy` + `pytest` green on the backend half; `tsc -b`
  clean on the frontend half.
