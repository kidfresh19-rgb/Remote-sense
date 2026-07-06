# Backlog 0043 — Individual-pass visualization: timelapse playback control

- Status: done (landed on develop `8850e7f`, pushed both remotes 2026-07-06)
- Type: frontend
- Parent: PRD 0004 (`docs/prd/0004-as-of-date-historical-anchor.md`), Extended scope, story 22
- Blocked by: None
- Invariants / decisions: ⚑ CONFIRM default playback interval is 1 pass/second and playback stops
  (does not loop) at the last pass - both are cheap to change later behind the same control, flagged
  rather than blocking. Global CLAUDE.md `prefers-reduced-motion` rule applies.

## Context

`passDate` is already the single source of truth driving the map, chart highlight, and scene list
active row (`MapPanel.tsx`, `IndexTimeseriesChart.tsx`, `SceneList.tsx`), but the only way to move
through a field's history today is one manual click per pass (scrubber tick, list row, or chart dot).
A play control turns that into an actual timelapse over the map, reusing state wiring that already
exists - no new state shape, just an interval driving the existing `setPassDate`.

## What to build

- A play/pause control (near `TimelineScrubber` or in the `MapPanel` control cluster) that, on play,
  advances `passDate` through the field's scenes sorted by `pass_date` at a fixed interval
  (⚑ CONFIRM 1s default).
- Stops automatically at the last pass - does not loop back to the start, so the analyst is never
  left wondering whether it is still running.
- Any manual interaction - a scrubber tick, a list row, a chart dot, or switching field/farm - stops
  playback immediately, so it never fights the analyst's own navigation.
- If the next pass's tile is still loading when its turn comes up, playback pauses on the current
  frame rather than skipping ahead - no pass is silently dropped from the sequence.
- `prefers-reduced-motion`: playback still functions (the raster still swaps on schedule), but no
  additional motion/transition is added beyond what the raster swap already does today.
- Play/pause state is visually unambiguous (icon swap) and keyboard-operable.

## Acceptance criteria

- [ ] A play control advances `passDate` through the field's passes in chronological order at a
  fixed interval.
- [ ] Playback stops automatically at the last pass (no loop) and stops immediately on any manual
  pass or field/farm selection.
- [ ] Playback pauses rather than skipping a frame while the next tile is still loading.
- [ ] `prefers-reduced-motion` is respected - no added transition beyond the existing raster swap.
- [ ] Play/pause is keyboard accessible with a clear focus state.
- [ ] `tsc -b` clean, no new dependencies.
