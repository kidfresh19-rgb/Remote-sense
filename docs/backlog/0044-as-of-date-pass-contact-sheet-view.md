# Backlog 0044 — Individual-pass visualization: contact-sheet / small-multiples map view

- Status: done (landed on develop `859c44f`, pushed both remotes 2026-07-06)
- Type: frontend
- Parent: PRD 0004 (`docs/prd/0004-as-of-date-historical-anchor.md`), Extended scope, story 23
- Blocked by: None. Soft dependency: reuse 0042's index-aware thumbnail URL helper if it has already
  landed; otherwise build the minimal version inline here and consolidate when 0042 ships. Not a hard
  block either direction - both are independently demoable.
- Invariants / decisions: global CLAUDE.md design rules (CSS Grid, no Lucide). IntersectionObserver
  lazy-loading is REQUIRED - a contact sheet can put far more thumbnails in view at once than a
  single scrolling list, so the fetch-storm risk 0022 already guarded against is larger here, not
  smaller.

## Context

The map shows exactly one pass at a time today; comparing more than two requires manually scrubbing
back and forth and holding earlier frames in memory. A grid of small per-pass maps - the same
per-index static thumbnail 0042 produces, laid out spatially instead of in a vertical list - lets an
analyst see a whole season at a glance without scrubbing.

## What to build

- A new view-mode toggle on `MapPanel` (alongside the existing raster/rgb/fcc/compare icon buttons)
  that replaces the single map with a responsive CSS Grid of per-pass static thumbnails, each at the
  workspace's active index (same URL construction as 0042).
- Each grid cell is labeled with its date and clear-fraction, matching the information already shown
  in `SceneList` rows.
- Clicking a cell exits contact-sheet mode and opens that pass on the full single map (sets
  `passDate`, mirroring how selecting a `SceneList` row does today).
- Off-screen cells do not fetch until they scroll into view (`IntersectionObserver`, same discipline
  as 0022/0042) - loading the full grid must not fire one request per pass on mount.
- Fields with long histories are paginated or capped (e.g. most recent 24 with pager controls) rather
  than rendering an unbounded grid.
- Mobile layout reflows to fewer columns and stays usable.
- Empty/placeholder cell state matches the existing `SceneList` "preview pending" pattern.

## Acceptance criteria

- [ ] A toggle switches the map panel into a grid of per-pass static thumbnails at the active index.
- [ ] Clicking a grid cell exits contact-sheet mode and opens that pass on the single map.
- [ ] Off-screen grid cells do not fetch until scrolled into view - confirmed no fetch storm on mount
  for a field with many passes.
- [ ] Fields with long histories are paginated/capped, not rendered as one unbounded grid.
- [ ] Mobile layout reflows to fewer columns and remains usable.
- [ ] A pass with no COG at the active index shows the existing placeholder, not a broken image.
- [ ] `tsc -b` clean, no new dependencies, no Lucide imports.
