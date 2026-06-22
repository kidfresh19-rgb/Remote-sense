# Backlog 0010 — Map layer: Natural Region boundaries

- Status: built
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 8a
- Blocked by: 0002
- Invariants / decisions: boundary GeoJSON is served to the browser by the internal workspace BFF
  (allowed; invariant 6 governs the gateway push, not the BFF).

## What to build

A toggleable MapLibre layer that draws the seeded Natural Region boundaries on the shared map,
reading boundary GeoJSON from the workspace BFF. Default off.

## Acceptance criteria

- [x] The Natural Region boundary layer is a toggleable MapLibre overlay, default off.
- [x] It renders the seeded Natural Region polygons from the BFF.
- [x] ruff + ruff format + mypy + pytest green; the frontend is validated per the frontend
  conventions and `/verify`.

## Tests (seams)

- Frontend: validated per the frontend's own conventions and `/verify`, not a backend seam. Any BFF
  read it depends on is covered by the group endpoints' DB-backed tests.
