# Backlog 0004 — Comparison engine: movement 2x2 attribution

- Status: ready-for-agent
- Type: AFK
- Parent: PRD 0002 (`docs/prd/0002-farm-comparison-groups.md`), slice 2b
- Blocked by: 0003
- Invariants / decisions: structural fact only, never auto-labels a cause (cause is left to the
  analyst and grounding context, ADR 0007); reuse the trailing-baseline and reference-pass alignment
  from backlog 0003.

## What to build

The movement attribution lens over a region cluster, built on backlog 0003. For each member, compute
its delta against its own trailing baseline (about 6 to 8 passes, 30 to 45 days). The group is
*systemic* when its median member-delta crosses a decline threshold. A member is *idiosyncratic* when
its delta is a robust median/MAD z-score beyond about 2 within the group. Assign the 2x2 label:
*nominal* (neither moved), *idiosyncratic* (member declined, group steady), *systemic* (both
declined), *resilient* (member steady while the group declined). The output is the structural label
only and never asserts drought or any cause. Gate on a minimum group size and the clear-fraction bar.

## Acceptance criteria

- [ ] A synthetic group where all members decline together yields `systemic` for a declining member.
- [ ] A group where one member alone declines yields `idiosyncratic` for it and `nominal` for the
  steady members.
- [ ] A member that holds while the group falls yields `resilient`.
- [ ] No movement output contains the word "drought" or any asserted cause (structural only).
- [ ] Movement is suppressed (no verdict) when the group has fewer than the minimum members (default
  5) with a valid read; members below the clear-fraction bar do not contribute.
- [ ] Trailing-baseline window, decline threshold, z-score cutoff, and minimum members are
  configurable, not hard-coded.
- [ ] ruff + ruff format + mypy + pytest green.

## Tests (seams)

- Pure, zero DB and zero network (prior art `test_indices.py`, `test_geo.py`): delta versus trailing
  baseline, median group-delta, median/MAD z-score, quadrant assignment, and both gates on synthetic
  series, including the four canonical quadrant scenarios.
- DB-backed, skips without PostGIS (prior art `test_farm_analytics.py`): the labels end-to-end over a
  seeded cluster.
