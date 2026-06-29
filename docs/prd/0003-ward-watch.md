# PRD 0003 — Ward Watch
## Communal-Farmer Crop Assessment and Food-Security Early Warning

**Status:** Draft v0.3 (reconciled with elicited decisions, 2026-06-25)
**Product name:** Ward Watch. The internal statistical object stays "cluster / peer cohort / region grouping" per ADR 0010. "Ward Watch" never refers to that primitive.
**Supersedes:** the v0.1 blueprint and the v0.2 architectural review.
**Depends on:** ADR 0010 / PRD 0002 (comparison groups), the scientific core (reflectance, SCL, COG, zonal stats), the orthophoto / RGB-COG work, AOI Studio primitives, and the rs_sync GatewayPort.
**Requires:** a light ADR for Ward Watch as a new consumer of the scientific core plus new additive inbound gateway flows (see §9). No section-1 invariant is moved.

---

## 0. What changed across revisions

v0.1 set direction. v0.2 corrected the statistics and the honesty of the framing. v0.3 reconciles the architecture with four elicited decisions and reverses v0.2's one wrong turn: the assumption that the product is a standalone identity authority with no gateway.

Carried forward from v0.2 (correct, kept):

| v0.1 said | Kept correction | Reason |
|---|---|---|
| Cohort distress via Z-score (x-μ)/σ | Robust movement lens: median + median/MAD over a trailing baseline | Mean/std is not robust. In a distressed cohort the severe cases inflate σ and bias μ, hiding the households you are triaging. |
| One flat distress score | The 2x2 movement label (nominal / idiosyncratic / systemic / resilient) | The idiosyncratic-vs-systemic split is the triage decision: one farm failing is an officer visit, a whole cohort failing is a food-security escalation. |
| Satellite "detects" the cause | Satellite flags and prioritises an anomaly; the officer diagnoses the cause | Cause attribution from spectral signal alone is unreliable, and honest framing matches the officer-as-multiplier thesis. |

Reversed in v0.3 (the load-bearing change):

| v0.2 said | v0.3 says | Reason |
|---|---|---|
| The product is its own identity authority; there is no gateway in the AGRITEX flow; split-ownership (#6) is retired | Additive extension. The gateway stays the identity authority and an inbound data source. Invariant #6 and the frozen AgriTrack wire contract stand. Ward Watch adds intelligence and triage, it does not own identity. | Elicited decisions: additive extension (not a new identity product); declared crop data may come from the gateway; drone already runs as a separate gateway app. The gateway is in the flow. |

Resolved open items from v0.2:
- **Product name:** Ward Watch. The "cluster" collision with ADR 0010 is gone; cluster/cohort stays the internal statistical object.
- **Ground-truth flywheel** stays a first-class output: every officer diagnosis is a labelled point (plot, observed crop, observed condition, cause) that, over two to three seasons, makes real crop classification and yield calibration feasible.

---

## 1. Vision

A low-cost agricultural early-warning platform that lets a handful of AGRITEX officers monitor thousands of communal households, using free Sentinel-2 analytics to surface distressed households before crop failure becomes a food-security event. Ward Watch multiplies officers, it does not replace them, and it accumulates the ground-truth that future ML layers depend on.

```
Gateway (identity, declared crop mix, planting window, drone refs)
        | inbound, read-only here (#6 preserved)
        v
Sentinel-2 -> Scientific Core -> Cohort Engine -> Officer Triage -> Field Diagnosis
                                      ^                                  |
                                      +------------- ground-truth -------+
                                                     (labels feed back)
        |
        v outbound, additive results
AgriTrack and Ministry dashboards (consumers)
```

---

## 2. Locked decisions (the four forks)

These four were elicited and are fixed for v1:

1. **Primary user: AGRITEX agronomist.** Triage cockpit for the 3 to 4 officers per ward, 300 to 600 households per officer. Farmers benefit through the officer; direct-to-farmer is deferred.
2. **Crop ID: hybrid.** Declared crop mix (from the gateway where it holds it, or from officer enrollment) is the source of truth. Our own satellite verification runs before cohorting: an absolute-index gate plus a phenology-shape check (observed NDVI/EVI2 green-up and senescence against the declared crop's expected curve), feeding a per-plot confidence score. Not a trained classifier yet, since labels do not exist; the flywheel earns that later (§5 of this list, Phase 5).
3. **Imagery: Sentinel-2 + orthophoto for v1.** Zero new cost. S2 for cohort stats and indices; the RGB orthophoto already rendered for visual inspection of tiny plots. Planet (~3 m) is a Tier-2 upgrade once funded. Drone is consumed from the existing separate gateway app, not built here.
4. **Contract: additive extension.** The scientific-core invariants stand. The frozen AgriTrack wire contract is untouched and is a floor, not a ceiling: we add new routes and new inbound flows freely and improve internals, and we never break the existing contract. The gateway stays the identity authority, so invariant #6 (split-ownership) is preserved. A light ADR documents Ward Watch as a new consumer plus the new inbound flows (§9).

---

## 3. Users and responsibilities

**Ward level (primary):** 3 to 4 AGRITEX officers. They enroll households, review the weekly triage queue, prioritise visits, diagnose in the field, record recommendations, and escalate.

**District / provincial / ministry (read and rollup):** district and provincial agronomists, ministry monitoring teams. Oversight dashboards, ward comparison, food-security rollups, program monitoring. No field-level data entry.

Officer responsibilities decompose into four loops: **Enrollment** (register household, proxy boundary, crop mix, planting window) into **Monitoring** (weekly distress queue) into **Intervention** (diagnose, recommend, escalate) into **Reporting** (ward report, seasonal performance, ground-truth capture).

---

## 4. Crop identification (hybrid, grounded in the existing indices)

**Source of truth:** the declared crop mix. It arrives from the gateway where the gateway holds it, and from officer enrollment otherwise. Pure satellite classification fails here for the usual communal reasons (intercropping, sub-pixel fields, irregular boundaries, mixed planting dates, trees and homesteads inside fields) and would need thousands of labelled plots plus annual retraining that does not exist yet.

**Verification layer (v1, cheap, reuses the cohort engine and runs before comparisons):** the elicited steer is that our own analyses should determine crop to the best extent satellite allows, before cohorting. So v1 does two things per declared crop:

1. **Absolute-index and cohort-outlier gates** (the v0.2 behaviour):
   - Declared maize, bare soil in January or very low NDVI in February: flag.
   - Declared fallow, high NDVI green-up: flag possible undeclared planting.
   - Declared groundnuts, signature tracking the maize cohort: flag for verification.
2. **Phenology-shape check** (added in v0.3): score the plot's observed NDVI/EVI2 trajectory against the declared crop's expected green-up and senescence shape for the season. This is a template-fit score, not a classifier, and it feeds the confidence score below. It runs before cohorting so a low-confidence plot can be down-weighted or held out of its cohort.

**Verification layer (later, Phase 5):** calibrated per-crop phenology templates per agro-ecological zone, refined by the flywheel labels. This is the path to real crop classification, deferred until a season of officer diagnoses exists.

**Confidence score per plot:**

| Score | Meaning | Action |
|---|---|---|
| 90 to 100 | Observed signature agrees with declared crop | none |
| 60 to 89 | Minor disagreement | low-priority review |
| below 60 | Strong disagreement | officer verification required |

**Mixed-pixel honesty (the binding constraint).** At 10 m a 0.10 ha backyard plot is about 10 S2 pixels before edge erosion; after eroding edges and homestead contamination, often 1 to 3 usable pixels remain. Per-household stats on the smallest plots are therefore noisy. Mitigations: (1) attach a **pixel-count quality flag** to every per-household stat; (2) lean on cohort aggregation, since comparing distributions is more forgiving than single estimates; (3) use the orthophoto, not S2, for the smallest plots' visual confirmation. Never present a confident NDVI for a 2-pixel plot.

---

## 5. Imagery strategy

| Purpose | Source |
|---|---|
| Field enrollment and boundary sanity check | Orthophoto (RGB) |
| Vegetation monitoring and distress detection | Sentinel-2 (the existing indices) |
| Crop verification | Sentinel-2 trajectory + orthophoto |
| Officer investigation | Both, plus drone reference where the gateway has it |
| High-resolution per-plot detail | Drone, consumed from the separate gateway app (not built here); Planet ~3 m as a Tier-2 upgrade once funded |

The RGB-COG download work already in flight feeds the orthophoto half of the physical-visit package (§7) directly: persist the RGB COG under the WKB-hashed prefix and the visit screen reads it the same way AOI Studio does. Drone imagery is not produced by Ward Watch; the visit package references the drone artefacts the gateway app already generates (Phase 5 integration). Planet stays a Tier-2 optional upgrade; neither drone nor Planet is required for v1.

---

## 6. Cohort intelligence engine (extend ADR 0010, do not fork it)

This is the core, and it is **not new code**. Ward Watch triage is the **movement lens** of the comparison-groups system, applied at household granularity, with two added strata and an AGRITEX-facing surface.

### 6.1 Cohort definition

The existing peer cohort is canonical crop + Natural Region + size bucket + optional irrigation tag. Ward Watch extends this with two communal-specific strata:

```
Natural Region (ZINGSA AEZ 2020)
        |
Ward (admin boundary)            <- new stratum, nested inside NR
        |
Dominant / canonical crop        <- reuse; cohort on dominant crop in v1, store full mix
        |
Planting window                  <- new stratum, critical (see below)
        |
Cohort
```

- **Ward** nests inside Natural Region. Wards are small relative to AEZ boundaries, so NR is often near-constant within a ward and acts as the coarse fallback level.
- **Crop mix** is stored as a matrix (maize 60 / cowpeas 30 / squash 10) but **cohorted on the dominant crop** in v1. Crop-mix-similarity cohorting is a later refinement.
- **Planting window** is genuinely needed: communal planting dates spread with rain onset. Comparing a late-planted plot against early-planted peers manufactures false distress. Capture planting date at enrollment (declared) and optionally refine from observed green-up onset later.

### 6.2 Statistics (robust, not Gaussian)

Per cohort, per index, over a trailing baseline window (reuse the existing ±14-day alignment window):

- centre = **median** of the cohort
- spread = **median / MAD** (median absolute deviation)
- a household's deviation is its robust distance from the cohort centre
- the **2x2 movement label** classifies each household:

| | Cohort stable | Cohort declining |
|---|---|---|
| **Household stable** | nominal | resilient |
| **Household declining** | **idiosyncratic** | **systemic** |

This 2x2 is the triage engine. **Idiosyncratic** means this household is failing while peers hold: the highest-value officer visit (pest, theft, local water failure, abandonment). **Systemic** means the whole cohort is sliding: drought or regional shock, a food-security escalation rather than individual visits. A flat Z-score cannot tell these apart, which is why v1's (x-μ)/σ proposal is replaced.

A **standing-lens percentile** (crop-stratified) is surfaced alongside the movement label for the "where does this household rank right now" view, exactly as PRD 0002 already does.

### 6.3 Small-cohort fallback (a real statistical gap v0.1 missed)

Ward x dominant-crop x planting-window cohorts can get tiny. Reuse the 50% quorum / ~200-farm materialisation thinking and add an explicit fallback ladder:

```
ward x crop x planting-window      (preferred)
   | if cohort < N_min
ward x crop                        (drop planting window)
   | if still < N_min
district x crop                    (widen geography)
   | if still < N_min
Natural Region x crop              (coarsest)
```

Set `N_min` empirically (start about 20 to 30). Stamp every household result with **which cohort level it was scored against** so officers and auditors can see when a score came from a thin sample.

### 6.4 Compute reuse

Group reference pass, on-the-fly stats with the ~200-farm materialisation seam, and immutable result keys all carry over from ADR 0010. Ward Watch adds household-granularity inputs and the ward and planting-window strata; the compute architecture is unchanged.

---

## 7. Officer-facing surfaces

### 7.0 Product surface (decision, override noted)

**One offline-first officer client** (low-end Android). Enrollment captures household identity and proxy geometry offline and **syncs to the gateway, which remains the authority**. The client is a capture-and-sync client, not the store of record, which preserves invariant #6. The triage cockpit and physical-visit package read Ward Watch intelligence plus the gateway-held identity.

Rationale: the rural offline reality (§8) rules out a purely online web workspace, and one app keeps the officer's enroll-to-visit loop in a single place.

Override note: if a single app proves too heavy for v1, the fallback is enrollment through an AgriTrack officer mode (gateway-native) plus a thin triage surface, at the cost of splitting the officer loop across two apps. This decision can be revisited at the Phase 1 plan gate without disturbing the rest of the PRD.

### 7.1 Weekly triage queue

Each week, ranked by movement-label severity then robust deviation:

`Household . Village . Distance from officer . Crop mix . Movement label . Trend sparkline . Cohort level used . Pixel-quality flag`

Cap the queue (for example top 15) so it is actionable, not a wall.

### 7.2 Alerts (index-grounded, framed as hints not diagnoses)

The system flags an anomaly and offers a most-likely category hint; the officer diagnoses. Grounded in the existing indices:

| Alert (hint) | Spectral signature | Index |
|---|---|---|
| Water stress | moisture drop leading vegetation drop | NDMI decline + NDVI decline |
| Delayed planting | green-up onset lagging cohort | NDVI/EVI2 phenology timing |
| Pest / disease (suspected) | chlorophyll/red-edge drop ahead of canopy | NDRE drop, idiosyncratic label |
| Flooding / waterlogging | sudden canopy drop + moisture spike post-rain | NDVI drop + NDMI spike + SCL water flags |
| Nutrient deficiency | persistent sub-cohort red-edge | NDRE chronically low |
| Possible crop failure | severe sustained negative deviation | any index, severe movement label |

UI caveat to keep: these are prioritisation hints. The officer's field diagnosis is the truth and is captured as ground-truth (§0 flywheel).

### 7.3 Physical visit package

On opening a household: household info, **orthophoto** (from the RGB-COG work), **Sentinel trend** (the index set), historical record, recommended questions, previous visits, intervention notes, drone reference where the gateway has it, and a **diagnosis capture form** (crop confirmed, condition, cause, action). The diagnosis form is what feeds the flywheel.

### 7.4 Officer loop

```
Weekly S2 update -> cohort movement labels -> top-15 distressed ->
visit -> field diagnosis (captured) -> recommendation -> outcome tracking
```

---

## 8. Mass enrollment and offline reality

### 8.1 Speed target

**Under 1 minute per household.** Workflow: open app, search or create household, drop centre pin, pick field-size class, enter crop mix, record planting window, save offline.

### 8.2 Auto-boundary generation (with a projection caveat)

Instead of drawing polygons, generate a proxy AOI from a centre pin plus a size class:

| Class | Area |
|---|---|
| Backyard | 0.10 ha |
| Small holding | 0.50 ha |
| Medium | 2.00 ha |
| Large | up to 100 ha (entered explicitly) |

Critical implementation note from the existing area-handling discipline: build the square or circle in a **projected CRS** (Zimbabwe UTM 35S west of 30E, UTM 36S east), not EPSG:4326 degrees, so the area is correct, then store the geometry back as 4326 GeoJSON. The proxy AOI is explicitly an approximation: tag it `geometry_source = officer_proxy` so downstream consumers know it is not a surveyed boundary.

### 8.3 Multi-crop data model

```
Household -> Plot -> Crop-mix matrix (crop, weight%)  +  planting_window  +  geometry(proxy)
```

The analytics engine evaluates crop communities, not single-crop pixels, which is realistic for intercropped communal plots. Store the full mix; cohort on the dominant crop for v1.

### 8.4 Offline sync (idempotent, UUID-keyed, gateway-targeted)

100% offline operation on low-end Android. Local SQLite, JSON queue, batch upload when the officer reaches connectivity. Apply the existing sync discipline, with the gateway as the sync target:

- each household and plot gets a **client-generated UUID** at creation
- the **gateway** performs idempotent upserts on UUID, so re-syncing a batch never duplicates and the gateway stays the identity authority (#6 preserved)
- remote-sense reads identity and geometry from the gateway; it does not store officer-enrolled identity as the authority
- enrollment records live on-device until sync; **do not** cache imagery locally (battery, storage). Rendering is server-side and online.

---

## 9. Scientific-core boundary and gateway integration (light ADR)

"Additive extension, scientific core stays" means Ward Watch is a new consumer of the scientific core through the same internal ports-and-adapters interface AgriTrack uses, plus new additive inbound flows from the gateway. The ADR must settle:

1. **Invariants preserved:** reflectance processing, SCL masking, scene classification, COG generation, provenance, temporal analytics, the index contract, Processing Baseline 04.00 conversion. The CLAUDE.md section-1 hard-locks stand.
2. **AgriTrack untouched:** the frozen wire contract through the rs_sync GatewayPort does not change; AgriTrack is one consumer.
3. **Identity ownership (no divergence):** the gateway owns household identity and geometry (read-only here), per invariant #6. Officer enrollment writes to the gateway (§8.4). Ward Watch owns the analytics, the cohort intelligence, and the triage cockpit. No invariant is moved, so the ADR stays light.
4. **New inbound flows (additive):** declared crop mix, planting window, and drone-imagery references arrive from the gateway. Define the inbound contract (fields, units, nullability, identity join key) as a dependency. These are additive and do not alter the frozen outbound wire. This is where "do not let the contract restrict improvements" applies: the floor is fixed, additions are free.
5. **New geometry layers:** ward administrative boundaries (ZimStat / Surveyor-General) in addition to the ZINGSA AEZ 2020 polygons already in procurement. Both version-stamped, centroid-containment assignment, consistent with ADR 0010.
6. **The only trigger for a heavier ADR:** officer enrollment storing identity locally as the authority instead of syncing to the gateway. The §7.0 and §8.4 decisions avoid that, so it does not arise in v1.

---

## 10. Reporting and food-security rollup

Household movement labels aggregate cleanly upward: the count and trend of idiosyncratic vs systemic households per ward, then district, then province. **Systemic** clusters are the food-security signal (whole cohorts sliding). **Idiosyncratic** counts measure officer workload and local intervention need. Ward-level seasonal reports and ministry dashboards read from the same labels, so there is no separate analytics path.

Yield forecasting (the "historical yield" slot in the visit package) is **not** in v1: NDVI-integral yield models need local ground-truth calibration. It becomes feasible once the flywheel has a season of officer-recorded outcomes. Do not promise yield numbers before calibration.

---

## 11. Phased roadmap

| Phase | Scope | Reuses |
|---|---|---|
| **0** | Light ADR (new consumer + additive inbound flows, #6 preserved); resolve ward and AEZ polygon procurement; define the gateway inbound data contract (crop mix, planting window, drone refs, identity join key) | ADR workflow, ZINGSA work, rs_sync GatewayPort |
| **1** | Offline enrollment client (UUID sync to the gateway, auto-AOI in UTM, crop-mix model); ingest households as a new AOI source via gateway identity; run the existing pipeline to per-household indices | AOI Studio, analysis pipeline, idempotent-upsert pattern, GatewayPort |
| **2** | Cohort engine: extend comparison groups with ward and planting-window strata at household granularity; movement labels; small-cohort fallback ladder; the phenology-shape verification check | ADR 0010 / PRD 0002 in full |
| **3** | Officer triage queue plus physical-visit package (orthophoto via RGB-COG, S2 trend, alert hints, diagnosis capture) | RGB-COG download work, the index set |
| **4** | Reporting plus food-security rollups plus ward and district dashboards | movement labels, MapLibre surfaces from PRD 0002 |
| **5** | Later: per-crop phenology templates, NDVI-integral yield calibration, Planet tier, and consuming drone references from the gateway app in the visit package | flywheel labels accumulated in Phases 3 and 4 |

**Pilot before scale.** Do not start at 9,000 households across 20 to 30 wards. Run one ward end to end (enrollment, triage, visit, diagnosis capture) for a season, validate the mixed-pixel quality flags and the cohort fallback against officer reality, then scale.

---

## 12. Open questions to resolve before merge

Resolved: product name (Ward Watch); product surface (one offline-first client, §7.0); gateway inbound
data contract (candidate `gw-inbound/v1` built behind the GatewayPort, see item 1).

Still open:

1. **Gateway inbound data contract — CANDIDATE BUILT (2026-06-29, backlog 0026).** A tolerant candidate schema (`gw-inbound/v1`) for declared crop mix, planting window, drone references, and the `canonical_household_id` join key is implemented behind `GatewayPort.fetch_household_declarations` (`packages/rs_sync/inbound.py`), recorded in `CONTRACT.md` + `contract/fixtures/`. Residual: ⚑ CONFIRM the real gateway field names, units, nullability, and the GET path with the gateway team, then rename behind the port and bump the version. This no longer blocks 0031 ingestion (which can build against synthetic + candidate declarations).
2. **`N_min` cohort floor** and the exact fallback-ladder thresholds (empirical, start about 20 to 30).
3. **Minimum usable pixel count** per plot before a per-household stat is shown vs suppressed; the erosion buffer for proxy AOIs.
4. **Crop-mix cohorting:** dominant-crop (v1) vs mix-similarity (later); the dominant-crop tie-break rule.
5. **Planting-window capture:** declared at enrollment, editable, and/or green-up-inferred; the bucket edges.
6. **Ward boundary source** procurement (ZimStat / Surveyor-General) alongside the AEZ polygons.
7. **RBAC:** ward / district / provincial / ministry roles (still open in PRD 0002 too; settle once for both).
8. **Institutional partnership reality:** the AGRITEX/Ministry pilot agreement, device provisioning, officer training, and who owns the data legally.
9. **Diagnosis schema:** the field-diagnosis form is the flywheel's data contract; design it deliberately (controlled vocab for crop, condition, cause) so labels are ML-usable later.

---

## 13. What this retires and what it keeps

Retires:
- The v0.1 Z-score cohort design (replaced by the robust movement lens).
- The v0.2 assumption that the product owns identity and there is no gateway (reversed: the gateway stays the authority, #6 preserved).
- Any plan to build a statistics engine separate from comparison groups.

Keeps (explicitly not retired):
- The gateway split-ownership sync model (invariant #6).
- The frozen AgriTrack wire contract through the rs_sync GatewayPort.
