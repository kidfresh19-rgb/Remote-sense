# Backlog 0005 — Region-boundary upload

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 5
- Blocked by: 0002
- Invariants / decisions: ADR 0010 (uploaded boundaries are remote-sense-owned analytical reference
  geometry, never pushed); reuse `validate_geometry` and `reproject`; geopandas is already a `geo`
  dependency (no new dependency); upload lives behind the workspace BFF, never on a frozen route.

## What to build

Analyst upload of a region-boundary layer as a `.zip` shapefile, GeoJSON, or GeoPackage through the
workspace BFF. The system reads every feature via geopandas, lets the analyst map which attribute
column names each region, validates and reprojects each feature to WGS84 reusing the geo helpers, and
persists one region boundary per valid feature in a single transaction, skipping and reporting broken
features. CRS comes from the `.prj`; a missing CRS prompts the analyst rather than guessing. On
success the recompute (backlog 0002) assigns existing farms into any containing new regions. The
upload is gated by a new `upload_region_boundary` permission.

## Acceptance criteria

- [ ] A `.zip` shapefile, GeoJSON, or GeoPackage upload reads all features; the analyst maps the name
  column.
- [ ] One multi-feature upload creates one region cluster per valid feature in a single transaction;
  invalid features are skipped and reported, never silently dropped and never fatal to the layer.
- [ ] Each feature is reprojected to WGS84 from its `.prj` CRS; a missing CRS prompts the analyst.
- [ ] After upload, existing farms are reassigned to any containing new region by the recompute, and
  assignments carry the new boundary-layer version.
- [ ] The endpoint is gated by `upload_region_boundary`; no new dependency is added.
- [ ] Uploaded reference geometry never appears in an outbound gateway payload (ADR 0010).
- [ ] ruff + ruff format + mypy + pytest green.

## Resolve before merge

- **Open Item 3 (RBAC).** Confirm the `upload_region_boundary` role mapping before merge (proposed:
  admin only). This is a confirm-before-merge gate, separate from any design review.

## Tests (seams)

- Pure, zero DB and zero network (prior art `test_geo.py`): multi-feature parsing, per-feature
  validation and reprojection, name-column mapping, broken-feature skip-and-report.
- DB-backed, skips without PostGIS (prior art `test_workspace_db.py`, `test_tasks_db.py`):
  persistence in one transaction and the post-upload recompute; the endpoint called with a
  `Principal`/`Role` for the RBAC gate.
