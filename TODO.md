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

## 📌 PRD 0003 — Ward Watch (backend foundation + ingestion + cohort assembly built; frontend next)

Spec: `docs/prd/0003-ward-watch.md` (on the `docs/ward-watch` branch). The full backend foundation is
built and pushed on `feat/ward-watch-movement-lens`:
- **Pure / model cores:** 0029 household/plot/crop-mix model + migration, 0032 cohort-key + planting
  strata, 0033 movement lens, 0034 fallback ladder, 0035 phenology verification, 0036 triage ranking,
  0039 food-security rollups.
- **Adapters / infra:** 0028 proxy-AOI primitive (`rs_core/proxy_aoi.py`, which supersedes the
  standalone `feat/ward-watch-proxy-aoi` branch, now deletable), 0027 ward administrative boundary
  layer (`seed_ward.py` + region-repo extensions), 0041 RBAC roles + permissions (`rs_core/rbac.py`,
  behind ⚑ CONFIRM mappings), and the 0036/0039 read endpoints (`/ward-watch/triage`,
  `/ward-watch/rollups`).
- **Gateway inbound contract:** 0026 candidate gateway inbound declarations contract
  (`gw-inbound/v1`, `packages/rs_sync/inbound.py`) + the read-only
  `GatewayPort.fetch_household_declarations` (synthetic on the recording sink, real read-only GET on
  the AgriTrack adapter), tolerant behind the port. Recorded in `CONTRACT.md` (ADDITIVE INBOUND
  section) + `contract/fixtures/gateway_inbound_declarations.example.json`. Field names + GET path are
  ⚑ CONFIRM pending the gateway team (PRD §12.1).
- **Per-household ingestion:** 0031 the per-plot `PlotAnalysis` store (provenance + clear_fraction +
  the §4 `low_pixel_quality` flag) written by a worker task that reuses the existing AOI engine; the
  0026 declarations reconcile (`canonical_household_id` + `dominant_crop` + `planting_window`); and the
  centroid ward + NR placement 0027 deferred (`assign_households_by_centroid` + `Household.dominant_nr`).
  Migration 0012. Operates on existing geometry-bearing Plot rows; reconcile is geometry-free.

Remaining work:
- [x] **0032 live cohort assembly + movement lens** (built 2026-06-30): `DbHouseholdAssessor` in
  `ward_watch.py` now assembles each household's plots into peer cohorts (`rs_core/cohort_assembly.py`
  pure + `rs_core/repositories/ward_cohorts.py` DB), climbing the fallback ladder and running the
  movement lens over the stored per-plot series, so the triage queue / rollups are non-empty. Member
  is the plot, household = its most severe plot. Live compute behind the unchanged materialization
  seam (no cohort table). District/province carry an `"unassigned"` placeholder (PRD §12.6 not yet
  sourced) and the district ladder rung is skipped, never fabricated. Unblocks 0037 (visit package).
- [ ] **0038 diagnosis schema** (PRD §12.9, `needs-info`): the controlled-vocabulary field-diagnosis
  form is the flywheel's data contract. Settle the vocab before building.
- [ ] **0041 owner sign-off** (PRD §12.7): confirm the role-to-permission mapping and wire server-side
  ward-scoping into the triage endpoint (the ⚑ CONFIRM in `ward_watch.py`). Settle once for PRD 0002
  and 0003.
- [ ] **Tuning + procurement** (PRD §12.2/3/5/6): `N_min` plus the fallback thresholds, minimum usable
  pixel count plus erosion buffer, the planting-window capture mechanism, and authoritative
  ward-boundary data procurement.

Frontend surfaces (0030 enrollment client, 0040 dashboards) now have a real backend wire contract to
build against. They await the product-surface call.
