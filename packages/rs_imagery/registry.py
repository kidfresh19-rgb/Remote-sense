"""Adapter selection. The active adapter is a config switch (CLAUDE.md invariant 1), so
choosing a satellite source is fully reversible and never wired into downstream code."""

from __future__ import annotations

from rs_core.config import ImageryAdapter, Settings, get_settings

from rs_imagery.adapters.mock import MockAdapter
from rs_imagery.adapters.server_compute import ServerComputeAdapter
from rs_imagery.adapters.windowed_cog import WindowedCogAdapter
from rs_imagery.port import AccessPort


def get_access_adapter(settings: Settings | None = None) -> AccessPort:
    settings = settings or get_settings()
    adapter = settings.imagery_adapter

    if adapter is ImageryAdapter.MOCK:
        return MockAdapter()

    # windowed_cog: the real CDSE adapter for the stored pipeline (ADR 0002). Constructs without
    # network or credentials; it validates config lazily on first search/fetch.
    if adapter is ImageryAdapter.WINDOWED_COG:
        return WindowedCogAdapter(settings)

    # server_compute: endpoint-side previews/live tiles via the CDSE Process API (ADR 0003).
    # Constructs without network or credentials; it validates config lazily on first use.
    if adapter is ImageryAdapter.SERVER_COMPUTE:
        return ServerComputeAdapter(settings)

    raise ValueError(f"Unknown imagery adapter: {adapter!r}")
