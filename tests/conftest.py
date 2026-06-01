"""Pytest-wide setup.

On Windows, psycopg3's async mode refuses to run on asyncio's default ProactorEventLoop and
needs a SelectorEventLoop. Without this the PostGIS-backed suites skip locally even when the
compose stack is up (the fixtures catch the InterfaceError as "not reachable"). Forcing the
selector policy lets the DB-gated tests run on a developer's Windows box; it is a no-op on the
Linux CI runners and in-container, which already default to a compatible loop."""

from __future__ import annotations

import asyncio
import sys
import warnings

if sys.platform == "win32":
    # The asyncio policy API is deprecated on 3.14+, but pytest-asyncio 1.x still selects the
    # loop through it; suppress the known warning rather than leave a compatible-loop hole.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
