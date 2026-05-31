"""rs_sync: outbound sync (Phase 6). Export builders (GeoTIFF/CSV/PDF/gateway payload)
and the GatewayPort, with retry + backoff + dead-letter + idempotency keys. Results are
pushed additively, keyed to the canonical farm ID; geometry is never returned.

Not yet implemented. The gateway URL/auth/payload is a parked decision (⚑ CONFIRM)."""
