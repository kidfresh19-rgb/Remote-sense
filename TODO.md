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
- [ ] **RBAC perms**: confirm role mappings for `upload_region_boundary`, `create_cohort` /
  `manage_cohort`, `view_group`. Gates the group-view, upload, and cohort slices.
