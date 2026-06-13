"""Generate the frozen external API contract snapshot.

Writes the live OpenAPI schema of the remote-sense API to ``contract/openapi.before.json``
with deterministic ordering, so the post-rebuild diff gate (see ``tests/contract/``) can
detect any drift on the external, gateway-facing routes. The frozen-vs-improvable split is
documented in ``CONTRACT.md``; the slice plan is ``docs/plan/S0.1-S0.2-contract-safety-net.md``.

Run from the repo root::

    python contract/snapshot_openapi.py

Re-run it deliberately only when an additive, intended change to the external contract has been
agreed. A non-additive diff against the committed snapshot is a regression, not a reason to
regenerate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.api.main import app  # noqa: E402  (sys.path bootstrap must run before this import)

SNAPSHOT_PATH = _REPO_ROOT / "contract" / "openapi.before.json"


def build_openapi() -> dict:
    """Return the API's full OpenAPI schema (paths, components, security)."""
    return app.openapi()


def write_snapshot(path: Path = SNAPSHOT_PATH) -> int:
    """Write the schema to ``path`` deterministically. Returns the path count."""
    schema = build_openapi()
    path.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return len(schema.get("paths", {}))


if __name__ == "__main__":
    count = write_snapshot()
    print(f"wrote {SNAPSHOT_PATH.relative_to(_REPO_ROOT)} ({count} paths)")
