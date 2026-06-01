"""The spectral index suite, locked by formula + band ids + version so a stored index name is
unambiguous and reproducible (PLAN §5, rule 6). Adding an index is a change here, not a schema
migration - the zonal-stats row is index-agnostic.

All formulas run on surface reflectance (never DN). Decisions baked in: EVI2 over classic EVI
(drops the blue band, removing an L2A atmospheric-noise source); NDRE locked to B08+B05 (never
mix B07/B8A variants within a series). Situational indices (GNDVI, NDWI-water, BSI) are
deliberately omitted here - their formula must travel with the name when added on demand."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import numpy as np

from rs_analysis.bands import coarsest_resolution_m


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """Element-wise division that yields NaN (not inf) where the denominator is zero, and
    propagates input NaN (masked / NoData pixels stay masked)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = numerator / denominator
    return np.where(np.isfinite(out), out, np.nan)


def _ndvi(b: Mapping[str, np.ndarray]) -> np.ndarray:
    return _safe_ratio(b["B08"] - b["B04"], b["B08"] + b["B04"])


def _evi2(b: Mapping[str, np.ndarray]) -> np.ndarray:
    nir, red = b["B08"], b["B04"]
    return _safe_ratio(2.5 * (nir - red), nir + 2.4 * red + 1.0)


def _savi(b: Mapping[str, np.ndarray]) -> np.ndarray:
    nir, red = b["B08"], b["B04"]
    return _safe_ratio(nir - red, nir + red + 0.5) * 1.5


def _ndre(b: Mapping[str, np.ndarray]) -> np.ndarray:
    return _safe_ratio(b["B08"] - b["B05"], b["B08"] + b["B05"])


def _ndmi(b: Mapping[str, np.ndarray]) -> np.ndarray:
    return _safe_ratio(b["B08"] - b["B11"], b["B08"] + b["B11"])


@dataclass(frozen=True)
class IndexSpec:
    """A locked index definition. `formula_version` travels into every stored result's
    provenance; bump it whenever the math or band selection changes."""

    name: str
    bands: tuple[str, ...]
    valid_range: tuple[float, float]
    formula_version: str
    _fn: Callable[[Mapping[str, np.ndarray]], np.ndarray]

    @property
    def resolution_m(self) -> int:
        """Native resolution this index is computed at: the coarsest of its bands."""
        return coarsest_resolution_m(self.bands)

    def compute(self, reflectance: Mapping[str, np.ndarray]) -> np.ndarray:
        """Compute the raw (unclipped) index from a dict of reflectance band arrays."""
        missing = [band for band in self.bands if band not in reflectance]
        if missing:
            raise KeyError(f"{self.name}: missing reflectance bands {missing}")
        return self._fn(reflectance)


# Core indices, stored every usable pass (PLAN §5). Band ids in zero-padded form.
INDICES: dict[str, IndexSpec] = {
    "ndvi": IndexSpec("ndvi", ("B08", "B04"), (-1.0, 1.0), "1", _ndvi),
    "evi2": IndexSpec("evi2", ("B08", "B04"), (-1.0, 1.0), "1", _evi2),
    "savi": IndexSpec("savi", ("B08", "B04"), (-1.5, 1.5), "1", _savi),
    "ndre": IndexSpec("ndre", ("B08", "B05"), (-1.0, 1.0), "1", _ndre),
    "ndmi": IndexSpec("ndmi", ("B08", "B11"), (-1.0, 1.0), "1", _ndmi),
}


def get_index(name: str) -> IndexSpec:
    try:
        return INDICES[name.lower()]
    except KeyError as exc:
        raise KeyError(f"unknown index {name!r}; known: {sorted(INDICES)}") from exc


def clip_to_range(arr: np.ndarray, low: float, high: float) -> np.ndarray:
    """Clip values to an index's valid range. np.clip preserves NaN, so masked pixels stay
    masked and are excluded from statistics downstream (PLAN §5, rule 5)."""
    return np.clip(arr, low, high)
