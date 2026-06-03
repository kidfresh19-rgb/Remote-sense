"""Adapters implementing AccessPort. mock (testing), server_compute (previews/live),
windowed_cog (the stored pipeline). The active one is selected by config in the registry."""

from rs_imagery.adapters.mock import MockAdapter
from rs_imagery.adapters.server_compute import (
    ProcessClient,
    RasterDecoder,
    RasterioRasterDecoder,
    ServerComputeAdapter,
)
from rs_imagery.adapters.windowed_cog import RasterioWindowSource, WindowedCogAdapter, WindowSource

__all__ = [
    "MockAdapter",
    "WindowedCogAdapter",
    "RasterioWindowSource",
    "WindowSource",
    "ServerComputeAdapter",
    "ProcessClient",
    "RasterDecoder",
    "RasterioRasterDecoder",
]
