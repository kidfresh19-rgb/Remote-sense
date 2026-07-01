# Backlog 0042 — Individual-pass visualization: index-aware pass thumbnails

- Status: ready-for-agent
- Type: frontend
- Parent: PRD 0004 (`docs/prd/0004-as-of-date-historical-anchor.md`), Extended scope, story 21
- Blocked by: None
- Invariants / decisions: no new backend surface — the tiler's `/static/{index}/...` route already
  colormaps any index (`render_params` in `services/tiler/render.py`), this only changes what the
  frontend requests. Global CLAUDE.md frontend design rules apply (no Lucide, IntersectionObserver
  lazy-load already in place from 0022 - preserve it).

## Context

`SceneList.tsx`'s per-pass thumbnail hardcodes the `rgb` composite in its URL construction, so the
pass list always shows a true-color photo regardless of which index the analyst is actually viewing.
The tiler endpoint behind it is already generic over index - `render_params(index)` returns a
colormap + rescale range for any spectral index, not just the visual composites - so this is a
frontend-only fix, not a new capability.

## What to build

- Replace the hardcoded `"rgb"` in the thumbnail URL builder with the workspace's active `index`
  (from `useWorkspace()`), so each pass's thumbnail renders as a mini analysis map in whatever index
  the analyst is currently looking at.
- RGB and FCC selections keep rendering as visual composites, unchanged.
- Cache fetched thumbnails per `(index, sceneId)` so toggling the active index back and forth does
  not refetch a thumbnail already seen.
- When the active index changes, visible (already-intersected) thumbnails re-fetch at the new index;
  off-screen ones still wait for `IntersectionObserver` per the existing 0022 pattern - no fetch storm
  on an index switch.
- A pass with no COG at the newly-active index shows the existing placeholder (same 404 handling
  0022 already built), not a broken image.

## Acceptance criteria

- [ ] Each pass thumbnail in `SceneList` renders at the workspace's currently active index's
  colormap, not always RGB.
- [ ] Switching the active index re-renders visible thumbnails to the new index without fetching
  off-screen ones.
- [ ] RGB/FCC composite selections continue to render as visual composites unchanged.
- [ ] A pass missing a COG at the active index shows the existing "preview pending" placeholder, not
  a broken image.
- [ ] Thumbnails already fetched for a given `(index, sceneId)` are cached - switching index back and
  forth does not refetch.
- [ ] `tsc -b` clean, no new dependencies.
