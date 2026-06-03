# ADR 0003 — The `server_compute` adapter, and one source of index math

- Status: accepted
- Date: 2026-06-03
- Phase: 4 (Preview & Live), Tier 0 of the improvement plan
- Builds on: ADR 0001 (ports and adapters), ADR 0002 (windowed_cog)

## Context

ADR 0002 landed `windowed_cog`, the stored-pipeline adapter where the engine owns the reflectance
and index math. The second real adapter, `server_compute`, drives the CDSE Process API: it can
render an index server-side for fast map previews and live tiles (PLAN §4). PLAN §4 originally said
that when an adapter computes server-side, "the engine stores rather than recomputes." Risk #5 warns
of the opposite hazard: if `server_compute` and `windowed_cog` ever disagree on a value, the stored
timeline mixes two truths.

## Decision

### 1. One source of index math; `server_compute` is the preview/live path

`server_compute` serves fast previews and live tiles. For stored values it does **not** compute the
index server-side and store the result; instead its `fetch` requests the **reflectance bands** from
the Process API, and the engine computes the index, exactly as for `windowed_cog`. The index formula
therefore has a single implementation (the engine), so the two real adapters cannot drift: parity is
structural, not merely asserted. This is a deliberate deviation from PLAN §4's "store rather than
recompute" wording, taken to retire risk #5. The server-side-render-and-store optimisation can be
revisited once live numeric parity is verified (the validation matrix, T0.3), and only then.

`preview` is the genuinely server-side path: the Process API renders a colorized index PNG (formula
plus the locked colormap) for the map. Previews are display artifacts, not stored analysis, so they
carry no drift risk.

### 2. Reflectance provenance

For `server_compute.fetch`, the Process API returns surface reflectance (it reads the same per-scene
radiometric metadata server-side), with SCL and a `dataMask` band for per-AOI masking (invariant 3).
`metadata` still reads the product `MTD_MSIL2A.xml` so the per-scene offset and quantification are
reportable (invariant 2). The exact Process API request body and the evalscripts are CDSE-specific
and are marked `# ⚑ CONFIRM`, the same boundary as the asset naming in ADR 0002; everything else is
tested offline.

### 3. Seams, so the logic is testable offline

HTTP to the Process API lives behind an injected `httpx.AsyncClient` (MockTransport in tests, with
429/5xx retry centralised here per invariant 1). Raster decoding of the returned GeoTIFF lives behind
a `RasterDecoder` seam (default `RasterioRasterDecoder`, geo extra, in-container); tests inject a fake
decoder. `search`/`metadata` reuse the shared `CdseStacClient` and the `cdse_metadata` parser.

## Consequences

- Swapping `windowed_cog` for `server_compute` (or back) never changes a stored value, because both
  feed the one engine. The validation matrix asserts the live numbers match the Copernicus Browser
  for whichever adapter is active (T0.3).
- `preview` gives the workspace fast, low-cost tiles without the stored COG path, complementing the
  tiler (which renders from already-stored COGs).
- The Process API wire format and evalscripts are the only `⚑ CONFIRM` items, confirmed against the
  live API with credentials. The adapter's logic ships tested.
