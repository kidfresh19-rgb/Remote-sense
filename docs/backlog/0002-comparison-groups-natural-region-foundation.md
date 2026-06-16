# Backlog 0002 — Natural Region foundation

- Status: ready-for-agent
- Type: AFK (with a prerequisite data input, see below)
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 1
- Blocked by: None - can start immediately once the prerequisite Natural Region file is in hand
- Invariants / decisions: ADR 0010 (region boundaries are remote-sense-owned analytical reference
  geometry, distinct from gateway-owned farm-identity geometry, never pushed); CRS reproject to UTM
  32735/32736 before any area or distance math; assignments keyed on canonical farm ID.

## Prerequisite (input, not deliverable)

The authoritative Zimbabwe Natural Region 2020 polygon file, with its source and year metadata, is
provided by Mishael before this slice starts. Do not source a candidate and do not start with a
placeholder. If the file is not available when the slice is ready to begin, this slice blocks until
it is.

## What to build

The persisted foundation for region clusters. A region-boundary layer entity, its per-feature
boundary polygons, and a farm-region assignment, with the Alembic migration. A one-time seed of the
version-stamped Natural Region layer (read-only) from the provided file. Centroid point-in-polygon
assignment of each farm to the region that contains it, with a boundary-adjacent flag for farms whose
centroid sits near an edge. A Celery recompute that re-evaluates a farm's region assignment when the
farm registers or its `geometry_version` changes. Assignments are stamped with the boundary-layer
version so a future re-survey is a tracked re-assignment, not a silent overwrite. No comparison stats
yet (that is backlog 0003).

## Acceptance criteria

- [ ] Region-boundary layer, boundary-feature, and farm-region-assignment tables exist via an Alembic
  migration; the seeded Natural Region layer is read-only and cannot be edited or overwritten by an
  upload.
- [ ] After a clean init, Zimbabwe Natural Regions I to V are present, stamped with source, year, and
  version, with no upload required.
- [ ] A farm is assigned to exactly one region per layer by centroid containment; the same farm
  always assigns to the same region (deterministic); the assignment row carries the boundary-layer
  version.
- [ ] A farm whose centroid is within the configured edge tolerance of a boundary is flagged
  boundary-adjacent and logged.
- [ ] Registering a farm or bumping its `geometry_version` triggers an idempotent recompute that
  lands the correct assignment; re-running the task changes nothing.
- [ ] Reference geometry never appears in an outbound gateway payload (ADR 0010); the contract diff
  gate stays green.
- [ ] ruff + ruff format + mypy + pytest green.

## Tests (seams)

- Pure, zero DB and zero network (prior art `test_geo.py`): centroid point-in-polygon assignment and
  the boundary-adjacent flag against synthetic Zimbabwean polygons, reusing `validate_geometry` and
  `reproject`.
- DB-backed, skips without PostGIS (prior art `test_farm_analytics.py`, `test_ingestion_db.py`):
  seeding, assignment persistence, version stamping, and the read-only seeded Natural Region layer.
- Celery DB-backed (prior art `test_tasks_db.py`): recompute-on-event idempotency.
