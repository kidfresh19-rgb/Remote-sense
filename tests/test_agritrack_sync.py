"""AgriTrack inbound sync mapping + auth gate (ADR 0006). Zero network, zero DB: the mapping is a
pure transform (their payload -> vendor-neutral FarmIn) and the key gate is checked against an
explicit Settings, so neither touches ingestion or the database."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from rs_core.config import Settings

from services.api.integrations import AgriTrackSyncIn, require_agritrack_key, to_farm_ins

_KEY = "atk_testkey0000000000000000000000"
_GEOM = {
    "type": "Polygon",
    "coordinates": [
        [
            [30.71358, -17.84649],
            [30.72, -17.84649],
            [30.72, -17.84],
            [30.71358, -17.84],
            [30.71358, -17.84649],
        ]
    ],
}


def _contract_sync() -> AgriTrackSyncIn:
    """The reconciliation-doc example payload."""
    return AgriTrackSyncIn.model_validate(
        {
            "farmer": {"agritrack_id": "2", "id": 2, "name": "Makuni", "email": "makuni@gmail.com"},
            "farms": [
                {
                    "farm_id": "2",
                    "farmer_id": "2",
                    "id": 2,
                    "name": "Makuni's Farm",
                    "location": "Norton",
                    "boundary": _GEOM,
                    "fields": [
                        {
                            "id": 4,
                            "field_id": "4",
                            "name": "Main Field",
                            "crop": None,
                            "area_ha": 4.14,
                            "boundary": _GEOM,
                            "sub_plots": [
                                {
                                    "id": 1,
                                    "plot_id": "1",
                                    "name": "Subfield 01 - North-West",
                                    "crop": None,
                                    "area_ha": 1.09,
                                    "boundary": _GEOM,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )


def test_to_farm_ins_maps_farm_field_and_subplot():
    farms = to_farm_ins(_contract_sync())
    assert len(farms) == 1
    farm = farms[0]
    assert farm.canonical_farm_id == "2"  # agritrack farm_id
    assert farm.agritrack_farmer_id == "2"  # agritrack farmer_id
    assert farm.name == "Makuni's Farm"
    assert farm.region == "Norton"  # location -> region
    assert farm.boundary == _GEOM
    # Field + sub-plot are both analysis units; the sub-plot id encodes its parent (ADR 0006).
    assert [f.canonical_field_id for f in farm.fields] == ["4", "4.1"]
    assert farm.fields[0].name == "Main Field"
    assert farm.fields[1].name == "Subfield 01 - North-West"
    assert farm.fields[1].geometry == _GEOM


def test_to_farm_ins_field_without_subplots():
    sync = AgriTrackSyncIn.model_validate(
        {
            "farms": [
                {
                    "farm_id": "9",
                    "boundary": _GEOM,
                    "fields": [{"field_id": "7", "boundary": _GEOM}],
                }
            ]
        }
    )
    farms = to_farm_ins(sync)
    assert [f.canonical_field_id for f in farms[0].fields] == ["7"]


def test_to_farm_ins_accepts_integer_ids():
    sync = AgriTrackSyncIn.model_validate(
        {
            "farms": [
                {
                    "farm_id": 2,
                    "boundary": _GEOM,
                    "fields": [
                        {
                            "field_id": 4,
                            "boundary": _GEOM,
                            "sub_plots": [{"plot_id": 1, "boundary": _GEOM}],
                        }
                    ],
                }
            ]
        }
    )
    farm = to_farm_ins(sync)[0]
    assert farm.canonical_farm_id == "2"
    assert [f.canonical_field_id for f in farm.fields] == ["4", "4.1"]


def test_to_farm_ins_handles_pre_prefixed_plot_id():
    sync = AgriTrackSyncIn.model_validate(
        {
            "farms": [
                {
                    "farm_id": "9",
                    "boundary": _GEOM,
                    "fields": [
                        {
                            "field_id": "7",
                            "boundary": _GEOM,
                            "sub_plots": [
                                {
                                    "plot_id": "7.1",
                                    "name": "Subfield 01",
                                    "boundary": _GEOM,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    farm = to_farm_ins(sync)[0]
    assert [f.canonical_field_id for f in farm.fields] == ["7", "7.1"]


def test_to_farm_ins_handles_null_boundaries():
    sync = AgriTrackSyncIn.model_validate(
        {
            "farms": [
                {
                    "farm_id": "9",
                    "boundary": _GEOM,
                    "fields": [
                        {
                            "field_id": "7",
                            "boundary": None,
                            "sub_plots": [
                                {
                                    "plot_id": "1",
                                    "name": "Subfield 01",
                                    "boundary": None,
                                },
                                {
                                    "plot_id": "2",
                                    "name": "Subfield 02",
                                    "boundary": _GEOM,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )
    farms = to_farm_ins(sync)
    assert len(farms) == 1
    farm = farms[0]
    # Both the field and subplot 1 should be skipped because they lack a boundary.
    # Only subplot 2 should be mapped as a FieldIn (with canonical_field_id="7.2")
    assert [f.canonical_field_id for f in farm.fields] == ["7.2"]
    assert farm.fields[0].name == "Subfield 02"
    assert farm.fields[0].geometry == _GEOM


async def test_require_agritrack_key_rejects_wrong_and_missing():
    settings = Settings(agritrack_api_key=_KEY)
    with pytest.raises(HTTPException) as wrong:
        await require_agritrack_key(x_api_key="atk_nope", settings=settings)
    assert wrong.value.status_code == 401
    with pytest.raises(HTTPException) as missing:
        await require_agritrack_key(x_api_key=None, settings=settings)
    assert missing.value.status_code == 401
    # The correct key passes (no exception raised).
    await require_agritrack_key(x_api_key=_KEY, settings=settings)


async def test_require_agritrack_key_500_when_unconfigured():
    with pytest.raises(HTTPException) as exc:
        await require_agritrack_key(x_api_key="anything", settings=Settings(agritrack_api_key=""))
    assert exc.value.status_code == 500
