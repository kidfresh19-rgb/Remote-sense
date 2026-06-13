# Phase 1 quality gates: S1.1 type gate, S1.2 coverage floor, S1.4 scans, S1.5 merge policy

Executed 2026-06-11 on `feat/imagery-agronomy-tiers-0-2`. Measurement-first: every gate was
measured locally before being wired, so each lands green rather than red. S1.3 (load-test
harness) stays queued separately; its SLO numbers come with S4.7.

## S1.1 static type gate: mypy, baseline brought to zero

- **Measured baseline: 28 errors in 13 files** (108 source files), small enough to fix outright
  rather than ratchet. All fixes are typing-level with zero behavior change, guarded by the
  full suite (387 passed before and after).
- Config: `[tool.mypy]` in `pyproject.toml` - `files = ["packages", "services"]`,
  `python_version 3.11`, `ignore_missing_imports` (covers the untyped geo/celery libs). CI runs
  plain `mypy` **after the full editable install**, so dependency types match a developer
  machine; running it in a deps-less lint job would silently check less (missing imports become
  `Any`). Tests are not yet in scope: widening `files` to `tests/` is the next ratchet step.
- The root causes worth remembering:
  - `models.py` declared geometry columns as `Mapped[object]`; the truthful type is
    `Mapped[WKBElement]` (geoalchemy2 ORM loads return WKBElement), which cleared four
    `to_shape` errors across three services at once. Annotations on `mapped_column`s with
    explicit column types are typing-only - SQLAlchemy does not re-derive the column from them.
  - `Interpretation.recent_activities` was annotated `Mapped[dict | None]` but stores a list;
    now `Mapped[list[dict[str, Any]] | None]`.
  - `LockClient` Protocol methods were `async def`, but redis.asyncio types its commands as
    sync methods returning `Awaitable[...]`; a Protocol must match that shape for `Redis` to
    pass structurally. Declared as `def ... -> Awaitable[...]`; an `async def` fake still
    matches (a coroutine is an Awaitable).
  - `analysis_upsert_kwargs` returned `dict[str, object]`, which cannot type-check `**`
    unpacking; it now returns an `AnalysisUpsertKwargs` TypedDict mirroring the
    `upsert_analysis` signature (runtime identical, it is still a dict).
  - The farm-analytics functions returned bare dict literals against TypedDict return types;
    they now construct the TypedDicts explicitly, which also surfaced one honest type
    correction (`FarmAnalyticsSummary.farm_name` is nullable). One narrowing quirk: an
    annotated `dict | None` variable assigned inside a loop body does not stay narrowed for
    indexed assignment; restructuring to a dict comprehension both fixed it and read better.

## S1.2 coverage floor: 85%, measured 88.73%

- Measured per-package (line coverage): rs_activity 99.1, rs_sync 96.5, rs_analysis 94.0,
  rs_weather 94.0, rs_core 91.3, rs_interpret 91.2, services/api 89.8, services/tiler 84.7,
  rs_imagery 83.0, **services/worker 77.9** (the lift target; the un-covered paths are mostly
  the broker-bound task runners). Total **88.73%**.
- Gate: `pytest tests -q --cov=packages --cov=services --cov-fail-under=85` in both CIs. The
  floor is a ratchet: raise it as the worker/imagery numbers climb. `pytest-cov` added to the
  dev extras; `.coverage` / `coverage.json` git-ignored.

## S1.4 supply-chain + secret scanning

- **pip-audit**: measured first - 5 findings, all the venv's bundled `setuptools 65.5.0`;
  upgraded locally and the scan is clean. Both CIs now `pip install -U pip setuptools` before
  the editable install (the audit checks the environment, so the runner's bundled setuptools
  must be current) and run `pip-audit --skip-editable` after it.
- **gitleaks**: measured first - full history (75 commits) scanned via the docker image, **no
  leaks found**, so the gate is red-on-finding from day one. Both CIs run the same
  `zricethezav/gitleaks:latest detect` command; checkouts switched to full depth
  (`fetch-depth: 0` / `fetchDepth: 0`) because both platforms default to shallow clones and a
  shallow scan would silently cover only the tip.

## S1.5 merge policy

One pipeline, every gate, red means no merge: ruff check, ruff format, mypy, pytest with the
coverage floor, pip-audit, gitleaks. Wired identically in `.github/workflows/ci.yml` and
`azure-pipelines.yml` (the Azure one is what actually runs; origin is dev.azure.com, and PR
validation rides the Build Validation branch policy noted in its header). `CLAUDE.md` section 0
now lists `mypy` in the per-slice definition of done and names the CI-side gates.

Remember the trigger gap from the S2.1 work: CI fires only on pushes/PRs to `develop`/`main`,
so feature branches must run the gates locally per slice; nothing has changed there.

## Verification

- `mypy`: Success, no issues in 108 source files.
- `ruff check .` + `ruff format --check .`: clean.
- Exact CI command `pytest tests -q --cov=packages --cov=services --cov-fail-under=85`:
  **387 passed, 4 skipped, total coverage 88.73%, floor reached.**
- `pip-audit --skip-editable`: no known vulnerabilities (post setuptools upgrade).
- gitleaks full-history scan: no leaks found.
