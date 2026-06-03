"""Adapters implementing ActivityLogPort. mock (testing/offline); the real AgriTrack/gateway feed
lands behind the same port. The active one is selected by config in the registry."""

from rs_activity.adapters.mock import MockActivityAdapter

__all__ = ["MockActivityAdapter"]
