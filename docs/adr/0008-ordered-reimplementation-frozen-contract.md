# ADR 0008 — Rebuild as an Ordered Re-implementation Behind a Frozen Gateway Contract

- Status: accepted (engagement approach; implementation planned, not yet built)
- Date: 2026-06-10
- Phase: Intelligence-core rebuild (L2 to L6) / Shape
- Builds on: ADR 0001 (ports and adapters), ADR 0006 (AgriTrack sync contract), CLAUDE.md section 1 invariants

## Context

The intelligence core (L2 collection, L3 imagery access, L4 analysis, L4b interpretation, L5 tiles,
L6 workspace) is functionally complete: 285 tests pass and the `windowed_cog` path is verified live
against CDSE. But it was built in an unordered way, and the owner reports accumulated disorder and a
number of bugs as a direct result. The goal is not to change what the app does. It is to rebuild the
interior to an industry-grade standard: ordered, test-first, and able to absorb a large and growing
volume of farm data.

Two hard constraints frame the work:

1. The way data is received from and sent to the gateway must not change. The gateway is owned by
   another team; altering the external contract would force them to adapt, which is out of scope.
2. No new credentials. Changing or adding auth tokens, schemes, or env var names is forbidden.

There was a genuine fork on what "rebuild" means. Greenfield re-derivation (throw away the design and
start cold) versus re-implementation behind the existing architecture. The owner resolved it: keep
the architecture and the external boundary, re-implement the interior cleanly, and reevaluate and
improve internal structure where a hard look says it raises the bar.

## Decision

We commit to an ordered re-implementation behind a frozen external boundary, with the following
load-bearing decisions:

1. **Freeze the external boundary.** The gateway-facing surfaces (ingestion, outbound push, and the
   `/api/v1/mobile/data` pull) are frozen: routes, methods, request and response schemas, status
   codes, auth headers, and env var names stay identical. We capture them as a contract snapshot
   (`contract/openapi.before.json` plus a `CONTRACT.md` that tags every route EXTERNAL-FROZEN or
   INTERNAL-IMPROVABLE) plus contract tests. A post-rebuild OpenAPI diff that is non-additive on a
   frozen route is a regression, enforced by a CI gate. The browser-facing BFF is internal and
   improvable.
2. **No new credentials.** Reuse the exact env var names in `.env.example`. No new auth schemes,
   headers, or tokens. CDSE and gateway auth keep their existing names untouched.
3. **An industry-grade quality bar is the definition of done**, expressed as seven checkable pillars:
   (1) test and correctness gate, (2) type and boundary safety, (3) observability and resilience,
   (4) performance SLOs, (5) security, (6) clarity and provenance, (7) scale and elasticity. Lead
   with 1, 3, and 6.
4. **Scale is a data-volume problem, not a request-concurrency one.** The headline load is hundreds
   of thousands of farms' data arriving from the gateway, processed and stored. The analyst user base
   is small. Engineering centers on ingestion, the Celery pipeline, object storage, and the database,
   not on CDN fleets or high-QPS web tiers.
5. **Triage-driven execution.** A reevaluation pass classifies every module as keep-and-harden,
   refactor-in-place, or rewrite. Verified science (the reflectance and index core, the access
   adapters, SCL masking) is not wholesale-rewritten.
6. **An ordered macro-sequence.** Slice 0 is the safety net (the contract snapshot plus
   characterization tests over current behavior); nothing is refactored or rewritten until it exists.
   Then a reevaluation pass produces the triage and a priority order (worst-bug-density first). Then
   vertical rebuild slices, each cutting through the layers and ending with every gate green. The
   as-of-date farm view is the first integrating tracer bullet.
7. **The section 1 invariants remain binding.** Any deliberate change to one requires its own ADR.

## Alternatives weighed

- **Greenfield rewrite from scratch.** Rejected. It discards verified science and the live-CDSE proof,
  puts the frozen gateway contract at risk, and adds maximum risk for what is actually a quality and
  order goal.
- **Refactor-in-place only, no rewrites.** Rejected as the sole tool. Some modules are tangled past
  saving, and a blanket no-rewrite rule would entrench them.
- **Leave as-is and only add features.** Rejected. It does not address the disorder and bugs, and it
  compounds new work on an unordered base.

## Consequences

- The external contract cannot silently drift. The snapshot plus diff gate turns drift into a failing
  test rather than something a reviewer has to catch by eye.
- Verified science is preserved; effort concentrates on the disordered and buggy modules.
- The rebuild is reviewable slice by slice, each proven against the safety net and the quality gates.
- New CI gates are required that the repo does not enforce today: static type-check, a coverage floor,
  the contract diff, and a load test. Standing these up is part of the work.
- The interior architecture may change, so internal interfaces and the BFF may move. Only the frozen
  external surfaces are guaranteed stable.
- Scale work centers on data throughput and storage growth: table partitioning, worker autoscaling,
  COG retention, database tuning, and governing the CDSE quota ceiling in the adapter.
