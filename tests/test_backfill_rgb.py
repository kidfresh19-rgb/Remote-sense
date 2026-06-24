"""Unit tests for the RGB COG backfill geometry resolution (backlog 0024, invariant 5 / DI-5).

No DB, no broker, no MinIO: the async session is faked and geometries round-trip through
geoalchemy2's pure WKB helpers, so this runs on a bare host. The contract under test is that the
backfill fetches bands against the boundary the pass was computed with (the FieldGeometryVersion
row), not the field's current outline, so an RGB COG written at an old geometry_version still lines
up with the index COGs already stored there.
"""

from __future__ import annotations

import uuid

import pytest


def _wkb(coords: list[tuple[float, float]]):
    """A geoalchemy2 WKBElement for a polygon, built without touching a database."""
    pytest.importorskip("geoalchemy2")
    pytest.importorskip("shapely")
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Polygon

    return from_shape(Polygon(coords), srid=4326)


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Session:
    """Returns the queued results in order and records how many queries ran."""

    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)
        self.calls = 0

    async def execute(self, _stmt: object) -> _Result:
        self.calls += 1
        return self._results.pop(0)


# A polygon near 31 E (the historical version) and one near 32 E (the field's current outline).
_VERSION_BOX = [(31.0, -17.8), (31.0, -17.81), (31.01, -17.81), (31.01, -17.8), (31.0, -17.8)]
_CURRENT_BOX = [(32.0, -18.0), (32.0, -18.01), (32.01, -18.01), (32.01, -18.0), (32.0, -18.0)]


async def test_resolve_boundary_prefers_versioned_geometry() -> None:
    """The boundary at geometry_version N comes from the immutable FieldGeometryVersion row, not
    the field's current boundary, so a reshaped field backfills against the geometry the pass used.
    One query is enough: the version row hits, the current-boundary fallback is never reached."""
    from services.worker.tasks.backfill_rgb import _resolve_boundary

    session = _Session([_Result(_wkb(_VERSION_BOX))])
    geom = await _resolve_boundary(session, uuid.uuid4(), geometry_version=3)

    assert session.calls == 1
    assert geom["type"] == "Polygon"
    assert geom["coordinates"][0][0][0] == pytest.approx(31.0)  # the 31 E version, not 32 E current


async def test_resolve_boundary_falls_back_to_current_when_no_version_row() -> None:
    """Pre-history legacy data with no FieldGeometryVersion row falls back to the current boundary
    (a second query), so the backfill still produces a COG rather than failing the pass."""
    from services.worker.tasks.backfill_rgb import _resolve_boundary

    session = _Session([_Result(None), _Result(_wkb(_CURRENT_BOX))])
    geom = await _resolve_boundary(session, uuid.uuid4(), geometry_version=1)

    assert session.calls == 2  # tried the version row, then the field
    assert geom["coordinates"][0][0][0] == pytest.approx(32.0)


async def test_resolve_boundary_raises_when_field_missing() -> None:
    """No version row and no field at all is a hard miss: raise LookupError so the task reports the
    pass as unrecoverable instead of writing a COG for a geometry it could not resolve."""
    from services.worker.tasks.backfill_rgb import _resolve_boundary

    session = _Session([_Result(None), _Result(None)])
    with pytest.raises(LookupError):
        await _resolve_boundary(session, uuid.uuid4(), geometry_version=1)
