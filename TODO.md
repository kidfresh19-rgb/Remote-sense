# remote-sense — Actionable Todo List (Read Tab Insights)

This todo list tracks the specific tasks required to implement the advanced agronomic insights and multi-source data fusion in the **Read** tab.

---

## 📋 Actionable Tasks

### 1. Grounding Engine (Backend / `rs_interpret`)
- [x] **Data Aggregator**: Create a helper function in `rs_interpret/grounding.py` that:
  - Reads the pass's target `Analysis` row.
  - Fetches the preceding 14-day weather metrics (GDD accumulation, total precipitation) via `WeatherPort`.
  - Fetches the preceding 30-day activity logs (planting, fertilisation, spray, irrigation) via `ActivityLogPort`.
- [x] **Grounding Evidence Schema**: Enrich the `Evidence` dataclass to hold the weather and activity log telemetry.
- [x] **Unit Tests**: Add unit tests in `tests/test_interpret_grounding.py` mocking the database, weather series, and activity log inputs.

### 2. API & Database Integration (Backend / `services/api`)
- [x] **Database Schema Expansion**: Add columns (or a JSONB payload) to the `interpretation` model to persist the grounding context (GDD, rainfall, and correlated activities) alongside the generated narrative.
- [x] **BFF Response Models**: Update `InterpretationOut` in `services/api/workspace.py` to return the enriched grounding context.
- [x] **DB-Backed Tests**: Update the API tests to verify that the generated readings successfully query and return these fields.

### 3. Workspace Scrubber & Charts (Frontend / `frontend`)
- [x] **Dual-Axis Chart Overlay**: Update the index time-series chart component (using Recharts) to support:
  - Dual-axis display showing cumulative rainfall (bars) and GDD (line).
  - Vertical annotation markers representing the date and type of AgriTrack activities.
- [x] **Read Panel Layout**: Update the **Read** tab panel to display key grounding numbers (GDD, rain, events) in a header alongside the Claude-generated narrative.

### 4. Prompt Context Refinement (Backend / `rs_interpret`)
- [x] **Context Guidelines**: Update `SYSTEM_CONTEXT` in `packages/rs_interpret/prompts.py` to instruct the model on how to evaluate index trends (e.g. ndvi/ndre) relative to temperature/GDD anomalies, water deficit, and recent farm activities.
- [x] **Prompt Versioning**: Bump `PROMPT_VERSION` to `interp/v2` to track the change.

---

## 📌 PRD 0002 — Farm Comparison Groups (entering Slice)

Spec: `docs/prd/0002-farm-comparison-groups.md` (Status: `ready-for-agent`). Sliced into
`docs/backlog/0002` through `0013` via `/to-issues` (dependency order; 0007 and 0009 are HITL).
Three open items to resolve before merge, each embedded in its gating slice file:
- [ ] **Size buckets**: agronomy-scientist review required (Zimbabwe typology; the placeholder
  0 to 10 / 10 to 50 / 50+ ha must not ship as-is). Gates the peer-cohort slice.
- [ ] **Neighbourhood params**: confirm default K (about 20) or radius (about 10 km), both
  configurable. Gates the neighbourhood slice.
- [x] **RBAC perms** (resolved 2026-06-19): role mappings confirmed and implemented in
  `packages/rs_core/rbac.py` - `upload_region_boundary` admin-only; `create_region_cluster`,
  `create_cohort`, `manage_cohort` analyst; `view_group` at viewer level. Engineering sign-off
  (owner/engineer); no agronomy dependency.

---

## 📌 PRD 0003 — Ward Watch (foundation built; remaining work gated)

Spec: `docs/prd/0003-ward-watch.md`. The pure + model foundation is built on
`feat/ward-watch-movement-lens` (0029 model + migration, 0032 pure strata, 0033 movement lens, 0034
fallback ladder, 0035 phenology verification, plus 0036/0039 pure cores) and 0028 proxy-AOI on
`feat/ward-watch-proxy-aoi`. The items below are DEFERRED pending an external/owner decision - skip
for now, revisit when the decision lands:
- [ ] **0027 — gateway inbound data contract** (PRD §12): how households / declared crop mix /
  planting window arrive from the gateway. Invariant-6-adjacent and ADR-worthy; owner decision.
  Blocks 0031 (household ingestion), which in turn blocks the live-membership half of 0032 and 0037.
- [ ] **0038 — diagnosis schema** (PRD §12, `needs-info`): the controlled-vocabulary diagnosis form
  is the ground-truth flywheel's data contract; settle the vocab before building.
- [ ] **0041 — Ward Watch RBAC roles** (PRD §12, `needs-info`): ward/district/province/ministry role
  hierarchy over `rs_core/rbac.py`; settle once for PRD 0002 + 0003.

Frontend surfaces (0030 enrollment client, 0040 dashboards) await their backend plus the product
surface call.
