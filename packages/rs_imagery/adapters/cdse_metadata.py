"""Parse the Sentinel-2 L2A product metadata (`MTD_MSIL2A.xml`) for the per-scene radiometric
values invariant 2 requires: the BOA quantification value and the per-band BOA additive offset.

These are read per scene, never hard-coded (CLAUDE.md invariant 2, the single highest-risk
correctness rule in the system). Pure: XML bytes in, `SceneMetadata` out, no network. The
windowed_cog adapter fetches the XML through its `WindowSource` seam and hands the bytes here."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from rs_imagery.types import SceneMetadata

# MTD `BOA_ADD_OFFSET` entries are keyed by `band_id`, an index into the Sentinel-2 band order.
# This maps that index to the zero-padded band id the rest of the system uses. SCL is a
# classification layer, not a reflectance band, so it has no offset and is absent here.
_BAND_BY_ID: dict[int, str] = {
    0: "B01",
    1: "B02",
    2: "B03",
    3: "B04",
    4: "B05",
    5: "B06",
    6: "B07",
    7: "B08",
    8: "B8A",
    9: "B09",
    10: "B10",
    11: "B11",
    12: "B12",
}


def _localname(tag: str) -> str:
    """A tag without its XML namespace (`{ns}Tag` -> `Tag`). The S2 MTD wraps the root in an
    `n1:` namespace; matching on the local name keeps the parser robust to it."""
    return tag.rsplit("}", 1)[-1]


def _find_local(root: ET.Element, name: str) -> ET.Element | None:
    for el in root.iter():
        if _localname(el.tag) == name:
            return el
    return None


def _findall_local(root: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in root.iter() if _localname(el.tag) == name]


def parse_scene_metadata(
    scene_id: str, xml_bytes: bytes, *, crs: str = "EPSG:4326"
) -> SceneMetadata:
    """Build `SceneMetadata` from `MTD_MSIL2A.xml` bytes.

    Raises `ValueError` if the quantification value is missing or unparseable. A product with no
    `BOA_ADD_OFFSET` list at all is a pre-baseline-04.00 product whose offset is 0 by definition,
    so every band gets 0.0. Offsets that are present but malformed are an error: we never guess a
    radiometric value (invariant 2)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"{scene_id}: malformed product metadata XML: {exc}") from exc

    quant_el = _find_local(root, "BOA_QUANTIFICATION_VALUE")
    if quant_el is None or quant_el.text is None:
        raise ValueError(f"{scene_id}: BOA_QUANTIFICATION_VALUE missing from product metadata")
    try:
        quantification = float(quant_el.text.strip())
    except ValueError as exc:
        raise ValueError(
            f"{scene_id}: BOA_QUANTIFICATION_VALUE not numeric: {quant_el.text!r}"
        ) from exc

    offsets: dict[str, float] = {}
    for el in _findall_local(root, "BOA_ADD_OFFSET"):
        raw_id = el.get("band_id")
        if raw_id is None or el.text is None:
            continue
        try:
            band_index = int(raw_id)
            value = float(el.text.strip())
        except ValueError as exc:
            raise ValueError(
                f"{scene_id}: malformed BOA_ADD_OFFSET (band_id={raw_id!r}, value={el.text!r})"
            ) from exc
        band = _BAND_BY_ID.get(band_index)
        if band is not None:
            offsets[band] = value

    # No offset list at all -> a pre-04.00 product; the offset is 0 for every band by definition.
    if not offsets:
        offsets = {band: 0.0 for band in _BAND_BY_ID.values()}

    baseline_el = _find_local(root, "PROCESSING_BASELINE")
    baseline = baseline_el.text.strip() if baseline_el is not None and baseline_el.text else None

    return SceneMetadata(
        scene_id=scene_id,
        quantification_value=quantification,
        boa_add_offset=offsets,
        processing_baseline=baseline,
        crs=crs,
    )
