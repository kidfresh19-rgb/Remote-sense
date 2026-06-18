# Backlog 0008 — Workspace group-context panel

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 4
- Blocked by: 0003, 0004
- Invariants / decisions: additive to the established three-panel workspace layout; same group
  reference pass as the group view.

## What to build

`GET /api/farms/:id/group-context` returning, for an open farm, one entry per group it belongs to,
each with the standing percentile, the 2x2 movement label, the reference-pass date, the clear
fraction, and a link target. Plus the compact group-context panel in the workspace, additive to the
existing three-panel layout, which lists those groups and links each out to its `/groups/:id` view.

## Acceptance criteria

- [ ] The panel shows one row per group a farm belongs to (its Natural Region, any containing uploaded
  region, each crop cohort, and its neighbourhood), each with standing, movement label, reference-pass
  date, clear fraction, and a link to `/groups/:id`.
- [ ] The panel uses the same group reference pass as the group view.
- [ ] No full group time-series in the panel; that is the group view's job.
- [ ] ruff + ruff format + mypy + pytest green; the frontend is validated per the frontend
  conventions and `/verify`.

## Tests (seams)

- DB-backed, skips without PostGIS (prior art `test_workspace_db.py`): the endpoint response shape
  across a farm with several group memberships, called directly with a `Principal`/`Role`.
