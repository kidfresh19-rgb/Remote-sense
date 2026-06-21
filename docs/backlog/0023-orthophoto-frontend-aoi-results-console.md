# Backlog 0023 — Orthophoto: frontend AOI Studio results console downloads + preview

- Status: ready-for-agent
- Type: frontend
- Parent: orthophoto download + natural color preview feature
- Blocked by: 0020 (natural color BFF endpoint), 0021 (AOI Studio temp COG + download endpoint)
- Invariants / decisions: global CLAUDE.md design rules apply. IntersectionObserver is REQUIRED for
  the ChartView filmstrip - do not fan out N natural-color calls on render. AOI Studio custom AOIs
  use the natural-color endpoint (POST /analyse/aoi/natural-color), NOT the tiler static endpoint
  (which serves registered-field COGs only). No new npm dependencies without package.json check.

## Context

`frontend/src/components/aoi/AOIResultsTable.tsx` displays AOI Studio results with CSV download,
chart/table toggle, and expandable controls. It needs:

1. **TableView**: a thumbnail column (64x64 natural color per pass) + a download button column.
2. **ChartView**: a "Scene images" filmstrip below the time-series chart (thumbnails in pass order).
3. **Per-pass GeoTIFF download** in both views, hitting the AOI job download endpoint (backlog 0021).

The thumbnail for a custom AOI pass calls `POST /analyse/aoi/natural-color` with `{scene_id, geometry}`.
This is NOT the tiler endpoint - the tiler only serves stored field COGs. The BFF caches the result
in MinIO (backlog 0020), so the second call for the same scene + geometry is fast.

## What to build

### 1. TableView thumbnail + download column

Add two columns at the start of the results table:

**Thumbnail column** (width: 72px):
- 64x64 JPEG, lazy-loaded via `IntersectionObserver` (same `useLazyImage` hook from backlog 0022).
- On intersection: fire `POST /analyse/aoi/natural-color` with `{scene_id: pass.scene_id, geometry: currentGeometry}`.
- The `currentGeometry` comes from the AOI Studio job context (the geometry the job was run on).
- Placeholder: muted gray square with `"..."` while loading; `"N/A"` on error.
- Thumbnail is inline (no modal on click in this view).

**Download column** (width: 48px):
- Icon button (Phosphor `DownloadSimple` or `FileTiff`).
- `href = GET /analyse/aoi/jobs/{job_id}/passes/{pass.date}/download?index={activeIndex}` from BFF.
- `target="_blank"` so the download does not navigate the page.
- Disabled with a tooltip `"COG unavailable"` when the endpoint returns 404 (handle gracefully).

### 2. ChartView filmstrip ("Scene images" panel)

A horizontally scrollable strip below the chart, visible when the ChartView is active:

- One 64x64 thumbnail per pass, ordered chronologically (same order as chart x-axis).
- **IntersectionObserver is mandatory.** Use a scroll container (`overflow-x: auto`) and a
  horizontal `IntersectionObserver` (or per-thumbnail observers with `rootMargin: "0px 200px"`)
  so only thumbnails near the visible scroll window are fetched.
- On thumbnail click: scroll the chart to align with that pass date (or highlight the chart point).
- A download icon overlaid on hover: triggers the same AOI job download as TableView.
- Empty state: if the job has no passes with scene IDs (interpolated-only passes), show a
  `"No scenes available"` label instead of an empty filmstrip container.

### 3. `useLazyImage` hook reuse

Import the `useLazyImage` hook from `frontend/src/hooks/useLazyImage.ts` (written in backlog 0022).
Do not re-implement the IntersectionObserver pattern here. If 0022 has not landed yet, stub the hook
and mark with `// ⚑ CONFIRM: import from 0022 once merged`.

### 4. Natural color fetch helper

```ts
async function fetchNaturalColor(
  sceneId: string,
  geometry: GeoJSON.Geometry,
): Promise<string> {
  // POST /analyse/aoi/natural-color
  // Returns an object URL from the response blob for use in <img src={...} />
  // Caller responsible for URL.revokeObjectURL cleanup
}
```

Wrap in a `useNaturalColorThumbnail(sceneId, geometry)` hook that handles loading / error state and
revokes the object URL on unmount.

## Acceptance criteria

- [ ] TableView shows a 64x64 thumbnail column; thumbnails are fetched only on scroll into view.
- [ ] TableView shows a download icon column; clicking downloads the index GeoTIFF for that pass.
- [ ] ChartView shows a horizontally scrollable filmstrip; thumbnails are fetched only as they
  approach the visible scroll window (IntersectionObserver confirmed via network tab inspection).
- [ ] A 404 from the download endpoint disables the button with a tooltip, not an uncaught error.
- [ ] Geometry passed to `POST /analyse/aoi/natural-color` is the AOI used for the job, not a
  derived or reconstructed geometry.
- [ ] Object URLs are revoked on component unmount (no memory leak).
- [ ] Empty filmstrip state renders `"No scenes available"` rather than an empty container.
- [ ] `prefers-reduced-motion`: thumbnails appear without fade-in.
- [ ] TypeScript compiles cleanly (`tsc --noEmit`).
- [ ] No Lucide imports introduced.
