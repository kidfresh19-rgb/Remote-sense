"""Live adapter parity (PLAN §10 step 3, risk #5): windowed_cog (S3 reads + local offset) and
server_compute (the CDSE Process API) must agree on the AOI-mean index for the same real scene. The
offline structural guard is test_adapter_parity.py; this is the live numeric check.

Gated like the live validation matrix: opt in with RS_LIVE_VALIDATION=1 plus real RS_CDSE_* creds
and the geo extra. Skipped by default so the zero-network suite stays green. The two adapters render
on slightly different grids (UTM windowed read vs the Process API render), so the tolerance is a bit
looser than the validation matrix; the observed difference is ~0.006."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from rs_analysis import analyze_index
from rs_core.config import get_settings
from rs_imagery.types import AOI, TimeRange

pytestmark = pytest.mark.skipif(
    os.environ.get("RS_LIVE_VALIDATION") != "1",
    reason="live CDSE parity: set RS_LIVE_VALIDATION=1 (+ real RS_CDSE_* creds, geo extra)",
)

TOLERANCE = 0.02
_BANDS = ["B04", "B05", "B08", "B11"]
_INDICES = ("ndvi", "evi2", "savi", "ndre", "ndmi")
_SCENE = "S2A_MSIL2A_20260517T075021_N0512_R135_T36KTF_20260517T130615"
_AOI = AOI(
    geometry={
        "type": "Polygon",
        "coordinates": [
            [[31.00, -17.85], [31.06, -17.85], [31.06, -17.79], [31.00, -17.79], [31.00, -17.85]]
        ],
    }
)


def _scene_day(scene_id: str) -> datetime:
    return datetime.strptime(scene_id.split("_")[2][:8], "%Y%m%d").replace(tzinfo=UTC)


async def test_windowed_cog_and_server_compute_agree_live():
    pytest.importorskip("rasterio")
    from rs_imagery.adapters.server_compute import ServerComputeAdapter
    from rs_imagery.adapters.windowed_cog import WindowedCogAdapter

    settings = get_settings()
    if not (settings.cdse_stac_url and settings.cdse_s3_endpoint and settings.cdse_process_url):
        pytest.skip("CDSE STAC/S3/Process not configured")

    day = _scene_day(_SCENE)
    rng = TimeRange(start=day, end=day + timedelta(days=1))
    windowed = WindowedCogAdapter(settings)
    server = ServerComputeAdapter(settings)
    w_scenes = await windowed.search(_AOI, rng, max_scene_cloud_pct=80.0)
    s_scenes = await server.search(_AOI, rng, max_scene_cloud_pct=80.0)
    w_ref = next((s for s in w_scenes if s.scene_id == _SCENE), None)
    s_ref = next((s for s in s_scenes if s.scene_id == _SCENE), None)
    if w_ref is None or s_ref is None:
        pytest.skip(f"pinned scene {_SCENE} not in catalogue window")

    w = await windowed.fetch(w_ref, _AOI, bands=_BANDS, resolution_m=20.0)
    s = await server.fetch(s_ref, _AOI, bands=_BANDS, resolution_m=20.0)
    assert w.clear_fraction > 0.5 and s.clear_fraction > 0.5

    for index_name in _INDICES:
        ow = analyze_index(
            reflectance=w.data.bands,
            index_name=index_name,
            resolution_m=20,
            clear_fraction_override=w.clear_fraction,
        )
        os_ = analyze_index(
            reflectance=s.data.bands,
            index_name=index_name,
            resolution_m=20,
            clear_fraction_override=s.clear_fraction,
        )
        assert ow.stats.mean == pytest.approx(os_.stats.mean, abs=TOLERANCE), (
            f"{index_name}: windowed_cog {ow.stats.mean:.4f} vs server_compute {os_.stats.mean:.4f}"
        )
