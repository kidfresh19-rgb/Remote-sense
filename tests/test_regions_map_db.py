"""PostGIS-backed tests for the workspace map's region-boundary reads (PRD 0002 slices 8a/8b): the
layer list with per-layer kind + count, and one layer's boundaries as a GeoJSON FeatureCollection.
These back the toggleable Natural Region and uploaded-boundary map overlays. SKIPS when no database
is reachable (prior art test_regions_db.py); runs for real in CI / `docker compose`."""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from rs_core import Principal, Role
from rs_core.db import Base
from rs_core.regions import ParsedRegionFeature
from rs_core.repositories.regions import create_uploaded_layer, seed_natural_regions
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.api.workspace import (
    list_region_layers_endpoint,
    region_layer_boundaries_endpoint,
)

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_VIEW = Principal(subject="analyst-map", roles=frozenset({Role.ANALYST}))


def _rect_mp(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> MultiPolygon:
    return MultiPolygon(
        [
            Polygon(
                [
                    (min_lon, min_lat),
                    (max_lon, min_lat),
                    (max_lon, max_lat),
                    (min_lon, max_lat),
                    (min_lon, min_lat),
                ]
            )
        ]
    )


@pytest_asyncio.fixture
async def maker_():
    engine = create_async_engine(_TEST_DB_URL, connect_args={"connect_timeout": 2})
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # no DB reachable -> skip the whole DB-backed suite
        await engine.dispose()
        pytest.skip(f"PostGIS not reachable at {_TEST_DB_URL}: {exc}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed_natural_region(maker) -> uuid.UUID:
    async with maker() as session:
        layer = await seed_natural_regions(
            session,
            name="Zimbabwe NR (test)",
            source="test-custodian",
            year=2020,
            version="v1",
            crs="EPSG:4326",
            features=[
                ParsedRegionFeature("Region A", _rect_mp(29.0, -18.0, 31.0, -16.0), "EPSG:4326"),
                ParsedRegionFeature("Region B", _rect_mp(31.0, -18.0, 33.0, -16.0), "EPSG:4326"),
            ],
        )
        await session.commit()
        return layer.id


async def _add_uploaded_layer(maker) -> uuid.UUID:
    async with maker() as session:
        layer = await create_uploaded_layer(
            session,
            name="wards.geojson",
            features=[
                ParsedRegionFeature("Ward 1", _rect_mp(29.5, -17.5, 30.5, -16.5), "EPSG:4326")
            ],
            creator="analyst-map",
            nr_polygons=[],
        )
        await session.commit()
        return layer.id


async def test_layer_list_reports_kind_and_count_newest_first(maker_) -> None:
    nr_id = await _seed_natural_region(maker_)
    uploaded_id = await _add_uploaded_layer(maker_)
    async with maker_() as session:
        layers = await list_region_layers_endpoint(principal=_VIEW, session=session)
    by_id = {item.layer_id: item for item in layers}

    nr = by_id[str(nr_id)]
    assert nr.kind == "seeded"
    assert nr.read_only is True
    assert nr.region_count == 2
    assert nr.custodian == "test-custodian"

    uploaded = by_id[str(uploaded_id)]
    assert uploaded.kind == "uploaded"
    assert uploaded.read_only is False
    assert uploaded.region_count == 1

    # Newest first: the uploaded layer was created after the seed.
    assert layers[0].layer_id == str(uploaded_id)


async def test_boundaries_endpoint_returns_feature_collection(maker_) -> None:
    nr_id = await _seed_natural_region(maker_)
    async with maker_() as session:
        fc = await region_layer_boundaries_endpoint(
            layer_id=nr_id, principal=_VIEW, session=session
        )
    assert fc.type == "FeatureCollection"
    assert {f["properties"]["name"] for f in fc.features} == {"Region A", "Region B"}
    for feature in fc.features:
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        assert feature["properties"]["source"] == "seeded"
        assert feature["properties"]["layer_id"] == str(nr_id)


async def test_boundaries_endpoint_404_for_unknown_layer(maker_) -> None:
    await _seed_natural_region(maker_)
    async with maker_() as session:
        with pytest.raises(HTTPException) as exc:
            await region_layer_boundaries_endpoint(
                layer_id=uuid.uuid4(), principal=_VIEW, session=session
            )
    assert exc.value.status_code == 404
