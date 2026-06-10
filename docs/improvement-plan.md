# remote-sense — Improvement Plan (competitive roadmap)

> Companion to `PLAN.md` (the original build plan) and `README.md` (authoritative build state).
> This file tracks the forward roadmap to reach and exceed Copernicus Browser and EOSDA Crop
> Monitoring. It is organised in tiers; Tier 0 is the foundation everything else depends on.
> Each item names the architectural seam it lands behind, so nothing here violates the §1
> invariants. New external edges go behind a Port; new indices are a config change; anything
> touching an invariant gets an ADR; agronomy tuning is agronomist-reviewed and never
> auto-published (risk #6).

## Where we stand

The engine, pipeline, workspace, interpretation and sync layers are built and internally
correct, but they currently run only against the `mock` imagery adapter. No real Sentinel-2
scene can be processed until a real `AccessPort` adapter lands. That is Tier 0, and it gates
every competitive feature below: you cannot match an imagery product without reading imagery.

Reference points we are measuring against:

- **Copernicus Browser** — breadth: a multi-sensor catalogue (Sentinel-1/2/3/5P, Landsat),
  on-the-fly custom band math (evalscripts), time-lapse, statistical info, analytical TIFF
  download, split comparison.
- **EOSDA Crop Monitoring** — agronomic decision support: weather (history / current /
  forecast) plus growing-degree-days, productivity and zoning maps for variable-rate
  application, growth stages, a scouting app with geo-tagged notes, alerts (vegetation drop,
  weather stress), yield prediction, crop classification, soil moisture, team and reports.

Our unique asset neither competitor has: **AgriTrack**, a farmer app whose field-activity logs
can be correlated against the satellite signal. That is the moat, and it is Tier 2.

---

## Tier 0 — Make it real (in progress)

The unlock. Tracked in detail in `docs/adr/0002-cdse-windowed-cog-adapter.md`.

| # | Item | Seam | Status |
|---|---|---|---|
| T0.1 | Real `windowed_cog` CDSE adapter: STAC search, per-scene metadata (offset/quantification), windowed COG reads, per-AOI SCL masking, reflectance via the single `stack_to_reflectance`, R-3 read backoff | `rs_imagery.AccessPort` | **built, tested offline** (ADR 0002); asset-naming `⚑ CONFIRM` + creds pending |
| T0.2 | `server_compute` adapter (CDSE Process API): server-side index previews, reflectance fetch (engine computes the index, one source of math, risk #5) | `rs_imagery.AccessPort` | **built, tested offline** (ADR 0003); evalscript `⚑ CONFIRM` + creds pending |
| T0.3 | Adapter parity (`server_compute` vs `windowed_cog`) + validation matrix vs Copernicus Browser on known Zimbabwean scenes | `tests/test_adapter_parity.py`, `tests/test_validation_matrix.py` | **parity test landed (offline)**; real Copernicus entries need live CDSE access |
| T0.4 | CI running ruff + the full suite (raster + DB-gated) on every push/PR (closes the silent-skip gap that hid raster bugs) | `.github/workflows/ci.yml` | **landed** |

Status: **Tier 0 is code-complete.** Everything buildable without CDSE credentials is done and
tested offline (89 imagery/pipeline tests pass locally; the gated suites run in the new CI). What
remains is purely credential-gated: confirm the two `⚑ CONFIRM` wire details (S2 asset-key naming,
Process API evalscripts) against the live catalogue, then the validation matrix's real Copernicus
entries (T0.3) close the loop.

Exit criteria (credential-gated): a real field over a real time range produces stored zonal stats
and an index COG whose NDVI matches the Copernicus Browser within tolerance, with the reflectance
offset provably applied per scene, and `server_compute` agreeing with `windowed_cog`.

---

## Tier 1 — Match EOSDA's agronomy core

Build on the foundation; these are the fastest agronomic wins on data we already store.

| # | Item | Seam | Notes |
|---|---|---|---|
| T1.1 | **WeatherPort**: current / forecast / historical weather, rainfall, reference ET0, and growing-degree-days per field | new `rs_weather.WeatherPort` | **Built and tested offline 2026-06-03** (ADR 0004): `WeatherPort` + `daily`/`forecast`, the deterministic `mock` adapter, pure agronomy math (GDD with cap, FAO-56 Hargreaves ET0, rainfall accumulation), config switch. The real `open_meteo` adapter (free, key-less, behind the port) is the next step. Field-level wiring (centroid -> series -> accumulated GDD/ET0) lands in T1.2. |
| T1.2 | **Per-field alerts**: NDVI drop vs prior pass and vs season baseline, NDMI water-stress, statistical anomaly, weather water-deficit. Delivered via an `AlertSink` | extends `rs_core/alerts.py` | **Built and tested offline 2026-06-03**: `evaluate_field` + the rules (pure, primitive inputs, so rs_core keeps no upward dep), `PassReading`/`FieldAlert`, and `AlertSink` (`RecordingAlertSink` + `LoggingAlertSink`; AgriTrack/webhook delivery is the parked ⚑ CONFIRM). Weather wiring: `rs_weather.agro.accumulate_et0` + `total_precip_mm` feed the water-deficit rule. The DB-backed `monitor_field` worker (read history -> evaluate -> sink) is the thin follow-on. |
| T1.3 | **Productivity / management zones**: cluster each field's multi-temporal NDVI stack (k-means) into zones for variable-rate application | `rs_analysis/zones.py` | **Built and tested offline 2026-06-03**: a pure NumPy k-means (k-means++ seeding, no scikit-learn dep) over per-pixel temporal-mean features; `productivity_zones` returns a zone-label raster + per-zone means/counts, relabeled ascending by productivity, NaN-masked pixels marked nodata; `zone_polygons` vectorises to a VRA map (geo-gated). The DB/COG-stack loader (read a field's stored index COGs -> stack -> zones) is the thin follow-on. |
| T1.4 | **Phenology / growth-stage tracking** from the NDVI time series (greenup, peak, senescence) | `rs_analysis/phenology.py` | **Built and tested offline 2026-06-03**: `phenology(dates, ndvi)` returns peak, baseline, amplitude, start/end of season (observed half-amplitude crossings, None when not observed), length, and time-integrated NDVI (a biomass proxy). Pure; reports partial seasons honestly. Feeds interpretation and alerts (wiring is the follow-on). |
| T1.5 | **Per-crop thresholds** (the parked `rs_interpret/thresholds.py` overrides) | `rs_interpret` | Owner-blocked: needs the agronomist. Never auto-published. |

---

## Tier 2 — Beat them with the moat and analytics

| # | Item | Seam | Notes |
|---|---|---|---|
| T2.1 | **AgriTrack field-activity correlation**: overlay planting / fertiliser / irrigation / spray logs on the index timeline; correlate interventions with vegetation response | new `rs_activity` package (read-only `ActivityLogPort`) + workspace overlay | **Completed 2026-06-10** (ADR 0007): Integrated preceding GDD, precipitation, and AgriTrack activities directly into the grounding engine. Telemetry is persisted on the Interpretation draft for narrative reproducibility (ADR 0007). The IndexTimeseriesChart overlays rainfall bars, GDD line, and activity vertical markers on the SVG timeline; the Read tab panel shows grounding stats in a header. |
| T2.2 | **Yield estimation**: regression on cumulative NDVI / GDD against historical yields, per crop | `rs_analysis` / new `rs_models` | Start with a simple, explainable model; gate behind review like interpretation. |
| T2.3 | **Anomaly detection** vs the field's own history and vs neighbours in the same agro-ecological zone | `rs_analysis` | Powers higher-quality alerts (T1.2). |
| T2.4 | **Local data layers**: ZimStat climate, ZINWA water, agro-ecological zones as overlays and correlation views | workspace + new reference data tables | Already in the original vision (PLAN §7). |
| T2.5 | **Custom index builder** (Copernicus evalscript equivalent): analysts define an index formula behind the locked-formula discipline | `rs_analysis.indices` (a validated, versioned user-defined spec) | Must carry formula + version + provenance like every built-in index. |

---

## Tier 3 — Copernicus-grade breadth

| # | Item | Seam | Notes |
|---|---|---|---|
| T3.1 | **Sentinel-1 SAR**: cloud-penetrating radar for the rainy season when optical fails (backscatter, coherence, SAR soil moisture) | new adapter + new index family | The biggest reliability win for Zimbabwe's wet season. Resolution/processing policy needs an ADR. |
| T3.2 | **Multi-temporal cloud-free compositing** (median / best-pixel over a window) to fill cloud gaps | `rs_analysis` | Must respect resolution honesty (no fake upsampling); composite provenance lists contributing scenes. |
| T3.3 | **Landsat / HLS harmonisation** for denser revisit and longer history | new adapter | Cross-sensor consistency needs an ADR (band differences). |
| T3.4 | **Time-lapse / animation export** | workspace + tiler | Copernicus parity. |

---

## Cross-cutting (quality and operations)

- **CI/CD** (Tier 0.4) is the highest-leverage process fix: the gated suites silently skip
  locally and there is no pipeline, which previously hid a cascade of raster bugs.
- **COG retention policy** enforcement (S-1): a multi-year backfill grows storage without bound;
  add an expiry/retention job.
- **Load and scale testing** (parked from Phase 7) and **observability dashboards** on the
  existing OTel + `pipeline_health` + alerts.
- **CDSE quota controls** in the adapter (the centralised place): token bucket + circuit breaker
  for mass backfill (R-3).

---

## Sequencing

Do Tier 0 first and in order (nothing else is real until T0.1 lands and T0.3 proves it). Then
Tier 1 T1.1 to T1.3 (fast wins on data we hold), then the AgriTrack correlation (T2.1, the moat),
then breadth. Owner-blocked items (per-crop thresholds, gateway wire format, live CDSE access for
the real validation entries) stay flagged and do not block the buildable work in front of them.
