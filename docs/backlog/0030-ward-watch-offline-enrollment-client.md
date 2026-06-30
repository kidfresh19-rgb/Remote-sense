# Backlog 0030 — Ward Watch: offline-first enrollment client (scoping)

- Status: scoping (not built). Gated on the §7.0 product-surface decision and the 0026 gateway
  field-name CONFIRM. This document is the Phase 1 plan-gate input, not a build order.
- Type: officer-facing client (low-end Android). Cross-team: the app and its sync target live on the
  AgriTrack / gateway side, not in this repo. remote-sense owns only the supporting seams below.
- Parent: PRD 0003 (`docs/prd/0003-ward-watch.md`), Phase 1; surface decision §7.0; enrollment §8.
- Blocked by:
  - §7.0 product-surface decision (standalone app vs AgriTrack officer mode) — see "Gating decision".
  - 0026 ⚑ CONFIRM (real gateway field names, units, nullability, GET path) for the inbound join.
  - PRD §12.8 institutional reality (device provisioning, officer training, data ownership, the
    AGRITEX/Ministry pilot agreement). This is a partnership precondition, not code.
- Invariants / decisions:
  - #6 split-ownership: the gateway owns household identity and geometry; remote-sense reads them
    read-only. The client is a capture-and-sync client, **not** the store of record (PRD §7.0, §8.4).
  - Enrollment writes to the gateway; remote-sense ingests from the gateway via the 0026 inbound
    pull. The client never writes to a remote-sense route. The frozen OUTBOUND wire is untouched.
  - #6 trigger (PRD §9.6): the client storing identity locally as the authority instead of syncing to
    the gateway would force a heavier ADR. The §8.4 design avoids it; keep it that way.

## Context

Mass enrollment runs in rural wards with no reliable connectivity (PRD §8). The officer workflow has a
hard target of **under one minute per household**: open app, search or create household, drop a centre
pin, pick a field-size class, enter the crop mix, record the planting window, save offline (§8.1). The
device is low-end Android; 100% offline operation with batch upload when the officer reaches signal
(§8.4). This is the front door of the whole Ward Watch loop (enroll → monitor → intervene → report):
nothing downstream exists for a household until it is enrolled and synced.

## Architectural boundary (read this first)

The single most important scoping fact: **the enrollment client is an AgriTrack / gateway-side
deliverable.** Its backend is the gateway, which is the identity and geometry authority (#6).
remote-sense is downstream of it. The data flow is:

```
[Android enrollment client]  --(offline SQLite + JSON queue, UUID-keyed)-->
[Gateway / AgriTrack]  (idempotent upsert on client UUID; identity + geometry authority)  -->
[remote-sense]  GatewayPort.fetch_household_declarations  (0026 inbound pull, read-only)  -->
[0031 ingestion]  reconcile crop mix / planting window onto geometry-bearing Plot rows  -->
[pipeline]  per-household indices  -->  [0040 cockpit / triage / rollups]
```

What this means for "build the enrollment client":

- The **app itself** (capture UI, on-device store, sync engine) and the **gateway enrollment/sync
  endpoints** it posts to are **outside this repo**. remote-sense has no mobile surface and no routing
  owner for Android work (see CLAUDE.md §6 routing table). Do not expect a remote-sense
  backend/frontend specialist to build it; it is an AgriTrack-app change or a new mobile project.
- Everything **remote-sense** must provide for an enrolled household to flow through is **already
  built** and merged on this branch:

  | Concern | Where (remote-sense) | State |
  |---|---|---|
  | Inbound declarations contract (crop mix, planting window, drone refs, join key) | `packages/rs_sync/inbound.py` (0026) | built (candidate `gw-inbound/v1`, field names ⚑ CONFIRM) |
  | Read-only pull from the gateway | `GatewayPort.fetch_household_declarations` | built (mock + real adapter) |
  | Proxy-AOI math (pin + size class → area-correct polygon) | `packages/rs_core/proxy_aoi.py` (0028) | built (UTM square, `officer_proxy` tag) |
  | Declared-crop vocabulary | `packages/rs_core/crops.py` (0029) | built |
  | Crop-mix resolution (full mix in, dominant out) | `packages/rs_core/cropmix.py` (0029) | built |
  | Household / Plot / crop-mix persistence | `packages/rs_core/models.py` + migration 0011/0012 (0029/0031) | built |
  | Ward administrative boundaries + centroid assignment | `seed_ward.py` (0027) | built (authoritative data ⚑ CONFIRM) |
  | Per-household ingestion + indices | 0031 worker task | built |

  So **0030 adds nothing to the remote-sense product code** beyond the residual seams in
  "remote-sense residual work" below. The substance of 0030 is the client app and the gateway side.

## Gating decision (§7.0 override — resolve at the plan gate before any build)

PRD §7.0 chose "one offline-first officer client" but explicitly parked an override: if a single app
is too heavy for v1, fall back to **enrollment through an AgriTrack officer mode (gateway-native) plus
a thin triage surface**, at the cost of splitting the officer loop across two apps. This decision
determines what 0030 even is, so it must be made first.

| Option | What 0030 becomes | Pros | Cons |
|---|---|---|---|
| **A. Officer mode in the existing AgriTrack app** (recommended for the pilot) | A feature/screen set inside AgriTrack, reusing its auth, sync, and gateway data layer | No new app to provision/sign/distribute; reuses AgriTrack's existing gateway sync and identity; fastest path to a one-ward pilot; keeps the gateway as the obvious authority | Officer loop split across AgriTrack (enroll) and the cockpit (triage/visit); AgriTrack team owns the timeline |
| **B. New standalone offline officer app** | A purpose-built Android app (enroll → triage → visit in one place) | Single app for the whole officer loop (the §7.0 ideal); tailored offline UX | A new app to build, provision, sign, train on, and maintain; must re-implement offline sync + gateway auth AgriTrack already has; heaviest path |

Recommendation: **Option A for the one-ward pilot** (PRD §11 "pilot before scale"). It reaches a real
ward fastest and reuses AgriTrack's existing gateway sync rather than rebuilding it. Revisit Option B
for scale-out once the pilot validates the capture workflow and the mixed-pixel quality flags against
officer reality. Either way the remote-sense side is identical (the gateway is the sync target), so
this decision does not block the remote-sense residual work.

## What the client must do (capture-and-sync responsibilities)

Independent of A vs B, the enrollment capability must:

1. **Capture workflow (< 1 min/household, §8.1):** search-or-create household; drop a centre pin on a
   map; pick a field-size class; enter the crop mix; record the planting window; save offline. Map
   tiles must be available offline or degrade to a lightweight basemap; **do not cache satellite
   imagery on-device** (battery, storage — §8.4).
2. **On-device data model (§8.3):** `Household → Plot → crop-mix matrix (crop, weight%) + planting
   window + proxy geometry`. A household may have multiple plots; a plot stores the full intercrop mix,
   not just the dominant crop. Mirror the canonical vocab in `rs_core/crops.py` so declared crops
   reconcile cleanly at ingest (do not invent crop strings on-device).
3. **Field-size class → proxy AOI (§8.2):** classes and areas are fixed by `proxy_aoi.py`:
   backyard 0.10 ha, small_holding 0.50 ha, medium 2.00 ha, large = explicit `area_ha` (up to ~100).
   The officer must **see** the generated AOI on the map at capture time to sanity-check coverage,
   which means the polygon is generated **on-device, offline** (see "proxy-AOI locus" below). Tag it
   `geometry_source = officer_proxy` (the value `proxy_aoi.PROXY_GEOMETRY_SOURCE`).
4. **Planting window (§8.2, §12.5):** captured at enrollment, declared, editable. Bucket edges and
   whether to also infer from observed green-up are open (§12.5) — capture a declared window now;
   inference is later.
5. **Offline sync, idempotent, UUID-keyed, gateway-targeted (§8.4):** local SQLite + JSON queue; each
   household and plot gets a **client-generated UUID at creation**; batch upload on connectivity; the
   **gateway** performs idempotent upserts on UUID so re-syncing a batch never duplicates and stays
   the identity authority. Records live on-device until acknowledged. Network failures retry; partial
   batches are safe to resend.

## Open decisions specific to enrollment

1. **Proxy-AOI locus + parity (the key technical call).** The officer needs to see the AOI offline at
   capture, so the polygon is generated **on-device**. But the canonical math is `proxy_aoi.py` (UTM
   square in EPSG:32735/6, reproject to 4326, one-pixel erosion for the pixel-count estimate). Two
   ways to avoid drift:
   - (recommended) On-device computes the polygon for display and sends **both** the raw inputs
     `(lat, lon, size_class, area_ha?)` **and** the resulting polygon to the gateway. The gateway
     stores the polygon as authority. A **parity test** asserts the on-device port matches
     `proxy_aoi.py` for a fixture grid of pins/classes, so the two implementations cannot diverge.
   - On-device sends only the raw inputs; the polygon is materialized server-side. Simpler client, but
     the officer cannot see/verify the AOI offline, which breaks the §8.1 sanity-check step. Rejected
     for that reason unless the map step is dropped.
   Note `proxy_aoi.py` currently builds a **square only** (PRD §8.2 mentions "square or circle"); v1 is
   square. If a circle is wanted, add it to `proxy_aoi.py` first so parity holds.
2. **Erosion buffer + minimum usable pixels (§12.3).** `proxy_aoi.py` uses a one-pixel (10 m) erosion
   and `MIN_USABLE_PIXELS = 3`. Confirm these against officer reality during the pilot; the client may
   warn at capture when a small/backyard class yields too few clear pixels to be trustworthy.
3. **Gateway field-name CONFIRM (§12.1, 0026).** The inbound contract is a tolerant candidate
   (`gw-inbound/v1`). The client's payload field names, units, and nullability must be reconciled with
   the gateway team, then `inbound.py` / `agritrack.py` renamed and the version bumped. Until then the
   contract tolerates unknown/empty (receiver tolerance, [[feedback_receiver_tolerance_wire_fields]]).
4. **Tech stack for low-end Android.** Native Kotlin vs cross-platform (Flutter/React Native). This is
   an AgriTrack-team call driven by their existing app; under Option A it is simply "however AgriTrack
   is built". Flag for the plan gate; not a remote-sense decision.
5. **Institutional reality (§12.8).** Device provisioning, officer training, the pilot agreement, and
   legal data ownership are preconditions for any field deployment. Out of engineering scope; named
   here because they block the pilot, not the code.

## remote-sense residual work for 0030 (the only in-repo slices)

Small, and mostly verification rather than new product code:

- [ ] **End-to-end ingest proof:** a test that drives a synthetic enrolled batch through
  `fetch_household_declarations` → 0031 reconcile → a geometry-bearing Plot with the declared crop mix
  and planting window, asserting an enrolled household becomes triage-visible. Most pieces exist; this
  asserts the seam as a whole for the enrollment path.
- [ ] **Proxy-AOI parity fixture (if Option-1 locus is chosen):** publish a small fixture of
  `(lat, lon, size_class) → expected 4326 polygon + area_m2` generated from `proxy_aoi.py`, for the
  client's on-device port to test against. Keeps the two implementations from drifting.
- [ ] **0026 confirm + version bump** when the gateway team returns real field names/path (shared with
  0026's "resolve before merge"; not new work, just the trigger).

That is the whole remote-sense surface for 0030. No new endpoint, no schema change, no migration.

## Acceptance criteria (the client; owned by the AgriTrack / mobile side)

- [ ] A household with one or more plots can be enrolled fully offline in under a minute, capturing
      pin, size class, full crop mix, and planting window.
- [ ] The proxy AOI is shown on the map at capture and matches `proxy_aoi.py` within parity tolerance.
- [ ] Household and plot each carry a client-generated UUID from creation.
- [ ] A batch syncs to the gateway idempotently: re-sending the same batch creates no duplicates.
- [ ] No satellite imagery is cached on-device.
- [ ] After sync, the household appears in remote-sense via the 0026 pull and becomes triage-visible
      once the pipeline runs (covered by the remote-sense end-to-end ingest proof above).

## Out of scope

- Building the Android app or the gateway enrollment/sync endpoints inside this repo.
- Any new remote-sense route, schema, or migration (the inbound path is read-only pull, already built).
- On-device imagery, on-device analysis, or the triage/visit surfaces (those are the 0040 cockpit,
  already built this branch; under Option A they are a separate AgriTrack screen set).
- Yield numbers (PRD §10: not in v1, needs flywheel calibration).

## Definition of done (for this scoping slice)

This document plus a recorded §7.0 plan-gate decision (Option A or B). The client build is owned by the
AgriTrack / mobile team and tracked there; the three remote-sense residual checkboxes above are the
only items that land in this repo, and the first two can proceed independently of the §7.0 call.
