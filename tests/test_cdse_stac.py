"""Tests for the CDSE STAC search client. Zero network: an injected httpx.MockTransport plays the
catalogue. Covers request shaping, FeatureCollection parsing, chronological ordering, transient
retry, and the band/metadata asset resolvers."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from rs_core.config import Settings
from rs_imagery.adapters.cdse_stac import (
    CdseStacClient,
    parse_item,
    resolve_band_asset,
    resolve_metadata_href,
)
from rs_imagery.types import AOI, TimeRange

_STAC_URL = "https://catalogue.example/stac"
_AOI = AOI(
    geometry={
        "type": "Polygon",
        "coordinates": [
            [[30.0, -17.9], [30.1, -17.9], [30.1, -18.0], [30.0, -18.0], [30.0, -17.9]]
        ],
    }
)
_RANGE = TimeRange(start=datetime(2023, 1, 1, tzinfo=UTC), end=datetime(2023, 3, 1, tzinfo=UTC))


def _settings() -> Settings:
    return Settings(cdse_stac_url=_STAC_URL, cdse_stac_collection="sentinel-2-l2a")


def _feature(scene_id: str, dt: str, cloud: float) -> dict:
    return {
        "id": scene_id,
        "type": "Feature",
        "geometry": _AOI.geometry,
        "properties": {"datetime": dt, "eo:cloud_cover": cloud, "productType": "S2MSI2A"},
        "assets": {
            "B04": {"href": f"s3://eodata/{scene_id}/B04_10m.jp2"},
            "B08": {"href": f"s3://eodata/{scene_id}/B08_10m.jp2"},
            "SCL": {"href": f"s3://eodata/{scene_id}/SCL_20m.jp2"},
            "product_metadata": {"href": f"s3://eodata/{scene_id}/MTD_MSIL2A.xml"},
        },
    }


def _transport(responses: list[httpx.Response]) -> tuple[httpx.MockTransport, list[dict]]:
    bodies: list[dict] = []
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode()))
        return remaining.pop(0)

    return httpx.MockTransport(handler), bodies


def _client(responses: list[httpx.Response]) -> tuple[CdseStacClient, list[dict]]:
    transport, bodies = _transport(responses)
    http = httpx.AsyncClient(transport=transport)
    client = CdseStacClient(
        _settings(), client=http, wait_min=0.0, wait_max=0.0, wait_multiplier=0.0
    )
    return client, bodies


def test_missing_stac_url_raises():
    with pytest.raises(ValueError):
        CdseStacClient(Settings())


async def test_search_returns_scenes_sorted_chronologically():
    fc = {
        "type": "FeatureCollection",
        "features": [
            _feature("S2_B", "2023-02-10T08:00:00Z", 20.0),
            _feature("S2_A", "2023-01-05T08:00:00Z", 5.0),
        ],
    }
    client, _ = _client([httpx.Response(200, json=fc)])
    scenes = await client.search_items(_AOI, _RANGE)
    assert [s.scene_id for s in scenes] == ["S2_A", "S2_B"]  # oldest first
    assert scenes[0].sensing_datetime == datetime(2023, 1, 5, 8, 0, tzinfo=UTC)
    assert scenes[0].cloud_cover == 5.0
    assert scenes[0].to_scene_ref().provider == "cdse"


def test_default_stac_collection_is_l2a():
    # The CDSE STAC v1 API keys the Sentinel-2 L2A collection `sentinel-2-l2a`, not the older
    # OData `SENTINEL-2` product-type name; a wrong default queries the wrong collection.
    # _env_file=None so this asserts the code default, not a developer's populated local .env.
    assert Settings(_env_file=None).cdse_stac_collection == "sentinel-2-l2a"


async def test_search_body_uses_collection_datetime_intersects_and_cql2_cloud_filter():
    fc = {"type": "FeatureCollection", "features": []}
    client, bodies = _client([httpx.Response(200, json=fc)])
    await client.search_items(_AOI, _RANGE, max_scene_cloud_pct=60.0)
    body = bodies[0]
    assert body["collections"] == ["sentinel-2-l2a"]
    assert body["datetime"] == "2023-01-01T00:00:00Z/2023-03-01T00:00:00Z"
    assert body["intersects"]["type"] == "Polygon"
    # No OData productType filter (it returns nothing against the STAC API); the collection already
    # restricts to L2A. Cloud cover is a coarse CQL2 pre-filter only (per-AOI SCL is authoritative).
    assert "query" not in body
    assert body["filter-lang"] == "cql2-json"
    assert body["filter"] == {"op": "<=", "args": [{"property": "eo:cloud_cover"}, 60.0]}


async def test_search_body_omits_cloud_filter_when_unset():
    fc = {"type": "FeatureCollection", "features": []}
    client, bodies = _client([httpx.Response(200, json=fc)])
    await client.search_items(_AOI, _RANGE)
    body = bodies[0]
    assert "filter" not in body
    assert "query" not in body


async def test_search_retries_transient_then_succeeds():
    fc = {"type": "FeatureCollection", "features": [_feature("S2_A", "2023-01-05T08:00:00Z", 5.0)]}
    client, bodies = _client([httpx.Response(429), httpx.Response(200, json=fc)])
    scenes = await client.search_items(_AOI, _RANGE)
    assert len(scenes) == 1
    assert len(bodies) == 2  # the 429 was retried


def test_parse_item_skips_feature_without_id_or_datetime():
    assert parse_item({"properties": {"datetime": "2023-01-01T00:00:00Z"}}) is None  # no id
    assert parse_item({"id": "x", "properties": {}}) is None  # no datetime


def test_resolve_band_asset_handles_key_schemes():
    assert resolve_band_asset({"B04": {"href": "a"}}, "B04", 10) == "a"
    assert resolve_band_asset({"B04_10m": {"href": "b"}}, "B04", 10) == "b"
    # Fallback: scan hrefs for the band token.
    assert resolve_band_asset({"red": {"href": "x/B04.tif"}}, "B04", 10) == "x/B04.tif"
    with pytest.raises(KeyError):
        resolve_band_asset({"B08": {"href": "c"}}, "B04", 10)


def test_resolve_metadata_href():
    assert resolve_metadata_href({"product_metadata": {"href": "m"}}) == "m"
    assert resolve_metadata_href({"x": {"href": "p/MTD_MSIL2A.xml"}}) == "p/MTD_MSIL2A.xml"
    with pytest.raises(KeyError):
        resolve_metadata_href({"B04": {"href": "a"}})
