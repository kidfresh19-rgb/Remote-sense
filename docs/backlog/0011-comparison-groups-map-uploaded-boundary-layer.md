# Backlog 0011 — Map layer: uploaded boundaries

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 8b
- Blocked by: 0005
- Invariants / decisions: uploaded boundary GeoJSON is served to the browser by the internal
  workspace BFF (allowed); uploaded reference geometry is never pushed to the gateway (ADR 0010).

## What to build

A toggleable MapLibre layer that draws analyst-uploaded region boundaries on the shared map, reading
boundary GeoJSON from the workspace BFF. Default off. The analyst can pick which uploaded layer to
show.

## Acceptance criteria

- [ ] The uploaded-boundary layer is a toggleable MapLibre overlay, default off.
- [ ] It renders the polygons of a selected uploaded layer from the BFF.
- [ ] ruff + ruff format + mypy + pytest green; the frontend is validated per the frontend
  conventions and `/verify`.

## Tests (seams)

- Frontend: validated per the frontend's own conventions and `/verify`, not a backend seam.
