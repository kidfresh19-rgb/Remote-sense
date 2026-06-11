# Rebuild progress

Status board for the intelligence-core rebuild. Plan: `docs/prd/0001-intelligence-core-rebuild.md`,
`docs/adr/0008-ordered-reimplementation-frozen-contract.md`, `docs/backlog/0001-intelligence-core-rebuild-backlog.md`.
Branch: `feat/imagery-agronomy-tiers-0-2`. Push target: Azure DevOps `origin`.

## Resume on another machine

1. `git pull origin feat/imagery-agronomy-tiers-0-2`
2. Read in order: PRD 0001, ADR 0008, `CONTRACT.md`, backlog 0001.
3. Continue from "Next" below.

## State (2026-06-11)

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

- **Triage item 5 (assess-only):** read `rs_core/alerts.py` (327) and `rs_sync/agritrack.py` (270);
  split only if reading shows real tangling.
- **Publish-endpoint consolidation** (behavior slice, prepared by item 3): `workspace/publish.py
  POST /farms/{id}/publish` vs `operations.py POST /publish/farm/{id}`, both 202, neither frozen.
  Decide the surviving URL (frontend calls the workspace one) and fold the other.
- **Phase 1 quality gates:** static type-check (S1.1), coverage floor (S1.2), supply-chain scanning
  (S1.4), merge policy (S1.5). CI (`.github/workflows/ci.yml`) already runs `pytest tests` plus
  `ruff check .` plus `ruff format --check .` on PR to `develop`/`main`; these gates extend it.
- **Then the vertical rebuild slices**, starting with the as-of-date farm view (S3.1).

## Flagged decisions / defaults taken while AFK

- `POST /ingest/farm` is tagged **FROZEN-CANDIDATE**: confirm whether the live gateway calls it or
  only `/api/v1/mobile/sync` (external-team fact). If internal, it leaves the frozen set.
- Duplicate publish endpoints (`operations.py /publish/farm/{id}` and `workspace.py
  /farms/{id}/publish`, both 202) flagged for consolidation in S2.1. Not frozen.
- SLO numbers (pillar 4) set during S1.3/S4.7. As-of-date between-pass policy set during S3.1. Tracker
  target deferred, so the backlog stays local for now.
