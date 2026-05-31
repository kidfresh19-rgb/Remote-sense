# remote-sense — Implementation Plan

> Internal satellite agricultural intelligence platform. The analysis backbone behind the
> **AgriTrack** farmer-facing mobile app. Sentinel-2 L2A via CDSE → per-field agronomic indices
> for Zimbabwean agriculture. Expert-facing (analysts, agronomists, managers); optimized for
> density and precision, not consumer simplicity.
>
> Status: **planning** (2026-05-31). Nothing built yet. Canonical spec:
> `remote-sense-sdp-architecture.docx`. This file is the working engineering reference.

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
| Frontend map + components ⚑ | MapLibre GL over Leaflet; Radix + Tailwind + TanStack Table/Query. | Vector tiles suit live rendering; internal expert tool wants data-grid density. |
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
