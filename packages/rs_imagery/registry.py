"""Adapter selection. The active adapter is a config switch (CLAUDE.md invariant 1), so
choosing a satellite source is fully reversible and never wired into downstream code."""

from __future__ import annotations

from rs_core.config import ImageryAdapter, Settings, get_settings

from rs_imagery.adapters.mock import MockAdapter
from rs_imagery.port import AccessPort


def get_access_adapter(settings: Settings | None = None) -> AccessPort:
    settings = settings or get_settings()
    adapter = settings.imagery_adapter

    if adapter is ImageryAdapter.MOCK:
        return MockAdapter()

    # Real adapters land in later phases. They live behind this same switch so nothing
    # downstream changes when they arrive.
    if adapter is ImageryAdapter.SERVER_COMPUTE:
        raise NotImplementedError(
            "server_compute adapter is implemented in Phase 4 (preview/live rendering)."
        )
    if adapter is ImageryAdapter.WINDOWED_COG:
        raise NotImplementedError(
            "windowed_cog adapter is implemented in Phase 2/3 (analysis + collection)."
        )

    raise ValueError(f"Unknown imagery adapter: {adapter!r}")
