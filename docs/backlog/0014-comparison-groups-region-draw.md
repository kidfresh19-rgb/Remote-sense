# Backlog 0014 — Draw a cluster area in the workspace

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 9
- Blocked by: 0005, 0010, 0011
- Invariants / decisions: ADR 0010 + its 2026-06-17 amendment; reuse the existing AOI draw UX and the
  `parseAOI` shapefile path (`frontend/src/lib/parseAOI.ts`, `shpjs`); region geometry is internal
  BFF data, never pushed. The viewed farm is only the entry point; a drawn region is a free-standing,
  reusable region cluster.

## What to build

A workspace control to define a region cluster around the farm being viewed, then read it as a group:

- A draw control offering a freeform polygon or a radius circle centred on the viewed farm; the
  circle compiles to a polygon client-side so the server sees one geometry.
- The same control accepts a single uploaded boundary file (`.zip` shapefile / GeoJSON) via the
  existing `parseAOI` path.
- A live readout, before saving, of how many farms fall inside the area and how many have usable data
  ("Mazowe block: 23 farms, 17 with usable data").
- Name-and-save calls the `create_region_cluster` endpoint (backlog 0005); on success the analyst is
  linked to the group view (`/groups/:id`) for the new cluster.
- The map region layers gain a `source` filter so analyst-drawn boundaries can be shown or hidden
  separately from seeded and uploaded layers (extends backlog 0010 / 0011).

## Acceptance criteria

- [ ] An analyst viewing a farm can draw a polygon or a radius circle and save it as a named region
  cluster; the circle is sent to the API as a polygon.
- [ ] A single `.zip` / GeoJSON boundary can be uploaded through the same control via `parseAOI`.
- [ ] Before saving, the control shows the count of farms captured and how many have usable data.
- [ ] Saving creates the region via `create_region_cluster` and links to its group view.
- [ ] Map region layers can filter by `source` (seeded / uploaded / drawn).
- [ ] The control is hidden or disabled without the `create_region_cluster` permission.
- [ ] Validated by the frontend's own conventions and `/verify` (no backend seam here).

## Tests (seams)

- Frontend conventions + `/verify`: draw polygon and circle, single-file upload via `parseAOI`, the
  captured-farm / usable-data readout, the save-then-link-to-group-view flow, and the `source` layer
  filter. The pure circle-to-polygon and composition math is covered in backlog 0005's pure seam.
