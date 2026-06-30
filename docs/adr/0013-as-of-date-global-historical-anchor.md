# 0013: As-of date — global historical anchor

Status: accepted

## Context

Analysts need to review the estate and individual fields as they appeared on a specific past date:
health scores, analytics, interpretations, and the satellite raster all frozen to that moment. The
use case is retrospective review — understanding what was known on a given day, not a side-by-side
comparison of two specific passes (which the existing `compareDate` / `SceneCompare` path already
handles).

Two isolated mechanisms existed before this ADR:

- `passDate` / `requestedDate` in workspace state: field-scoped, transient, not persisted to the
  URL, drives only the COG layer and the timeline scrubber.
- `compareDate`: puts the map into side-by-side mode for two specific scenes of the same field.

Neither covers an estate-wide "view everything as of a date" need.

## Decision

Introduce a single global `asOf` date, controlled from the Header and persisted as a
`?asOf=YYYY-MM-DD` URL query parameter so historical views are shareable and survive navigation
between routes.

**Semantic rule: latest pass before or on the date.** For a given field, the as-of date resolves to
the most recent persisted analysis whose `pass_date <= asOf`. No future data bleeds in. A field with
no pass on or before the date shows "no data."

**Coverage.** When the anchor is set, every data surface reflects the historical state:

- Overview dashboard: stat cards, field health map polygon colours, analytics cards, and the
  activity feed (filtered to interpretations published on or before the date).
- Analyst workspace: Passes list filtered to on-or-before; Read tab shows the interpretation
  current as of that date; Notes and Audit filtered to on-or-before; Series chart retains full
  history but marks the as-of pass with a vertical rule.

**Interaction with manual pass selection.** The as-of date resolves an initial `passDate` per field,
exactly like the existing `requestedDate` → `applyResolvedPass` flow. A manual scrubber pick in the
workspace retires the as-of date for that field (the analyst overrode the resolution); navigating to
a new field re-applies the anchor. This keeps the existing override pattern intact without a new
concept.

**No-data farms.** A farm with no pass on or before the as-of date renders grey on the health map
and surfaces a targeted `collect specific dates` shortcut pre-filled with the anchor date, so the
analyst can fill the gap without leaving the view.

**Mode indicator.** A persistent banner below the Header reads "Viewing as of [date]" with an "Exit"
button that clears `?asOf` and returns to live view. The Header date control also shows the active
date. Both indicators together make it impossible to overlook that the view is historical.

**Ward Watch deferral.** Ward Watch is still under construction on a parallel branch. The as-of date
wires into Ward Watch surfaces after that feature lands; the anchor is designed as a single read from
the URL so extending it to a new surface is additive, not structural.

## Why this needs an ADR

The `?asOf=YYYY-MM-DD` URL parameter becomes part of shared analyst links the moment it ships.
Renaming it or changing its semantics is a breaking change for any bookmarked or shared URL. The
"latest before" rule is the non-obvious part: the group reference pass uses a ±N-day snap window,
and the `collect specific dates` flow also snaps within ±7 days. A future reader will reasonably ask
"why not snap here too?" The answer is in the Alternatives section. The decision is hard to reverse
and surprising without context, so it warrants an ADR.

## Alternatives considered

**Snap to nearest pass within ±N days (like the group reference pass).** Rejected. The group
reference pass snaps bidirectionally because it is comparing farms on a *common* date and needs
enough members with data to form a quorum. The as-of date is a strict historical view: pulling in a
pass from after the chosen date would show data the analyst could not have seen on that day, breaking
the time-travel guarantee. Nearest-before is the only semantically correct rule.

**Exact date match only.** Rejected. Most calendar days have no Sentinel-2 pass (5-day cadence, plus
cloud). An exact-only rule would make the date picker return "no data" for the majority of dates
selected, which is confusing and limits the feature to the small subset of analysts who already know
the exact pass schedule.

**Per-surface date controls (not global).** Rejected. An analyst navigating from the dashboard to the
workspace would lose context. Sharing a workspace link with a particular date would not reproduce the
dashboard state. A single URL param propagates automatically across routes.

**App state only (no URL param).** Rejected. Historical views are meaningless if they cannot be
shared with a colleague or linked from an interpretation note. The URL is the only durable, shareable
store available without introducing a new persistence layer.

## Consequences

- The BFF workspace endpoints (`/api/workspace/...`) gain an optional `as_of: date` query parameter.
  Endpoints without it continue to return latest data; passing it activates the before-or-on filter.
  The contract routes (frozen per ADR 0008) are unaffected — the as-of date is a workspace-internal
  BFF concern.
- The `WorkspaceState` in `frontend/src/state/workspace.tsx` gains an `asOfDate: string | null`
  field, initialised from the URL param on mount and kept in sync via `useSearchParams`.
- The Header gains a date-picker control and an "active" indicator. The banner component is a new
  layout slot below the Header, conditionally rendered.
- The existing `requestedDate` / `applyResolvedPass` pattern is unchanged; `asOfDate` feeds into it
  as the initial requested date when a field is opened while the anchor is active.
- Ward Watch is unaffected until its feature branch merges; no placeholder or stub is needed because
  the anchor is read from the URL at the call site.
