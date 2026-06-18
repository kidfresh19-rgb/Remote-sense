# Backlog 0006 — On-demand neighbourhood

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 7
- Blocked by: 0003, 0004
- Invariants / decisions: CRS reproject to UTM 32735/32736 before any distance math; neighbourhood is
  subject-centric and computed on demand, with no persisted identity (CONTEXT.md: *cluster*).

## What to build

Subject-centric neighbourhood comparison. Given a farm, find the farms near it (KNN or radius)
constrained to its Natural Region, computed on demand with no persisted cluster row, then run the
standing and movement comparison (backlog 0003 and 0004) over that ad-hoc group. Proximity uses
PostGIS KNN (`<->`) or `ST_DWithin` against the stored centroid and boundary, with distance computed
in the working UTM zone.

## Acceptance criteria

- [ ] For a selected farm, the neighbourhood is the nearby farms within its Natural Region (KNN or
  radius), computed on demand with no persisted cluster row.
- [ ] Standing and movement run over the neighbourhood through the same engine (backlog 0003 and
  0004), with the same reference pass and gates.
- [ ] Distance and radius are computed in UTM (32735/32736), not in degrees.
- [ ] K and radius are runtime-configurable (see resolve-before-merge), not hard-coded.
- [ ] ruff + ruff format + mypy + pytest green.

## Resolve before merge

- **Open Item 2 (neighbourhood parameters).** Confirm the default K (about 20 farms) or radius (about
  10 km within the Natural Region) before merge. Both must be runtime-configurable.

## Tests (seams)

- Pure, zero DB and zero network (prior art `test_geo.py`): KNN and radius selection over synthetic
  coordinates within a Natural Region, with UTM distance.
- DB-backed, skips without PostGIS (prior art `test_farm_analytics.py`): the neighbourhood read and
  its comparison over seeded farms.
