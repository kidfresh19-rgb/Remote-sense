# Backlog 0009 — Peer cohorts

- Status: ready-for-agent
- Type: HITL (size-bucket thresholds need an agronomy-scientist decision) + RBAC confirm
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 6
- Blocked by: 0003
- Invariants / decisions: cohorts are remote-sense-owned derived analyses keyed on canonical farm ID;
  region means the Natural Region assignment, never the gateway `Farm.region` string; crop uses the
  canonical `CROPS` set from `rs_interpret`; irrigation is a remote-sense-owned optional tag, never
  gateway-synced.

## What to build

Peer cohorts. A persisted cohort definition keyed on `(canonical crop, Natural Region, size bucket)`,
with membership computed live at `(farm, crop)` granularity: crop from the canonical `CROPS` set (no
free-text; an unknown crop places the farm in no crop cohort), region from the Natural Region
assignment (not `Farm.region`), and farm size as the sum of field `area_m2` bucketed by Zimbabwe
typology. An optional nullable irrigation tag on the farm (remote-sense-owned, never gateway-synced,
never a membership criterion, filterable when known). The cohort comparison reuses the standing
engine; movement-aware filtering can come post-merge. The cohort lifecycle is gated by
`create_cohort` and `manage_cohort`.

## Acceptance criteria

- [ ] A maize-and-tobacco farm produces one maize-cohort membership and one tobacco-cohort
  membership, each benched on that crop's area-weighted NDVI.
- [ ] Cohorts key off the Natural Region assignment, not `Farm.region`; crop uses the canonical
  `CROPS` set, and an unknown crop is placed in no crop cohort.
- [ ] Farm size is the sum of field `area_m2`, bucketed by the resolved Zimbabwe typology buckets.
- [ ] A farm with `irrigation` null still appears in its cohorts; an irrigation filter narrows
  results only when applied.
- [ ] The cohort definition is persisted; membership is computed live.
- [ ] The cohort lifecycle is gated by `create_cohort` / `manage_cohort`.
- [ ] ruff + ruff format + mypy + pytest green.

## Resolve before merge

- **Open Item 1 (size buckets) - blocks merge.** An agronomy-scientist must set the size-bucket
  thresholds before launch. The placeholder `0 to 10 / 10 to 50 / 50+ ha` does NOT fit Zimbabwe
  typology (communal smallholdings often under 2 to 3 ha; A1 about 5 to 6 ha arable; A2 commercial 20
  to 2000+ ha) and must not ship as-is. Carry the placeholder, flag for review, resolve before merge.
- **Open Item 3 (RBAC) - RESOLVED 2026-06-19.** `create_cohort` / `manage_cohort` mapped to analyst,
  confirmed and implemented in `packages/rs_core/rbac.py` (engineering sign-off, owner/engineer; no
  agronomy dependency). Endpoint principals are wired when this slice builds the cohort endpoints.

## Tests (seams)

- Pure, zero DB and zero network (prior art `test_indices.py`): size bucketing and `(farm, crop)`
  cohort keying.
- DB-backed, skips without PostGIS (prior art `test_farm_analytics.py`): live membership for a
  multi-crop farm yielding multiple cohorts, region keyed on the Natural Region assignment, and a
  null-irrigation farm still included.
