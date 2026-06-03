# remote-sense

Internal satellite agricultural intelligence platform. Turns Sentinel-2 L2A imagery (via the
Copernicus Data Space Ecosystem) into per-field agronomic indices for Zimbabwean agriculture. It
is the analysis backbone behind the **AgriTrack** farmer-facing mobile app; the two work through
a gateway. Expert-facing tool: optimized for analytical density and precision, not consumer
simplicity.

> Status (2026-06-03): the backend spine is built through interpretation. Ingestion + the PostGIS
> data model (L1), the collection pipeline with live Celery backfill/forward-fill + a daily scan
> beat (L2), the imagery access port + `mock` adapter (L3), the analysis core (L4), the
> plain-language interpretation layer (L4b), outbound sync (L7), and Phase 7 RBAC/auth are in and
> tested. The in-container raster render (L5) is in: the tiler renders colorized index tiles from
> stored COGs. The React + MapLibre analyst workspace (L6) is built in `frontend/` (Vite, Tailwind
> v4, MapLibre), including side-by-side pass comparison, saved views, a shared team-visible
> field-notes store, and a provenance/audit panel. Index thresholds, colormaps, and the
> interpretation prompt carry v1 Zimbabwe-tuned defaults pending an agronomist's sign-off. Remaining
> work is owner-blocked (live CDSE, agronomist sign-off, the gateway wire format, ingestion-endpoint
> auth).

## Build status

| Layer | What | State |
|---|---|---|
| L1 Ingestion & validation | `POST /ingest/farm`, PostGIS farm/field/analysis, geometry versioning, idempotent + concurrent-safe upsert | done |
| L2 Collection pipeline | planning kernel, Redis-locked collection (R-1), live Celery backfill/forward-fill, daily scan beat, per-field cursor | done |
| L3 Imagery access | `AccessPort` + `mock` adapter (config switch); real `windowed_cog` (ADR 0002) and `server_compute` (ADR 0003) CDSE adapters built behind seams, logic tested offline | mock + 2 real adapters (live validation pending CDSE creds) |
| L4 Analysis core | reflectance (per-scene −1000 offset, fail-fast guards on non-positive quantification and missing per-band BOA offset), per-AOI SCL masking, NDVI/EVI2/SAVI/NDRE/NDMI, zonal stats, validation matrix | done |
| L4b Interpretation | grounded Claude-API reads per field/pass, never auto-published (agronomist review) | done |
| L5 Preview & tiles | S-4 UTC/CAT time; the tiler renders colorized index tiles from stored COGs via rio-tiler (per field/scene/geometry version), fed by D1 COG emission + D7 store-and-discard | done (in-container) |
| L6 Analyst workspace | RBAC'd BFF endpoints (farms/fields/time-series/scenes/interpretations/audit, plus a write-backed **field-notes** store under the `annotate` permission); React + MapLibre workspace in `frontend/` (Vite, Tailwind v4, TanStack Query) with side-by-side pass comparison, saved views, shared field notes, and a provenance/audit panel | done |
| L7 Outbound sync | CSV + JSON payload, `GatewayPort` + http/recording adapters, `publish_farm` + `sync_outbox` (additive, idempotent, geometry never returned); GeoTIFF export in-container, PDF parked | done |
| Platform (Phase 7) | RBAC (view/annotate/run-analysis/publish), HS256 JWT auth + `require()` on endpoints, `GET /pipeline/health` + log-based health alerting; structlog + OTel | partial (load testing parked) |

Migrations: Alembic `0001`-`0005`. The heavy raster libs (`rasterio` / `rio-tiler`) live in the
`geo` extra and run inside the container, not on the host; the COG object store uses `boto3` (the
`storage` extra); the Anthropic SDK is the `interpret` extra. Compose builds each service with only
the extras it needs through the `INSTALL_EXTRAS` build arg (the tiler with `geo`, the COG-emitting
worker with `geo,storage`, the rest with none), so the base images stay lean. DB/broker/SDK/raster-gated
tests skip locally and run for real in CI: `.github/workflows/ci.yml` runs ruff plus the full suite
against PostGIS + Redis service containers with the `geo` extra installed, on every push and PR.

## Documents

- `PLAN.md` — product spec and phased roadmap.
- `CLAUDE.md` — engineering rulebook and architecture invariants.
- `DELIVERY.md` — the agent org and delivery playbook.
- `docs/adr/` — architecture decision records.

## Layout

```
packages/    rs_core · rs_imagery · rs_analysis · rs_interpret · rs_sync   (importable libraries)
services/    api · worker · tiler                                          (runnable apps)
frontend/    React + TypeScript + MapLibre analyst workspace (Vite SPA)    (L6)
tests/       unit + the index validation matrix
```

## Quickstart (local)

One command brings the whole backend up. `start.py` resolves the Docker CLI and Node (both are
commonly installed off PATH on Windows), seeds `.env` from the contract, frees the host DB port if
a stray test container holds it, then runs compose:

```bash
python start.py               # --frontend also starts the Vite workspace; --build forces a rebuild
```

Or drive compose directly:

```bash
cp .env.example .env          # then fill in any real credentials
docker compose up             # postgres+postgis, redis, minio, nginx, api, worker, beat, tiler
```

- API health: <http://localhost:8000/healthz> · config: <http://localhost:8000/readyz>
- API docs: <http://localhost:8000/docs>
- Tiler health: <http://localhost:8000/tiler/healthz> (nginx fronts the tiler; tiles serve at `/tiles/`)
- MinIO console: <http://localhost:9001> (minioadmin / minioadmin)

## Develop without Docker

```bash
python -m venv .venv && . .venv/Scripts/activate    # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest          # the scientific core and access layer test with zero network and zero DB
ruff check .
```

## Frontend (analyst workspace)

A Vite + React + TypeScript SPA in `frontend/`, styled with Tailwind v4 and mapping with MapLibre
GL. It talks to the RBAC-gated BFF (`services/api/workspace.py`) and the tiler.

**Auth (set this up first).** The BFF verifies an HS256 JWT on every request, so the backend `.env`
must set `RS_JWT_SECRET` before the workspace is usable; with an empty secret every gated endpoint
returns `500 "auth is not configured"`. In production the gateway IdP issues tokens. For local dev,
generate a secret and mint one signed with it, then paste it into the token gate or set
`VITE_DEV_TOKEN` in `frontend/.env` to skip the gate.

```bash
python -c "import secrets; print(secrets.token_urlsafe(36))"   # paste into RS_JWT_SECRET in .env
```

```python
# mint a local-dev token (the gateway IdP issues these in production)
import base64, hashlib, hmac, json, time
from rs_core import get_settings

def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
claims = b64(json.dumps({"sub": "you", "roles": ["admin"], "exp": int(time.time()) + 30 * 86400}).encode())
sig = b64(hmac.new(get_settings().jwt_secret.encode(), f"{header}.{claims}".encode(), hashlib.sha256).digest())
print(f"{header}.{claims}.{sig}")
```

The API must allow the workspace's browser origin or the preflight `OPTIONS` is blocked and no data
loads: set `RS_CORS_ALLOW_ORIGINS` (comma-separated; the dev default is the Vite server at
`http://localhost:5173`) to the deployed workspace origin(s) in production.

Workspace features: a three-panel layout (farms/fields, map, field inspector); the field inspector
tabs through the index time series, pass list, agronomic read, field notes, and the provenance/audit
log. The map toggles the index raster overlay and a side-by-side comparison of two passes on a shared
camera. Views (a field plus its index) can be bookmarked as saved AOIs. Field notes persist to the
shared, write-backed annotation store (RBAC `annotate`-gated BFF endpoints, geometry-version pinned
server-side); saved views and AOIs persist to the browser today, with a shared store for those a
parked backend decision.

```bash
cd frontend
npm install
cp .env.example .env       # point VITE_API_BASE_URL / VITE_TILER_BASE_URL at your stack
npm run dev                # http://localhost:5173
```

## Architecture in one line

`Gateway ▸ Ingestion ▸ Collection ▸ Access Layer ⇄ CDSE ▸ Analysis ▸ Substrate ▸ Tiles ▸ Workspace ▸ Outbound ▸ Gateway`

The access layer is the keystone: all satellite data flows through one port behind a swappable
adapter (`mock` / `server_compute` / `windowed_cog`), so the source choice is a config switch.
