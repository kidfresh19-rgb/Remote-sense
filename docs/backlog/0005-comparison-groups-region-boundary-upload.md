# Backlog 0005 — Region creation: upload (multi + single) and draw-to-create

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 5
- Blocked by: 0002
- Invariants / decisions: ADR 0010 + its 2026-06-17 amendment (all region boundaries are
  remote-sense-owned analytical reference geometry, never pushed; analyst-drawn and single-feature
  regions are the same category as uploaded wards); reuse `validate_geometry` and `reproject`;
  geopandas is already a `geo` dependency (no new dependency); creation lives behind the workspace
  BFF, never on a frozen route. Centroid containment is the one membership rule for every `source`.

## What to build

The backend for creating a region boundary three ways, all the same reference-geometry category,
each tagged with a `source` (`seeded` | `uploaded` | `drawn`) and its creator:

- **Multi-feature upload** as a `.zip` shapefile, GeoJSON, or GeoPackage through the workspace BFF.
  Read every feature via geopandas, let the analyst map the name column, validate and reproject each
  feature to WGS84 reusing the geo helpers, and persist one region boundary per valid feature in a
  single transaction, skipping and reporting broken features. CRS comes from the `.prj`; a missing
  CRS prompts the analyst rather than guessing.
- **Single-feature upload** is the same path with one feature.
- **Draw-to-create** via a `create_region_cluster` endpoint that accepts a single GeoJSON polygon
  (the frontend compiles a radius circle to a polygon before sending, so the server sees one rule).
  The viewed farm is only the entry point; the persisted region is free-standing and reusable,
  identical to an uploaded ward, owned by no farm.

Every boundary stores a derived area-weighted **Natural Region composition** (e.g. `{III: 0.71,
IV: 0.29}`) and a **dominant Natural Region**, computed at create time and recomputed whenever the
boundary geometry changes (wire into the same recompute path as backlog 0002). On success the
recompute assigns existing farms into any containing new region by centroid, stamping each assignment
with the boundary version. A cross-zone boundary is never clipped or rejected.

Creating a drawn or single-feature region is gated by a new `create_region_cluster` permission
(analyst); bulk multi-feature uploads stay gated by `upload_region_boundary` (admin).

## Acceptance criteria

- [ ] A `.zip` shapefile, GeoJSON, or GeoPackage upload reads all features; the analyst maps the name
  column; a single-feature file is accepted by the same path.
- [ ] One multi-feature upload creates one region cluster per valid feature in a single transaction;
  invalid features are skipped and reported, never silently dropped and never fatal to the layer.
- [ ] The `create_region_cluster` endpoint persists one `source=drawn` region from a GeoJSON polygon,
  owned by no farm, with centroid membership applied identically to an uploaded ward.
- [ ] Every region boundary carries a `source` and a creator; the seeded Natural Region layer cannot
  be edited or overwritten by any create path.
- [ ] Each feature is reprojected to WGS84 from its `.prj` CRS; a missing CRS prompts the analyst.
- [ ] Every boundary stores an area-weighted Natural Region composition and a dominant Natural Region,
  both recomputed on a geometry edit; no create path clips or rejects a boundary for spanning Natural
  Regions.
- [ ] After create, existing farms are reassigned to any containing new region by the recompute, and
  assignments carry the new boundary version.
- [ ] Bulk upload is gated by `upload_region_boundary`; draw / single create is gated by
  `create_region_cluster`; no new dependency is added.
- [ ] Region geometry never appears in an outbound gateway payload (ADR 0010).
- [ ] ruff + ruff format + mypy + pytest green.

## Resolve before merge

- **Open Item 3 (RBAC).** Confirm the `upload_region_boundary` (proposed: admin) and the new
  `create_region_cluster` (proposed: analyst) role mappings before merge.
- **Open Item 4 (dominant-NR split threshold).** Confirm the default share (proposed: `dominant_nr
  >= 0.85` treats a boundary as effectively single-Natural-Region) before merge; agronomy-scientist
  review.

## Tests (seams)

- Pure, zero DB and zero network (prior art `test_geo.py`): multi-feature parsing, per-feature
  validation and reprojection, name-column mapping, broken-feature skip-and-report, circle-to-polygon
  compile, and area-weighted Natural Region composition + dominant-NR derivation over synthetic
  geometry.
- DB-backed, skips without PostGIS (prior art `test_workspace_db.py`, `test_tasks_db.py`):
  persistence in one transaction, `source` tagging, the draw-create endpoint, the post-create
  recompute and the composition recompute on a geometry edit; both endpoints called with a
  `Principal` / `Role` for the two RBAC gates.
