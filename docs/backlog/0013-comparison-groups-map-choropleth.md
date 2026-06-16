# Backlog 0013 — Map layer: group movement choropleth

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 8d
- Blocked by: 0004
- Invariants / decisions: the choropleth colours regions by the 2x2 movement label (backlog 0004),
  which is a structural signal only and never a drought or cause label.

## What to build

A toggleable MapLibre choropleth that colours region clusters by their group movement (the 2x2
breakdown: nominal, idiosyncratic, systemic, resilient) for the current group reference pass. Default
off. The legend names the structural labels and asserts no cause.

## Acceptance criteria

- [ ] The choropleth colours region clusters by their group movement breakdown for the current
  reference pass.
- [ ] The layer is toggleable, default off; the legend uses the structural labels and asserts no
  cause (no "drought").
- [ ] Regions below the minimum-members or clear-fraction gate render as no-verdict, not as a false
  reading.
- [ ] ruff + ruff format + mypy + pytest green; the frontend is validated per the frontend
  conventions and `/verify`.

## Tests (seams)

- Frontend: validated per the frontend's own conventions and `/verify`, not a backend seam. The
  movement labels it colours by are covered by backlog 0004's tests.
