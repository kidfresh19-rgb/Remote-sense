# ADR 0010 — Region boundaries: remote-sense owns analytical reference geometry

- Status: accepted
- Date: 2026-06-16
- Feature: farm comparison groups (region clusters, peer cohorts, neighbourhood) - internal analyst
  workspace only
- Relates to: invariant 1 (ports and adapters), invariant 6 (split-ownership sync), ADR 0006 (the
  AgriTrack sync contract), ADR 0008 (frozen external contract)

## Context

The comparison-groups feature lets analysts group farms and benchmark a farm's health against its
group (`CONTEXT.md`: *comparison group*, *cluster*, *peer cohort*). The spatial half of that grouping
is the **region cluster**: farms whose location falls inside an agro-ecological or administrative
boundary. Delivering it means remote-sense must (1) store polygon geometry for those boundaries (a
seeded Zimbabwe **Natural Region** map, plus analyst-uploaded ward/district/custom shapefiles) and
(2) assign each farm to the region that contains it.

This looks, at first glance, like it crosses a line. Invariant 1 keeps all external-edge access
behind adapters; invariant 6 states the gateway owns farm identity and geometry (read-only here) and
that **geometry is never returned**. A future reader who sees remote-sense storing polygons and
assigning farms to them could reasonably assume the split-ownership model has been violated, and try
to "fix" it. This ADR records why it has not.

## Decision

**Region boundaries are *analytical reference geometry*, a category distinct from *farm-identity
geometry*. remote-sense owns reference geometry; the gateway still solely owns farm and field
identity and geometry. The two never mix and never flow into each other.**

Concretely:

1. **Region boundary layers live in remote-sense and are owned here.** The seeded Natural Region
   layer is read-only and version-stamped (source, year, version tag); analyst uploads
   (`.zip` shapefile, GeoJSON, GeoPackage, multi-feature) extend it. One layer of N polygons yields
   N region clusters.
2. **Farm-to-region membership is derived, not authored.** It is computed by centroid
   point-in-polygon and stored as an assignment stamped with the boundary layer's version, so a zone
   re-survey is a tracked re-assignment rather than a silent overwrite. This is the same provenance
   discipline `geometry_version` already applies to field boundaries (invariant 5).
3. **This geometry is internal and never leaves.** Region boundaries and assignments are never farm
   geometry, never appear in the outbound `GatewayPayload`, and are never pushed or returned. The push
   stays geometry-free, so invariant 6 holds exactly as ADR 0006 fixed it.
4. **Boundary ingestion is an internal workspace action, not a contract surface.** Uploads are an
   RBAC-gated analyst capability inside the workspace BFF, parsed with geopandas (already a `geo`
   dependency). They add no new external edge, no vendor SDK on a hot path, no new credential, and no
   change to any frozen AgriTrack route (invariants 1 and 8, ADR 0008).

## Considered options

1. **Gateway owns region boundaries too.** Rejected. A Natural Region is an analytical grouping
   remote-sense derives and reasons about (agro-ecological comparison); it is not farmer identity. The
   gateway has no concept of it and no interest in maintaining it, and pushing region ownership across
   the boundary would couple an internal analytical concern to the frozen external contract for no
   benefit.
2. **Persist nothing; fetch region membership on demand from an external source.** Rejected. Region
   clusters need stable, reproducible, version-stamped membership. A static set of reference polygons
   gains nothing from an external dependency, has no offline reproducibility, would itself need a port
   and a credential, and breaks the version-stamping of farm-region assignments.
3. **remote-sense owns analytical reference geometry, distinct from farm-identity geometry.** Chosen,
   as set out in the decision above.

## Consequences

- **New invariant (a refinement of invariant 6, not a loosening): remote-sense owns analytical
  reference geometry; the gateway owns farm-identity geometry. The two are distinct and never
  confused, and neither side writes the other's.** Farm-identity geometry still flows only
  gateway -> remote-sense and is still never returned; reference geometry is created and owned here
  and never leaves. This is the language CONTEXT.md and any future reader should carry alongside
  invariants 1 and 6.
- A new geometry-bearing entity category enters the data model (region-boundary layers and their
  polygons, plus farm-region assignments); migrations add the corresponding tables. This ADR is the
  recorded reason a reader will find remote-sense storing polygons without it being an invariant-6
  violation.
- The contract gate (`tests/contract/`) and invariant 6 are unaffected, because reference geometry
  never enters an outbound payload. A reviewer who sees region polygons stored here should read this
  ADR rather than "fix" the layer away or loosen the invariant to let farm geometry leak - the two
  failure modes this ADR exists to prevent.
- Boundary uploads introduce an internal ingestion path (shapefile and friends via geopandas) that
  did not exist before; it is RBAC-gated and lives behind the workspace BFF, never on a frozen route.
- A persisted *emergent* neighbourhood partition (DBSCAN) is deliberately out of scope and is **not**
  authorised by this ADR. It changes membership-stability semantics and would need its own ADR if a
  need ever appears (`CONTEXT.md`: *cluster*).

## Amendment (2026-06-17): analyst-created regions, source provenance, Natural Region composition

This extends the decision above; it does not loosen it. Region boundaries may now also be created by
an analyst **drawing** in the workspace (a freeform polygon or a radius circle around the farm being
viewed, the circle compiled to a polygon) or uploading a **single** feature, in addition to the
seeded layer and bulk multi-feature uploads. All of these are the same analytical-reference-geometry
category; none is farm-identity geometry; and none is ever pushed. The centroid containment rule is
unchanged and identical for every source.

- **Governance.** Creating a drawn or single-feature region is an analyst capability
  (`create_region_cluster`); bulk multi-feature uploads remain admin (`upload_region_boundary`). We
  considered making drawn regions private-until-promoted and rejected it for v1: regions stay global
  and shared like wards, but every boundary now carries a `source` (`seeded` | `uploaded` | `drawn`)
  and its creator, so surfaces distinguish and filter official geometry from analyst-drawn geometry.
  The map can hide the analyst-drawn layer.
- **Cross-zone boundaries.** A boundary may span Natural Regions. We do not clip or reject it: the
  analyst's intent is authoritative and cross-gradient comparison is legitimate. "Like with like" is
  an analysis-layer concern, not a region-definition one, so each boundary carries a derived
  area-weighted **Natural Region composition** (e.g. `{III: 0.71, IV: 0.29}`) and a **dominant
  Natural Region**. The analysis layer benchmarks within the dominant region by default and breaks out
  per-region stats when the composition spread is meaningful (a flagged config default; PRD 0002 Open
  Item 4).
- **Provenance and drift.** `source`, creator, composition, and dominant region are derived,
  geometry-dependent attributes owned by remote-sense. Composition and dominant region are recomputed
  in the same path that handles a boundary geometry change, so they never drift from the geometry.
- **Unaffected.** Invariant 6 and the contract gate still hold: this geometry is internal and never
  enters an outbound payload. The data model gains `source` / creator and composition / dominant-NR
  fields and one lighter permission; nothing about the gateway push changes.
