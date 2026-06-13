"""Contract diff gate: the external (frozen) routes must not drift.

Regenerates the live OpenAPI schema and asserts that, for every EXTERNAL-FROZEN path, the
committed snapshot (``contract/openapi.before.json``) is an additive sub-structure of the
current schema. Removing or renaming a path, method, field, status code, or enum value, or
changing a type, fails. Purely additive changes (new optional fields, new responses, new
routes) pass. The browser BFF is intentionally not checked. See ``CONTRACT.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.snapshot_openapi import build_openapi

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SNAPSHOT = _REPO_ROOT / "contract" / "openapi.before.json"

# The external boundary. Keep in sync with CONTRACT.md.
FROZEN_PATHS = (
    "/api/v1/mobile/sync",
    "/api/v1/mobile/data",
    "/ingest/farm",
)


def _resolve(node: object, components: dict, seen: tuple[str, ...] = ()) -> object:
    """Inline ``$ref`` refs so a renamed-but-same-shape model is compared by structure."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref:
            if ref in seen:
                return {"$ref": ref}  # cycle guard
            name = ref.split("/")[-1]
            target = components.get("schemas", {}).get(name, {})
            return _resolve(target, components, seen + (ref,))
        return {k: _resolve(v, components, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, components, seen) for v in node]
    return node


def _assert_additive(before: object, current: object, trail: str) -> None:
    """Every key/value present in ``before`` must be present and equal in ``current``."""
    if isinstance(before, dict):
        assert isinstance(current, dict), f"{trail}: shape changed (object expected)"
        for key, bval in before.items():
            assert key in current, f"{trail}.{key}: removed from the frozen contract"
            _assert_additive(bval, current[key], f"{trail}.{key}")
    elif isinstance(before, list):
        assert before == current, f"{trail}: list changed in the frozen contract"
    else:
        assert before == current, f"{trail}: value changed ({before!r} -> {current!r})"


@pytest.fixture(scope="module")
def schemas() -> tuple[dict, dict]:
    before = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    current = build_openapi()
    return before, current


def test_snapshot_exists() -> None:
    assert _SNAPSHOT.exists(), "run `python contract/snapshot_openapi.py` to create the snapshot"


@pytest.mark.parametrize("path", FROZEN_PATHS)
def test_frozen_path_present(schemas: tuple[dict, dict], path: str) -> None:
    before, current = schemas
    assert path in before["paths"], f"{path} missing from the committed snapshot"
    assert path in current["paths"], f"{path} was removed from the live API (frozen route)"


@pytest.mark.parametrize("path", FROZEN_PATHS)
def test_frozen_path_additive_only(schemas: tuple[dict, dict], path: str) -> None:
    before, current = schemas
    b = _resolve(before["paths"][path], before.get("components", {}))
    c = _resolve(current["paths"][path], current.get("components", {}))
    _assert_additive(b, c, trail=path)
