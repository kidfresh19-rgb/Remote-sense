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

- **Every backlog slice is done** (Phases 0-4; S4.6's enforcement flip is config, not code).
  What remains is external-fact-gated or needs new infrastructure: the ⚑ S4.6 enforcement flip
  (`RS_INGEST_REQUIRE_KEY=true` once the gateway team confirms they send `X-Api-Key` on ingest
  and the `ingest.keyless_call` / `ingest.key_mismatch` logs are quiet), and the ⚑ S4.4 replica
  provisioning (set `RS_DATABASE_READ_URL` when a streaming replica exists; routing is wired).
- Owner-blocked confirms: live CDSE (D2) and agronomist sign-off. RESOLVED 2026-06-13: the
  as-of-date policy (nearer side, tie -> before) and the COG retention default (None = match
  backfill depth) are both confirmed as shipped; markers cleared from code and tests.

## State (2026-06-13)

- **Two ⚑ confirms RESOLVED by the owner** (markers cleared in `services/api/workspace/fields.py`,
  `tests/test_workspace_db.py`, `packages/rs_core/config.py`, plan docs): the S3.1 as-of-date
  resolution policy stays nearer-of-either-side with ties going to before, and the S4.3 COG
  retention default stays None = match `backfill_months`. S4.6 enforcement stays dark
  (`RS_INGEST_REQUIRE_KEY=false`) until the gateway team confirms the key and the ingest logs are
  quiet. **Owner approved landing the branch:** PR from `feat/imagery-agronomy-tiers-0-2` into
  `develop` (52 commits ahead; PR-1 "api connect" content already byte-identical on the branch;
  `git merge-tree` dry run is conflict-free). Post-sync verification on this machine: ruff clean,
  mypy clean (118 files), 450 passed / 11 skipped, coverage 86.75% over the 85% floor.

## State (2026-06-12, seventh pass)

- **FROZEN-CANDIDATE resolved + S4.6 ingest auth DONE** (DI-1, R13). Owner confirmed the live
  gateway still calls `POST /ingest/farm`, so it is EXTERNAL-FROZEN permanently (CONTRACT.md
  updated). A required header would be non-additive on a frozen route, so auth shipped as the
  two-step migration: the route now verifies the same shared `X-Api-Key` the gateway already
  presents to `/api/v1/mobile/*` (no new credential/header/scheme, per the rebuild constraint),
  read from the raw request so the frozen OpenAPI stays byte-identical. Enforcement sits behind
  ⚑ `RS_INGEST_REQUIRE_KEY` (ships false): keyless calls behave exactly as before and log
  `ingest.keyless_call`; a key that would fail logs `ingest.key_mismatch` so the flip is
  observable-safe. When true: 401 on missing/wrong key, fail-closed 500 when unconfigured,
  mirroring the mobile routes. Both modes pinned zero-DB in
  `tests/contract/test_ingest_auth.py` (6 tests).

## State (2026-06-12, sixth pass)

- **S4.2 + S4.4 runnable core DONE** (R15/R18); doc: `docs/plan/S4.2-S4.4-scaling.md`. S4.2:
  the compose worker autoscales (`--autoscale=${RS_WORKER_AUTOSCALE:-4,1}`, verified in the
  boot banner), `worker_prefetch_multiplier=1` pairs with acks_late so long collection tasks
  dispatch fairly and redeliver cleanly, and `--scale worker=N` is documented safe (enqueue-time
  dedup/locking; migrations in the one-shot migrate service). Queue separation considered and
  skipped at current volumes. S4.4: pool tuning is env-driven (`RS_DB_POOL_*`, pure
  `engine_kwargs`), and reads are replica-ready behind ⚑ `RS_DATABASE_READ_URL` (empty =
  `get_read_engine()` IS the primary engine, zero cost): analytical reads (field timeseries /
  scenes / as-of / audit, farm + field lists, the frozen mobile data pull) ride
  `ReadSessionDep`; read-after-write surfaces (annotations, review queue, publish status,
  collect trigger) stay on the primary. The contract gate earned its keep twice: a docstring
  edit on `/api/v1/mobile/data` failed the OpenAPI diff (docstrings ARE the frozen description;
  reverted byte-identical, note moved to a comment), and the frozen-route auth tests now
  override `get_read_session` alongside `get_session`. Tests: +6 hermetic scaling-config tests;
  suite 449 passed / 4 skipped; ruff + mypy clean; live probes green (autoscale banner,
  /farms, timeseries through the read path). **Phase 4 is closed** except the S4.6 / replica
  items above.

## State (2026-06-12, fifth pass)

- **S4.7 real SLO numbers + load-test run DONE** (R12/R15/R16), measured against the dev stack
  on real data (858 live-CDSE passes); full record + repeatable commands:
  `docs/plan/S4.7-slo-numbers.md`. Thresholds (≈2x measured latency, ≈half measured
  throughput) now live in `services/loadtest/scenarios.py`, no longer placeholders: tile p95
  <= 400 ms (measured 177.7), p99 <= 800 ms (181.5); farm ingest >= 40 rps (89.5), p95 <= 500 ms
  (213.5); backfill enqueue >= 100 rps (251.1, worker stopped + queue purged so nothing reached
  CDSE); per-field analysis p95 <= 60 s (derived from pipeline history + the 4 rps quota math;
  the HTTP run is staging-only because each trigger spends real quota). The R16 data-in proof:
  at the measured ingest rate, 250k farm payloads re-ingest in ~47 minutes via the idempotent
  `unchanged` path (verified zero DB mutation). Pipeline reality from history: full 18-month
  field backfills ran 12-57 min (1.7-10.2 passes/min); the 4 rps CDSE budget caps the pipeline
  near 24 passes/min, above every measured rate, so governance does not bind at current
  concurrency. Harness gained `--header` + `--json-file` + the `ingest` SLO set. Suite 443
  passed / 4 skipped; ruff + mypy clean. With S4.1 and S4.5 done this closes every
  runnable-here Phase 4 item; S4.2/S4.4 are deployment-shaped, S4.6 owner-blocked.

## State (2026-06-12, fourth pass)

- **S4.1 analysis partitioning DONE** (R15/R18): `analysis` is RANGE-partitioned by month on
  `pass_date` (PG16). The primary key widens to (id, pass_date) and `uq_analysis_identity`
  gains `pass_date` as its trailing column; scientific identity stays the 5-tuple because
  pass_date is functionally dependent on the scene's immutable sensing date. Indexes
  consolidated: the four single-column indexes became one composite
  (field_id, index_name, pass_date) - every read path leads with field_id or bounds pass_date;
  scene_id keeps its FK but loses its index (no reverse read path; scenes are never deleted).
  Migration 0008 rebuilds in place (rename aside -> partitioned successor -> copy -> drop),
  seeding monthly partitions over [oldest stored pass, today+3mo] plus an `analysis_default`
  catch-all; rehearsed on a scratch DB with 2019/2025/2026 rows: upgrade routes correctly,
  downgrade restores the flat original (names included), re-upgrade clean. New
  `services/worker/partitions.py`: pure month planner (one slack month behind the backfill
  horizon to a 3-month lookahead, reusing `backfill_window` so the depths never drift) +
  idempotent `ensure_analysis_partitions` (per-month savepoints; a month DEFAULT already holds
  is reported skipped, never fatal); weekly beat Sun 02:30 UTC, ahead of the prune. `create_all`
  environments (the DB-gated tests) get the DEFAULT partition from an after_create hook on the
  model. Two casualties handled: `(xmax = 0)` is illegal in RETURNING through a partitioned
  parent, so the upsert's created-vs-refreshed signal is now `(created_at = now())`
  (created_at is insert-only); retention's `session.get` takes the composite (id, pass_date).
  Doc: `docs/plan/S4.1-partition-analysis.md`. Tests: 8 pure planner + 4 DB-gated partition
  tests; suite 441 passed / 4 skipped; ruff + mypy clean.
- **⚑ CDSE rate limit RESOLVED** (the S4.5 remainder): a CDSE general account allows 300
  Process-API requests/min - the binding limit for the one bucket shared by STAC, Process and
  windowed reads. Production value 4 rps / burst 10 (80% of quota; no 60-second window can
  exceed 250 requests), set live in `.env` and shipped as the `.env.example` default; the code
  default stays None because the rate belongs to the deployed account.
- Hermeticity fix found by the live rate limit: the S4.5 settings-factory tests built
  `Settings()` against the developer's real `.env`, so a configured rate limit flipped their
  outcomes; they now pass `_env_file=None`.

## State (2026-06-12, third pass)

- **S4.5 CDSE quota governance DONE** (R-3/R18; the reactive 429/read backoff from D6 was already
  complete). New `packages/rs_imagery/resilience.py`: a cross-worker Redis **token bucket** (one
  `cdse:quota` budget shared by STAC search, Process API renders, and windowed/metadata reads;
  pure `refill_and_consume` reference math + an atomic Lua mirror, pinned by a Redis-gated parity
  test; fails OPEN when Redis is down) and a per-process **circuit breaker** (stdlib state
  machine, CLOSED/OPEN/HALF_OPEN on *final* failures, refuses fast for the cool-off; deliberately
  not pybreaker - sync-oriented, optional extra not installed on workers, and zero-infra
  testability per §3). Wired into `CdseStacClient`, `ProcessClient`, and `RasterioWindowSource`;
  config `cdse_rate_limit_rps` (⚑ CONFIRM: None = off until the real account quota is known) +
  `cdse_rate_limit_burst`. Also fixed a latent boot-breaker: `Settings` now sets
  `env_ignore_empty`, so the empty values `.env.example` documents (e.g.
  `RS_COG_RETENTION_MONTHS=`) fall back to defaults instead of crashing the numeric-optional
  fields. Tests: 17 no-infra (math, breaker, fakes mirroring the Lua, STAC wiring incl.
  breaker-open fast-refusal) + 2 Redis-gated (Lua parity, real round-trip), all green; ruff +
  mypy clean.

## State (2026-06-12, second pass)

- **D1 doc hygiene DONE**: PLAN.md's "the only outbound contact is one HTTP POST" reconciled with
  the `/api/v1/mobile/data` pull: same geometry-free `GatewayPayload` on both paths, per
  `CONTRACT.md`.
- **S4.3 COG retention DONE** (the policy half of risk S-1; D7 did raw-band discard). New
  `services/worker/retention.py`: pure `select_prunable` decision (mirrors planning.py) over two
  prongs - COGs at a stale geometry version (unreachable since the tiler route carries the
  current one) and passes older than the retention horizon; `prune_cogs` orchestrator deletes
  from the store and NULLs `analysis.cog_uri` (idempotent: S3 delete of an absent key succeeds,
  re-runs converge; stats/provenance rows never touched). `CogStore` gained `delete`;
  `maintenance.prune_cogs` Celery task + weekly beat (Sun 03:00 UTC); config
  `cog_retention_months` (⚑ CONFIRM: None = match `backfill_months`, so previews exist exactly
  for the advertised history depth). Tests: pure policy (7) + DB-gated end-to-end incl.
  idempotency (2), all green against real PostGIS.

## State (2026-06-11, third pass)

- **S3.1 spine DONE** (`docs/plan/S3.1-as-of-date-view.md`): `GET /fields/{id}/as-of` resolves an
  arbitrary date to the nearest usable pass each side (per-AOI clear floor, current geometry
  version, never fabricated), policy = nearer side, tie -> before (⚑ CONFIRM); the workspace
  Scenes panel gained the Jump-to-date control with honest true-date + day-gap labeling and a
  one-click flip to the other side; pass rows now show % clear. Suite 390 passed / 4 skipped,
  coverage 88.82%, frontend build clean.

## State (2026-06-12)

- **S1.3 load-test harness DONE** (the last queued Phase 1 item), so Phase 1 quality-gate
  scaffolding is now complete (S1.1-S1.5). New `services/loadtest/` package: a pure
  latency/throughput summary (`stats.py`, nearest-rank percentiles), SLO targets + pass/fail
  evaluation (`slo.py`), an async concurrency-bounded runner driving an injected operation
  (`runner.py`), the three placeholder SLO scenarios (tile latency, per-field analysis time,
  backfill throughput) with httpx op factories (`scenarios.py`), and a `python -m services.loadtest`
  CLI that exits non-zero on an SLO breach (`__main__.py`). SLO numbers are ⚑ CONFIRM placeholders
  until S4.7. Unit-tested with synthetic timings + httpx MockTransport, zero network/DB:
  `tests/test_loadtest.py` (10 tests). Gates green: ruff clean, mypy clean (114 files), 10/10 new
  tests pass. Also: the FCC commit `2cc5626` closed S3.1's L4 false-color remainder, leaving only
  the ⚑ resolution-policy confirm.

## Flagged decisions / defaults taken while AFK

- ~~`POST /ingest/farm` is tagged **FROZEN-CANDIDATE**~~ RESOLVED 2026-06-12: the owner confirmed
  the live gateway still calls it -> EXTERNAL-FROZEN permanently; S4.6 auth shipped as the
  optional-then-enforced migration (see the seventh-pass state above).
- Duplicate publish endpoints (`operations.py /publish/farm/{id}` and `workspace.py
  /farms/{id}/publish`, both 202) flagged for consolidation in S2.1. Not frozen.
- SLO numbers (pillar 4) set during S1.3/S4.7. As-of-date between-pass policy set during S3.1. Tracker
  target deferred, so the backlog stays local for now.
