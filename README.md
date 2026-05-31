# remote-sense

Internal satellite agricultural intelligence platform. Turns Sentinel-2 L2A imagery (via the
Copernicus Data Space Ecosystem) into per-field agronomic indices for Zimbabwean agriculture. It
is the analysis backbone behind the **AgriTrack** farmer-facing mobile app; the two work through
a gateway. Expert-facing tool: optimized for analytical density and precision, not consumer
simplicity.

> Status: **Phase 0 — Foundations**. The infrastructure, configuration, and the imagery
> access-layer port + mock adapter are in place. Ingestion, analysis, collection, rendering, the
> analyst workspace, and outbound sync follow per the roadmap.

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
