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
- **Client / farmer vs analyst / agronomist** two distinct human roles, never conflated. A *client*
  (farmer) is onboarded through the mobile app, owns farms, and never uses remote-sense directly:
  their data flows farmer -> gateway -> remote-sense, and results flow back the same way. The
  *analysts / agronomists* are the small expert group who operate the remote-sense workspace and
  answer client questions.
- **AOI** area of interest: the polygon a computation is clipped to. A **field AOI** is a
  registered field's gateway-owned geometry, so its analyses are persisted and pushable. A **custom
  AOI** is an analyst-drawn, searched, or uploaded polygon with no canonical gateway identity, used
  only for an *AOI Studio preview*.
- **AOI Studio preview** a multi-pass index read over a *custom AOI* (a batch of specific calendar
  dates, exact-day; or a months-back sweep), computed through the production engine but **never
  persisted and never pushed** to the gateway, because it has no canonical identity to key a record
  to (split-ownership sync). An analyst exploration tool; results leave only as CSV. Contrast with a
  *field collect*, whose output is real, provenance-stamped, and pushable.
- **Collect specific dates** a targeted *field collect*: an analyst picks up to a few dozen dates
  for a registered field; each snaps to the nearest usable pass (within +/-7 days, deduped) and is
  collected and persisted exactly like a backfill pass, so it joins the field's analyses and becomes
  pushable. The cheaper, targeted alternative to a full backfill of the whole window.
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
- **CDSE quota budget** the CDSE account's request rate (a general account allows ~300/min), shared
  by every satellite call (STAC search, Process API render, windowed and metadata reads) across all
  workers. The systemic throughput ceiling for both collection and AOI Studio previews, so going
  faster means making fewer requests, not adding workers. _Avoid_: rate limit, throttle, bucket.
- **Split-ownership sync** the gateway owns identity and geometry (read-only here); remote-sense owns
  analyses. Joined on canonical farm ID, pushed additively, geometry never returned.
- **Interpretation / review-gate** the plain-language agronomic read from `rs_interpret`; always a
  draft an agronomist reviews before it can publish.
- **Overview dashboard** the landing route (`/`) for analysts: estate-level stat cards, a field
  health map showing all farm polygons coloured by `overall_health`, analytics cards (health
  distribution, coverage %, dead-letter count), and a recent-interpretations activity feed.
  Distinct from the **analyst workspace** (the three-panel cockpit at `/workspace`). These are two
  separate routes via TanStack Router; the dashboard has its own header with an "Enter Workspace"
  CTA; the workspace header has a House icon linking back.
- **Workspace BFF** the internal backend-for-frontend layer in `services/api` (`workspace.py`) that
  the React dashboard and analyst workspace call. Browser-facing and internal, so unlike the frozen
  AgriTrack contract it may be improved freely (CLAUDE.md §0); the new group endpoints live here.
- **Grounding context** the weather (GDD, precipitation) and AgriTrack activity telemetry fused into
  an interpretation so the read reflects field reality, not the index alone (ADR 0007).
- **Validation matrix** the test suite comparing each index against the Copernicus Browser on known
  scenes. No index ships without an entry.
- **Health status** for a farm, the area-weighted mean NDVI on its latest pass, classified crop-aware
  via `classify("ndvi", value, crop)` into `{healthy, moderate, stressed, critical}` (the
  `overall_health` field). The single definition every farm-level and group-level comparison reuses;
  forking it would fork the meaning of "health."
- **Comparison group** the umbrella for any persisted set of farms an analyst compares one farm
  against. The farm-vs-group health comparison and the systemic-vs-idiosyncratic attribution both
  operate on it uniformly. Realised as a *cluster* or a *peer cohort*. Generalises the existing
  field-vs-farm anomaly (a field underperforming its farm average, `get_farm_analytics_anomalies`)
  up one rung, to farm-vs-group.
- **Cluster** a *spatial* comparison group, with two bases. *Neighbourhood* is subject-centric and
  computed on demand: the farms near a given farm (KNN or radius within its Natural Region), with no
  persisted identity and so no membership churn. *Region* is persisted and rule-defined: every farm
  whose centroid falls inside a region boundary (seeded, uploaded, or analyst-drawn; see *Region
  boundary*). A persisted *emergent* partition (DBSCAN) is
  deliberately out of scope for now; reaching for it later is an ADR-worthy call. A farm belongs to
  many groups at once, so membership is many-to-many.
- **Peer cohort** an *attribute-similarity* comparison group keyed on `(canonical crop, Natural
  Region, size bucket)`: "farms like this one," independent of location. Crop is the canonical
  `CROPS` set (no free-text), so a multi-crop farm joins one cohort per crop it grows - granularity
  is `(farm, crop)`, and the benched quantity is that farm's crop-specific area-weighted NDVI,
  matching the crop-stratified standing lens. Region means *Natural Region*, never the gateway
  `Farm.region` string. Size buckets follow Zimbabwe farm typology (smallholder, A1, A2/commercial).
  Irrigation is optional context (a nullable analyst-set tag on the farm), not a membership
  criterion: a farm with unknown irrigation still belongs, and analysts may filter on it when known.
- **Region boundary** a polygon used to form a region cluster, created three ways, all the same
  reference-geometry category: the seeded *Natural Region* map; an analyst **upload** (`.zip`
  shapefile, GeoJSON, or GeoPackage - multi-feature with the analyst mapping the name column, one
  layer of N polygons yielding N region clusters, a single-feature file the natural special case);
  or an analyst **draw** in the workspace (a freeform polygon or a radius circle around the farm
  being viewed - the circle compiles to a polygon, so a single membership rule stands). Each boundary
  carries a `source` (`seeded` | `uploaded` | `drawn`) and its creator, so surfaces can tell official
  layers from analyst-drawn ones and filter them. The farm being viewed is only the entry point: a
  drawn region is a free-standing, reusable region cluster, identical to an uploaded ward, not owned
  by that farm. Reference geometry is owned by remote-sense and is distinct from gateway-owned
  farm-identity geometry, never confused with it and never pushed, so invariant 6 is unaffected
  (ADR 0010 and its 2026-06-17 amendment). A farm's region membership is fixed by centroid containment
  - identical for every `source` - and stamped with the boundary's version, so a re-survey or an edit
  of a drawn boundary is a tracked re-assignment, not a silent overwrite (the `geometry_version`
  provenance discipline, applied to regions). A boundary may span more than one Natural Region; it
  therefore carries a derived, area-weighted **Natural Region composition** (e.g. `{III: 0.71,
  IV: 0.29}`) and a **dominant Natural Region**, recomputed whenever its geometry changes. Creating a
  drawn or single-feature region is an analyst capability (`create_region_cluster`); bulk
  multi-feature uploads stay admin-gated (`upload_region_boundary`).
- **Natural Region** Zimbabwe's agro-ecological zones (I to V), seeded read-only at init from the
  current official map (version-stamped with source + year). The environmental key for comparing like
  with like - the dimension a peer cohort holds fixed, and the default scope a region cluster
  benchmarks within. A region cluster is not forced to nest inside one: it carries a Natural Region
  composition and a dominant Natural Region (see *Region boundary*), so a cross-zone boundary is
  handled at the analysis layer (benchmark within the dominant region by default; break out per-region
  stats when the spread is meaningful) rather than by clipping or rejecting the boundary.
- **Standing (benchmark lens)** where a farm sits *now* within its comparison group: its
  crop-stratified percentile on a common reference pass (like-crop ranked against like-crop), falling
  back to the classified-status distribution where a crop is too sparse to stratify. Never ranks raw
  NDVI across crops.
- **Movement (attribution lens)** whether a farm's *change* in health is shared by its group or
  unique to it, as a 2x2 of (did the farm move?) x (did the group move?): **nominal** (neither),
  **idiosyncratic** (farm declined, group steady), **systemic** (both declined, e.g. area-wide
  stress), and **resilient** (farm steady while the group declined - coping better than peers, worth
  learning from). The headline read, crop-robust because each farm is measured against its own
  baseline. A structural signal only: shared decline is not automatically drought (it may be
  synchronised crop maturation); the meaning is left to the analyst and grounding context
  (weather/activity, ADR 0007).
- **Group reference pass** the common pass a comparison group is measured on, instead of each farm's
  own latest. The most recent date by which a quorum (default 50%) of members has a clear read; each
  member contributes its nearest clear pass within +/-N days (default 14), and a member with no
  in-window clear pass sits out that round. The fix for the temporal dishonesty of comparing reads
  taken on different dates; reuses the nearest-pass alignment (commit 40d5f2d).
- **Group view / group-context panel** the two analyst surfaces for comparison groups. The *group
  view* is a new dashboard-family route (estate-level): one selected cluster or cohort with its
  member count, health distribution, 2x2 movement breakdown, ranked standing list, group time-series
  and boundary on the map. The *group-context panel* lives in the workspace beside an open farm: that
  farm's standing percentile and 2x2 movement label in each group it belongs to, linking out to the
  group view.

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
- Architecture decisions: `docs/adr/` (0001 ports-and-adapters through 0011 AOI Studio preview performance)
- Working process: `docs/process/WORKFLOW.md`
- Open backlog: `TODO.md` (groomed with `/triage`)
- Cross-session memory: the agent memory system (`MEMORY.md` index)
