# ADR 0002 — The real `windowed_cog` CDSE adapter

- Status: accepted
- Date: 2026-06-03
- Phase: 2/3 (Analysis + Collection), Tier 0 of the improvement plan
- Builds on: ADR 0001 (ports and adapters for imagery access)

## Context

ADR 0001 established `AccessPort` with a `mock` adapter so the whole platform could be built and
tested without touching CDSE. Everything downstream is now built, but only `mock` works: the
registry raises `NotImplementedError` for both real adapters, so no real Sentinel-2 scene can be
processed. This ADR covers the first real adapter, `windowed_cog`, which the stored backfill
pipeline uses and where reflectance-offset correctness must be guaranteed (the engine owns the
math, PLAN §4).

CDSE exposes Sentinel-2 L2A two ways relevant here: a STAC catalogue API
(`RS_CDSE_STAC_URL`) for discovery, and the `eodata` S3 store holding the product rasters,
reachable over GDAL `/vsis3/` with S3 credentials issued from the CDSE dashboard. Each product
carries an `MTD_MSIL2A.xml` with the per-scene radiometric metadata.

## Decision

### 1. Three modules behind one adapter

- `cdse_metadata.py` — pure parser of the L2A product metadata XML. Extracts
  `BOA_QUANTIFICATION_VALUE` and per-band `BOA_ADD_OFFSET` (mapping the MTD `band_id` index to
  band names) into a `SceneMetadata`. This is the single highest-risk rule (invariant 2), so it
  is isolated, pure, and unit-tested on synthetic XML with zero network.
- `cdse_stac.py` — STAC search client. POSTs a standard STAC `search` (collections, datetime,
  `intersects` the AOI, `eo:cloud_cover` filter), parses the standard FeatureCollection into
  `SceneRef`s, and caches the per-scene asset map so `fetch`/`metadata` can resolve hrefs
  without a second round trip. httpx-injectable, tested with `MockTransport`.
- `windowed_cog.py` — the `AccessPort` implementation. Resolves the band asset, reads the AOI
  window per band through a `WindowSource` seam, applies reflectance, masks per-AOI on SCL, and
  returns the normalised `NormalizedResult`.

### 2. Reflectance is applied in the adapter, via the one canonical function

The `NormalizedResult` contract promises reflectance-corrected bands, and the collection pipeline
passes `fetched.data.bands` straight into `analyze_index` as reflectance. So the adapter converts
DN to reflectance using `rs_analysis.reflectance.stack_to_reflectance` (the per-scene offset and
quantification from `cdse_metadata`). We import that one function rather than duplicate the
formula, so invariant 2 has exactly one implementation and cannot drift between the adapter and
the engine. `engine.analyze_from_dn` remains the end-to-end path the validation matrix uses to
prove the offset is applied.

### 3. All rasterio lives behind a `WindowSource` seam

Reading a windowed array, rasterising the AOI polygon to the read grid, and reading the metadata
XML bytes are the only operations that need the `geo` extra and network. They sit behind a
`WindowSource` protocol with a default `RasterioWindowSource` (lazy `rasterio` import, GDAL
`/vsis3/` env from `rs_core.storage.gdal_s3_env`, same rasterio-1.4 credential handling as the
tiler). Tests inject a fake `WindowSource` returning synthetic DN arrays, so the adapter's logic
is fully unit-testable with zero network and zero `geo` extra, satisfying the testing discipline.

### 4. Resolution honesty and masking

`fetch` reads each band at the requested `resolution_m` (the caller groups indices by their
coarsest band's native resolution, so a 20 m index is read on a 20 m grid, never upsampled). SCL
is read at 20 m and resampled with nearest-neighbour only; reflectance bands resample with
bilinear. The per-AOI clear fraction and clear mask come from `rs_analysis.scl` over the
rasterised field polygon (invariant 3), never from the scene-level cloud percent.

### 5. Config switch, no downstream change

Selected by `RS_IMAGERY_ADAPTER=windowed_cog`. New config: `RS_CDSE_STAC_COLLECTION`,
`RS_CDSE_S3_ENDPOINT`, `RS_CDSE_S3_ACCESS_KEY`, `RS_CDSE_S3_SECRET_KEY`, `RS_CDSE_S3_REGION`. The
adapter constructs without network or credentials (it validates config lazily on first use), so
the registry can return it and unit tests can exercise its logic offline.

## To confirm against the live catalogue (`# ⚑ CONFIRM`)

The STAC standard fields (id, datetime, geometry, bbox, `eo:cloud_cover`, `assets[].href`) are
stable. The Sentinel-2-specific **asset key naming** (whether bands are keyed `B04`, `B04_10m`,
or similar) and the exact `eo:cloud_cover` query syntax must be confirmed against the live CDSE
STAC. The asset resolver is therefore a documented, overridable heuristic with a sensible default,
marked in code, until verified with real credentials. This is the one genuinely network-dependent
detail; everything else is tested offline.

## Consequences

- The platform can process real Sentinel-2 once CDSE credentials are configured. The
  long-standing D2/D6 blockers (real validation entries, 429 backoff exercised on real traffic)
  become reachable.
- The adapter and `server_compute` (T0.2) must agree on values for the same scene and formula;
  parity is asserted in the validation matrix (T0.3).
- Adding a second optical provider later is another adapter behind the same port, with zero
  downstream change, exactly as ADR 0001 intended.
