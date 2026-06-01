# remote-sense workspace (L6)

The analyst-facing workspace for the remote-sense platform. An expert tool: dense, precise, built
for reading per-field index history rather than consumer simplicity. It is a Vite + React + TypeScript
SPA that talks to the RBAC-gated BFF (`services/api/workspace.py`) and the tiler (`services/tiler`).

## Stack

- **Vite + React 19 + TypeScript.** The workspace is a client-heavy authenticated SPA (maps, charts,
  selection state), so server rendering adds no value here and complicates the MapLibre + token flow.
- **Tailwind v4** via `@tailwindcss/vite` (no PostCSS). Semantic tokens in `src/index.css` drive a
  zinc base with a single muted-cobalt accent, dark by default with a light toggle.
- **MapLibre GL** for the map. **TanStack Query** for server state. **Phosphor** icons.
  **`motion/react`** is available for motion (kept minimal; most transitions are CSS transform/opacity).
- Fonts self-hosted via `@fontsource-variable` (Outfit for UI, JetBrains Mono for figures).

## Run

```bash
npm install
cp .env.example .env     # point VITE_API_BASE_URL / VITE_TILER_BASE_URL at your stack
npm run dev              # http://localhost:5173
npm run typecheck        # tsc, no emit
npm run build            # type-check + production bundle
```

Every workspace read is RBAC-gated, so the app needs a bearer token. Paste one into the token gate,
or set `VITE_DEV_TOKEN` for local dev. The real sign-in (gateway OIDC) is parked behind
`TokenProvider`; swap that provider's internals when the contract lands and nothing else changes.

## Structure

```
src/
  lib/        config, typed API client, query hooks, index metadata + colormaps, formatting, theme
  auth/       TokenProvider (token context) + TokenGate (stand-in sign-in)
  state/      workspace selection (farm / field / index / pass) via context + reducer
  components/ Header, FarmFieldSidebar, MapPanel, FieldInspector, IndexTimeseriesChart,
              SceneList, TimelineScrubber, InterpretationPanel, IndexLegend, ui, states
  App.tsx     providers + the three-column shell
```

Data flow: select a farm then a field in the sidebar, the map fits the field boundary and (when
toggled) overlays the index raster from the tiler, and the inspector loads the per-index time series,
the pass list with a shared timeline scrubber, and the agronomic reads.

## Wired to the BFF

`/farms`, `/farms/{id}/fields` (geometry for the map), `/fields/{id}/timeseries`, `/fields/{id}/scenes`,
`/fields/{id}/interpretations`. The index raster layer points at the tiler XYZ template; it stays empty
until the raster stack is up in-container (the tiler 503s without rio-tiler, 501s until COG emission).

## Deferred (frontend backlog)

Built behind the existing layout, not yet implemented: side-by-side scene comparison, the annotation
layer, audit history, and saved AOIs. The index display ranges and the interpretation status-to-tone
map carry `⚑ CONFIRM` markers pending agronomy review.
