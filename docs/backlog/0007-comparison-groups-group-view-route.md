# Backlog 0007 — Group view API and dashboard route

- Status: ready-for-agent
- Type: HITL (design checkpoint + RBAC confirm, both before merge)
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 3
- Blocked by: 0003, 0004
- Invariants / decisions: returning region-boundary GeoJSON to the browser is allowed because
  invariant 6 governs the gateway push, not the internal workspace BFF (which already serves field
  polygons to the map); same group reference pass as the rest of the engine.

## What to build

`GET /api/groups/:id/overview` returning the group identity (id, kind, name, definition, boundary
version, member count), the reference pass with its clear fraction, the health-status distribution,
the 2x2 movement-breakdown counts, the ranked standing member list, the group time-series, and the
boundary GeoJSON for the map. Plus the new `/groups/:id` dashboard-family route rendering all six
panels and the clear-fraction indicator, where a member-row click opens that farm in `/workspace`
while preserving group context. The endpoint lives in the workspace BFF.

## Acceptance criteria

- [ ] `/groups/:id` renders member count, health distribution, the 2x2 movement breakdown, the ranked
  standing list, the group time-series, the boundary map, and a clear-fraction indicator.
- [ ] A member-row click opens that farm in `/workspace` and preserves group context.
- [ ] The endpoint and route are gated by `view_group` and use the same group reference pass as the
  rest of the engine for a given group and date.
- [ ] Returning region-boundary GeoJSON to the browser is allowed (internal BFF, not the gateway
  push); nothing here is pushed to the gateway.
- [ ] ruff + ruff format + mypy + pytest green; the frontend is validated per the frontend
  conventions and `/verify`.

## HITL design checkpoint (before merge)

Six net-new components on a new top-level dashboard route is too much surface to merge without a
design eyeball. Gate: a visual and UX review for coherence with the existing Overview dashboard at
`/`, plus a check against the global frontend design rules, before merge. Default gate format: a
screenshot or visual review on the PR. The exact format (Figma or mockup approval, PR screenshot
review, or an in-progress review at a defined milestone) is to be confirmed by Mishael and recorded
here before the slice starts.

## Resolve before merge

- **Open Item 3 (RBAC).** Confirm the `view_group` role mapping before merge (proposed: any
  authenticated user with farm access). Separate gate from the design checkpoint; both apply.

## Tests (seams)

- DB-backed, skips without PostGIS (prior art `test_workspace_db.py`): the endpoint response shape,
  the clear-fraction being surfaced, and the RBAC gate, with the endpoint called directly using a
  `Principal`/`Role`.
