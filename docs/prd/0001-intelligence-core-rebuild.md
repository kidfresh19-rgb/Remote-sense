# PRD 0001 — Intelligence-Core Rebuild (Ordered Re-implementation)

- Status: approved 2026-06-10 (Shape and Specify locked; entering Slice)
- Date: 2026-06-10
- Related: ADR 0008 (rebuild approach), CLAUDE.md section 1 invariants, PLAN.md, CONTEXT.md,
  docs/improvement-plan.md

## 1. Problem

The intelligence core (L2 to L6) is functionally complete (285 tests green, live CDSE verified) but
was built in an unordered way and has accumulated disorder and bugs as a result. We want it rebuilt
to an industry-grade standard: ordered, test-first, bug-free, and able to absorb a large and growing
volume of farm data, without changing how data is received from or sent to the gateway, and without
introducing new credentials.

## 2. North star, goals, non-goals

**North star:** the agronomic decision-support layer that beats EOSDA on the AgriTrack
field-activity moat. Interpretation, grounding, and alerts are the headline; index-viewing supports
them.

**Goals**

- G1. The industry-grade quality bar (the seven pillars in section 4) is met across the core.
- G2. The external gateway contract is provably unchanged (contract snapshot and diff gate green).
- G3. The system handles hundreds of thousands of farms' worth of data (ingestion, pipeline, storage,
  and database SLOs met under load).
- G4. Ship the as-of-date farm view as the first integrating feature.

**Non-goals**

- Not a redesign of what the app does.
- Not changing the gateway contract, and not adding or rotating credentials.
- Not scaling for 100k concurrent workspace users (the analyst base is small).
- Not building this session. This PRD is plan-first.

## 3. Users

- **Analyst / agronomist** (primary, few): operate the workspace, review and publish interpretations,
  answer client questions.
- **Client / farmer** (indirect, many): onboarded via the mobile app; data flows farmer to gateway to
  remote-sense and results flow back the same way; never uses remote-sense directly.

## 4. Definition of done (the quality bar)

Seven pillars, each a checkable gate. Lead with 1, 3, and 6.

1. **Test and correctness:** tests plus `ruff` plus `pytest` green per slice; the zero-network and
   zero-DB unit testability of `rs_imagery` and `rs_analysis` preserved; a validation-matrix row plus
   adapter-parity for any index touched; contract tests green; a coverage floor (about 85 percent) on
   the core packages.
2. **Type and boundary safety:** full type hints, Pydantic v2 at every edge, a static type-check
   (mypy or pyright) wired as a CI gate.
3. **Observability and resilience:** OTel spans and structured logs on every I/O path; retry,
   backoff, circuit-breaker, and quota centralized in the adapters; pipeline-health and alerts wired;
   no unhandled exception on a hot path.
4. **Performance SLOs:** measured targets for tile-serve latency, per-field analysis time, and
   backfill throughput.
5. **Security:** server-side RBAC; no secrets in code; the ingestion endpoint authenticated (closes
   the open DI-1 gap); dependency and secret scanning in CI.
6. **Clarity and provenance:** deep modules with clean seams; comments explain the why; CONTEXT.md
   current; an ADR for every invariant move; CONTRACT.md as the frozen-boundary record; CI red means
   no merge.
7. **Scale and elasticity:** stateless services; the analysis and zonal-stats table partitioned for
   growth; queues and workers autoscaled; connection pooling and read replicas on PostGIS; a COG
   retention policy; a load test proving target data throughput against the section 4 SLOs.

## 5. Frozen boundary (what cannot change)

- **External, FROZEN:** gateway ingestion, the outbound push, and `/api/v1/mobile/data`. Routes,
  methods, schemas, status codes, auth headers, and env var names stay identical. Captured in
  `contract/openapi.before.json` and `CONTRACT.md`; enforced by contract tests and a post-rebuild
  OpenAPI diff gate.
- **Internal, IMPROVABLE:** the browser BFF and all interior modules.
- **Credentials:** reuse `.env.example` var names; no new auth schemes, headers, or tokens.
- **Data minimization:** store only what the intelligence layer needs; never expose unused PII such as
  phone numbers.

## 6. Execution model

Triage each module into keep-and-harden, refactor-in-place, or rewrite. Never wholesale-rewrite
verified science. Macro-order:

- **Slice 0, safety net:** the contract snapshot plus characterization tests over current behavior.
- **Reevaluation pass** (`architect` plus `/improve-codebase-architecture`): the per-module triage and
  a prioritized order, worst-bug-density first.
- **Vertical rebuild slices:** each cuts through the layers and ends with every gate green.
- **First integrating tracer bullet:** the as-of-date farm view.

## 7. Flagship feature: as-of-date farm view

A client asks about a farm on a specific calendar date, possibly a farm-visit date that falls between
satellite passes. We show that farm's imagery and analysis for that date, in selectable
visualizations (true color RGB, false color or NIR, index colormaps), across the backfill and live.

Key rule: resolution honesty (invariant 4). We never fabricate a pass. The default is the nearest
usable (clear, per-AOI SCL) pass, labeled with its true acquisition date and the day-gap to the
requested date. Open question for slicing: nearest-before, nearest-either-side, or show both
bracketing passes.

## 8. Risks

- **Science regression during refactor** (reflectance offset, SCL, formulas). Mitigated by
  characterization tests, the validation matrix, and keep-and-harden of verified science.
- **Contract drift.** Mitigated by the snapshot and the diff gate.
- **Scale surprises.** Mitigated by explicit SLOs and a load test. CDSE quota is a hard upstream
  ceiling, governed in the adapter.
- **Scope creep from the improvable interior.** Mitigated by triage discipline and an ADR for any
  invariant move.

## 9. Slice outline (tracer bullets, to be formalized by /to-issues)

This is a guide, not the final backlog. `/to-issues` will cut and order the real slices with explicit
blocking relationships.

- **Foundation A. Safety net:** contract snapshot (`openapi.before.json`, `CONTRACT.md`, contract
  tests) plus characterization tests over current behavior.
- **Foundation B. Quality-gate scaffolding:** static type-check, coverage floor, contract-diff gate,
  and a load-test harness, each wired red-to-green in CI.
- **Reevaluation pass:** per-module triage document and prioritized order (`architect`, read-only).
- **Rebuild slices (priority order):** vertical slices, each gate-green. The as-of-date farm view is
  the first integrating slice (L3 search by date, L4 composite and visual, L5 true and false-color
  render, L6 date-picker UI). Scale-hardening (table partitioning, worker autoscaling, COG retention)
  folds into whichever slice first touches the relevant module.

## 10. Open questions

- The as-of-date between-pass policy (nearest-before vs either-side vs bracketing).
- Exact SLO numbers for tile latency, per-field analysis time, and backfill throughput (set during the
  quality-gate slice).
- The tracker target for publishing the backlog (Azure DevOps vs GitHub) before `/to-issues`.

## 11. Approved decisions (2026-06-10)

The full planning decision list (35 recommendations spanning engagement and scope, the north star, the
seven-pillar quality bar, scale strategy, the frozen boundary, credentials and data handling, the
execution model, the as-of-date feature, and process) was reviewed and **approved in full** by the
owner on 2026-06-10. ADR 0008 is the architecture record for the approach; this PRD is the product
record. Both are now locked as the basis for slicing.

Two approved items still need a concrete input before or during the build, and do not otherwise block
slicing:

- **SLO numbers (pillar 4):** approved in principle. The actual targets for tile-serve latency,
  per-field analysis time, and backfill throughput are set during the quality-gate foundation slice.
- **Tracker target:** approved to publish the backlog, but the destination (Azure DevOps Boards vs a
  GitHub repo, vs a local backlog file first) is chosen before `/to-issues` runs.

Shape and Specify are locked. The next phase is Slice (`/to-issues`), gated only on the tracker
target.

## Appendix A — Full decision list (R1 to R35)

All items below are approved as of 2026-06-10. The `[confirmed]` / `[new]` tag records whether the
item was agreed during the Shape grill or first approved in the consolidated list. `(net-new)` marks
work that does not exist in the codebase today. The sliced backlog lives in
`docs/backlog/0001-intelligence-core-rebuild-backlog.md` and references these R-numbers.

**A. Engagement, scope, model**
- R1 [confirmed] Plan-first: produce the plan and sliced backlog now, build later. No product code this session.
- R2 [confirmed] "Rebuild" = ordered re-implementation behind a frozen boundary, not a greenfield redesign.
- R3 [confirmed] Dedicated architecture-reevaluation workstream may improve the interior; boundary and §1 invariants stay fixed.
- R4 [confirmed] Run the project pipeline end to end (Shape, Specify, Slice, Plan, Build, Verify, Land) with the owning agent per layer.
- R5 [confirmed] Use Opus 4.8 for the dev work and the `rs_interpret` product layer.

**B. North star**
- R6 [confirmed] North star A: agronomic decision-support centered on the AgriTrack moat (interpretation, grounding, alerts headline).
- R7 [confirmed] Capability breadth and correctness/trust are supporting tracks under A.

**C. Quality bar (seven pillars)**
- R8 [confirmed] Adopt all seven pillars, leading with #1, #3, #6.
- R9 [new] Pillar 1: per-slice tests + ruff + pytest green; zero-network/DB testability preserved; validation-matrix + adapter-parity per index; contract tests green; ~85% coverage floor on core packages.
- R10 [new] Pillar 2: full type hints + Pydantic v2 at every edge; static type-check gate (net-new).
- R11 [new] Pillar 3: OTel + structured logs on every I/O path; centralized retry/backoff/breaker/quota; pipeline-health + alerts; no unhandled hot-path exceptions.
- R12 [new] Pillar 4: define and measure SLOs for tile latency, per-field analysis time, backfill throughput (numbers TBD).
- R13 [new] Pillar 5: server-side RBAC; no secrets in code; authenticate ingestion endpoint (DI-1, net-new); dependency + secret scanning (net-new).
- R14 [new] Pillar 6: deep modules; comment the why; CONTEXT.md current; ADR per invariant move; CONTRACT.md as the frozen-boundary record; CI red = no merge.
- R15 [new] Pillar 7: stateless services; partition the analysis table (net-new); autoscale queues/workers (net-new); pooling + read replicas (net-new); COG retention (net-new); load test vs SLOs (net-new).

**D. Scale strategy**
- R16 [confirmed] Scale is a data-volume problem (hundreds of thousands of farms' data in), not request concurrency.
- R17 [confirmed] Do not engineer for 100k concurrent UI users or farmer-facing read fleets (farmers hit the gateway).
- R18 [new] Concrete levers: table partitioning, worker + queue autoscaling, idempotent high-volume ingestion, DB indexing, COG retention, CDSE token-bucket + circuit-breaker in the adapter.

**E. Frozen boundary and contract safety**
- R19 [confirmed] Freeze only the external gateway edges (ingestion, outbound push, `/api/v1/mobile/data`): routes, methods, schemas, status codes, auth headers, env var names identical.
- R20 [confirmed] Treat the browser BFF as internal and improvable.
- R21 [confirmed] Slice 0 = contract snapshot (`openapi.before.json` + `CONTRACT.md` tagging routes + contract tests).
- R22 [confirmed] Post-rebuild OpenAPI diff gate failing only on a non-additive change to a frozen route (net-new).

**F. Credentials and data handling**
- R23 [confirmed] No new credentials: reuse exact `.env.example` var names; no new auth schemes, headers, or tokens.
- R24 [new] Data minimization: store only what the intelligence layer needs; never expose unused PII (e.g. phone numbers).

**G. Execution model and sequencing**
- R25 [confirmed] Triage every module (keep-and-harden / refactor-in-place / rewrite); never wholesale-rewrite verified science.
- R26 [confirmed] Macro-order: safety net, then reevaluation pass, then vertical rebuild slices, each gate-green.
- R27 [confirmed] Prioritize rebuild slices worst-bug-density first (order set by the reevaluation pass).
- R28 [confirmed] As-of-date farm view is the first integrating tracer bullet.
- R29 [confirmed] Refactor under characterization tests so behavior is held constant.

**H. Flagship feature (as-of-date farm view)**
- R30 [confirmed] Query a farm on any calendar date, including between passes, across backfill and live.
- R31 [confirmed] Visualizations: true color RGB, false color / NIR, per-index colormaps, selectable.
- R32 [new] Between-pass default: nearest usable (clear, per-AOI SCL) pass, labeled with true date + day-gap; never fabricate a pass. Final policy chosen at slice time.

**J. Process and next steps**
- R33 [confirmed] Keep planning artifacts local until approval (done; approved 2026-06-10).
- R34 [new] Cut the backlog after approval. Resolved: drafted locally in `docs/backlog/0001-...` (local-first chosen).
- R35 [new] Decide the tracker target before publishing. Resolved: deferred. Backlog stays local until import.
