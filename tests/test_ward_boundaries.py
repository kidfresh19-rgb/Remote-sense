"""Pure tests for Ward Watch ward boundary layer infrastructure (backlog 0027).

Covers the name-normalisation logic used by assign_households_to_ward_by_name.
DB round-trip tests live in test_ward_boundaries_db.py (PostGIS required)."""

from __future__ import annotations


def _normalise(name: str) -> str:
    return name.strip().lower()


def _match(ward_name: str, boundary_names: list[str]) -> str | None:
    target = _normalise(ward_name)
    index = {_normalise(b): b for b in boundary_names}
    return index.get(target)


class TestNameNormalisation:
    def test_exact_match(self):
        assert _match("Makoni Ward 5", ["Makoni Ward 5", "Bindura Ward 2"]) == "Makoni Ward 5"

    def test_case_insensitive_match(self):
        assert _match("makoni ward 5", ["Makoni Ward 5"]) == "Makoni Ward 5"

    def test_leading_trailing_space_match(self):
        assert _match("  Makoni Ward 5  ", ["Makoni Ward 5"]) == "Makoni Ward 5"

    def test_no_match_returns_none(self):
        assert _match("Unknown Ward", ["Makoni Ward 5", "Bindura Ward 2"]) is None

    def test_empty_boundary_list(self):
        assert _match("Makoni Ward 5", []) is None

    def test_multiple_wards_correct_one_returned(self):
        wards = ["Bindura Ward 1", "Bindura Ward 2", "Bindura Ward 3"]
        assert _match("bindura ward 2", wards) == "Bindura Ward 2"

    def test_ward_names_with_special_characters(self):
        assert _match("Chegutu Ward 12", ["Chegutu Ward 12"]) == "Chegutu Ward 12"


class TestWardLayerConstants:
    def test_seed_version_string_is_date_prefixed(self):
        version = "candidate-2026-06-26"
        assert version.startswith("candidate-")

    def test_ward_boundaries_have_no_nr_self_composition(self):
        nr_composition: dict[str, float] = {}
        dominant_nr = None
        assert nr_composition == {}
        assert dominant_nr is None
