"""The live layer of the validation matrix (CLAUDE.md §3): real CDSE Sentinel-2 scenes over
Zimbabwe AOIs, asserting `windowed_cog`'s AOI-mean index values match the Copernicus Browser's own
engine (the CDSE Process API) within 0.01 on all five indices. This is the network + credential +
`geo`-extra gated companion to the offline `test_validation_matrix.py`.

The reference values were captured from the Process API with masking matched to
`rs_analysis.scl.CLEAR_CLASSES` ({4,5,6,7}) + the Process `dataMask`, computed at 20 m, so the
comparison is apples-to-apples with `windowed_cog`. Each row pins an immutable scene id, so the
`windowed_cog` side is deterministic and re-running reproduces the same numbers. The residual is
grid/resampling difference (UTM windowed read vs the geographic Process render), well under 0.01.

Opt in with `RS_LIVE_VALIDATION=1` plus real `RS_CDSE_*` creds and the `geo` extra. Skipped by
default so the zero-network suite stays green.

--- Comparison methodology: remote-sense vs Copernicus Browser ---

When comparing remote-sense statistics to the Copernicus Browser Statistical panel, four structural
differences will always produce different numbers -- they are not bugs:

1. Percentile bins: remote-sense stores p5/p10/p90/p95; the Browser shows p5/p95 only. The inner
   p10/p90 will naturally differ from the outer p5/p95 for any non-uniform distribution.

2. SCL masking set: remote-sense CLEAR_CLASSES = {4,5,6,7} (VEGETATION, NOT_VEGETATED, WATER,
   UNCLASSIFIED). The Copernicus Browser Statistical tool defaults to {4,5} only. Water and
   unclassified edge pixels included in our mask pull the mean slightly toward their lower-NDVI
   signal.

3. AOI polygon: the Browser shows statistics for the polygon drawn interactively. Unless the exact
   same GeoJSON is exported from remote-sense and imported into the Browser, the pixel populations
   will differ. Always export the field polygon from remote-sense for a valid comparison.

4. Mosaicking: server_compute uses `mosaickingOrder: mostRecent` in the Process API body. This
   selects the most recent acquisition within the day window rather than the least-cloudy one.
   windowed_cog pins to the exact scene id, which is always the source of truth.

--- Adding a row ---

1. Pick a near-cloudless scene over a Zimbabwe AOI (search the CDSE STAC catalogue for scene_id).
2. Draw the exact field polygon in Copernicus Browser; export as GeoJSON.
3. Run the Process API Statistical endpoint for each of the five indices with the evalscript below,
   masking on SCL {4,5,6,7} + dataMask, at 20 m output resolution. Record the AOI-mean for each.
4. Run windowed_cog.fetch() against the same scene + AOI and record the stats.mean for each index.
5. Add a row to LIVE_VALIDATION_MATRIX with browser_ref = Process API means, and a comment
   "wc: <windowed_cog values>" for reference.

The evalscript for the Process API Statistical request (one per index, same pattern as
`server_compute._bands_evalscript`), with the SCL exclusion filter baked in via dataMask:

  function setup() {
    return { input: ["B04","B08","SCL","dataMask"],
      output: { bands: 1, sampleType: "FLOAT32" } };
  }
  function evaluatePixel(s) {
    var clear = [4,5,6,7].includes(s.SCL) && s.dataMask > 0;
    return [clear ? (s.B08 - s.B04) / (s.B08 + s.B04) : NaN];
  }
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from rs_analysis import analyze_index
from rs_core.config import get_settings
from rs_imagery.types import AOI, TimeRange

pytestmark = pytest.mark.skipif(
    os.environ.get("RS_LIVE_VALIDATION") != "1",
    reason="live CDSE validation: set RS_LIVE_VALIDATION=1 (+ real RS_CDSE_* creds, geo extra)",
)

TOLERANCE = 0.01

# AOI-mean index values from the CDSE Process API (the Copernicus Browser engine), captured
# 2026-06-04. wc=<windowed_cog value observed at capture> is recorded in a comment for context.
LIVE_VALIDATION_MATRIX = [
    {
        "label": "harare_cropland",
        "scene_id": "S2A_MSIL2A_20260517T075021_N0512_R135_T36KTF_20260517T130615",
        "aoi": {
            "type": "Polygon",
            "coordinates": [
                [
                    [31.00, -17.85],
                    [31.06, -17.85],
                    [31.06, -17.79],
                    [31.00, -17.79],
                    [31.00, -17.85],
                ]
            ],
        },
        "browser_ref": {  # wc: ndvi=0.3800 evi2=0.1930 savi=0.2040 ndre=0.2418 ndmi=-0.0179
            "ndvi": 0.3856,
            "evi2": 0.1938,
            "savi": 0.2049,
            "ndre": 0.2460,
            "ndmi": -0.0152,
        },
    },
    {
        "label": "mazowe_valley",
        "scene_id": "S2A_MSIL2A_20260517T075021_N0512_R135_T36KTF_20260517T130615",
        "aoi": {
            "type": "Polygon",
            "coordinates": [
                [
                    [30.90, -17.70],
                    [30.96, -17.70],
                    [30.96, -17.64],
                    [30.90, -17.64],
                    [30.90, -17.70],
                ]
            ],
        },
        "browser_ref": {  # wc: ndvi=0.3804 evi2=0.1831 savi=0.1967 ndre=0.2190 ndmi=-0.1469
            "ndvi": 0.3815,
            "evi2": 0.1832,
            "savi": 0.1969,
            "ndre": 0.2199,
            "ndmi": -0.1463,
        },
    },
    {
        "label": "harare_cropland_2026-05-05",
        "scene_id": "S2C_MSIL2A_20260505T080601_N0512_R135_T36KTF_20260505T125413",
        "aoi": {
            "type": "Polygon",
            "coordinates": [
                [
                    [31.00, -17.85],
                    [31.06, -17.85],
                    [31.06, -17.79],
                    [31.00, -17.79],
                    [31.00, -17.85],
                ]
            ],
        },
        "browser_ref": {  # a different acquisition (S2C, 2026-05-05); windowed_cog within 0.006
            "ndvi": 0.4083,
            "evi2": 0.2079,
            "savi": 0.2189,
            "ndre": 0.2488,
            "ndmi": -0.0076,
        },
    },
]

_RESOLUTION_M = 20.0  # one fetch at the coarsest band's native resolution for all five indices


def _scene_day(scene_id: str) -> datetime:
    """The sensing day parsed from the S2 product id (e.g. ..._20260517T075021_...)."""
    stamp = scene_id.split("_")[2][:8]
    return datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=UTC)


@pytest.mark.parametrize(
    "entry", LIVE_VALIDATION_MATRIX, ids=[e["label"] for e in LIVE_VALIDATION_MATRIX]
)
async def test_windowed_cog_matches_browser(entry: dict) -> None:
    pytest.importorskip("rasterio")
    from rs_imagery.adapters.windowed_cog import WindowedCogAdapter

    settings = get_settings()
    if not settings.cdse_stac_url or not settings.cdse_s3_endpoint:
        pytest.skip("CDSE STAC/S3 not configured")

    adapter = WindowedCogAdapter(settings)
    aoi = AOI(geometry=entry["aoi"])
    day = _scene_day(entry["scene_id"])
    # The scene id is pinned; search the one-day window that contains it so the adapter caches the
    # scene's asset hrefs, then select that exact scene.
    # max_scene_cloud_pct is loose (the pinned scene is near-cloudless) but exercises the CQL2
    # cloud-filter path of the live STAC query end to end.
    scenes = await adapter.search(
        aoi, TimeRange(start=day, end=day + timedelta(days=1)), max_scene_cloud_pct=80.0
    )
    ref = next((s for s in scenes if s.scene_id == entry["scene_id"]), None)
    if ref is None:
        pytest.skip(f"pinned scene {entry['scene_id']} not in catalogue window")

    result = await adapter.fetch(
        ref, aoi, bands=["B04", "B05", "B08", "B11"], resolution_m=_RESOLUTION_M
    )
    assert result.clear_fraction > 0.5, "AOI too cloudy to validate against the Browser"

    # Compute via the production engine (the locked formula, _safe_ratio and clip_to_range), not a
    # hand-coded copy, so this validates the shipped index math. windowed_cog has already SCL-masked
    # the reflectance to NaN, so the scl=None path applies (zonal_stats drops NaN).
    for index_name, reference in entry["browser_ref"].items():
        out = analyze_index(
            reflectance=result.data.bands,
            index_name=index_name,
            resolution_m=int(_RESOLUTION_M),
        )
        assert out.stats.mean == pytest.approx(reference, abs=TOLERANCE), (
            f"{index_name} ({entry['label']}): windowed_cog {out.stats.mean:.4f} "
            f"vs Browser {reference:.4f}"
        )
