# remote-sense — Engineering Rulebook

Internal satellite agricultural intelligence platform. The analysis backbone behind the
**AgriTrack** farmer app. These rules are binding for every contributor (human or agent) and
override default behavior. The product spec lives in `PLAN.md`; this file is *how we build*.

> Note: the user's global `~/.claude/CLAUDE.md` governs all **frontend/UI** work (design dials,
> anti-slop rules, accessibility). Those rules apply to `frontend/` automatically and are not
> repeated here. This file governs **backend, geospatial, pipeline, infra, and process**.

---

## 0. Active rebuild constraints (non-negotiable while the intelligence-core rebuild runs)

We are re-implementing the intelligence core (L2 to L6) in an ordered, test-first way behind a
frozen external boundary. Full plan: `docs/adr/0008-ordered-reimplementation-frozen-contract.md`,
`docs/prd/0001-intelligence-core-rebuild.md`, `docs/backlog/0001-intelligence-core-rebuild-backlog.md`.

- **The external contract is FROZEN.** The gateway / AgriTrack-facing routes and the outbound push
  (`POST /api/v1/mobile/sync`, `GET /api/v1/mobile/data`, `POST /ingest/farm`, and the `rs_sync`
  push payload) keep their exact routes, methods, schemas, status codes, and auth headers. The map
  is `CONTRACT.md`; the snapshot is `contract/openapi.before.json`; the gate is `tests/contract/`. A
  non-additive change to a frozen route is a regression, not a refactor. The browser BFF
  (`workspace.py`) is internal and may be improved.
- **No new credentials.** Reuse the exact `RS_`-prefixed env var names in `.env.example`. Never
  create, rename, rotate, or hard-code an auth token, key, header, or scheme.
- **Keep the architecture; rebuild the interior.** A cleaner re-implementation, not a redesign. The
  section 1 invariants and the stack (FastAPI + Celery/Redis, ports-and-adapters imagery,
  pre-computed tiles) stand. Improve interior structure where the reevaluation calls for it; moving
  an invariant still needs its own ADR.
- **Every green light is committed.** A slice is done only when `ruff check`, `ruff format --check`,
  `mypy`, and `pytest` are all clean. CI additionally enforces the 85% coverage floor, `pip-audit`,
  and a gitleaks secret scan; a red CI means no merge (S1.5). Small Conventional Commits; push each
  green slice.

---

## 1. Architecture invariants (never violate without an ADR)

1. **Ports & adapters at both external edges.** Satellite access goes through `rs_imagery`'s
   `AccessPort` only. The gateway push goes through `rs_sync`'s `GatewayPort` only. No endpoint
   URL, auth scheme, or vendor SDK call may appear outside an adapter. The active adapter is a
   config switch.
2. **Reflectance first, always.** Index math runs on surface reflectance:
   `ρ = (DN + BOA_ADD_OFFSET) / QUANTIFICATION_VALUE`, both read **per scene from metadata**.
   Never hard-code the offset or quantification value. `DN == 0` is NoData. This is the single
   highest-risk correctness rule in the system.
3. **Per-AOI cloud masking.** Cloud is assessed per field polygon via the SCL band, never from
   scene-level cloud %. Store the clear-pixel fraction with every result.
4. **Resolution honesty.** Compute each index at its coarsest band's native resolution. Never
   upsample 20 m to 10 m and label the output 10 m.
5. **Provenance on every analysis.** `(provider, provider_scene_id, processing_mode,
   formula_version, geometry_version)` travel with every stored result. Reproducibility is
   non-negotiable.
6. **Split-ownership sync.** Gateway owns identity + geometry (read-only here). remote-sense owns
   analyses (read-only there). Join on canonical farm ID. Results pushed additively. **Geometry
   is never returned.** No field is ever written by both sides.
7. **Raw bands are transient.** Download for processing, derive COG + zonal stats, then discard.
   Persisting raw scenes per field is forbidden (unbounded growth).
8. **No secrets in code or git.** All config via `pydantic-settings` from env. `.env` is
   git-ignored; `.env.example` is the contract.

## 2. Stack & language standards

- **Python 3.11+**, fully async on I/O paths (FastAPI, httpx, Celery tasks call async via the
  port). Type hints everywhere; **Pydantic v2** at every boundary.
- **Lint/format:** `ruff` (lint + format). No file ships with ruff errors.
- **Geospatial:** `rasterio`, `rio-tiler`, `shapely`, `pyproj`, `numpy`. **PyQGIS is banned** —
  it was deliberately removed. Do not reintroduce it.
- **Resilience lives in the adapter:** `httpx` + `tenacity` (retry/backoff) + `pybreaker`
  (circuit breaker) + a Redis token-bucket for quota. Centralized, never scattered.
- **Time:** store UTC, display Central Africa Time (CAT, UTC+2). Resolve user ranges in local,
  query in UTC.
- **CRS:** store source CRS; reproject to UTM (EPSG:32735 west of 30°E, EPSG:32736 east) before
  any area/distance math.

## 3. Testing discipline (definition of done)

- A change is **not done** until: it has tests, `ruff` is clean, and `pytest` is green.
- `rs_imagery` and `rs_analysis` must be testable with **zero network and zero DB** — use the
  `mock` adapter and synthetic arrays.
- **The validation matrix is sacred.** Every index is compared numerically against the Copernicus
  Browser on known scenes. Reflectance-offset handling is the #1 thing it verifies. No index
  ships without a matrix entry.
- Both real adapters (`server_compute`, `windowed_cog`) must agree on values for the same scene +
  formula. Parity is asserted in tests.

## 4. Process

- **Trunk-based with short-lived branches.** Conventional Commits (`feat:`, `fix:`, `chore:`,
  `test:`, `docs:`, `refactor:`). Small, reviewable commits.
- **Every diff gets `/code-review`** before merge; auth/RBAC/secrets/sync changes also get
  `/security-review`.
- **ADRs for invariant changes.** Anything touching section 1 requires a short ADR in
  `docs/adr/NNNN-title.md` explaining the why.
- **Parked decisions stay flagged.** The three open items (arrival notification, gateway push
  spec, frontend component lib) are built behind interfaces with sensible defaults and a
  `# ⚑ CONFIRM` marker until the user confirms.
- **Follow the working process.** Every unit of work runs the idea-to-merge pipeline in Section 6
  (full version in `docs/process/WORKFLOW.md`). Don't improvise the path.

## 5. Output quality

- No placeholder code, no `// ...`, no TODO-as-shortcut. Ship complete, runnable files.
- Comment the non-obvious **why**, never the what. Well-named code is the documentation.
- No em-dashes in user-visible copy or docs (global rule). Use a period or restructure.

## 6. Working process (agents, skills, planning)

The full pipeline is `docs/process/WORKFLOW.md`; the domain map is `CONTEXT.md`. This section is the
binding summary.

**Pipeline.** Every non-trivial unit of work runs idea to merge in order: Shape (`/grill-with-docs`
to align on intent and shared language, plus `architect` for invariants and `/prototype` when UI or
state is uncertain), Specify (`/to-prd`), Slice (`/to-issues`, vertical tracer bullets), Plan
(`architect`, read-only), Build (owning specialist + `/tdd`), Verify (`qa-engineer`, `/verify`, then
`/review` for standards and spec, `/code-review` for correctness, and `/security-review` for auth,
RBAC, secrets, sync), Land (Conventional Commit, ADR if an invariant moved). Small changes take the
fast lane (Build + Verify) but never skip tests. Backlog grooming is `/triage`; cross-session
continuity is `/handoff`. Each phase leaves a durable artifact (PRD, issue, ADR, memory, matrix row,
test, handoff doc): that artifact is the memory, since agents have none between sessions.

**Routing (intent to agent).** Delegate domain work to its owner:

| Intent | Agent |
|--------|-------|
| Satellite access, index math, raster, SCL, COG, tiles | `geospatial-engineer` |
| API, schema, PostGIS model, migrations, ingestion, `rs_sync`, weather / activity ports | `backend-engineer` |
| Celery, scheduling, collection state, backfill / forward-fill | `pipeline-engineer` |
| Crop thresholds, agronomic interpretation, `rs_interpret` | `agronomy-scientist` |
| `frontend/` (React + MapLibre workspace) | `frontend-engineer` |
| Docker, config and secrets, observability, CI, deploy | `devops-engineer` |
| Tests, validation matrix, adapter parity, edge cases | `qa-engineer` |
| Design, ADRs, invariant review, cross-cutting trade-offs (read-only) | `architect` |

**Delegation rules.** Default to working inline. Delegate only when the work is squarely one
specialist's substantial domain, is a broad fan-out search (use `Explore`), or is read-only design
or review (`architect` / `Plan`). Don't spawn for trivia or for what you already have the context to
do. Serialize dependent work, parallelize independent calls, one slice per agent.

**Context and token discipline.** Agents return the decision, a diff summary, and `file:line`
anchors, never raw file dumps. Read ranges, not whole files. Prefer the dedicated file and search
tools over shell. Plan-gate before multi-file work. Leave the durable artifact, drop the scratch.

## Agent skills

The installed Matt Pocock engineering skills (`to-prd`, `to-issues`, `triage`, `tdd`, `diagnose`,
`improve-codebase-architecture`, `zoom-out`) read this repo's tracker, label, and domain
configuration from `docs/agents/`. Re-run `/setup-matt-pocock-skills` only to switch trackers or
restart from scratch.

### Issue tracker

Local markdown: PRDs in `docs/prd/NNNN-*.md`, implementation slices in `docs/backlog/NNNN-*.md`,
open items in `TODO.md` (groomed with `/triage`). No hosted tracker. See
`docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles use their default strings (`needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `wontfix`), recorded as a `Status:` line per file. See
`docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
