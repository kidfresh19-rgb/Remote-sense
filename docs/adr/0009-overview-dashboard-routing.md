# ADR 0009 — Overview Dashboard: Route-Based Navigation with TanStack Router

- Status: accepted
- Date: 2026-06-13
- Phase: L6 Analyst Workspace (frontend improvement)
- Builds on: PLAN.md §6 (frontend map + components confirmed 2026-06-03)

## Context

The analyst workspace previously had a single entry point: the three-panel cockpit
(farm sidebar / MapLibre map / field inspector). Analysts landed directly in the map with no
aggregate picture of the estate. There was no way to answer "how many farms are healthy?"
or "how many fields are still awaiting backfill?" without digging into individual fields.

A new **overview dashboard** was needed to surface estate-level health at a glance: stat cards
(farms, fields, area, backfill queue, pending reviews), a field health map coloured by
`overall_health`, analytics cards (health distribution, coverage %, gateway dead-letter count),
and a recent-interpretations activity feed.

Three navigation models were considered:

**A) Top-level route swap.** Dashboard at `/`, workspace at `/workspace`. Full page swap,
browser back button works. Requires a client-side router.

**B) Persistent left nav rail.** Dashboard and workspace as tabs within the same shell; a
64 px icon rail visible at all times. No router required.

**C) Dashboard as a full-screen overlay.** An "Overview" button in the cockpit header opens
the dashboard as a modal/drawer. Zero routing change; the cockpit stays the home screen.

## Decision

We chose **A (route-based navigation)** via TanStack Router v1. Reasons:

1. **Context isolation.** The workspace carries substantial in-memory state
   (`WorkspaceProvider`: selected farm, field, pass date, raster/RGB/FCC toggles, custom AOI,
   compare date). That state is meaningless on the dashboard and should not persist across the
   context switch. A route boundary gives each view its own provider scope:
   `WorkspaceProvider` wraps only the `/workspace` route, not the root.

2. **Bookmarkability and deep-linking.** Analysts can bookmark `/workspace` and land directly in
   the cockpit without passing through the dashboard, or share a direct link to the overview.
   Option C (modal) and option B (nav rail without routes) cannot be bookmarked.

3. **Ecosystem fit.** TanStack Query is already in the project. TanStack Router shares the same
   design philosophy (type-safe, data-driven) and integrates without adding a second mental model.
   React Router was also considered; TanStack Router was preferred for its stronger TypeScript
   integration and because it is the natural pairing for the existing TanStack stack.

4. **The two views are genuinely different contexts.** The dashboard is an estate overview
   (aggregate, read-only, all farms). The workspace is a deep inspection tool (one field at a
   time, drawing tools, raster overlays, annotation writes). A rail or modal would conflate them.

Option B (nav rail) was ruled out because it blurs the context boundary and requires the
dashboard to live inside the cockpit shell, complicating the provider tree. Option C (modal)
was ruled out because it makes the cockpit the home screen, burying the aggregate view, and
cannot be deep-linked.

**Chrome decision:** The dashboard has its own 72 px header (logo + theme + "Enter Workspace"
CTA). The workspace keeps its existing 56 px header, with a House icon added to navigate back.
This gives each route a purposeful first impression without a shared nav chrome that would imply
they are tabs of one view.

## Consequences

- **`WorkspaceProvider` is route-scoped.** It wraps only the `/workspace` route component in
  `router.tsx`. Any component that calls `useWorkspace()` outside that route will throw; this is
  intentional — it is a bug, not a missing fallback.
- **URL is the source of truth for which view is active.** Do not re-add a global "active view"
  state variable to replicate this; use `useNavigate` or `<Link>` instead.
- **Adding a third route** (e.g. a per-farm summary page at `/farms/:id`) follows the same
  pattern: add a `createRoute` entry in `router.tsx` and scope its providers there.
- **The large JS bundle (MapLibre GL, ~1.5 MB min)** is currently shared across both routes.
  If load time becomes a concern, code-split at the route level with `React.lazy` + dynamic
  `import()` so the cockpit bundle loads only when the analyst navigates to `/workspace`.
