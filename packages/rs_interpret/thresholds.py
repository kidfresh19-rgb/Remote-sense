"""Index interpretation bands: turn a numeric index value into an agronomic class label. This is
the grounding the interpretation layer is constrained to - the model classifies nothing itself, it
only explains the bands computed here (risk #6).

# ⚑ CONFIRM (agronomy review): the bands below are conservative, generic cropland defaults for
Zimbabwean smallholder + commercial agriculture. Crop- and growth-stage-specific tuning (maize at
tasseling peaks higher than early-stage sorghum, tobacco and cotton differ again) is the
agronomist's to set via CROP_BANDS; until then every crop falls back to the base bands. Owned by
the agronomy-scientist."""

from __future__ import annotations

from dataclasses import dataclass

CROPS = ("maize", "tobacco", "sorghum", "cotton")


@dataclass(frozen=True)
class Band:
    """One classification band for an index: a value up to `upper` (inclusive) gets this label.
    `upper=None` marks the open-topped final band."""

    upper: float | None
    label: str
    note: str


# NDVI / EVI2 / SAVI measure canopy vigour and biomass; NDRE tracks chlorophyll / nitrogen status
# (meaningful at higher biomass, at lower absolute values); NDMI tracks canopy moisture.
_VIGOUR_BANDS: tuple[Band, ...] = (
    Band(0.2, "bare", "bare soil or non-vegetated: pre-emergence, post-harvest, or severe failure"),
    Band(0.4, "sparse", "sparse or early-stage canopy, or moisture/nutrient stress"),
    Band(0.6, "developing", "developing canopy with moderate vigour"),
    Band(0.8, "vigorous", "vigorous, well-developed canopy"),
    Band(None, "dense", "very dense canopy near peak biomass"),
)
_NDRE_BANDS: tuple[Band, ...] = (
    Band(0.1, "low", "low chlorophyll: possible nitrogen deficiency or low biomass"),
    Band(0.3, "moderate", "moderate chlorophyll / nitrogen status"),
    Band(0.5, "good", "good chlorophyll / nitrogen status"),
    Band(None, "high", "high chlorophyll content"),
)
_NDMI_BANDS: tuple[Band, ...] = (
    Band(0.0, "dry", "low canopy moisture: water stress likely"),
    Band(0.2, "moderate", "moderate canopy moisture"),
    Band(0.4, "adequate", "adequate canopy moisture"),
    Band(None, "wet", "high canopy moisture"),
)

_BASE_BANDS: dict[str, tuple[Band, ...]] = {
    "ndvi": _VIGOUR_BANDS,
    "evi2": _VIGOUR_BANDS,
    "savi": _VIGOUR_BANDS,
    "ndre": _NDRE_BANDS,
    "ndmi": _NDMI_BANDS,
}

# Per-crop overrides, empty until the agronomist tunes them. # ⚑ CONFIRM.
CROP_BANDS: dict[str, dict[str, tuple[Band, ...]]] = {crop: {} for crop in CROPS}


def bands_for(index: str, crop: str | None) -> tuple[Band, ...]:
    """The bands for an index, crop-tuned if an override exists, else the base set."""
    name = index.lower()
    if crop is not None:
        override = CROP_BANDS.get(crop.lower(), {}).get(name)
        if override:
            return override
    try:
        return _BASE_BANDS[name]
    except KeyError as exc:
        raise KeyError(f"no interpretation bands for index {index!r}") from exc


def classify(index: str, value: float, crop: str | None = None) -> Band:
    """The band a value falls in for an index (optionally crop-tuned)."""
    bands = bands_for(index, crop)
    for band in bands:
        if band.upper is None or value <= band.upper:
            return band
    return bands[-1]
