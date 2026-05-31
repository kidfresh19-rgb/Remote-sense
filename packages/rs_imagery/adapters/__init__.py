"""Adapters implementing AccessPort. mock (testing), server_compute (previews/live),
windowed_cog (the stored pipeline). The active one is selected by config in the registry."""

from rs_imagery.adapters.mock import MockAdapter

__all__ = ["MockAdapter"]
