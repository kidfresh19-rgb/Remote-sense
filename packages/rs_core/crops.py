"""The canonical declared-crop vocabulary for Ward Watch enrollment (PRD 0003 §8.3, backlog 0029).

Communal plots are intercropped, so a declared crop mix routinely names staples and legumes and
cucurbits the interpretation layer has no calibrated index bands for. This vocabulary is therefore
deliberately BROADER than `rs_interpret.thresholds.CROPS` (the four band-calibrated commercial crops
maize / tobacco / sorghum / cotton): those four are a subset here, and the rest are the common
Zimbabwe Nov-Apr rainfed intercrops an officer records but which we cannot yet phenology-verify or
threshold. Crop-mix entries validate against this set so an unknown crop is a loud rejection, never
free text silently stored (invariant: no free text for crops).

Pure data, zero deps - the foundational home so the model layer can validate without importing up
into `rs_interpret`. ⚑ CONFIRM (agronomy-scientist): the intercrop list below is a literature/local
starting set; confirm and extend it (and any name normalisation, e.g. plural or vernacular aliases)
before pilot enrollment."""

from __future__ import annotations

# Band-calibrated commercial/staple crops. These four mirror rs_interpret.thresholds.CROPS (the set
# with calibrated interpretation bands); kept in sync by review, not import, since the two sets mean
# different things (declared-for-enrollment here vs has-index-bands there).
COMMERCIAL_CROPS: tuple[str, ...] = ("maize", "tobacco", "sorghum", "cotton")

# Common communal intercrops (Zimbabwe smallholder, Nov-Apr rainfed). ⚑ CONFIRM with agronomy.
INTERCROPS: tuple[str, ...] = (
    "pearl_millet",
    "finger_millet",
    "groundnut",
    "bambara_nut",
    "cowpea",
    "sugar_bean",
    "soya_bean",
    "round_nut",
    "sunflower",
    "pumpkin",
    "squash",
    "sweet_potato",
    "sesame",
)

# The full declared-crop vocabulary: commercial staples plus communal intercrops.
DECLARED_CROPS: tuple[str, ...] = COMMERCIAL_CROPS + INTERCROPS

# Canonical-order rank, used as the deterministic tie-break when two crops share the top weight in a
# mix (commercial staples first, then the intercrop order above).
_CROP_RANK: dict[str, int] = {crop: index for index, crop in enumerate(DECLARED_CROPS)}


def normalize_crop(crop: str) -> str:
    """Canonical form of a declared crop name: trimmed and lowercased. Name aliasing (plurals,
    vernacular) is out of scope for v1 and deferred to the CONFIRM above."""
    return crop.strip().lower()


def is_declared_crop(crop: str) -> bool:
    """Whether `crop` (after normalisation) is in the declared-crop vocabulary."""
    return normalize_crop(crop) in _CROP_RANK


def crop_rank(crop: str) -> int:
    """Canonical-order rank of a declared crop, for deterministic dominant-crop tie-breaks. Raises
    KeyError for an unknown crop (callers validate membership first)."""
    return _CROP_RANK[normalize_crop(crop)]
