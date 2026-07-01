# PRD 0004: As-of date — global historical anchor

Status: ready-for-agent

## Problem Statement

An analyst reviewing the estate today sees every surface — the field health map, stat cards,
interpretations, and field passes — anchored to the present. There is no way to ask "what did we
know on 14 March?" and have every panel answer that question together. The existing `passDate`
scrubber moves one field's raster layer; everything else stays live. This makes retrospective review
(post-season audits, crisis investigations, shared historical context with a colleague) impossible
without manually cross-referencing timestamps across panels.

## Solution

Introduce a global **as-of date** that shifts every analyst surface to show data exactly as it
existed on a chosen past date. The analyst sets the date via a control in the shared Header; the
date persists in the URL as `?asOf=YYYY-MM-DD` so historical views are navigable and shareable.
Clearing it returns all surfaces to live data. While the anchor is active, a persistent banner below
the Header makes the historical mode unmistakable.

The semantic rule is *latest pass before or on the date* per field: no future data bleeds in. A
field with no pass on or before the date shows "no data" and offers a targeted collect shortcut.

## User Stories

1. As an analyst, I want to set a global as-of date from the Header, so that every surface I visit reflects how the estate looked on that day without configuring each panel separately.
2. As an analyst, I want the as-of date to persist in the URL, so that I can share a link with a colleague and they land on the same historical view.
3. As an analyst, I want a persistent banner to tell me I am in historical view, so that I never mistake past data for live data.
4. As an analyst, I want to clear the as-of date with a single click on the banner's "Exit" button, so that I can return to the live view without navigating away.
5. As an analyst, I want the field health map on the overview dashboard to colour each farm polygon by its health as of the chosen date, so that I can see the estate's spatial health at that moment in time.
6. As an analyst, I want the estate stat cards (total area, farms, fields, health) to reflect the as-of date, so that the numbers match what the map is showing.
7. As an analyst, I want the analytics cards (health distribution, coverage %) to reflect the as-of date, so that I can understand the distribution of health at a past point in time.
8. As an analyst, I want the recent-interpretations activity feed to filter to interpretations with a pass date on or before the as-of date, so that the feed shows what had been written as of that day.
9. As an analyst, I want the Passes tab in the field inspector to list only passes with a pass date on or before the as-of date, so that I see the field's history as it existed then.
10. As an analyst, I want the Series chart to retain the full pass history but mark the as-of pass with a vertical rule, so that I can see the trajectory that led to that moment without losing context.
11. As an analyst, I want the Read tab to show the interpretation that was current as of the chosen date (the most recently published read with a pass date on or before it), so that I see the agronomic read that would have been live on that day.
12. As an analyst, I want the Notes tab to filter annotations to those created on or before the as-of date, so that I see only notes that existed at that point.
13. As an analyst, I want the Audit tab to filter provenance records to those created on or before the as-of date, so that the trail reflects what was known then.
14. As an analyst, I want the workspace's timeline scrubber to start at the as-of pass when I open a field under an active anchor, so that I land immediately on the right scene without manual adjustment.
15. As an analyst, I want to override the as-of pass with the scrubber for a specific field without clearing the global anchor, so that I can explore a field's other passes while keeping the estate-level view anchored.
16. As an analyst, I want a farm on the health map that has no pass before the as-of date to show as "no data" with a grey polygon, so that absent data is clearly distinguished from poor health.
17. As an analyst, I want a "no data" farm to offer a targeted collect-specific-dates shortcut pre-filled with the as-of date, so that I can fill the gap without leaving the historical view.
18. As an analyst, I want the as-of date control in the Header to show a date picker, so that I can type or select the target date without knowing the exact pass schedule.
19. As an analyst, I want navigating between the dashboard and workspace to keep the as-of date active, so that the historical context is not lost when I move between routes.
20. As an analyst, I want the as-of date to be bounded to today at the latest, so that I cannot accidentally request a future date.

## Implementation Decisions

### URL persistence and routing

The as-of date lives in the URL as `?asOf=YYYY-MM-DD`. TanStack Router's search-param API reads and
writes it. Both `/` (dashboard) and `/workspace` read from the same param; navigating between routes
preserves it. The Header date picker is the single write point.

### Frontend state

`WorkspaceState` in `frontend/src/state/workspace.tsx` gains `asOfDate: string | null`, initialised
from the router search params on mount. All existing TanStack Query hooks (`useFarms`, `useScenes`,
`useTimeseries`, `useInterpretations`, `useAnnotations`, `useAudit`, `useReviewQueue`) gain the
as-of date as an additional query-key dimension and pass it as a `?as_of=YYYY-MM-DD` query param to
their BFF calls. A null as-of date means no param is sent; the BFF returns live data unchanged.

### Interaction with the existing requestedDate pattern

When a field is opened while an as-of anchor is active, the as-of date is fed into the existing
`requestedDate` → `applyResolvedPass` flow. A manual scrubber pick issues `setPassDate` (which
clears `requestedDate`), retiring the as-of resolution for that field only. The global `asOfDate`
remains active for all other fields and for the dashboard surfaces.

### BFF query-param extensions

Each workspace BFF endpoint gains an optional `as_of: date | None = None` query parameter. When
provided:

- `GET /farms` — `get_farm_analytics_summary()` in `rs_core` filters analyses to `pass_date <=
  as_of`, taking the row with the maximum `pass_date` in that window. A farm with no qualifying
  analysis returns null health fields (rendered as "no data").
- `GET /fields/{id}/scenes` — filters to `pass_date <= as_of`.
- `GET /fields/{id}/interpretations` — filters to `pass_date <= as_of`.
- `GET /fields/{id}/annotations` — filters to `created_at::date <= as_of`.
- `GET /fields/{id}/audit` — filters to `created_at::date <= as_of`.
- `GET /interpretations/review-queue` — filters to `pass_date <= as_of`.

The frozen contract routes (`/api/v1/mobile/*`, `/ingest/*`) are unaffected (ADR 0008).

### Series chart

The `IndexTimeseriesChart` always fetches the full time series (no `as_of` filtering here — full
history is more informative than truncation for a chart). When an as-of date is active, the chart
receives the resolved `passDate` and renders a vertical rule at that pass.

### Mode indicator

A new `AsOfBanner` component renders immediately below the `Header` when `asOfDate` is non-null.
It shows "Viewing as of [formatted date]" and an "Exit" button that clears `?asOf` from the URL.
The Header date-picker control also reflects the active date with a non-empty value.

### No-data farm collect shortcut

When a farm in the health map has null health under an active as-of anchor, its polygon tooltip
includes a "Collect for this date" action that opens the existing `collect specific dates` flow with
the as-of date pre-filled.

### Ward Watch deferral

Ward Watch surfaces are not wired to the as-of date in this PRD. The `?asOf` URL param is read at
the call site, so wiring it in is additive and requires no structural change here (ADR 0013).

## Testing Decisions

A good test for this feature exercises the BFF's filtering behaviour against a real database with
known fixtures (consistent with the project's integration-test pattern over `rs-testpg`). Tests
should not mock the ORM layer — the correctness of the `pass_date <= as_of` filter depends on SQL,
not Python logic.

**BFF integration tests** (prior art: existing workspace endpoint tests):

- `GET /farms?as_of=X` returns health from the latest pass on or before X; a farm with no pass
  before X returns null health.
- `GET /fields/{id}/scenes?as_of=X` excludes passes after X.
- `GET /fields/{id}/interpretations?as_of=X` excludes interpretations with `pass_date > X`.
- `GET /fields/{id}/annotations?as_of=X` excludes annotations created after X.
- `GET /fields/{id}/audit?as_of=X` excludes audit records created after X.
- `GET /interpretations/review-queue?as_of=X` excludes queue items with `pass_date > X`.
- Each of the above with `as_of` absent returns the full unfiltered set (backwards compatibility).

**`rs_core` unit test** (prior art: `get_farm_analytics_summary` tests):

- `get_farm_analytics_summary(session, farm_id, as_of=X)` selects the correct latest-before pass
  when multiple passes exist, and returns null health when no pass qualifies.

**Frontend query-key tests** (prior art: existing React Query hook tests if present, otherwise
skip; the BFF contract is the higher-value seam):

- Confirm that when `asOfDate` is non-null, the query key includes the date and the request URL
  carries `?as_of=`.

## Extended scope: individual-pass visualization (folded in 2026-07-01)

The original scope above propagates a resolved date to more surfaces; it deliberately does not
change how a single pass is rendered. A separate review of the pass-selection UX (`SceneList`,
`TimelineScrubber`, `SceneCompare`, `IndexTimeseriesChart`) found the selection mechanics extensive
but the visualization of an individual pass thin: exactly one raster, swapped per click. These five
user stories fold that richer visualization in as additive scope, sliced into their own backlog
items (see Further Notes) so they can ship independently of the global-anchor propagation work above
and of each other.

21. As an analyst, I want each pass's thumbnail to render in the index I'm currently viewing (not
    always true color), so that the pass list itself shows a mini analysis map for every date at a
    glance. Implementation note: `SceneList.tsx`'s thumbnail URL hardcodes the `rgb` composite, but
    the tiler's `/static/{index}/...` route already colormaps any index (`render_params` in
    `services/tiler/render.py`) — swap the hardcoded string for the workspace's active `index`.
    Frontend-only, no backend change.
22. As an analyst, I want to play the field's history as a timelapse over the map, so that I can
    watch canopy development or decline across a season without manually clicking every pass.
    Implementation note: a play/pause control advancing `passDate` through the field's sorted scene
    list on an interval; reuses the existing `setPassDate` path the scrubber already drives.
    Frontend-only.
23. As an analyst, I want to see many passes laid out as small maps side by side (a contact sheet),
    so that I can compare the field's appearance across a whole season spatially, not one pass at a
    time. Implementation note: a map-panel view mode laying out N passes' static thumbnails (per
    story 21, at the active index) in a grid; reuses the same tiler endpoint, no new backend
    surface.
24. As an analyst, I want a difference layer between two passes rendered as its own map, so that
    decline or growth is visually obvious without mentally subtracting two side-by-side images.
    Implementation note: a new tiler render path reading two index COGs for the same field and
    geometry version and rendering their pixel-wise difference with a diverging colormap. This is
    new raster math (`rs_analysis` / tiler) but render-only, like the existing RGB preview — no new
    stored analysis, no provenance change.
25. As an analyst, I want to see which pixels were cloud-masked on the selected pass, so that a low
    clear-fraction reading is visually explained rather than a percentage I have to trust.
    Implementation note: surface the per-pixel SCL-derived clear/cloud mask invariant 3 already
    computes during masking, as a static overlay layer for the active pass. The `windowed_cog`
    adapter already computes an AOI mask during fetch (`adapters/windowed_cog.py:358`,
    per `docs/plan/orthophoto-download-improvements.md` P2) — reuse it rather than recomputing.

None of the five touch an architecture invariant (story 24 and 25 add render-only raster paths, not
stored analyses), so no new ADR is required. Stories 24 and 25 are geospatial-engineer-owned (new
raster/tiler work); 21-23 are frontend-only.

## Out of Scope

- Ward Watch surfaces — deferred until the Ward Watch feature merges (ADR 0013).
- Future-date protection beyond a `max` attribute on the date picker input — no server-side
  enforcement needed since future data cannot exist.
- The tiler / COG layer for the global-anchor mechanism itself — the raster is already driven by
  `passDate` (resolved by the existing `requestedDate` flow); no tiler change is needed for the
  anchor. (The Extended scope section above does add tiler work, but that is pass-visualization
  richness, a separate concern from date propagation.)
- Exporting a historical snapshot as PDF or CSV — analysts use the existing CSV export paths.
- Restricting the date picker's minimum to the earliest available pass — the "no data" state
  handles dates before any data gracefully without needing a constrained picker.

## Further Notes

- ADR 0013 (`docs/adr/0013-as-of-date-global-historical-anchor.md`) documents the "latest before"
  semantic choice and the rejection of snap-within-±N-days and exact-match alternatives.
- The as-of date glossary entry in `CONTEXT.md` is the canonical term definition.
- The `useAsOf` hook already in `queries.ts` performs single-field date resolution for the
  workspace scrubber (S3.1); it is a different concern from the global anchor and is not replaced
  by this feature.
- Implementation branch: open a new branch off `main` after the Ward Watch branch
  (`feat/ward-watch-movement-lens`) merges.
- The Extended scope section's five user stories (21-25) are sliced into
  `docs/backlog/0042` through `0046` (2026-07-01). Unlike stories 1-20, these are independently
  buildable now - they do not need the global anchor to land first and do not block each other.
  0045's pairing-default and colormap questions, and 0046's mask-rendering treatment, were decided at
  slicing time (recorded in each backlog file's header) rather than left as open CONFIRM markers.
