# remote-sense

Internal satellite agricultural intelligence platform. Turns Sentinel-2 L2A imagery (via the
Copernicus Data Space Ecosystem) into per-field agronomic indices for Zimbabwean agriculture. It
is the analysis backbone behind the **AgriTrack** farmer-facing mobile app; the two work through
a gateway. Expert-facing tool: optimized for analytical density and precision, not consumer
simplicity.

> Status (2026-06-01): the backend spine is built through interpretation. Ingestion + the PostGIS
> data model (L1), the collection pipeline with live Celery backfill/forward-fill + a daily scan
> beat (L2), the imagery access port + `mock` adapter (L3), the analysis core (L4), and the
> plain-language interpretation layer (L4b), and outbound sync (L7) are all in and tested.
> Preview/tiles (L5, parked on the raster stack) and the React analyst workspace (L6) follow per
> `PLAN.md`.

## Build status

| Layer | What | State |
|---|---|---|
| L1 Ingestion & validation | `POST /ingest/farm`, PostGIS farm/field/analysis, geometry versioning, idempotent + concurrent-safe upsert | done |
| L2 Collection pipeline | planning kernel, Redis-locked collection (R-1), live Celery backfill/forward-fill, daily scan beat, per-field cursor | done |
| L3 Imagery access | `AccessPort` + `mock` adapter (the active adapter is a config switch); `server_compute` / `windowed_cog` parked on the raster stack | mock done |
| L4 Analysis core | reflectance (per-scene −1000 offset), per-AOI SCL masking, NDVI/EVI2/SAVI/NDRE/NDMI, zonal stats, validation matrix | done |
| L4b Interpretation | grounded Claude-API reads per field/pass, never auto-published (agronomist review) | done |
| L5 Preview & tiles | S-4 UTC/CAT time, tiler skeleton + pure render/tile math (503 without the raster stack); COG render in-container | skeleton (render parked) |
| L6 Analyst workspace | RBAC'd BFF read endpoints (farms/fields/time-series/scenes/interpretations); React + MapLibre UI pending | BFF done |
| L7 Outbound sync | CSV + JSON payload, `GatewayPort` + http/recording adapters, `publish_farm` + `sync_outbox` (additive, idempotent, geometry never returned); GeoTIFF/PDF parked | done |
| Platform (Phase 7) | RBAC (view/annotate/run-analysis/publish), HS256 JWT auth + `require()` on endpoints, `GET /pipeline/health`; structlog + OTel | partial (load testing + alerting parked) |

Migrations: Alembic `0001`–`0003`. The heavy raster libs (`rasterio` / `rio-tiler`) live in the
`geo` extra and run inside the container, not on the host; the Anthropic SDK is the `interpret`
extra. DB/broker/SDK-gated tests skip locally and run in CI / `docker compose`.

## Documents

- `PLAN.md` — product spec and phased roadmap.
- `CLAUDE.md` — engineering rulebook and architecture invariants.
- `DELIVERY.md` — the agent org and delivery playbook.
- `docs/adr/` — architecture decision records.

## Layout

```
packages/    rs_core · rs_imagery · rs_analysis · rs_interpret · rs_sync   (importable libraries)
services/    api · worker · tiler                                          (runnable apps)
frontend/    React + TypeScript + MapLibre analyst workspace               (Phase 5)
tests/       unit + the index validation matrix
```

## Quickstart (local)

```bash
cp .env.example .env          # then fill in any real credentials
docker compose up             # postgres+postgis, redis, minio, api, worker, beat
```

- API health: <http://localhost:8000/healthz> · config: <http://localhost:8000/readyz>
- API docs: <http://localhost:8000/docs>
- MinIO console: <http://localhost:9001> (minioadmin / minioadmin)

## Develop without Docker

```bash
python -m venv .venv && . .venv/Scripts/activate    # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest          # the scientific core and access layer test with zero network and zero DB
ruff check .
```

## Architecture in one line

`Gateway ▸ Ingestion ▸ Collection ▸ Access Layer ⇄ CDSE ▸ Analysis ▸ Substrate ▸ Tiles ▸ Workspace ▸ Outbound ▸ Gateway`

The access layer is the keystone: all satellite data flows through one port behind a swappable
adapter (`mock` / `server_compute` / `windowed_cog`), so the source choice is a config switch.
