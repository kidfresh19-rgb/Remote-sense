"""Sentinel-2 band identifiers and their native ground resolution. Band ids are the
zero-padded form the adapters use ("B04", "B08", ...). Resolution honesty (CLAUDE.md
invariant 4) starts here: an index is computed at the coarsest native resolution of its
bands, never by upsampling a 20 m band to 10 m."""

from __future__ import annotations

# Native resolution in metres for the bands remote-sense uses. SCL is delivered at 20 m.
BAND_RESOLUTION_M: dict[str, int] = {
    "B02": 10,
    "B03": 10,
    "B04": 10,
    "B08": 10,
    "B05": 20,
    "B06": 20,
    "B07": 20,
    "B8A": 20,
    "B11": 20,
    "B12": 20,
    "SCL": 20,
}


def coarsest_resolution_m(bands: tuple[str, ...]) -> int:
    """The native resolution an index made of these bands must be computed at: the coarsest
    (largest metres) of them. Computing here, rather than upsampling, keeps output honest."""
    try:
        return max(BAND_RESOLUTION_M[b] for b in bands)
    except KeyError as exc:
        raise ValueError(f"unknown band {exc.args[0]!r}") from exc
