# Backlog 0001 — Intelligence-Core Rebuild (local sliced backlog)

- Status: draft slices, local-first (not on a tracker yet)
- Date: 2026-06-10
- Source: `docs/prd/0001-intelligence-core-rebuild.md`, `docs/adr/0008-ordered-reimplementation-frozen-contract.md`
- Method: vertical tracer-bullet slices, each ending with every quality gate green (PRD section 4).
  This is the local equivalent of `/to-issues`. When a tracker target is chosen (Azure DevOps Boards
  or GitHub), each `Sx.y` becomes one work item or issue, preserving the dependency order below.

Decision references `(Rn)` point to PRD 0001 Appendix A.

## Phase 0 — Freeze and safety net (blocks everything)

### S0.1 Contract snapshot
- Dump the current OpenAPI to `contract/openapi.before.json`. Write `CONTRACT.md` listing every
  external endpoint, payload schema, status code, auth header, and env var name, tagging each route
  EXTERNAL-FROZEN (ingestion, outbound push, `/api/v1/mobile/data`) vs INTERNAL-IMPROVABLE (the BFF).
- Depends on: none.
- Done when: `CONTRACT.md` exists, the frozen set is enumerated, no code changed. (R19, R20, R21)

### S0.2 Contract tests + OpenAPI diff gate
- Contract tests asserting the shape of every frozen route. CI gate diffing the live OpenAPI against
  `openapi.before.json`, failing on any non-additive change to a frozen route.
- Depends on: S0.1.
- Done when: the gate is green on current main and would fail on a frozen-route change. (R22)

### S0.3 Characterization tests
- Golden-master tests pinning current behavior on paths we will refactor or rewrite: reflectance and
  index outputs, zonal stats, SCL clear-fraction, interpretation drafts, tile renders.
- Depends on: none (can run alongside S0.1).
- Done when: the suite captures current outputs so later refactors prove behavior held. (R29)

### S0.4 Freeze constraints in writing
- Add a "Rebuild constraints (non-negotiable)" block to `CLAUDE.md`: frozen external contract, fixed
  sync model, no new credentials, reuse `.env.example` var names. Verify `.env.example` lists every
  real env var name with dummy values so no new vars get invented.
- Depends on: S0.1.
- Done when: `CLAUDE.md` carries the block and `.env.example` is complete. (R23)

## Phase 1 — Quality-gate scaffolding (blocks all rebuild slices)

### S1.1 Static type-check gate
- Wire mypy or pyright in CI; bring the baseline clean or ratchet. (R10) (net-new)

### S1.2 Coverage floor
- Coverage measurement plus an ~85% floor on core packages in CI. (R9) (net-new enforcement)

### S1.3 Load-test harness
- Stand up a load-test harness with SLO placeholders for tile latency, per-field analysis time, and
  backfill throughput. Real numbers are set in S4.7. (R12, R15) (net-new)

### S1.4 Supply-chain scanning
- Dependency and secret scanning in CI. (R13) (net-new)

### S1.5 Merge policy
- Assemble all gates (ruff, pytest, types, coverage, contract-diff, scans). CI red means no merge. (R14)

## Phase 2 — Reevaluation pass (read-only)

### S2.1 Per-module triage and priority order
- Run `architect` plus `/improve-codebase-architecture` over L2 to L6 and the BFF. Classify each
  module keep-and-harden / refactor-in-place / rewrite. Produce a prioritized rebuild order,
  worst-bug-density first, as a committed doc.
- Depends on: S0.* (the safety net must exist first).
- Done when: the triage doc and ordered slice list exist. (R3, R25, R26, R27)

## Phase 3 — Rebuild slices (vertical, priority order)

### S3.1 As-of-date farm view (first integrating tracer bullet)
- L3: search and select by an arbitrary date with nearest-clear-pass resolution (per-AOI SCL),
  labeled with the true acquisition date and the day-gap; works across backfill and live.
- L4: true color and false color / NIR composites, plus the index.
- L5: render. L6: date-picker UI plus a visualization toggle.
- Resolution honesty: never fabricate a pass (invariant 4). Settle nearest-before vs either-side vs
  bracketing here.
- Depends on: S0.*, S1.*, and the spine modules S2.1 flags for this path.
- Done when: a real farm and date render correctly with every gate green. (R28, R30, R31, R32)

### S3.2 .. S3.N Rebuild slices (from the S2.1 order)
- One vertical slice per prioritized module group, each gate-green. Scale-hardening folds into the
  slice that first touches the relevant module. The concrete list is finalized by S2.1.

## Phase 4 — Scale and hardening (explicit must-dos, folded where modules are touched)

- S4.1 Partition and index the analysis / zonal-stats table for growth. (R15, R18) (net-new)
- S4.2 Celery worker and queue autoscaling; idempotent high-volume ingestion. (R18)
- S4.3 COG retention / expiry job (S-1). (R15) (net-new)
- S4.4 PostGIS read replicas and connection pooling. (R15) (net-new)
- S4.5 CDSE token-bucket and circuit-breaker quota governance in the adapter. (R18)
- S4.6 Authenticate the ingestion endpoint (DI-1). (R13) (net-new)
- S4.7 Set the real SLO numbers and run a load test proving target data throughput. (R12)

## Doc hygiene

- D1 Reconcile `PLAN.md` wording ("the only outbound contact is one HTTP POST") with the existence of
  `/api/v1/mobile/data`, so the docs are internally consistent. (acquired finding, 2026-06-10)

## Import note

This backlog is local and reversible. When the tracker target is chosen, import each `Sx.y` as one
work item or issue, preserving the dependency order above. Until then, nothing is published.
