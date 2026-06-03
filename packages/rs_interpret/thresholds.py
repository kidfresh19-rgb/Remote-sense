"""Index interpretation bands: turn a numeric index value into an agronomic class label. This is
the grounding the interpretation layer is constrained to - the model classifies nothing itself, it
only explains the bands computed here (risk #6).

# ⚑ CONFIRM (agronomy review): v1 proposed 2026-06-03, pending agronomist sign-off. The base bands
are generic Zimbabwean-cropland defaults; CROP_BANDS adds per-crop overrides for maize, tobacco,
sorghum and cotton. Sources: per-index typical-range literature (maize peak NDVI ~0.75-0.85;
NDRE 0.1-0.6 nitrogen/chlorophyll band; NDMI EOS water-stress table; SAVI L=0.5 range compression),
crop canopy-architecture differences, and Zimbabwe agro-ecology (FAO natural regions, Nov-Apr
rainfed season). Bands are season-agnostic thresholds read against a single pass; growth-stage
context is carried in the notes and supplied to the model via the prompt, not encoded as separate
stage tables (that is the next calibration step). Owned by the agronomy-scientist."""

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
# (meaningful at higher biomass, at lower absolute values); NDMI tracks canopy moisture. Boundaries
# below are the generic cropland fallback; per-crop sets follow.
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

# --- Per-crop overrides ------------------------------------------------------------------------
# Each crop's vigour tuple is shared across NDVI/EVI2/SAVI (matching _BASE_BANDS). The numeric edges
# are NDVI-calibrated; for EVI2 (saturation-damped) and especially SAVI (L=0.5 range compression)
# they are approximate, so a SAVI value near a boundary should be read as indicative. Refining
# separate SAVI/EVI2 edges per crop is the next calibration step. # ⚑ CONFIRM.

# Maize (C4 staple, NR I-III): closes a dense canopy fast and peaks high (NDVI ~0.8-0.85 at
# tasseling/silking), so the canopy reaches "vigorous" sooner and a healthy crop sits in the top two
# bands. Thin canopy past mid-season (V6+) is a real stress/stand signal, not just early growth.
_MAIZE_VIGOUR: tuple[Band, ...] = (
    Band(0.2, "bare", "bare soil: pre-emergence, post-harvest, or a failed stand"),
    Band(0.35, "sparse", "sparse stand or early vegetative (pre-V6); past mid-season a stand gap"),
    Band(0.55, "developing", "canopy closing; mid-vegetative vigour"),
    Band(0.8, "vigorous", "vigorous canopy, typical of healthy maize from late vegetative to silk"),
    Band(None, "dense", "very dense canopy at/near peak biomass (tasseling-silking)"),
)
# Maize is nitrogen-hungry; NDRE at silking is the key N-status signal. Edges sit a touch above the
# generic mid bands because well-fertilised maize accumulates high leaf chlorophyll; the ~0.2-0.35
# "moderate" band is the bottom of the healthy envelope and below ~0.15 is genuine deficiency.
_MAIZE_NDRE: tuple[Band, ...] = (
    Band(0.15, "low", "low red-edge: probable nitrogen deficiency or thin canopy; watch at silk"),
    Band(0.35, "moderate", "moderate nitrogen / chlorophyll status"),
    Band(0.5, "good", "good nitrogen / chlorophyll status"),
    Band(None, "high", "high chlorophyll, typical of a well-fertilised vigorous crop"),
)

# Tobacco (flue-cured, NR II high-value): lush, dark, deliberately N-rich canopy that peaks high,
# then is topped and ripened (NDVI/NDRE fall on purpose). NDRE is the management-critical signal
# (N drives yield and leaf quality), so its bands are the most distinctive.
_TOBACCO_VIGOUR: tuple[Band, ...] = (
    Band(0.2, "bare", "bare soil: pre-transplant or post-reaping"),
    Band(0.4, "sparse", "recently transplanted or establishing canopy"),
    Band(0.6, "developing", "developing canopy approaching full leaf"),
    Band(0.82, "vigorous", "full, vigorous leaf canopy (pre-topping)"),
    Band(None, "dense", "very dense dark canopy near peak leaf"),
)
_TOBACCO_NDRE: tuple[Band, ...] = (
    Band(0.25, "low", "low red-edge: nitrogen short, or post-topping/ripening decline"),
    Band(0.4, "moderate", "moderate nitrogen / chlorophyll"),
    Band(0.55, "good", "high nitrogen, consistent with an actively growing pre-topping crop"),
    Band(None, "high", "very high leaf chlorophyll / nitrogen"),
)

# Sorghum (C4, drought-tolerant, NR III-V): water-saving and stay-green strategies mean a smaller,
# sparser canopy and a lower peak NDVI than maize, so vigour bands sit lower (a sorghum stand reads
# "vigorous" below where maize would). NDMI bands are more forgiving: low canopy moisture is part of
# its drought strategy, not necessarily acute stress.
_SORGHUM_VIGOUR: tuple[Band, ...] = (
    Band(0.2, "bare", "bare soil: pre-emergence or post-harvest"),
    Band(0.35, "sparse", "sparse or early canopy"),
    Band(0.5, "developing", "developing canopy with moderate vigour"),
    Band(0.7, "vigorous", "vigorous canopy for a drought-tolerant cereal"),
    Band(None, "dense", "dense canopy near peak biomass"),
)
_SORGHUM_NDMI: tuple[Band, ...] = (
    Band(-0.1, "dry", "low canopy moisture; in sorghum often water-saving, not acute stress"),
    Band(0.15, "moderate", "moderate canopy moisture"),
    Band(0.35, "adequate", "adequate canopy moisture"),
    Band(None, "wet", "high canopy moisture"),
)

# Cotton (row crop, NR II-III/V): slow canopy closure with bare inter-row soil for much of the
# season, so SAVI (soil-damped) is the more reliable vigour index and absolute NDVI peaks moderate
# (~0.7-0.8 at peak bloom/boll). Vigour bands sit a little lower and are flatter at the top.
_COTTON_VIGOUR: tuple[Band, ...] = (
    Band(0.2, "bare", "bare soil: pre-emergence, wide inter-row, or post-harvest"),
    Band(0.35, "sparse", "sparse early canopy with exposed inter-row soil (squaring)"),
    Band(0.55, "developing", "canopy developing toward row closure (flowering)"),
    Band(0.75, "vigorous", "vigorous canopy, typical at peak bloom / boll set"),
    Band(None, "dense", "dense closed canopy near peak biomass"),
)

CROP_BANDS: dict[str, dict[str, tuple[Band, ...]]] = {
    "maize": {
        "ndvi": _MAIZE_VIGOUR,
        "evi2": _MAIZE_VIGOUR,
        "savi": _MAIZE_VIGOUR,
        "ndre": _MAIZE_NDRE,
    },
    "tobacco": {
        "ndvi": _TOBACCO_VIGOUR,
        "evi2": _TOBACCO_VIGOUR,
        "savi": _TOBACCO_VIGOUR,
        "ndre": _TOBACCO_NDRE,
    },
    "sorghum": {
        "ndvi": _SORGHUM_VIGOUR,
        "evi2": _SORGHUM_VIGOUR,
        "savi": _SORGHUM_VIGOUR,
        "ndmi": _SORGHUM_NDMI,
    },
    "cotton": {
        "ndvi": _COTTON_VIGOUR,
        "evi2": _COTTON_VIGOUR,
        "savi": _COTTON_VIGOUR,
    },
}


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
