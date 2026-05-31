# remote-sense — Engineering Rulebook

Internal satellite agricultural intelligence platform. The analysis backbone behind the
**AgriTrack** farmer app. These rules are binding for every contributor (human or agent) and
override default behavior. The product spec lives in `PLAN.md`; this file is *how we build*.

> Note: the user's global `~/.claude/CLAUDE.md` governs all **frontend/UI** work (design dials,
> anti-slop rules, accessibility). Those rules apply to `frontend/` automatically and are not
> repeated here. This file governs **backend, geospatial, pipeline, infra, and process**.

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

## 5. Output quality

- No placeholder code, no `// ...`, no TODO-as-shortcut. Ship complete, runnable files.
- Comment the non-obvious **why**, never the what. Well-named code is the documentation.
- No em-dashes in user-visible copy or docs (global rule). Use a period or restructure.
