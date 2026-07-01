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

## 📌 PRD 0004 — As-of date global historical anchor + individual-pass visualization

Spec: `docs/prd/0004-as-of-date-historical-anchor.md` (Status: `ready-for-agent`, ADR 0013 accepted).
Original scope (stories 1-20, the global `?asOf=` anchor across dashboard + workspace surfaces) is
specced but **not yet sliced into backlog items** - its own text schedules the implementation branch
for after `feat/ward-watch-movement-lens` merges. Not a "next tracer" to pick up on this branch.

- [ ] **Global as-of anchor** (stories 1-20): not yet sliced. Slice via `/to-issues` once the Ward
  Watch branch merges, per the PRD's own rollout note.

**Extended scope: individual-pass visualization** (stories 21-25, folded in 2026-07-01) sliced into
five independent backlog items - none block each other, any agent can grab any one:
- [ ] **0042** index-aware pass thumbnails (frontend-only; `SceneList` thumbnails follow the active
  index instead of hardcoded RGB).
- [ ] **0043** timelapse playback control (frontend-only; auto-advance `passDate` on an interval).
- [ ] **0044** contact-sheet / small-multiples map view (frontend-only; grid of per-pass thumbnails).
- [ ] **0045** pass-to-pass difference layer (backend + frontend; decided 2026-07-01: no implicit
  default pairing, extends `SceneCompare`'s explicit A/B; symmetric diverging colormap centered on
  zero).
- [ ] **0046** cloud-mask / clear-pixel honesty overlay (backend + frontend; decided 2026-07-01:
  semi-transparent hatch, reuses the existing per-AOI SCL mask). Recommended before 0045.
