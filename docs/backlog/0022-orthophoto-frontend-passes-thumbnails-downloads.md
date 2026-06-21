# Backlog 0022 — Orthophoto: frontend passes list thumbnails + download shelf

- Status: ready-for-agent
- Type: frontend
- Parent: orthophoto download + natural color preview feature
- Blocked by: 0018 (tiler static endpoint), 0019 (BFF download endpoint)
- Invariants / decisions: global CLAUDE.md design rules apply (no Lucide, CSS Grid, IntersectionObserver
  for lazy-load, min-h-[100dvh], no em-dashes in copy). IntersectionObserver is REQUIRED for thumbnail
  lazy-loading - do not fire N parallel requests to the tiler on mount; only fetch thumbnails that
  scroll into view. No new npm dependencies without checking package.json first.

## Context

`frontend/src/components/SceneList.tsx` renders each pass as a flat button: date, scene_id, clear
fraction, and an active dot. There is no thumbnail and no download button.

Adding a 64x64 natural color thumbnail and a per-index download shelf turns each pass from a flat
list item into a small card that expands on selection. The thumbnail calls
`GET /tiles/static/rgb/{geometry_version}/{field_id}/{scene_id}.jpg` from the tiler. The download
shelf calls `GET /fields/{field_id}/scenes/{scene_id}/download?index={index}&geometry_version={gv}`
from the BFF, which returns a 302 redirect the browser follows automatically.

## What to build

### SceneList card redesign

Each pass becomes an expandable card:

**Collapsed state** (always visible):
- Date (primary, semibold)
- Clear-fraction badge + confidence label
- RGB thumbnail: 64x64 px, `object-cover`, `rounded-sm`
  - Lazy-loaded via `IntersectionObserver` (see below)
  - `"Preview pending"` text placeholder while loading or when 404
- Active indicator dot (unchanged)

**Expanded state** (on click/selection):
- All collapsed content
- Download shelf: one chip/button per available index + `rgb`
  - Label: the index name in caps (e.g. `NDVI`, `SAVI`, `RGB`)
  - On click: `window.location.href = BFF_download_url` (browser follows the 302 natively)
  - Show only indices that have a COG in the store - derive availability from the existing
    `cog_uri` field already present on `AuditRecordOut`, or from a lightweight HEAD check;
    prefer the existing field to avoid an extra request per index per pass.
  - Loading state: spinner inside chip while the presigned URL resolves.

### Thumbnail lazy-loading with IntersectionObserver

Do NOT `useEffect` to fetch all thumbnails on mount. Use an `IntersectionObserver` per thumbnail
element:

```tsx
// rough pattern - implement cleanly in a useLazyImage hook
const ref = useRef<HTMLDivElement>(null);
const [src, setSrc] = useState<string | null>(null);
useEffect(() => {
  const el = ref.current;
  if (!el) return;
  const obs = new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting) {
      setSrc(tiledUrl);
      obs.disconnect();
    }
  }, { rootMargin: "100px" });
  obs.observe(el);
  return () => obs.disconnect();
}, [tiledUrl]);
```

A single `useLazyImage(src: string | null)` hook encapsulating this pattern is the right
abstraction. Put it in `frontend/src/hooks/useLazyImage.ts`.

### Thumbnail URL construction

```ts
// in a constants or util file, not inline
function staticThumbnailUrl(
  fieldId: string,
  sceneId: string,
  geometryVersion: number,
  index = "rgb",
): string {
  return `${TILER_BASE_URL}/static/${index}/${geometryVersion}/${fieldId}/${sceneId}.jpg`;
}
```

`TILER_BASE_URL` should come from an env variable already defined in the frontend config - do not
hardcode.

### Download URL construction

```ts
function cogDownloadUrl(
  fieldId: string,
  sceneId: string,
  geometryVersion: number,
  index: string,
): string {
  return `/api/workspace/fields/${fieldId}/scenes/${sceneId}/download?index=${index}&geometry_version=${geometryVersion}`;
}
```

## Acceptance criteria

- [ ] Each pass in the SceneList renders a 64x64 RGB thumbnail only when the card scrolls into the
  viewport (IntersectionObserver confirmed: no network request for off-screen cards).
- [ ] A pass with no `rgb.tif` (404 from tiler) shows the "Preview pending" placeholder, not a
  broken image.
- [ ] Selecting a pass expands the download shelf with one chip per available index.
- [ ] Clicking a download chip triggers a file download without navigating away from the page.
- [ ] Mobile layout: card stacks vertically, thumbnail left-aligned with date right, download shelf
  wraps to full width below.
- [ ] `prefers-reduced-motion`: thumbnails appear without a fade-in transition when reduced motion
  is active.
- [ ] TypeScript compiles cleanly (`tsc --noEmit`).
- [ ] No Lucide imports introduced.
- [ ] ruff equivalent for frontend: `eslint` passes (or whatever lint gate the project uses).
