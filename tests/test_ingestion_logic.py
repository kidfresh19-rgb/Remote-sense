"""Ingestion decision-logic tests that need no database: boundary resolution (declared vs
derived vs neither, DI-4), the nesting guard (DI-3), polygon normalisation, the
canonical/spatial matching used for idempotency, and the Pydantic contract validators."""

from __future__ import annotations

import uuid

import pytest
from geoalchemy2.shape import from_shape
from pydantic import ValidationError
from rs_core.geo import to_shape
from rs_core.models import Farm, Field
from rs_core.schemas import FarmIn, FieldIn
from sqlalchemy.exc import IntegrityError

from services.api.ingestion import (
    IngestionError,
    _as_multipolygon,
    _check_nesting,
    _match_existing,
    _resolve_farm_boundary,
    _validate_fields,
    get_or_create_farm,
    get_or_create_field,
)


def _square(center_lon: float, center_lat: float, side_deg: float) -> dict:
    half = side_deg / 2
    lon, lat = center_lon, center_lat
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - half, lat - half],
                [lon + half, lat - half],
                [lon + half, lat + half],
                [lon - half, lat + half],
                [lon - half, lat - half],
            ]
        ],
    }


_HARARE = (31.05, -17.83)


def _stored_field(geom_dict: dict, canonical_field_id: str | None) -> Field:
    return Field(
        canonical_field_id=canonical_field_id,
        boundary=from_shape(_as_multipolygon(to_shape(geom_dict)), srid=4326),
    )


def test_as_multipolygon_promotes_polygon() -> None:
    mp = _as_multipolygon(to_shape(_square(*_HARARE, 0.01)))
    assert mp.geom_type == "MultiPolygon"


def test_resolve_uses_declared_boundary() -> None:
    payload = FarmIn(canonical_farm_id="F1", boundary=_square(*_HARARE, 0.02), fields=[])
    geom = _resolve_farm_boundary(payload, [])
    assert geom.area > 0


def test_resolve_derives_boundary_from_fields() -> None:
    payload = FarmIn(
        canonical_farm_id="F2",
        fields=[
            FieldIn(canonical_field_id="a", geometry=_square(31.04, -17.83, 0.01)),
            FieldIn(canonical_field_id="b", geometry=_square(31.06, -17.83, 0.01)),
        ],
    )
    validated = _validate_fields(payload)
    geom = _resolve_farm_boundary(payload, validated)
    # The union must cover both field centres.
    assert geom.covers(to_shape(_square(31.04, -17.83, 0.001)))
    assert geom.covers(to_shape(_square(31.06, -17.83, 0.001)))


def test_resolve_rejects_empty_farm() -> None:
    payload = FarmIn(canonical_farm_id="F3", boundary=None, fields=[])
    with pytest.raises(IngestionError, match="nothing to analyse"):
        _resolve_farm_boundary(payload, [])


def test_validate_fields_raises_on_bad_geometry() -> None:
    payload = FarmIn(
        canonical_farm_id="F4",
        boundary=_square(*_HARARE, 0.02),
        fields=[FieldIn(canonical_field_id="x", geometry=_square(*_HARARE, 1e-8))],
    )
    with pytest.raises(IngestionError):
        _validate_fields(payload)


def test_check_nesting_rejects_field_outside_farm() -> None:
    payload = FarmIn(
        canonical_farm_id="F5",
        boundary=_square(*_HARARE, 0.02),
        fields=[FieldIn(canonical_field_id="far", geometry=_square(31.5, -17.83, 0.01))],
    )
    validated = _validate_fields(payload)
    farm_geom = _resolve_farm_boundary(payload, validated)
    with pytest.raises(IngestionError, match="not nested"):
        _check_nesting(validated, farm_geom, derived_from_farm=False)


def test_check_nesting_skipped_when_derived() -> None:
    payload = FarmIn(
        canonical_farm_id="F6",
        fields=[FieldIn(canonical_field_id="a", geometry=_square(*_HARARE, 0.01))],
    )
    validated = _validate_fields(payload)
    farm_geom = _resolve_farm_boundary(payload, validated)
    # Should not raise: the boundary was derived from the fields, so nesting is trivial.
    _check_nesting(validated, farm_geom, derived_from_farm=True)


def test_match_existing_by_canonical_id() -> None:
    f = _stored_field(_square(*_HARARE, 0.01), "field-7")
    by_canonical = {"field-7": f}
    matched = _match_existing("field-7", to_shape(_square(*_HARARE, 0.01)), by_canonical, [])
    assert matched is f


def test_match_existing_falls_back_to_geometry() -> None:
    f = _stored_field(_square(*_HARARE, 0.01), None)
    matched = _match_existing(None, to_shape(_square(*_HARARE, 0.01)), {}, [f])
    assert matched is f


def test_match_existing_returns_none_when_no_match() -> None:
    f = _stored_field(_square(*_HARARE, 0.01), "field-8")
    matched = _match_existing("field-9", to_shape(_square(*_HARARE, 0.01)), {"field-8": f}, [])
    assert matched is None


def test_schema_rejects_non_polygon_field() -> None:
    with pytest.raises(ValidationError):
        FieldIn(geometry={"type": "LineString", "coordinates": [[0, 0], [1, 1]]})


def test_schema_rejects_non_polygon_boundary() -> None:
    with pytest.raises(ValidationError):
        FarmIn(canonical_farm_id="F", boundary={"type": "Point", "coordinates": [0, 0]})


def test_schema_requires_canonical_farm_id() -> None:
    with pytest.raises(ValidationError):
        FarmIn(canonical_farm_id="")


# --- D8: concurrent first-create hardening (no DB; the savepoint/IntegrityError recovery branch
# of get_or_create_farm is verified with a fake session, the live path in test_ingestion_db). ---


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Nested:
    async def __aenter__(self) -> _Nested:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False  # propagate the IntegrityError, like a real savepoint rollback


class _RacingSession:
    """A session that loses a concurrent first-create: the pre-insert fetch finds nothing, the
    savepoint flush raises IntegrityError (a competitor inserted the same canonical id), and the
    re-fetch returns the winner."""

    def __init__(self, winner: Farm) -> None:
        self._fetches: list[object] = [None, winner]
        self.flush_calls = 0

    def begin_nested(self) -> _Nested:
        return _Nested()

    def add(self, obj: object) -> None:
        pass

    async def flush(self) -> None:
        self.flush_calls += 1
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    async def execute(self, _stmt: object) -> _Result:
        return _Result(self._fetches.pop(0))


async def test_get_or_create_farm_recovers_from_concurrent_create() -> None:
    winner = Farm(canonical_farm_id="FARM-RACE")
    session = _RacingSession(winner)
    built: list[Farm] = []

    def _build() -> Farm:
        f = Farm(canonical_farm_id="FARM-RACE")
        built.append(f)
        return f

    farm, created = await get_or_create_farm(session, canonical_farm_id="FARM-RACE", build=_build)

    assert created is False
    assert farm is winner  # the competitor's row, not our discarded build
    assert session.flush_calls == 1
    assert len(built) == 1  # we did attempt our own insert before losing the race


class _ExistingSession:
    def __init__(self, existing: Farm) -> None:
        self._existing = existing

    async def execute(self, _stmt: object) -> _Result:
        return _Result(self._existing)


async def test_get_or_create_farm_returns_existing_without_building() -> None:
    existing = Farm(canonical_farm_id="FARM-X")
    called = {"build": False}

    def _build() -> Farm:
        called["build"] = True
        return Farm(canonical_farm_id="FARM-X")

    farm, created = await get_or_create_farm(
        _ExistingSession(existing), canonical_farm_id="FARM-X", build=_build
    )

    assert created is False
    assert farm is existing
    assert called["build"] is False  # no insert attempted when the farm already exists


# --- D10: the same hardening for the field create (uq_field_farm_canonical). A keyed field recovers
# from a concurrent first-create; an unkeyed/derived field has no unique key so it inserts directly.


class _FieldRacingSession:
    """Loses a concurrent first-create on a keyed field: the savepoint flush raises IntegrityError
    (a competitor inserted the same farm+canonical id) and the re-fetch returns the winner."""

    def __init__(self, winner: Field) -> None:
        self._winner = winner
        self.flush_calls = 0

    def begin_nested(self) -> _Nested:
        return _Nested()

    def add(self, obj: object) -> None:
        pass

    async def flush(self) -> None:
        self.flush_calls += 1
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    async def execute(self, _stmt: object) -> _Result:
        return _Result(self._winner)


class _DirectSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.nested_calls = 0

    def begin_nested(self) -> _Nested:
        self.nested_calls += 1
        return _Nested()

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


async def test_get_or_create_field_recovers_from_concurrent_create() -> None:
    farm_id = uuid.uuid4()
    winner = Field(farm_id=farm_id, canonical_field_id="fld-9")
    session = _FieldRacingSession(winner)
    built: list[Field] = []

    def _build() -> Field:
        f = Field(farm_id=farm_id, canonical_field_id="fld-9")
        built.append(f)
        return f

    field, created = await get_or_create_field(
        session, build=_build, farm_id=farm_id, canonical_field_id="fld-9"
    )

    assert created is False
    assert field is winner  # the competitor's row, not our discarded build
    assert session.flush_calls == 1
    assert len(built) == 1


async def test_get_or_create_field_unkeyed_creates_directly() -> None:
    farm_id = uuid.uuid4()
    session = _DirectSession()
    built = Field(farm_id=farm_id, canonical_field_id=None)

    field, created = await get_or_create_field(
        session, build=lambda: built, farm_id=farm_id, canonical_field_id=None
    )

    assert created is True
    assert field is built
    assert built in session.added
    assert session.nested_calls == 0  # no savepoint: an unkeyed field has nothing to dedupe on
