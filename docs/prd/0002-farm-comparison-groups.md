# PRD 0002 — Farm Comparison Groups & Neighbourhood Health Comparison

- Status: ready-for-agent
- Date: 2026-06-16
- Phase: Shape and Specify locked (this session's `/grill-with-docs`); entering Slice
- Surface: internal analyst workspace only. No AgriTrack / gateway contract change in this phase.
- Related: ADR 0010 (region boundaries are remote-sense-owned analytical reference geometry),
  ADR 0007 (grounding-context fusion), CONTEXT.md (comparison-group glossary added this session),
  CLAUDE.md section 1 invariants (5 provenance, 6 split-ownership), PLAN.md.

## Problem

Analysts look at one farm at a time. When a farm's health declines they cannot easily tell whether
the cause is local to that farm (something the farmer can act on, like an irrigation failure or a
localised pest) or area-wide (a regional stress no single farmer caused, like a dry spell), and they
have no quick, honest way to benchmark a farm against comparable farms. Comparing raw NDVI across
farms is misleading because different crops sit at different index levels and farms are imaged on
different dates. As the number of registered farms grows, one-farm-at-a-time review does not scale:
there is no way to manage many farmers as a group, or to surface which farms in an area need
attention first. The platform exists to uplift Zimbabwean farming communities, and community-scale
insight is exactly what is missing.

## Solution

Group farms into **comparison groups** and let analysts compare a farm's **health status** against
its group through two honest lenses, all inside the analyst workspace.

- A **comparison group** is any persisted set of farms an analyst compares a farm against. It is a
  *cluster* (spatial) or a *peer cohort* (attribute similarity). A *cluster* is either a *region*
  cluster (farms inside a region boundary, persisted) or an on-demand *neighbourhood* (the farms near
  a given farm). A *region boundary* is the polygon layer that defines region clusters: the seeded
  Zimbabwe Natural Region map, or an analyst-uploaded ward/district/custom layer.
- The **standing** lens answers "where does this farm sit now?" as a crop-stratified percentile
  within the group, never a raw cross-crop ranking.
- The **movement** lens (the headline) answers "is this farm's change shared by its group or unique
  to it?" as a 2x2: *nominal*, *idiosyncratic*, *systemic*, *resilient*. It reports the structural
  fact only and never auto-labels "drought."
- A new **group view** dashboard route lets analysts manage and read a whole group at once; a
  **group-context panel** in the workspace shows an open farm's standing and movement in each group
  it belongs to; shared map layers draw the boundaries and nearby farms.

Honesty is built in: comparisons run on a common **group reference pass** so dates line up, gate on
clear-pixel fraction and a minimum group size, and reuse the existing crop-aware `classify` and the
area-weighted health definition so "health" means one thing everywhere.

## Slices and blocking order

Vertical tracer bullets for `/to-issues`. Blocking order in brackets; the gating open item (see the
Open Items section) is noted where one applies.

1. **Natural Region foundation** [no deps]. Region-boundary and farm-region-assignment models plus
   migration; seed the version-stamped Natural Region layer at init; centroid assignment with the
   boundary-adjacent flag; Celery recompute on register and geometry-change.
2. **Comparison engine** [needs 1]. Pure functions for group reference pass, standing, and the
   movement 2x2, plus the region-cluster repository read. The `get_cluster_stats` abstraction with an
   always-miss cache check (the materialization seam).
3. **Group view API and route** [needs 2; gated by Open Item 3 RBAC `view_group`]. `GET
   /api/groups/:id/overview` and the `/groups/:id` dashboard route.
4. **Workspace group-context panel** [needs 2]. `GET /api/farms/:id/group-context` and the panel.
5. **Region-boundary upload** [needs 1; gated by Open Item 3 RBAC `upload_region_boundary`].
   Multi-feature `.zip` shapefile / GeoJSON / GeoPackage upload, name-column mapping, recompute.
6. **Peer cohorts** [needs 2; gated by Open Item 1 size buckets and Open Item 3 RBAC cohort
   permissions]. Cohort-definition model, live membership, irrigation tag, cohort comparison.
7. **On-demand neighbourhood** [needs 2; gated by Open Item 2 neighbourhood params]. KNN/radius
   within Natural Region and neighbourhood comparison.
8. **Map layers** [needs 1, 5, 7]. Natural Region boundaries, uploaded boundaries, neighbourhood
   overlay, and the cluster choropleth, all toggleable and default off.

## User Stories

1. As an analyst, I want to see the field boundaries of farms near a farm I am viewing, so that I
   understand its spatial context.
2. As an analyst, I want farms grouped into comparison groups, so that I can reason about many
   farmers at once instead of one at a time.
3. As an analyst, I want a farm's health expressed as a crop-stratified percentile within its group,
   so that I can benchmark it without being misled by what crop it grows.
4. As an analyst, I want the system to never rank raw NDVI across different crops, so that a healthy
   groundnut farm is not falsely flagged as worse than thriving maize.
5. As an analyst, I want to know whether a farm's decline is shared by its group (systemic) or unique
   to it (idiosyncratic), so that I can tell the farmer whether it is something they can act on.
6. As an analyst, I want farms that hold steady while their group declines flagged as *resilient*, so
   that I can learn and propagate what is working.
7. As an analyst, I want the movement read to report the structural fact only and never assert
   "drought," so that I do not over-claim a cause the index alone cannot prove.
8. As an analyst, I want to interpret a systemic decline using the existing grounding context
   (weather, activity), so that I decide drought versus normal maturation myself.
9. As an analyst, I want Zimbabwe's Natural Regions available out of the box, so that I can compare
   like-with-like agro-ecologically without sourcing a map first.
10. As an analyst, I want to upload a ward or district shapefile, so that I can build region clusters
    the seeded map does not cover.
11. As an analyst, I want one upload of a multi-feature shapefile to create many region clusters at
    once, so that a 1,200-ward file is one action, not 1,200.
12. As an analyst, I want to choose which attribute column names each region, so that uploaded
    clusters carry meaningful names.
13. As an analyst, I want broken features in an upload skipped and reported rather than silently
    dropped or fatal, so that one bad polygon does not lose the whole layer.
14. As an analyst, I want a farm assigned to the region that contains its centroid, so that
    membership is deterministic and reproducible.
15. As an analyst, I want farms whose centroid sits near a boundary flagged, so that I can sanity
    check edge cases.
16. As an analyst, I want a farm benchmarked against peers like it (same crop, Natural Region, size)
    regardless of distance, so that I can compare even when neighbours are sparse.
17. As an analyst, I want a multi-crop farm's maize compared to maize peers and its tobacco to
    tobacco peers, so that each crop is judged fairly.
18. As an analyst, I want to tag a farm's irrigation status, so that cohorts can separate irrigated
    from rainfed when I know which is which.
19. As an analyst, I want farms with unknown irrigation still included in cohorts, so that missing
    data never silently drops a farm from a comparison.
20. As an analyst, I want to filter a cohort by irrigation when it is known, so that I can look at
    just rainfed maize in a region when that is the question.
21. As an analyst, I want every comparison computed on a common reference pass, so that I am not
    comparing reads taken two weeks apart.
22. As an analyst, I want a farm with no clear pass near the reference date to sit out that round
    rather than error, so that cloud cover degrades the comparison gracefully.
23. As an analyst, I want the clear fraction (how many members contributed) shown on every group
    comparison, so that I can judge how much to trust it.
24. As an analyst, I want a verdict withheld when a group has fewer than the minimum members, so that
    I am not shown statistics computed on too few farms.
25. As an analyst, I want a group view page showing member count, health distribution, the 2x2
    movement breakdown, a ranked member list, the group time-series, and the boundary on a map, so
    that I can read a whole group at a glance.
26. As an analyst, I want to click a farm in the group member list and open it in the workspace, so
    that I can drill from group to farm without losing my place.
27. As an analyst, I want a panel in the workspace showing an open farm's standing and movement in
    each of its groups, so that I get group context without leaving the farm.
28. As an analyst, I want toggleable map layers for Natural Region boundaries, uploaded boundaries,
    the neighbourhood of a selected farm, and a choropleth of group health, so that I control what
    the map shows.
29. As an analyst, I want group membership to update automatically when a farm registers or its
    boundary changes, so that groups stay current without manual upkeep.
30. As an analyst, I want a region re-survey recorded as a tracked re-assignment stamped with the new
    boundary version, so that history is not silently overwritten.
31. As an analyst, I want comparisons to reuse the same area-weighted, crop-aware health definition
    used elsewhere, so that "health" means one consistent thing across the platform.
32. As a maintainer, I want region boundaries owned by remote-sense and never pushed to the gateway,
    so that the split-ownership contract stays intact (ADR 0010).
33. As an administrator, I want control over who can upload region boundaries, manage cohorts, and
    view groups, so that these new capabilities are governed by RBAC.

## Implementation Decisions

Each decision carries at least one **Acceptance** line. Module and concept names are used rather than
file paths, which go stale. No new external edge, vendor SDK, or credential is introduced; the
ports-and-adapters edges are unchanged.

### D1. Vocabulary and ownership

`comparison group` is the umbrella, realised as a `cluster` (spatial: `region` or on-demand
`neighbourhood`) or a `peer cohort` (similarity). `region boundary` is the distinct polygon layer.
A comparison group is a derived analysis owned by remote-sense and keyed on canonical farm ID;
region boundaries are analytical reference geometry, also owned by remote-sense and distinct from
gateway-owned farm-identity geometry (ADR 0010).

- **Acceptance:** the glossary terms in CONTEXT.md are the terms used across code, API field names,
  and UI copy. No code path pushes a comparison group, region boundary, or farm-region assignment to
  the gateway; the contract diff gate stays green.

### D2. Two lenses, farm as the unit

The comparison unit is the **farm**, using the existing area-weighted, crop-aware health definition
(`overall_health`, derived through `classify("ndvi", value, crop)`). **Standing** is a crop-stratified
percentile within the group; where a crop is too sparse to stratify, it falls back to comparing the
classified-status distribution rather than raw values. **Movement** is the headline lens.

- **Acceptance:** standing ranks a farm only against same-crop area within the group; a synthetic
  group mixing maize and tobacco never ranks one crop against the other on raw NDVI. Health values
  reported by the group endpoints match the existing farm-analytics health for the same farm and
  pass.

### D3. Movement is a 2x2, structural fact only

Cross "did the farm move?" with "did the group move?": `nominal`, `idiosyncratic` (farm declined,
group steady), `systemic` (both declined), `resilient` (farm steady while group declined). A farm's
delta is measured against its own trailing baseline (about 6 to 8 passes, 30 to 45 days). The group
is *systemic* when its **median** member-delta crosses a decline threshold; a farm is *idiosyncratic*
when its delta is a robust **median/MAD** z-score beyond about 2 within the group. The output is the
structural label only; it never asserts drought. Interpretation of cause is left to the analyst and
grounding context (ADR 0007).

- **Acceptance:** a synthetic group where every member declines together yields `systemic` for a
  declining member; a group where one member alone declines yields `idiosyncratic` for it and
  `nominal` for the steady members; a member that holds while the group falls yields `resilient`. No
  movement output contains the word "drought" or any asserted cause.
- **Acceptance:** movement is suppressed (no verdict) when the group has fewer than the minimum
  members (default 5) with a valid read, and members below the clear-fraction bar do not contribute.

### D4. Grouping mechanics

Region clusters are persisted and rule-defined; neighbourhood is subject-centric and computed on
demand (KNN or radius within the farm's Natural Region); peer cohorts are rule-defined. A persisted
emergent partition (DBSCAN) is out of scope and would require its own ADR (ADR 0010). Membership is
many-to-many. A Celery task recomputes rule-defined membership on farm register, geometry-version
change, boundary-layer upload, and cohort-criteria edit.

- **Acceptance:** a farm appears in every group it qualifies for at once (its Natural Region, any
  containing uploaded region, and each `(crop)` cohort it grows). Registering or re-bounding a farm
  triggers a recompute that lands it in the correct region assignment idempotently. No persisted
  DBSCAN entity exists.

### D5. Region boundaries (see ADR 0010)

Seed the Zimbabwe Natural Region layer (I to V) read-only at init, version-stamped with source and
year (the current official map). Support analyst uploads as `.zip` shapefile, GeoJSON, or GeoPackage,
multi-feature, read via geopandas (already a `geo` dependency, no new dependency), with the analyst
mapping the name column. Each feature is validated and reprojected to WGS84 reusing the existing geo
helpers; CRS comes from the `.prj`, and a missing CRS prompts the analyst rather than guessing. Farms
are assigned by centroid point-in-polygon, boundary-adjacent farms are flagged, and each assignment is
stamped with the boundary-layer version. Majority-area-overlap assignment is deferred to v2.

- **Acceptance:** Natural Regions are present after a clean init with no upload. One multi-feature
  upload creates one region cluster per valid feature in a single transaction; invalid features are
  skipped and reported, not fatal. A given farm always assigns to the same region under the centroid
  rule, and the assignment row carries the boundary version. The seeded Natural Region layer cannot be
  edited or overwritten by an upload.

### D6. Peer cohorts

A cohort is keyed on `(canonical crop, Natural Region, size bucket)`, at `(farm, crop)` granularity.
Crop is the canonical `CROPS` set from `rs_interpret` (no free-text); a farm with an unknown crop is
not placed in a crop cohort. Region means the **Natural Region** assignment, never the gateway
`Farm.region` string. Size is the sum of the farm's field `area_m2` (from `FieldGeometryVersion`),
bucketed by Zimbabwe farm typology (buckets are Open Item 1). Irrigation is an optional nullable
analyst-set tag on the farm, owned by remote-sense and not gateway-synced; it is never a membership
criterion, and analysts may filter on it when known. Cohort definitions are persisted; membership is
computed live.

- **Acceptance:** a maize-and-tobacco farm produces one maize-cohort membership and one
  tobacco-cohort membership, each benched on that crop's area-weighted NDVI. Cohorts key off the
  Natural Region assignment, not `Farm.region`. A farm with `irrigation` null still appears in its
  cohorts; an irrigation filter narrows results only when applied.

### D7. Compute and the materialization seam

Comparisons run on a **group reference pass**: the most recent date by which a quorum (default 50%) of
members has a clear read, with each member contributing its nearest clear pass within plus or minus N
days (default 14); members with no in-window clear pass sit out, and the clear fraction is surfaced.
The movement lens applies the same alignment at "now" and at the trailing baseline. Only definitions
and region assignments are persisted; membership and stats are computed on read in v1, consistent with
the existing on-the-fly `get_farm_analytics_*`. A thin `get_cluster_stats` abstraction checks for a
cache and falls back to live compute; in v1 the cache is always empty, so the seam exists but is not
activated.

- **Acceptance:** every farm in a single comparison uses a pass within plus or minus N days of one
  reference date; quorum and N are configurable, not hard-coded; the response surfaces contributing
  members versus total. No materialized cache table is built in v1, and the stats read path routes
  through the `get_cluster_stats` abstraction.

### D8. Surfaces

A new dashboard-family route `/groups/:id` renders member count, health distribution, the 2x2
movement breakdown, a ranked standing member list, the group time-series, the boundary on the map,
and the clear-fraction indicator, served by `GET /api/groups/:id/overview`. A workspace
**group-context panel** shows an open farm's standing percentile and 2x2 label per group with a link
to the group view, served by `GET /api/farms/:id/group-context`. Both endpoints live in the workspace
BFF (internal, browser-facing, free to evolve; not the frozen contract). The shared MapLibre map gains
toggleable layers for Natural Region boundaries, uploaded boundaries, the selected farm's
neighbourhood, and a group-health choropleth, all default off. Returning region-boundary GeoJSON to
the browser is allowed: invariant 6 governs the gateway push, not the internal BFF, which already
serves field polygons to the map.

- **Acceptance:** `/groups/:id` renders all six panels and the clear-fraction indicator; a member-row
  click opens that farm in the workspace and preserves group context. The group-context panel shows
  one row per group a farm belongs to, each with standing, movement label, reference-pass date, and
  clear fraction. Map layers are individually toggleable and off by default. Both endpoints and the
  group view use the same group reference pass for a given group and date.

## Testing Decisions

A good test asserts external behaviour (the computed verdict, the assigned region, the response
shape, the gate firing), not internal structure. All new logic is testable with zero network, and the
pure math is testable with zero DB. The validation matrix and adapter-parity suites are untouched
because no index or adapter changes; the comparison reads existing `Analysis` rows.

- **Seam 1, pure comparison and geometry logic (zero DB, zero network).** Prior art: `test_geo.py`,
  `test_indices.py`. Cover the movement 2x2 (delta versus trailing baseline, median group-delta,
  median/MAD z-score, quadrant assignment, n>=5 gate), group reference-pass selection (quorum date,
  nearest clear pass within plus or minus N, sit-out, clear-fraction), standing (crop-stratified
  percentile and sparse-crop fallback), centroid point-in-polygon assignment and the boundary-adjacent
  flag, and multi-feature parsing reusing `validate_geometry` per feature.
- **Seam 2, repository and endpoint functions (DB-backed, skips without PostGIS).** Prior art:
  `test_farm_analytics.py`, `test_workspace_db.py`. Seed synthetic `Farm`/`Field`/
  `FieldGeometryVersion`/`Analysis` with the `_mp` helper, then exercise the group-overview and
  per-farm group-context reads, region-boundary persistence with version-stamped assignment, cohort
  definition with live membership (multi-crop farm to multiple cohorts), and the two endpoints called
  directly with a `Principal`/`Role` for RBAC.
- **Seam 3, Celery membership recompute (DB-backed task).** Prior art: `test_tasks_db.py`. Recompute
  on register, geometry-change, upload, and criteria-edit is idempotent and stamps the boundary
  version.
- **Frontend** (`/groups/:id`, the group-context panel, the map layers) is validated by the
  frontend's own conventions and `/verify`, not a backend seam.

## Open Items (resolve before merge)

These three are explicitly tracked so they do not dissolve between phases. Each becomes its own
"resolve before merge" ticket in `/to-issues`, attached to the slice it gates.

1. **Size-bucket thresholds (gates Slice 6, peer cohorts).** Agronomy-scientist review is required
   before launch. The placeholder `0 to 10 / 10 to 50 / 50+ ha` does not fit Zimbabwe typology:
   communal smallholdings are often under 2 to 3 ha; A1 is roughly 5 to 6 ha arable; A2 commercial
   ranges 20 to 2000+ ha. As drafted, "smallholder = 0 to 10 ha" collapses genuinely different farm
   types and must not ship as-is. Carry the placeholder, flag for review, resolve before launch.
2. **Neighbourhood parameters (gates Slice 7, neighbourhood).** Default K is about 20 farms, or about
   a 10 km radius within the Natural Region. Both must be runtime-configurable, not hard-coded. The
   PRD specifies them as config with these defaults; the final choice is confirmed before merge.
3. **RBAC extensions (gates Slices 3, 5, 6).** New permissions extend the existing view/annotate model
   in `rs_core` RBAC, with proposed role mappings confirmed before merge:
   - `upload_region_boundary` (proposed: admin only).
   - `create_cohort` / `manage_cohort` (proposed: analyst).
   - `view_group` (proposed: any authenticated user with farm access).

## Out of Scope

- Any AgriTrack farmer-app delivery or change to the frozen gateway contract. This phase is the
  internal analyst workspace only.
- Persisted emergent (DBSCAN) neighbourhood clusters. Deferred and would need their own ADR (ADR 0010).
- Majority-area-overlap region assignment. v1 uses centroid containment; overlap is a v2 item.
- Imagery-derived irrigation detection. Irrigation is an optional analyst-set tag in v1.
- A materialized `cluster_stat` cache. v1 leaves the seam and computes live.
- Field-level comparison as the unit. v1 compares farms; field-level drill-down is a later slice.
- Any automated drought or cause labelling. Movement reports structure only.

## Further Notes

- **Mission.** The headline value is community-scale honesty: telling a farmer whether their problem
  is theirs to fix or the whole area's, surfacing resilient farms to learn from, and letting analysts
  steward a growing population of Zimbabwean farms by exception rather than one by one.
- **Invariants honoured.** Split-ownership sync (comparison groups are remote-sense-owned derived
  analyses keyed on canonical farm ID; reference geometry is never pushed, ADR 0010); reflectance-first
  and per-AOI cloud masking (the comparison reuses existing provenance-stamped `Analysis` rows);
  resolution honesty and provenance carried through; CRS reprojected to UTM (32735/32736) before any
  distance or area math; ports-and-adapters edges unchanged; the frozen AgriTrack contract untouched.
- **Reuse.** This generalises the existing field-versus-farm anomaly (`get_farm_analytics_anomalies`)
  up one rung to farm-versus-group, and reuses `classify`/`vigour_to_status`, the area-weighted health
  definition, the nearest-pass alignment (commit 40d5f2d), and the geometry validation helpers.
