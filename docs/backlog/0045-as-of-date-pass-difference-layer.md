# Backlog 0045 — Individual-pass visualization: pass-to-pass difference/change layer

- Status: ready-for-agent
- Type: backend (`rs_analysis` / tiler) + frontend
- Parent: PRD 0004 (`docs/prd/0004-as-of-date-historical-anchor.md`), Extended scope, story 24
- Blocked by: None
- Invariants / decisions: DECIDED 2026-07-01 (owner confirmed, no longer open) -
  **no implicit default pairing.** This extends `SceneCompare` with a third display mode; the diff
  renders only once both A and B passes are explicitly selected via the pickers that already exist
  there. It never guesses which two passes to diff - the same posture invariant 4 already takes
  elsewhere (never fabricate a pass; show explicit absence instead of a guess).
  **Colormap: symmetric diverging, centered on zero**, clamped to
  `±get_colormap(index).vmin/.vmax` (`packages/rs_analysis`, the index's already-locked display
  range) - decline and growth get equal visual weight.
  Render-only: reads two already-computed index COGs and subtracts them for display. No new stored
  analysis and no provenance change (invariant 5 is untouched - nothing new is persisted); invariant 2
  is untouched because the reflectance conversion already happened upstream when each index COG was
  produced.

## Context

`SceneCompare` already puts two passes side by side on synced cameras (`syncCameras`), but the
analyst still has to mentally subtract two images to see what changed. A literal difference raster
- index(B) minus index(A), rendered as its own colored map - makes decline or growth visually
explicit instead of relying on the analyst's eye to spot it across two panes.

## What to build

- A third mode in `SceneCompare`, alongside the existing side-by-side mode, selectable only once both
  the A and B pass pickers have a value (reuse the existing `PassPicker` components - no new
  selection UI).
- A new tiler render path that reads both index COGs for the same field and geometry version and
  renders their pixel-wise difference (B minus A) with the symmetric diverging colormap described
  above.
- Switching between side-by-side and diff-overlay preserves the current A/B selection - no re-pick
  required when toggling modes.
- Either pass missing a COG at the active index shows the existing "preview pending"/placeholder
  pattern (same 404 handling as the single-pass and 0042 cases), not a crash or a blank layer.
- A legend that explicitly states the comparison direction and dates (e.g. "B minus A - 12 Mar minus
  3 Feb"), so the sign of the color is never ambiguous.

## Acceptance criteria

- [ ] The diff layer renders only once both A and B passes are explicitly selected - there is no
  auto-computed default pair anywhere in the code path.
- [ ] The diff is colored on a symmetric diverging scale centered on zero, clamped to the active
  index's existing locked display range (`get_colormap(index).vmin/.vmax`).
- [ ] Switching between side-by-side and diff view preserves the same A/B selection.
- [ ] Either pass missing a COG at the active index shows the existing placeholder pattern, not a
  crash or blank layer.
- [ ] The legend explicitly states the comparison direction and both dates.
- [ ] An adapter-parity/synthetic-render test proves the diff raster is pixel-correct against two
  known synthetic index arrays (including a zero-diff case and a known-sign case).
- [ ] `ruff check` + `ruff format --check` + `mypy` + `pytest` green on the backend half; `tsc -b`
  clean on the frontend half.
