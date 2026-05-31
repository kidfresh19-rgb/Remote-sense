# remote-sense — Delivery Plan (the "startup" operating model)

How we ship a world-class, industry-ready application from zero. Companion to `PLAN.md` (product
spec) and `CLAUDE.md` (engineering rules). This file is the org chart + the playbook.

---

## 1. The team (agent roster)

| Agent | Role | Owns | Model |
|---|---|---|---|
| `architect` | Tech lead / systems architect | Plans, ADRs, invariant guard. Read-only. | opus |
| `geospatial-engineer` | Scientific core | `rs_imagery`, `rs_analysis` | opus |
| `backend-engineer` | API + data | `rs_core`, `rs_sync`, `services/api` | sonnet |
| `pipeline-engineer` | Collection | `services/worker`, collection state | sonnet |
| `frontend-engineer` | Analyst workspace | `frontend/` | sonnet |
| `devops-engineer` | Platform | Docker, config, observability, CI | sonnet |
| `qa-engineer` | Quality gates | `tests/`, validation matrix, parity | sonnet |
| `agronomy-scientist` | Domain + interpretation | `rs_interpret`, thresholds | opus |

The main session acts as **engineering manager / orchestrator**: it sequences work, dispatches to
agents, integrates their output, and owns the merge. Spawning an agent is the expensive path, so
dispatch deliberately - one agent per well-scoped unit of work, not per keystroke.

## 2. Skills each role leverages

- **All code changes** → `/code-review` before merge; `/security-review` for auth/RBAC/secrets/
  sync. `/simplify` for quality-only cleanups. `full-output-enforcement` so nothing ships
  truncated.
- **`architect`** → the `Plan` mindset; `init` to keep `CLAUDE.md` current.
- **`geospatial-engineer`** → `WebSearch`/`WebFetch` for live band/SCL/metadata facts; `verify`
  to run and observe index output.
- **`agronomy-scientist`** → `claude-api` (prompt caching, current model IDs) for `rs_interpret`.
- **`frontend-engineer`** → the global design rulebook plus `emil-design-eng` (polish/animation),
  `design-taste-frontend` (direction), and `industrial-brutalist-ui` (data-cockpit aesthetic);
  `imagegen-frontend-web` to art-direct reference images before coding.
- **`devops-engineer`** → `update-config` for settings/permissions/hooks; `fewer-permission-
  prompts` to smooth the local loop.
- **`qa-engineer`** → `verify` (run it, observe it) and the validation-matrix discipline.

## 3. Phase ownership (who builds what, in order)

| Phase | Lead | Support | Gate to next phase |
|---|---|---|---|
| 0 Foundations | devops + architect | geospatial (port/mock) | stack up healthy; `AccessPort`+mock tested |
| 1 Ingestion & data model | backend | architect, qa | idempotent upsert; bad geometry rejected |
| 2 Analysis core | geospatial | qa, agronomy | **validation matrix green vs Browser** |
| 3 Collection pipeline | pipeline | geospatial, devops | no double-enqueue under concurrency |
| 4 Preview & live | geospatial | frontend, devops | live tile < target latency; pre-warm works |
| 4b Interpretation | agronomy | backend | grounded + review-gated; prompt caching on |
| 5 Analyst workspace | frontend | backend (BFF) | panels real-data; passes design checklist |
| 6 Outbound sync | backend | qa, security | retry/DLQ/idempotency; geometry never sent |
| 7 Auth, observability, hardening | devops + backend | qa | RBAC enforced; health dashboard; load test |

**Critical path:** Phase 0's `AccessPort` unblocks 2/3/4 against the mock before any real adapter
exists. Phase 2 must go green before Phase 3 (cheaper to fix formulas than reprocess archives).
DI-1 (onboarding contract) is the only external blocker for Phase 1 - pin the gateway team early.

## 4. Definition of done (every unit of work)

Tests written and green · `ruff` clean · invariants in `CLAUDE.md` §1 upheld · provenance
attached where results are produced · `/code-review` passed · parked decisions still flagged
`# ⚑ CONFIRM` · no secrets committed · docs/ADR updated if an invariant moved.

## 5. Quality bar ("world-class, industry-ready")

1. The science is **verified against an external reference**, not assumed (validation matrix).
2. The system is **reproducible** (Docker, pinned versions, provenance, formula versions).
3. The architecture is **reversible** (endpoints behind adapters; vendor choice is a config
   switch).
4. It is **observable** (structured logs, traces, quota/latency metrics, health dashboard).
5. It **degrades honestly** (low-confidence flags, clear-pixel fractions, no silent failures).
6. It is **secure** (RBAC, secrets in env, geometry never leaks back to the gateway).
