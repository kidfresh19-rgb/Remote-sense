# Rebuild progress

Status board for the intelligence-core rebuild. Plan: `docs/prd/0001-intelligence-core-rebuild.md`,
`docs/adr/0008-ordered-reimplementation-frozen-contract.md`, `docs/backlog/0001-intelligence-core-rebuild-backlog.md`.
Branch: `feat/imagery-agronomy-tiers-0-2`. Push target: Azure DevOps `origin`.

## Resume on another machine

1. `git pull origin feat/imagery-agronomy-tiers-0-2`
2. Read in order: PRD 0001, ADR 0008, `CONTRACT.md`, backlog 0001.
3. Continue from "Next" below.

## State (2026-06-11, second pass)

- **Triage item 5 assessed: keep both.** `rs_core/alerts.py` (pure rulebook) and
  `rs_sync/agritrack.py` (one adapter, invariant-1-shaped) need no split. Doc:
  `docs/plan/S2.1-item5-and-publish-consolidation.md`.
- **Publish consolidation DONE.** The workspace `POST /farms/{id}/publish` is the single
  publish trigger (frontend already called it; richer 404/503/typed response); the operations
  duplicate removed; RBAC tests repointed; the workspace publish endpoints gained their first
  direct tests (202 / 404 / 503). Suite is now 387 passed / 4 skipped.
- **Phase 1 quality gates DONE (S1.1, S1.2, S1.4, S1.5)** - measured first, landed green. Doc:
  `docs/plan/S1-quality-gates.md`:
  - S1.1 mypy gate, baseline fixed to **zero** (was 28 errors / 13 files); `[tool.mypy]` in
    pyproject; CI runs `mypy` after the full install.
  - S1.2 coverage floor **85%** (measured 88.73%); pytest-cov wired into both CIs.
  - S1.4 pip-audit (clean after a setuptools upgrade) + gitleaks full-history scan (clean, 75
    commits) in both CIs; checkouts switched to full depth for the history scan.
  - S1.5 merge policy: every gate in one pipeline, red = no merge; CLAUDE.md section 0 updated.
  - S1.3 (load-test harness) stays queued; SLO numbers come with S4.7.

## State (2026-06-11, first pass)

- **Phase 0 safety net built and green** (S0.1, S0.2, S0.4 done; S0.3 folded per-slice). Details in
  the 2026-06-10 state below.
- **S2.1 reevaluation triage done** (`docs/plan/S2.1-reevaluation-triage.md`): broad coverage, no
  rewrites flagged; keep-and-harden plus four refactor-in-place targets.
- **All four S2.1 refactor-in-place items DONE** - pure moves into packages, byte-verbatim slices,
  full re-export `__init__`s, zero call-site edits, baseline 385 passed / 4 skipped held on each:
  - **Item 1** `rs_core/repositories.py` (985) -> per-aggregate package. `05e193d`.
  - **Item 2** `services/worker/tasks.py` (613) -> per-wire-namespace Celery package (explicit
    `name=` everywhere, registry parity verified). `5028bfc`.
  - **Item 3** `services/api/workspace.py` (644) -> per-resource routers aggregated in `__init__`;
    OpenAPI path-set parity exact. `fe9f6ca`.
  - **Item 4** `services/api/ingestion.py` (466) -> validation / persistence / service layers
    along the test seams; frozen `POST /ingest/farm` verbatim. `7fe2c47`.
  - Method + per-item knowledge: `docs/plan/S2.1-item{1,2,3,4}-*.md`. Splitter scripts preserved
    (Temp paths named in the docs; full item-1 script embedded in its doc).
- Pre-existing branch lint debt cleared (`dafaa85`); root cause: CI only fires on `develop`/`main`.
- Green light: `ruff check` clean, `ruff format --check` clean (183 files), `pytest` 385 passed /
  4 skipped (local, DB up).

## Commit trail (2026-06-11 session, oldest first)

- `62b1e0e` docs: S2.1 triage
- `dafaa85` style: clear pre-existing ruff debt on the branch
- `05e193d` refactor: repositories split (item 1) + `a59bf74` docs
- `5028bfc` refactor: worker tasks split (item 2) + `9be9b38` docs
- `fe9f6ca` refactor: workspace BFF split (item 3) + `78cf529` docs
- `7fe2c47` refactor: ingestion split (item 4) + `897fb02` docs

## State (2026-06-10)

- Pipeline: Shape, Specify, Slice, and the first-slice Plan are done and committed.
- **Phase 0 safety net built and green:**
  - **S0.1** contract snapshot: `contract/snapshot_openapi.py`, `contract/openapi.before.json` (21
    paths), `CONTRACT.md`. DONE.
  - **S0.2** contract gates: `tests/contract/test_openapi_diff.py` (frozen routes additive-only),
    `tests/contract/test_outbound_payload.py` (push shape + no-geometry invariant). DONE.
  - **S0.4** freeze constraints in `CLAUDE.md`; `.env.example` verified complete (every `RS_*` var).
    DONE.
  - **S0.3** characterization tests: deferred to per-slice (added right before refactoring each
    weakly-covered module; the existing suite plus the contract gates already pin external behavior).
- Green light: `ruff check` clean, `ruff format --check` clean, `pytest` 379 passed / 4 skipped
  (local, DB up).

## Commit trail (2026-06-10 session, oldest first)

- `a85dc42` docs: plan the rebuild (ADR 0008, PRD 0001, backlog, S0.1 plan, CONTEXT.md entry)
- `4103c60` feat: grounding fusion (ADR 0007) [pre-existing WIP, committed + lint/format-cleaned]
- `a70634c` chore: contract snapshot + CONTRACT.md (S0.1)
- `a1328e3` test: OpenAPI diff gate (S0.2)
- `76363a5` style: ruff format the grounding files (CI format gate)
- `f14862b` test: outbound payload + no-geometry pin (S0.2)
- `1d6b761` docs: freeze constraints in CLAUDE.md (S0.4)

## Next (from the backlog, in order)

- **S3.1 remainder:** L4 false-color / NIR composite through the worker's visual-emission path
  (plan sketched in `docs/plan/S3.1-as-of-date-view.md`), and the user's call on the ⚑ CONFIRM
  resolution policy (nearer-of-either-side, tie -> before).
- Later: S1.3 load-test harness (with S4.7 SLOs), Phase 4 scale items, doc hygiene D1, and the
  parked FROZEN-CANDIDATE confirmation for `POST /ingest/farm` (external-team fact).

## State (2026-06-11, third pass)

- **S3.1 spine DONE** (`docs/plan/S3.1-as-of-date-view.md`): `GET /fields/{id}/as-of` resolves an
  arbitrary date to the nearest usable pass each side (per-AOI clear floor, current geometry
  version, never fabricated), policy = nearer side, tie -> before (⚑ CONFIRM); the workspace
  Scenes panel gained the Jump-to-date control with honest true-date + day-gap labeling and a
  one-click flip to the other side; pass rows now show % clear. Suite 390 passed / 4 skipped,
  coverage 88.82%, frontend build clean.

## Flagged decisions / defaults taken while AFK

- `POST /ingest/farm` is tagged **FROZEN-CANDIDATE**: confirm whether the live gateway calls it or
  only `/api/v1/mobile/sync` (external-team fact). If internal, it leaves the frozen set.
- Duplicate publish endpoints (`operations.py /publish/farm/{id}` and `workspace.py
  /farms/{id}/publish`, both 202) flagged for consolidation in S2.1. Not frozen.
- SLO numbers (pillar 4) set during S1.3/S4.7. As-of-date between-pass policy set during S3.1. Tracker
  target deferred, so the backlog stays local for now.
