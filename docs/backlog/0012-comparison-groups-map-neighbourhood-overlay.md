# Backlog 0012 — Map layer: neighbourhood overlay

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 8c
- Blocked by: 0006
- Invariants / decisions: the neighbourhood is subject-centric and computed on demand (no persisted
  identity); distance computed in UTM upstream (backlog 0006).

## What to build

A toggleable MapLibre overlay that, when a farm is selected, draws that farm's neighbourhood extent
(the KNN set or the radius) and highlights its member farms. Default off.

## Acceptance criteria

- [ ] When a farm is selected, the overlay draws its neighbourhood extent (KNN set or radius) and its
  member farms.
- [ ] The overlay is toggleable, default off, and clears when no farm is selected.
- [ ] ruff + ruff format + mypy + pytest green; the frontend is validated per the frontend
  conventions and `/verify`.

## Tests (seams)

- Frontend: validated per the frontend's own conventions and `/verify`, not a backend seam. The
  neighbourhood selection it draws is covered by backlog 0006's tests.
