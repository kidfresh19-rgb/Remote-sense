"""rs_interpret: plain-language agronomic interpretation (Phase 4b). A Claude-API layer
that explains what an index means for a specific field, crop and season, grounded in the
actual zonal statistics and thresholds. Every generated read is a draft an agronomist
reviews and edits before it can be published; interpretation is never auto-published.

Not yet implemented. Uses prompt caching on the static agronomic context."""
