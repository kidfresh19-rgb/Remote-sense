# remote-sense — Implementation Plan

> Internal satellite agricultural intelligence platform. The analysis backbone behind the
> **AgriTrack** farmer-facing mobile app. Sentinel-2 L2A via CDSE → per-field agronomic indices
> for Zimbabwean agriculture. Expert-facing (analysts, agronomists, managers); optimized for
> density and precision, not consumer simplicity.
>
> Status (2026-06-02): **built through L7 outbound sync and Phase 7 auth/observability (partial)**;
> the React + MapLibre analyst workspace (L6) and the in-container tiler (L5) are in. See §10 and
> `README.md` for the authoritative build state. Remaining work is owner-blocked (live CDSE, agronomy
> thresholds, the gateway wire format, ingestion-endpoint auth). Canonical spec:
> `remote-sense-sdp-architecture.docx`; the phase plan (§7) is the original roadmap, kept for context.

---

## 1. What we're building (compacted)

remote-sense ingests farm identity + geometry from a **gateway** (onboarding owned by another
team, read-only here), runs the full agronomic index suite per inner field automatically on a
continuous backfill + forward-fill pipeline, lets analysts explore a unified historical/live
timeline, and pushes selected results back to the gateway — keyed to the canonical farm ID,
additive, geometry never returned.

It is the intelligence layer; **AgriTrack** is the delivery layer. The two never couple in
real time — the only outbound contact is one HTTP POST. AgriTrack field-activity logs are
correlated against satellite observations as a differentiator.

Sentinel-2 is an **archive-query** system, not a commandable satellite: the app searches
existing passes, it does not task captures.

History: began as `sentinel_app` (per-request processing, PyQGIS). Deliberately moved to
lightweight rasterio + GDAL and a **pre-computed tile pipeline** (the Sentinel Hub / EOSDA /
Planet pattern) for scale and a smaller production footprint. The spectral math is identical;
the footprint is far smaller.

---

## 2. Repo layout (monorepo: Python backend + React frontend)

```
remote-sense/
├─ docker-compose.yml            # postgres+postgis, redis, minio, api, worker, beat, tiler
├─ .env.example                  # all config switches + secrets templated
├─ pyproject.toml                # workspace (uv/poetry)
├─ alembic/                      # PostGIS-aware migrations
├─ packages/
│  ├─ rs_core/                   # config, db session, domain models, RBAC, logging/otel
│  ├─ rs_imagery/                # L3: AccessPort + adapters {mock, server_compute, windowed_cog}
│  ├─ rs_analysis/               # L4: reflectance, indices, SCL masking, zonal stats
│  ├─ rs_interpret/              # Claude-API plain-language agronomic interpretation
│  └─ rs_sync/                   # L7: export builders (GeoTIFF/CSV/PDF) + GatewayPort
├─ services/
│  ├─ api/                       # FastAPI: ingestion (L1), workspace BFF (L6), publish (L7)
│  ├─ worker/                    # Celery: backfill, forward-fill, analysis, interpret, push
│  └─ tiler/                     # rio-tiler app (L5) — separate so map traffic scales alone
├─ frontend/                     # React + TS + MapLibre GL (Phase 5)
└─ tests/                        # unit + a validation-matrix suite vs Copernicus Browser
```

`rs_imagery` and `rs_analysis` must be unit-testable with **zero network, zero DB** (mock
adapter + synthetic arrays). That enforces the rule that the access-layer port is the only seam
to the satellite.

---

## 3. Logical layers

| Layer | Responsibility | Key tech |
|---|---|---|
| L1 Ingestion & Validation | Receive farm data, validate geometry, normalise CRS, dedup, store immutably | FastAPI, PostGIS, Pydantic v2 |
| L2 Collection Pipeline | Backfill historical passes + forward-fill new passes per field | Celery, Redis, STAC |
| L3 Imagery Access Layer | Broker all satellite data behind a swappable port; normalise every result | ports & adapters, httpx |
| L4 Analysis Engine | Reflectance, per-AOI SCL masking, index suite, zonal stats per field per pass | rasterio, GDAL, NumPy |
| L4b Interpretation | Plain-language agronomic read of each result, agronomist-reviewed | Claude API (prompt caching) |
| Data Substrate | Index COGs to object storage; geometry/metadata/stats/provenance to the DB | PostGIS, MinIO/S3, Redis |
| L5 Preview & Tiles | Low-res previews + live map tiles from COG overviews | rio-tiler |
| L6 Analyst Workspace | Multi-panel UI, timeline scrubber, comparison, annotation, audit, charts | React, TS, MapLibre |
| L7 Outbound Sync | Build export formats; push selected results to the gateway robustly | httpx, retry queue, idempotency keys |
| Platform Services | Auth/RBAC, observability, config — across all layers | OAuth2/JWT, structlog, OTel |

Spine: `Gateway ▸ L1 ▸ L2 ▸ L3 ⇄ CDSE ▸ L4 ▸ L4b ▸ Substrate ▸ L5 ▸ L6 ▸ L7 ▸ Gateway`

---

## 4. Imagery access layer (the keystone)

One port, called by the engine, pipeline and preview. Operations: `search · fetch · preview ·
metadata`. Every adapter returns the **same normalized shape** — reflectance-corrected data, in
a known CRS, with a clear-pixel fraction and a provenance tag — so downstream code is identical
regardless of which endpoint served it. Adapters:

- **`mock`** — synthetic data; the whole app is testable without touching a real endpoint.
- **`server_compute`** — endpoint computes the index server-side (Process-API style). Fast
  previews and live tiles. When an adapter computes server-side, the engine **stores rather
  than recomputes**.
- **`windowed_cog`** — windowed/overview COG reads; the engine owns the math. Used for the
  stored backfill pipeline, where reflectance-offset correctness must be guaranteed.

OAuth2 token management, retry/backoff, circuit-breaking and quota tracking are centralized here
(one place), not scattered. The gateway push (L7) uses the same ports-and-adapters pattern.

---

## 5. Analysis engine spec (the scientific core)

Global rules, in order, before any value is stored:
1. **Reflectance first.** `ρ = (DN + BOA_ADD_OFFSET) / QUANTIFICATION_VALUE`, both read per scene
   from metadata, never hard-coded. Baseline 04.00 (operational 2022-01-25) → offset −1000 for
   L2A; the whole backfill window is post-2022 so it always applies. Raw CDSE products are not
   harmonised — we apply it.
2. **NoData.** DN = 0 is NoData; mask it, never treat as zero reflectance.
3. **Per-AOI cloud mask** via SCL on the field polygon before any statistics.
4. **Resolution honesty.** Compute at the coarsest band's native resolution; no 20 m → 10 m
   upsampling presented as 10 m.
5. **Clip & exclude.** Clip to valid range; exclude masked pixels from stats.
6. **Lock formula + band IDs in config** so index names are unambiguous and reproducible.

Core indices (stored every usable pass), on reflectance ρ:

| Index | Formula | Bands | Res | Range |
|---|---|---|---|---|
| NDVI | (B8 − B4) / (B8 + B4) | B8,B4 | 10 m | −1..1 |
| EVI2 | 2.5(B8 − B4) / (B8 + 2.4·B4 + 1) | B8,B4 | 10 m | ≈−1..1 |
| SAVI | ((B8 − B4)/(B8 + B4 + 0.5))·1.5 | B8,B4 | 10 m | ≈−1.5..1.5 |
| NDRE | (B8 − B5) / (B8 + B5) | B8,B5 | 20 m | −1..1 |
| NDMI | (B8 − B11) / (B8 + B11) | B8,B11 | 20 m | −1..1 |

Decisions: EVI2 over classic EVI (drops the blue band → removes an L2A atmospheric-noise
source); NDRE locked to B8+B5 (never mix B7/B8A variants in a series). Situational / on-demand
only: GNDVI, NDWI-water, BSI — the exact formula must travel with the name (NDWI-water vs NDMI
collide algebraically). Emit an index COG (for the map) + zonal stats mean/min/max/std/p10/p90
(for the chart), each with a confidence indicator.

Zonal-stats row is index-agnostic: `(field_id, scene_id, pass_date, index_name, stats…,
clear_fraction, resolution, formula_version)`. Adding an index is a config change, not a schema
migration.

---

## 6. Open decisions (sensible defaults, flagged ⚑ CONFIRM)

| Item | Default behind an interface | Why |
|---|---|---|
| Arrival notification ⚑ | `ArrivalSource` port; ship DB-polling first. Webhook + LISTEN/NOTIFY drop in later. | Zero coupling to gateway readiness; resumable. Blocks Phase 1/3. |
| Gateway push contract ⚑ | `GatewayPort`, configurable URL + bearer token, versioned payload, idempotency key. | Unblocks Phase 6 build/test; wire format is the only unknown. |
| Boundary-change policy ⚑ | Geometry versioning: changed boundary → fresh backfill; prior analyses retained, tagged. | Matches DI-5; conservative + reproducible. |
| Frontend map + components (confirmed 2026-06-03) | As built: MapLibre GL, Tailwind v4, TanStack Query, Phosphor icons, Motion; bespoke dense panels (no Radix, no TanStack Table). | Vector/raster tiles suit live rendering; the bespoke panels gave the analyst cockpit tighter control than a data-grid lib. |
| CRS | Per-farm by centroid: EPSG:32735 (35S, west of 30°E) / 32736 (36S, east). Store source + working CRS. | Zimbabwe straddles two UTM zones. |
| Backfill window | 18 months (doc says 12–18), configurable. | Conservative default. |

---

## 7. Phase plan (each independently testable; gap resolutions slotted in)

- **Phase 0 — Foundations** *(critical path)*: docker-compose (Postgres+PostGIS, Redis, MinIO,
  api/worker shells); `rs_core` config + structlog/OTel; **`AccessPort` + `mock` adapter**; CDSE
  OAuth2 client with refresh; ⚑ agree onboarding data contract (DI-1).
- **Phase 1 — Ingestion & Data Model**: farm→field→analysis schema + provenance + geometry_version;
  per-scene reflectance-metadata table; validation (validity, CRS→UTM DI-2, nesting tolerance DI-3,
  no-field farms DI-4); immutable storage, idempotent upsert, versioning (DI-5).
- **Phase 2 — Analysis Core** *(highest scientific risk; build against `mock`)*: reflectance
  conversion; index suite on L2A (SA-2); per-AOI SCL masking (SA-1); range clip + masked-pixel
  exclusion (SA-3); mixed-resolution policy; zonal-stats storage (S-3); per-index colormaps;
  **validation matrix vs Copernicus Browser** (offset handling is the #1 thing to verify).
- **Phase 3 — Collection Pipeline**: Celery + Redis; backfill + forward-fill on ~5-day cadence;
  per-field state + enqueue-time dedup/lock (R-1); CDSE 429 backoff in the adapter (R-3);
  gap detection/retry; raw-band discard (S-1).
- **Phase 4 — Preview & Live**: COG-overview AOI previews; live tiles via rio-tiler; pre-warm
  active farms only (S-2); UTC store / CAT display, ranges local→UTC (S-4).
- **Phase 4b — Interpretation**: `rs_interpret` Claude-API layer — plain-language read per field/
  crop/season; agronomist review/edit before publish. Prompt caching on the static agronomic
  context.
- **Phase 5 — Analyst Workspace**: React+TS+MapLibre; multi-panel (map·chart·metadata·stats);
  unified timeline scrubber (cached history + live edge); side-by-side comparison, saved AOIs,
  annotation layer, audit history, trend charts.
- **Phase 6 — Outbound Sync**: export builders (GeoTIFF/CSV/PDF/gateway payload) + destination &
  index selector; robust push — retry/backoff, dead-letter queue, idempotency keys; additive by
  farm ID (R-2).
- **Phase 7 — Auth, Observability & Hardening**: RBAC view/annotate/run-analysis/publish (R-5);
  pipeline-health dashboard + alerting (R-4); load/scale testing.

Local data layering (ZimStat climate, ZINWA water, agro-ecological zones, AgriTrack field-log
correlation) threads through Phases 5–6 as overlay layers + correlation views.

---

## 8. Critical path

- **Phase 0's `AccessPort` is the keystone.** Phases 2/3/4 call only the port, so they build and
  test against `mock` *before* the real adapters exist — decoupling the schedule from CDSE access
  and quota.
- **Phase 2 before Phase 3.** Get the science right (validation matrix) before industrializing
  collection. Cheaper to fix formulas than reprocessed archives.
- **DI-1 (onboarding contract)** is the only external blocker for Phase 1; pin the gateway team
  early. The DB-polling default lets work proceed meanwhile.

---

## 9. Top risks

1. **Reflectance offset correctness** — the −1000 Baseline-04.00 offset silently wrecks EVI2/SAVI
   and depresses NDVI if missed. The validation matrix vs the Copernicus Browser is the safety net.
2. **Per-AOI vs scene cloud %** — filtering on scene cloud is wrong both ways; SCL-per-polygon
   masking must land in Phase 2, not be deferred.
3. **Unbounded raster growth (S-1)** — discard raw bands and set a COG retention policy in Phase 3,
   or storage explodes (backfill × fields × indices × farms × forever).
4. **CDSE throttling (R-3)** during mass backfill — backoff must live in the adapter, the one
   centralized place.
5. **Server-compute vs own-math drift** — if the `server_compute` adapter and `windowed_cog`
   adapter disagree on a value, the timeline mixes two truths. Pin both to the same locked formula
   + provenance tag, and assert parity in the validation matrix.
6. **Claude interpretation hallucination** — agronomic text must be grounded in the actual zonal
   stats + thresholds and gated behind agronomist review before any push. Never auto-publish.

---

## 10. Deferred & skipped work (tracking)

Built so far: Phase 0 (done), Phase 1 (ingestion + data model), Phase 2 (analysis core, science
verified against synthetic references), Phase 3 (collection pipeline). Landed 2026-05-31 once the
firmware blocker cleared: the analysis→DB persistence helper (D3), the collection-state table +
migration `0002` + cursor persistence (D5), concurrent first-create hardening on ingest (D8), the
Redis enqueue-lock + lock-gated collection (R-1), and the live collection itself - `run_collection`
/ `prepare_and_run` (scene-metadata + zonal-stats persistence + cursor/backfill state), the
`backfill_field` / `forward_fill_field` Celery tasks, and the daily `scan_and_enqueue` beat
scheduler (D4). Reviewed via `/code-review` + `/security-review`: one correctness fix applied (the
forward-fill geometry-version join), two items tracked as D10/D11 below. **Phase 4b
(`rs_interpret`, L4b) landed 2026-06-01** (buildable without the parked infra): index-band
thresholds (⚑ agronomy), grounding (`AnalysisOutput` → evidence), the cacheable agronomic prompt,
the never-auto-published `interpret` service, the `AnthropicInterpretClient` (prompt caching, SDK
isolated behind the `interpret` extra), the `interpretation` table + Alembic `0003` +
first-draft-wins upsert, and the `interpret_field_pass` worker + `interpret.field_pass` task.
**Phase 6 (`rs_sync`, L7) landed 2026-06-01**: the versioned, geometry-free `GatewayPayload` +
`build_payload` + deterministic idempotency keys, the CSV export, `GatewayPort` with
`RecordingGatewayPort` / `HttpGatewayPort` (bearer + Idempotency-Key header + tenacity retry), the
`sync_outbox` ledger + Alembic `0004`, and the `publish_farm` worker + `sync.publish_farm` task
(R-2 idempotent: an already-published result set is not re-pushed; a failed push dead-letters).
The PDF exporter stays parked on the PDF stack; **GeoTIFF export landed 2026-06-02**
(`rs_sync.index_geotiff`: a tiled COG with provenance embedded as GDAL tags, in-container).
**Phase 7 (auth + observability,
partial) landed 2026-06-01**: RBAC (`rs_core/rbac.py` - view/annotate/run-analysis/publish +
viewer/analyst/publisher/admin roles, permissions derived server-side), stdlib HS256 JWT
verification (alg-pinned, constant-time, `exp`-checked) + the `require(permission)` dependency
(`services/api/auth.py`), and the RBAC-gated `POST /publish/farm/{id}` (publish) +
`GET /pipeline/health` (view; coverage + dead-letter summary, R-4). **Log-based health alerting
landed 2026-06-02** (`rs_core/alerts.py` evaluates the summary; the health endpoint logs fired
alerts as structured warnings for a log/webhook notifier, R-4). Load/scale testing and
authenticating the ingestion endpoint (tied to the ⚑ DI-1 arrival contract) remain. **L5/L6
(partial) landed 2026-06-01**: S-4 UTC/CAT time handling (`rs_core/timeutil.py`), the tiler service
skeleton (`services/tiler` - pure render-params + XYZ→bbox math; the tile route 503s without the
raster stack, real render is in-container), and the RBAC'd workspace BFF read endpoints
(`services/api/workspace.py` - farms / fields-with-geometry / time-series / scenes /
interpretations + a read-only **provenance/audit** endpoint over the existing analysis tuple,
no migration). The L6 frontend is **built** in `frontend/` (Vite + TS + Tailwind v4 + MapLibre +
TanStack Query: app shell, farm/field sidebar, map with field geometry + tiler index overlay,
per-index time-series chart, pass list + shared timeline scrubber, interpretation panel, token-gate
auth). The four workspace features landed 2026-06-01: **side-by-side pass comparison** (two
synced MapLibre maps sharing one camera, via the extracted `useFieldMap` hook), **saved views**
(bookmarked AOIs, local store), **field notes** (annotation layer), and the **provenance/audit**
panel. **The shared, write-backed annotation store landed 2026-06-02**: the `annotation` table +
Alembic `0005`, the RBAC `annotate`-gated `GET`/`POST /fields/{id}/annotations` +
`DELETE /annotations/{id}`, geometry-version pinned server-side (invariant 5); the frontend writes
through it. Saved views and custom AOIs stay browser-local behind their interface (the shared store
for those is still a ⚑ CONFIRM backend decision).
The **in-container raster
render (L5) is in**: the tiler renders a colorized PNG tile from a stored index COG via rio-tiler
(route `/tiles/{index}/{geometry_version}/{field_id}/{scene_id}/{z}/{x}/{y}.png`), fed by D1 COG
emission + D7 store-and-discard; both run in-container. The Python slices each ship with no-infra
unit tests;
their DB/broker-backed tests are written and skip locally, set to run under `docker compose` / CI.
The items below remain consciously deferred. Nothing here is forgotten work, it is parked against
a dependency.

**Tier 0 of the improvement plan (real imagery) is code-complete (2026-06-03).** Both real adapters
are built and tested offline: `windowed_cog` (ADR 0002 - `CdseStacClient` STAC search, `cdse_metadata`
per-scene offset/quantification for invariant 2, `WindowedCogAdapter` with per-AOI SCL masking +
reflectance via the one `stack_to_reflectance`, R-3 read backoff, all rasterio behind a `WindowSource`
seam) and `server_compute` (ADR 0003 - CDSE Process API previews + reflectance fetch behind HTTP and
decoder seams, with the engine computing the index for both adapters so they cannot drift, risk #5).
An offline adapter-parity test (`test_adapter_parity.py`) guards that contract, and CI
(`.github/workflows/ci.yml`) now runs ruff plus the full suite (raster + DB-gated) on every push/PR,
closing the silent-skip gap. The forward roadmap (Tiers 1 to 3: weather, alerts, zoning, the
AgriTrack field-activity correlation, SAR) lives in `docs/improvement-plan.md`. What remains is
purely credential-gated: confirm the two `⚑ CONFIRM` wire details (S2 asset-key naming, the Process
API evalscripts) against the live catalogue, then the real Copernicus validation-matrix entries (D2).

**Environment dependency (resolved 2026-06-02).** UEFI virtualisation (VT-x) is enabled, WSL2 is
installed, and Docker (v29) is installed and running. `docker compose up` brings the whole stack
up: Postgres+PostGIS, Redis and MinIO (healthchecked) plus api / worker / beat / tiler behind
nginx on `:8000`. So the DB/broker-gated tests (S1) can now run for real against the live stack
and in CI, and the raster stack (`rasterio` / `rio-tiler`, the `geo` extra) runs inside the tiler
and COG-emitting worker containers, so nothing is blocked on a host C++ toolchain any more. The
remaining blockers are external, not environmental: live CDSE access, the gateway wire format,
and the agronomy thresholds.

| # | Deferred / skipped item | Phase | Status / why parked | Unblocks when |
|---|---|---|---|---|
| D1 | COG emission in the analysis path | 2 | **done 2026-06-01** — `rs_analysis/cog.py`: `index_raster` (pure) + `write_cog` (rasterio-guarded). The pipeline emits per-index COGs to the object store when a `CogStore` is wired (`run_collection(cog_store=...)`). Pure prep host-tested; encode round-trip geo-gated | runs in-container |
| D2 | Real Copernicus Browser entries in the validation matrix | 2 | parked: needs live CDSE data. The real `windowed_cog` adapter is now built (ADR 0002), so this unblocks on credentials, not code | CDSE credentials |
| D3 | `upsert_analysis` persistence helper (AnalysisOutput → `analysis` row) | 2/3 | **done 2026-05-31** — additive/idempotent upsert on `uq_analysis_identity` (refresh-in-place); unit-tested, DB idempotency/additivity test skips locally | — |
| D4 | Live Celery execution + Redis enqueue-lock (R-1 concurrency) | 3 | **done 2026-05-31** — enqueue-lock + `collect_field_locked` + live `run_collection`/`prepare_and_run` + Celery `backfill_field`/`forward_fill_field` + daily `scan_and_enqueue` beat; orchestration DB-gated-tested for CI | — |
| D5 | Per-field collection-state table + cursor persistence + migration `0002` | 3 | **done 2026-05-31** — `field_collection_state` model + migration + cursor helpers (monotonic `advance_cursor`); DB-gated helper tests skip locally | live verification on the stack |
| D6 | CDSE 429 backoff (R-3) in the data adapter | 3 | **partial 2026-06-03** — the `windowed_cog` STAC client retries 429/5xx with exponential backoff (`cdse_stac.py`, tenacity); windowed-read backoff in `RasterioWindowSource` still to add | runs against live CDSE |
| D7 | Explicit COG-then-discard step (S-1) | 3 | **done 2026-06-01** — `collect_field(emit_rasters=...)` carries the index arrays out, `run_collection` writes + stores the COG, and the raw bands stay transient (never persisted). Wired through the worker via `cog_store_from_settings` (no-op when boto3 absent) | runs in-container |
| D8 | Concurrent first-create idempotency hardening (ingest) | 1→3 | **done 2026-05-31** — `get_or_create_farm` savepoint + IntegrityError recovery; race branch unit-tested, DB test skips locally | — |
| D9 | Deactivate fields dropped from a later gateway payload | 1 | parked: open policy decision | gateway-team confirmation |
| D10 | Field-level concurrent first-create hardening | 1→3 | **done 2026-06-01** — `get_or_create_field` (savepoint + IntegrityError re-fetch on `uq_field_farm_canonical`) woven into `_upsert_field`; a lost first-create converges to `unchanged` instead of a 500. Unkeyed/derived fields insert directly (no unique key — tracked under R-1). Race branch unit-tested; real-constraint recovery DB-gated | — |
| D11 | Per-pass backfill fan-out | 3 | **done 2026-06-01** — `backfill_field` now `plan_backfill_scenes` → enqueues one `collect_pass` task per outstanding pass (per-scene lock, own transaction); an empty re-plan converges to backfill-complete. `prepare_and_run(is_backfill=True)` kept as the synchronous primitive. Planner + per-pass DB-gated-tested | live verification on the stack |
| S1 | DB/infra-gated tests that skip locally — now **19** (was 7): ingestion + get-or-create farm/field (`test_ingestion_db`), analysis upsert (`test_analysis_db`), collection-state cursor (`test_collection_state_db`), live collection + fan-out + due-selection (`test_tasks_db`) | 1→3 | no local PostGIS/Redis | Docker stack up / CI |

**Domain knobs, current status:** the SCL clear-class set (`rs_analysis/scl.py`), per-index
colormap display ranges (`colormaps.py`), and clear-fraction confidence thresholds (`engine.py`)
are now locked to documented defaults (Sen2Cor L2A convention; display-only colour stretch; tested
operational cutoffs). Still `# ⚑ CONFIRM` pending agronomy review: the interpretation index-band
thresholds + per-crop overrides (`rs_interpret/thresholds.py`). The static agronomic system prompt
(`rs_interpret/prompts.py`) is complete and grounded (it forbids prescriptive advice and restates
only supplied numbers), so it carries no marker. The thresholds carry generic defaults until an
agronomist tunes them; the interpretation layer never auto-publishes (risk #6), so a human reviews
every read.

**Recommended path to Copernicus parity (assessment 2026-06-04, owner-blocked — TODO).** Verdict:
the index values are built to match the Copernicus Browser exactly. The reflectance offset
(ρ = (DN − 1000) / 10000, read per scene) and the locked formulas match its definitions — proven
against hand-computed references plus the with/without −1000-offset check in
`test_validation_matrix.py`; `server_compute` renders via the CDSE Process API (Copernicus's own
engine, identical by construction); and `windowed_cog` is asserted to agree with it offline
(`test_adapter_parity.py`, risk #5). On *capability* it already exceeds the Browser (per-AOI SCL
masking vs scene-level cloud %, stored zonal stats + clear-fraction + confidence + full provenance,
the historical/live timeline, the agronomic interpretation layer, productivity zones, weather +
activity correlation). The one unproven thing is the **live numeric diff against the real Browser** —
purely credential-gated, not a code gap. Steps, in order:

1. **Obtain CDSE credentials** (Copernicus Data Space): OAuth2 client id/secret + S3 `eodata` keys;
   set `RS_CDSE_*` in `.env`. The keystone — unblocks 2–4. An operator registration, not engineering.
2. **Land D2:** add 2–3 real Browser scenes/AOIs as `VALIDATION_MATRIX` rows (all five indices),
   asserting both real adapters match the Browser within ~0.01 on the AOI mean. The framework already
   iterates a table, so each is one row.
3. **Live adapter parity:** run the `test_adapter_parity` check against real scenes (today it is
   offline with fakes) to confirm `windowed_cog` == `server_compute` on live data.
4. **Agronomist sign-off** on the interpretation index-band thresholds + per-crop overrides
   (`rs_interpret/thresholds.py`, still `# ⚑ CONFIRM`).
5. **Finish D6:** add windowed-read 429/transient backoff in `RasterioWindowSource` (the STAC client
   already retries; the per-read path does not yet).

Status (2026-06-04): the full suite runs **285 passed / 0 skipped** CI-equivalent in-container (all
extras + real PostGIS + Redis); frontend builds, serves, and authenticates against the live BFF; the
stack boots healthy. The app is functionally complete short of the credential-gated live diff above.
