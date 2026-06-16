# Backlog 0003 — Comparison engine: group reference pass + standing

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 2a
- Blocked by: 0002
- Invariants / decisions: reuse the existing area-weighted, crop-aware health definition
  (`classify`, `vigour_to_status`) so "health" means one thing everywhere; reuse the nearest-pass
  alignment (commit 40d5f2d); assignments keyed on canonical farm ID.

## What to build

The read-side comparison foundation for a region cluster. A pure group-reference-pass selector: the
most recent date by which a quorum (default 50%) of members holds a clear read, with each member
contributing its nearest clear pass within plus or minus N days (default 14); members with no
in-window clear pass sit out, and the contributing-versus-total count (clear fraction) is surfaced. A
pure standing computation: a farm's crop-stratified percentile within the group, falling back to the
classified-status distribution where a crop is too sparse to stratify, never ranking raw NDVI across
crops. The persisted region-cluster read path that assembles a group's members and their
reference-pass health. A thin `get_cluster_stats` abstraction that checks a cache then falls back to
live compute (the cache is always empty in v1; this is the materialization seam). No movement labels
yet (that is backlog 0004).

## Acceptance criteria

- [ ] The group reference pass is the most recent date with a quorum (default 50%, configurable) of
  members holding a clear read; each member contributes its nearest clear pass within plus or minus N
  days (default 14, configurable); members with no in-window clear pass sit out; the response
  surfaces contributing versus total members.
- [ ] Standing ranks a farm only against same-crop area within the group; a mixed maize and tobacco
  group never ranks one crop against the other on raw NDVI; where a crop is too sparse, standing
  falls back to the classified-status distribution.
- [ ] Health values match the existing farm-analytics health for the same farm and pass (one
  definition).
- [ ] The stats read path routes through `get_cluster_stats`; in v1 the cache is always empty and
  live compute always runs; no materialized cache table is created.
- [ ] Quorum and N are configurable, not hard-coded.
- [ ] ruff + ruff format + mypy + pytest green.

## Tests (seams)

- Pure, zero DB and zero network (prior art `test_geo.py`, `test_indices.py`): reference-pass
  selection (quorum, nearest-within-N, sit-out, clear fraction) and standing (crop-stratified
  percentile and the sparse-crop fallback) on synthetic series.
- DB-backed, skips without PostGIS (prior art `test_farm_analytics.py`): the region-cluster read over
  seeded synthetic farms, fields, and analyses; health parity with existing farm analytics.
