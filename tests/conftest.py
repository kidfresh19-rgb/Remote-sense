"""Pytest-wide setup.

On Windows, psycopg3's async mode refuses to run on asyncio's default ProactorEventLoop and
needs a SelectorEventLoop. Without this the PostGIS-backed suites skip locally even when the
compose stack is up (the fixtures catch the InterfaceError as "not reachable"). Forcing the
selector policy lets the DB-gated tests run on a developer's Windows box; it is a no-op on the
Linux CI runners and in-container, which already default to a compatible loop."""

from __future__ import annotations

import asyncio
import os
import sys
import warnings

# Safety guard (highest priority): the DB-backed suites read RS_TEST_DATABASE_URL at module-import
# time and run Base.metadata.drop_all on setup AND teardown. Their hard-coded fallback points at the
# live dev database (.../remote_sense), so a plain `pytest` with the env var unset would silently
# drop every farm/field/analysis in the running stack. conftest is imported before any test module,
# so setting the default here shadows that fallback with a throwaway DB unless the caller overrides
# it explicitly. Never remove this without also fixing the per-file fallbacks.
os.environ.setdefault(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense_test"
)

if sys.platform == "win32":
    # The asyncio policy API is deprecated on 3.14+, but pytest-asyncio 1.x still selects the
    # loop through it; suppress the known warning rather than leave a compatible-loop hole.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
