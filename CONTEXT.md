# CONTEXT: remote-sense

Orientation map for humans and agents: the shared language and the module layout, so a new session
starts grounded. This is a map, not a rulebook. Binding rules are in `CLAUDE.md`, the product spec
is `PLAN.md`, decisions are in `docs/adr/`, and the working process is `docs/process/WORKFLOW.md`.
Keep this file current with `/grill-with-docs` during Shape: it is the shared language new work
reuses, so the codebase stays consistent and low-repetition.

remote-sense is an internal satellite agricultural-intelligence platform: the analysis backbone
behind the AgriTrack farmer app. It ingests field geometry, pulls Sentinel-2 imagery, computes
spectral indices on surface reflectance, interprets them in plain agronomic language grounded in
weather and farm activity, and pushes reviewed results back to the gateway.

## Domain glossary

- **Farm / field / analysis** the data hierarchy. A farm owns fields (polygons); an analysis is one
  index over one field for one scene (`rs_core` data model).
- **AOI** area of interest: the field polygon a computation is clipped to.
- **DN / reflectance / BOA offset** raw pixel digital number vs surface reflectance.
  `rho = (DN + BOA_ADD_OFFSET) / QUANTIFICATION_VALUE`, read per scene from metadata. `DN == 0` is
  NoData. The single highest correctness risk (CLAUDE.md 1.2).
- **SCL mask** Scene Classification Layer; cloud and shadow assessed per AOI, never from scene-level
  cloud percentage.
- **Clear-pixel fraction** share of AOI pixels left after masking; stored with every result and used
  as a confidence gate.
- **Zonal stats** per-AOI aggregates (mean/min/max/std/p10/p90) of an index.
- **COG** cloud-optimized GeoTIFF, the derived raster output. Raw bands are transient (1.7).
- **Provenance tuple** `(provider, provider_scene_id, processing_mode, formula_version,
  geometry_version)`, travelling with every analysis for reproducibility.
- **Backfill / forward-fill** the historical sweep (default 18 months) vs ongoing collection on the
  roughly 5-day Sentinel-2 cadence.
- **Split-ownership sync** the gateway owns identity and geometry (read-only here); remote-sense owns
  analyses. Joined on canonical farm ID, pushed additively, geometry never returned.
- **Interpretation / review-gate** the plain-language agronomic read from `rs_interpret`; always a
  draft an agronomist reviews before it can publish.
- **Grounding context** the weather (GDD, precipitation) and AgriTrack activity telemetry fused into
  an interpretation so the read reflects field reality, not the index alone (ADR 0007).
- **Validation matrix** the test suite comparing each index against the Copernicus Browser on known
  scenes. No index ships without an entry.

## Module map

| Module | Responsibility | Seam / boundary | Owner agent |
|--------|----------------|-----------------|-------------|
| `packages/rs_imagery` | Satellite access: `AccessPort` + `mock` / `server_compute` / `windowed_cog` adapters | the only path to satellites | `geospatial-engineer` |
| `packages/rs_analysis` | Reflectance, indices, SCL masking, zonal stats, COG output | pure lib, no DB or service imports | `geospatial-engineer` |
| `packages/rs_core` | Config, PostGIS data model (farm/field/analysis), RBAC, repositories | shared core | `backend-engineer` |
| `packages/rs_sync` | Outbound export builders + `GatewayPort` | the only path to the gateway | `backend-engineer` |
| `packages/rs_weather` | `WeatherPort` + Open-Meteo adapter (GDD, precipitation) for grounding | external-data port | `backend-engineer` |
| `packages/rs_activity` | `ActivityLogPort` for AgriTrack field-log correlation | external-data port | `backend-engineer` |
| `packages/rs_interpret` | Crop thresholds + Claude-API interpretation, grounded and review-gated | domain science | `agronomy-scientist` |
| `services/api` | FastAPI ingestion, workspace BFF, publish trigger | the only browser-facing API | `backend-engineer` |
| `services/worker` | Celery tasks, beat scheduler, collection state | backfill / forward-fill | `pipeline-engineer` |
| `services/tiler` | rio-tiler tile server rendering COGs for the map | serves the frontend | `geospatial-engineer` |
| `frontend/` | React + MapLibre analyst workspace | talks to the BFF only | `frontend-engineer` |

The weather and activity ports are plumbed by `backend-engineer` but exist to feed interpretation
grounding, which `agronomy-scientist` owns the meaning of. Cross-cutting: infra, CI, and
observability are `devops-engineer`; tests and the validation matrix are `qa-engineer`; design,
ADRs, and invariant review are `architect` (read-only).

## Where things live

- Binding build rules: `CLAUDE.md`
- Product spec: `PLAN.md`
- Architecture decisions: `docs/adr/` (0001 ports-and-adapters through 0007 read-tab grounding fusion)
- Working process: `docs/process/WORKFLOW.md`
- Open backlog: `TODO.md` (groomed with `/triage`)
- Cross-session memory: the agent memory system (`MEMORY.md` index)
