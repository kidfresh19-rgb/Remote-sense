"""Activity-log adapter selection. The active adapter is a config switch (CLAUDE.md invariant 1),
so the AgriTrack feed source is reversible and never wired into downstream code."""

from __future__ import annotations

from rs_core.config import ActivityAdapter, Settings, get_settings

from rs_activity.adapters.mock import MockActivityAdapter
from rs_activity.port import ActivityLogPort


def get_activity_adapter(settings: Settings | None = None) -> ActivityLogPort:
    settings = settings or get_settings()
    adapter = settings.activity_adapter

    if adapter is ActivityAdapter.MOCK:
        return MockActivityAdapter()

    # The real AgriTrack/gateway feed lands behind this same port. Its wire format is parked with
    # the gateway contract (⚑ CONFIRM), so it raises until confirmed, like server_compute did.
    if adapter is ActivityAdapter.GATEWAY:
        raise NotImplementedError(
            "gateway activity adapter is parked on the AgriTrack feed contract (⚑ CONFIRM)."
        )

    raise ValueError(f"Unknown activity adapter: {adapter!r}")
