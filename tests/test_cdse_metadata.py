"""Tests for the Sentinel-2 L2A product-metadata parser (invariant 2: offset + quantification read
per scene, never hard-coded). Pure: synthetic MTD XML in, SceneMetadata out, zero network."""

from __future__ import annotations

import pytest
from rs_imagery.adapters.cdse_metadata import parse_scene_metadata


def _mtd_xml(
    *,
    quantification: str | None = "10000",
    offsets: dict[int, float] | None = None,
    baseline: str = "05.00",
) -> bytes:
    if offsets is None:
        offsets = {i: -1000.0 for i in range(13)}
    offset_xml = "".join(
        f'<BOA_ADD_OFFSET band_id="{i}">{v:g}</BOA_ADD_OFFSET>' for i, v in offsets.items()
    )
    quant_xml = (
        f'<BOA_QUANTIFICATION_VALUE unit="none">{quantification}</BOA_QUANTIFICATION_VALUE>'
        if quantification is not None
        else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<n1:Level-2A_User_Product xmlns:n1="https://psd.example/PSD/L2A.xsd">'
        "<n1:General_Info>"
        f"<Product_Info><PROCESSING_BASELINE>{baseline}</PROCESSING_BASELINE></Product_Info>"
        "<Product_Image_Characteristics>"
        f"<QUANTIFICATION_VALUES_LIST>{quant_xml}</QUANTIFICATION_VALUES_LIST>"
        f"<BOA_ADD_OFFSET_VALUES_LIST>{offset_xml}</BOA_ADD_OFFSET_VALUES_LIST>"
        "</Product_Image_Characteristics>"
        "</n1:General_Info>"
        "</n1:Level-2A_User_Product>"
    ).encode()


def test_parses_quantification_and_per_band_offset():
    meta = parse_scene_metadata("S2_X", _mtd_xml())
    assert meta.scene_id == "S2_X"
    assert meta.quantification_value == 10000.0
    assert meta.processing_baseline == "05.00"
    # band_id index maps to the zero-padded band id used everywhere else.
    assert meta.boa_add_offset["B04"] == -1000.0  # band_id 3
    assert meta.boa_add_offset["B08"] == -1000.0  # band_id 7
    assert meta.boa_add_offset["B8A"] == -1000.0  # band_id 8
    assert meta.boa_add_offset["B11"] == -1000.0  # band_id 11


def test_band_id_mapping_is_not_off_by_one():
    # Distinct offsets per band so a mis-mapped index would surface as a wrong value.
    offsets = {3: -1000.0, 7: -1234.0, 4: -555.0}
    meta = parse_scene_metadata("S2_X", _mtd_xml(offsets=offsets))
    assert meta.boa_add_offset["B04"] == -1000.0  # id 3
    assert meta.boa_add_offset["B08"] == -1234.0  # id 7
    assert meta.boa_add_offset["B05"] == -555.0  # id 4


def test_missing_quantification_raises():
    with pytest.raises(ValueError, match="BOA_QUANTIFICATION_VALUE"):
        parse_scene_metadata("S2_X", _mtd_xml(quantification=None))


def test_no_offset_list_is_pre_baseline_zero_offset():
    # A product with no BOA_ADD_OFFSET entries is pre-04.00: the offset is 0 by definition.
    meta = parse_scene_metadata("S2_OLD", _mtd_xml(offsets={}))
    assert meta.quantification_value == 10000.0
    assert meta.boa_add_offset["B04"] == 0.0
    assert meta.boa_add_offset["B08"] == 0.0


def test_malformed_offset_value_raises():
    bad = (
        b'<?xml version="1.0"?>'
        b"<Level-2A_User_Product>"
        b"<QUANTIFICATION_VALUES_LIST>"
        b"<BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE>"
        b"</QUANTIFICATION_VALUES_LIST>"
        b'<BOA_ADD_OFFSET band_id="3">not-a-number</BOA_ADD_OFFSET>'
        b"</Level-2A_User_Product>"
    )
    with pytest.raises(ValueError, match="BOA_ADD_OFFSET"):
        parse_scene_metadata("S2_X", bad)


def test_malformed_xml_raises():
    with pytest.raises(ValueError, match="malformed product metadata XML"):
        parse_scene_metadata("S2_X", b"<not xml")
