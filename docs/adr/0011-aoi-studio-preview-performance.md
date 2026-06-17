# 0011: AOI Studio preview performance: concurrency and a three-layer cache

Status: accepted (Phase 1). The deferred all-indices track is gated; see below.

## Context

AOI Studio previews (multi-pass index reads over a custom AOI; see `CONTEXT.md`) feel slow. A
single-index batch of about 24 dates issues roughly 96 CDSE reads (each pass is N band reads plus
SCL plus a metadata XML), run serially through blocking rasterio reads, so wall-clock is
latency-bound *below* the rate the quota already allows.

The binding ceiling is the **CDSE quota budget**: one Redis-backed token bucket (`cdse:quota`,
`packages/rs_imagery/resilience.py`) shared across every worker at about 4 rps. This was verified to
be genuinely global, so adding workers cannot beat the floor. The only levers that help are reducing
the number of reads and hiding per-read latency up to the ceiling.

We scope the fix to the **single-index** custom-date path and defer the all-indices path. The
justification is foundation-first, not a frequency guess: every Phase 1 lever (band and metadata
memoization, pass-level concurrency, the result and search caches) is required by the all-indices fix
too. The deferred track only adds a cross-process band cache (or a request-contract reshape) on top.
Nothing in Phase 1 is throwaway.

## Decision (Phase 1)

1. **Pass-level concurrency.** Replace the serial pass loop in
   `services/worker/tasks/analysis.py` with a bounded `asyncio.Semaphore` gather, and move each
   blocking CDSE read to `asyncio.to_thread` inside `WindowedCogAdapter.fetch`. Band reads stay
   sequential within one fetch (this preserves the reference-grid shape check, and the band cache is
   what removes their cost). The progress meter and the oldest-first ordering are preserved.

2. **Semaphore size = `min(2 * RS_CDSE_RATE_LIMIT_RPS, 16)`**, with the formula documented in config.
   The 2x covers round-trip variance so the bucket stays saturated; the cap bounds the thread pool
   and doubles as the fail-open ceiling. The bucket fails open when its Redis is unreachable, so the
   semaphore is what bounds the read storm in that degraded mode.

3. **Circuit breaker made thread-safe** with a `threading.Lock` inside the state machine
   (`CircuitBreaker`, `resilience.py`), recording the transition atomically at the actual outcome.
   We rejected the alternative of keeping the breaker decision on the loop thread (call `allow()`
   before the `to_thread`, record the outcome after): that introduces a time-of-check/time-of-use
   race in which two passes both see green, both fail, and the breaker permits the very call it
   exists to block, exactly when CDSE is failing hard. The lock is held for microseconds (a state
   check plus a counter), with the reads outside it, so contention is negligible.

4. **Three-layer cache at the read-level seam** (`RasterioWindowSource`, the only code that touches
   CDSE):

   - **Band-window cache.** Key `(href, bbox-in-scene-CRS quantized to the read's output grid at
     resolution_m, resolution_m, resampling)`. The polygon is deliberately excluded: the read is
     bbox-scoped and the polygon mask is applied later in `fetch`, so two AOIs sharing a bbox
     legitimately share one read. Quantize to the read grid, never the scene native grid, so the key
     can never be coarser than the window derivation; coarser would risk returning a wrong array,
     while at-grid-or-finer risks only a missed hit. href implies CRS (verified: the read returns
     data in the scene's native CRS and no warp enters this path; only `transform_bounds` and
     `transform_geom` reproject coordinates, and the UTM reprojection in `rs_core/geo.py` is
     geometry-only). Phase 1 backs this with an in-process dict; this is the seam the deferred track
     swaps for Redis. TTL is effectively infinite (immutable COG objects), eviction and memory only.

   - **Result cache (Redis).** Per-pass, keyed by what the pass was *resolved from*, so every entry
     is immutable. `ok`: `(canonical-geometry-hash, index, formula_version, provider_scene_id)`.
     `interpolated`: the same with the ordered pair of bracket scene ids. The full polygon IS in this
     key (the stats depend on the mask), the mirror of the band-read key. Geometry is canonicalized
     before hashing: round to 6 decimal places (sub-pixel at 10 m), normalize ring winding and start
     vertex, then hash WKB; this collapses serialization variance and sub-tolerance jitter to a hit
     while a genuinely redrawn polygon correctly misses. TTL is generous (immutable scene math),
     eviction only. `no_pass` is not cached (a scene exists but failed cloud-mask; it flips when
     imagery arrives and is cheap to recompute). `no_scenes` cannot be cached under this scheme at
     all, because there is no scene id to key on.

   - **Search cache (Redis).** `(canonical-geometry-hash, date-range)` to the STAC item list, short
     TTL (about 5 minutes). It sits upstream of resolution, so a hit skips the round-trip straight to
     the immutable result lookup and a miss simply runs the search. New imagery still invalidates
     downstream, because the resolved scene ids are what key the result. The search is always run
     (cached or live), which is what makes the immutable-key property hold.

**Why immutable keys and an always-live search (the load-bearing rationale).** Under a CDSE outage a
mutable date-keyed cache would return stale-but-confident numbers with no signal, while immutable
keys make the search fail fast through the open breaker so the analyst sees a degraded system. For an
exploration tool where analysts form hypotheses from the numbers, loud failure beats silent
staleness. The read-budget saving (the search is about 1 read of 96) is secondary to this.

The three TTLs brace each other and are one decision: band cache effectively infinite (immutable
COGs), result cache generous (immutable scene math), search cache short (new imagery is the only
mutation that matters). Splitting this into separate ADRs would fragment a design whose force comes
from the pieces supporting each other.

## Invariant 7 reconciliation

Invariant 7 (`CLAUDE.md` §1.7): raw bands are transient; download for processing, derive COG plus
zonal stats, then discard; persisting raw scenes per field is forbidden (unbounded growth).

- **Phase 1 does not touch it.** The band-window memo lives only for the duration of one task and is
  dropped at task end, which is "download for processing, then discard" with intra-task reuse. The
  result and search caches store derived stats and STAC item lists, never raw bands.
- **The deferred Redis band cache is the only invariant-7-adjacent piece**, and it is a clarification
  of the invariant's boundary, not a change. §1.7 forbids per-field accumulation of raw scenes, whose
  named harm is unbounded growth. A bounded, eviction-based cache of small AOI windows for previews
  is a different object: it never becomes the system of record (the COG and zonal stats remain the
  persisted artifacts), it is size-capped, and it is not per-field accumulation. §1.7 is correctly
  stated and is left unedited; this ADR records where its boundary sits.

## Deferred: the all-indices track (gate-at-pickup, empirical)

The all-indices path (five per-index jobs contending on the one bucket, so roughly serializing) is
deferred behind the Phase 1 read-level seam. Crossing the invariant-7 boundary to a Redis band cache,
or reshaping the request contract from single-index to multi-index, is gated on `architect` review at
pickup. The gate is a decision from named data, not a re-confirmation of this reasoning:

1. **Phase 1's measured latency on the all-indices path.** Bracket memoization and pass-level
   concurrency apply there too; Phase 1 alone may drop it below the felt-slow threshold, in which case
   crossing the boundary buys nothing real. This is the input most likely to retire the track.
2. **Measured all/single dispatch frequency** (from the Phase 1 instrumentation). If all-indices is a
   small fraction of usage, the track may not be worth building at all.
3. **Production band-memo and result-cache hit rates.** A high intra-task band hit rate means
   cross-task sharing has diminishing returns.
4. **Whether the CDSE quota has been raised** in the interim, which reframes the whole conversation.

The seam stays mechanically cheap to swap (in-process dict to Redis at the same key). Only the
decision to cross the boundary is deferred, and it has a defined exit criterion rather than an
open-ended "later."

## Consequences

- Expected Phase 1 effect: a cold single-index batch moves from latency-bound (well above the quota
  floor) toward the roughly 22 s floor for about 96 reads; re-runs become near-instant via the result
  cache; interpolation-heavy batches shrink further because bracket scenes are read once.
- New shared mutable state (the breaker lock) and two new Redis keyspaces (result, search). Redis is
  already broker, result backend, and quota store, so a Redis outage already halts dispatch; the
  caches add no new single point of failure.
- Out of scope: the all-indices track (above), raising the CDSE quota, and fanning a series across
  worker processes (a global bucket makes that moot for throughput).
