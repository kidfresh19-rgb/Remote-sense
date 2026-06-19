# Agronomy thresholds v1 — review and sign-off (PLAN §10 step 4)

> Status: **§4 SCL + §5 confidence ratified 2026-06-18 (commit 2f63404); §1/§2/§3 carry provisional
> engineering approval 2026-06-19; agronomist sign-off still outstanding (tracked in
> docs/backlog/0015-agronomy-thresholds-v1-agronomist-signoff.md).** Prepared 2026-06-04 by
> consolidating the v1 defaults proposed 2026-06-03. These are the numbers the interpretation layer
> is grounded in; the Claude model classifies nothing itself, it only explains the bands defined
> here (risk #6), and no interpretation is ever auto-published. This document is for an agronomist to
> approve or adjust.

## Why this needs a human

Every threshold below carries a `# ⚑ CONFIRM (agronomy review)` marker in code. Per CLAUDE.md §4 a
parked decision stays flagged until the owner confirms. An engineer can propose defaults from the
literature; only an agronomist can sign off that they are right for Zimbabwean cropland. On approval
the markers come down and the reviewer + date are recorded (see Sign-off below).

The five sites under review:

| # | What | File |
|---|---|---|
| 1 | Base index classification bands | `packages/rs_interpret/thresholds.py` |
| 2 | Per-crop band overrides (maize, tobacco, sorghum, cotton) | `packages/rs_interpret/thresholds.py` |
| 3 | Display colormap ranges (display only, not classification) | `packages/rs_analysis/colormaps.py` |
| 4 | SCL clear-pixel classes | `packages/rs_analysis/scl.py` |
| 5 | Clear-fraction confidence cutoffs | `packages/rs_analysis/engine.py` |

A band reads "a value up to and including `upper` gets this label"; the last band is open-topped.

## 1. Base bands (generic Zimbabwean cropland fallback)

**Vigour (NDVI / EVI2 / SAVI share one set):**

| upper | label | meaning |
|---|---|---|
| 0.2 | bare | bare soil: pre-emergence, post-harvest, or severe failure |
| 0.4 | sparse | sparse / early canopy, or moisture / nutrient stress |
| 0.6 | developing | developing canopy, moderate vigour |
| 0.8 | vigorous | vigorous, well-developed canopy |
| — | dense | very dense canopy near peak biomass |

**NDRE (chlorophyll / nitrogen):** low ≤0.1, moderate ≤0.3, good ≤0.5, high >0.5.
**NDMI (canopy moisture):** dry ≤0.0, moderate ≤0.2, adequate ≤0.4, wet >0.4.

*Literature cross-check (for the reviewer):* red-edge (NDRE) tracks canopy chlorophyll/nitrogen and
flags stress ~12-16 days before NDVI, working in a ~0.1-0.6 band over crops. NDMI guidance commonly
places water stress below ~0.1-0.2, healthy 0.2-0.4, well-watered 0.4-0.6. See Sources. The EVI2/SAVI
edges reuse the NDVI numbers, which is approximate (SAVI's L=0.5 compresses its range) — flagged in #2.

## 2. Per-crop overrides

Vigour edges shared across NDVI/EVI2/SAVI per crop; NDRE/NDMI overridden where the crop's management
makes them distinctive.

| crop | vigour bare/sparse/developing/vigorous | NDRE low/mod/good | NDMI dry/mod/adequate | rationale |
|---|---|---|---|---|
| maize | 0.2 / 0.35 / 0.55 / 0.8 | 0.15 / 0.35 / 0.5 | (generic) | C4 staple, closes a dense canopy, N-hungry; NDRE at silking is the key N signal |
| tobacco | 0.2 / 0.4 / 0.6 / 0.82 | 0.25 / 0.4 / 0.55 | (generic) | lush N-rich canopy, topped/ripened on purpose; NDRE management-critical |
| sorghum | 0.2 / 0.35 / 0.5 / 0.7 | (generic) | -0.1 / 0.15 / 0.35 | drought-tolerant, lower peak NDVI; low NDMI is water-saving, not always stress |
| cotton | 0.2 / 0.35 / 0.55 / 0.75 | (generic) | (generic) | slow canopy closure, bare inter-row; SAVI more reliable, moderate NDVI peak |

## 3. Display colormap ranges (display only — NOT classification)

These set the legend stretch; they never change which pixels enter stats or any index value. Mirrored
in `frontend/src/lib/indices.ts` (must stay in sync).

| index | colormap | vmin | vmax |
|---|---|---|---|
| ndvi | RdYlGn | -0.2 | 0.9 |
| evi2 | RdYlGn | -0.1 | 0.8 |
| savi | RdYlGn | -0.1 | 0.7 |
| ndre | RdYlGn | -0.1 | 0.6 |
| ndmi | BrBG (diverging at 0) | -0.3 | 0.5 |

## 4. SCL clear-pixel classes

Kept as a usable land-surface observation: **4 VEGETATION, 5 NOT_VEGETATED, 6 WATER, 7 UNCLASSIFIED**.
Masked out: 0 NoData, 1 saturated/defective, 2 dark/shadow, 3 cloud shadow, 8/9 cloud med/high, 10
thin cirrus, 11 snow. The clear-pixel fraction stored with every result is computed from this set.

## 5. Clear-fraction confidence cutoffs

`clear >= 0.8` high, `>= 0.5` medium, else low. Display/confidence only; never changes which pixels
enter the stats. Below 0.5 the interpretation prompt is told to flag the pass as low-confidence.

## Open questions for the agronomist (the decisions to make)

> Update 2026-06-18/19: Q1 and Q6 are resolved - ratified 2026-06-18 (commit 2f63404). Q2-Q5 (the
> thresholds.py questions) and the §3 display ranges remain open and are tracked in
> docs/backlog/0015-agronomy-thresholds-v1-agronomist-signoff.md.

1. **WATER (SCL 6) in the clear set?** Kept in v1 so the clear fraction is a generic "usable land
   surface". For a crop-only vigour read a water pixel is not a crop observation. Exclude for crop
   AOIs (a crop-aware mask), or leave in the base set?
2. **Generic NDMI "dry" cutoff at 0.0.** The literature commonly puts water stress below ~0.1-0.2.
   Is 0.0 too conservative for the generic (non-sorghum) crops? (Sorghum already uses -0.1.)
3. **Separate SAVI / EVI2 per-crop edges.** They currently reuse the NDVI numbers. SAVI especially
   peaks lower (L=0.5). Worth calibrating distinct edges, or accept "indicative near a boundary"?
4. **Growth-stage awareness.** Bands are season-agnostic thresholds against a single pass; stage
   context is passed to the model via notes, not encoded. Should there be stage-specific tables
   (e.g. maize pre-V6 vs silking)?
5. **Per-crop coverage gaps.** cotton has no NDRE/NDMI override; sorghum has NDMI but not NDRE;
   maize/tobacco have no NDMI override. Are the generic bands acceptable for those, or add overrides?
6. **Confidence cutoffs (0.5 / 0.8).** Appropriate for the Nov-Apr convective-cloud wet season, or
   should the low-confidence hinge move?

## Sign-off

This sign-off has two distinct fields. Provisional engineering approval unblocks the layer for v1;
it does NOT satisfy the agronomist requirement, which stays open until a named agronomist reviews.

**engineering_review:** Mishael Gwede, 2026-06-19, capacity owner/engineer. Provisional approval of
the literature-derived v1 defaults (§1/§2 bands, §3 display ranges), verified unchanged from the
2026-06-03 proposal, so no `formula_version` / `prompt_version` bump was needed. The `⚑ CONFIRM`
markers in `thresholds.py`, `colormaps.py`, and `indices.ts` were replaced with provenance notes
that state these are not agronomist-confirmed. NOT an agronomist sign-off. (§4 `scl.py` and §5
`engine.py` markers were already cleared on 2026-06-18, commit 2f63404.)

**agronomist_signoff:** PENDING — not yet performed. Open questions Q2-Q5 and the §3 display ranges
are tracked in `docs/backlog/0015-agronomy-thresholds-v1-agronomist-signoff.md`.

On an actual agronomist review, the reviewer should:

- [ ] Confirm or amend §1/§2 bands and §3 display ranges; resolve Q2-Q5.
- [ ] Record the agronomist name + date in the `agronomist_signoff` field above and in the code
      provenance notes ("agronomist-confirmed <name>, <date>").
- [ ] If any band edge changes, bump `prompt_version` so prior stored reads keep their identity and
      are re-drafted under the new version (never silently overwritten). Display-range changes are
      display-only and need no version bump.
- [ ] Close backlog 0015.

engineering_review: Mishael Gwede  Date: 2026-06-19  (provisional, owner/engineer)
agronomist_signoff: ______________________  Date: __________

## Sources

- [Red-edge bands for canopy chlorophyll / nitrogen (Sentinel-2)](https://offnadir-delta.com/blog/chlorophyll-red-edge-sentinel2)
- [Understanding vegetation indices in precision agriculture (Alabama Cooperative Extension)](https://www.aces.edu/blog/topics/crop-production/understanding-vegetation-indices-used-in-precision-agriculture/)
- [NDMI for crop water stress (EOS)](https://eos.com/make-an-analysis/ndmi/)
- [Normalized Difference Moisture Index thresholds (GeoPard)](https://geopard.tech/blog/normalized-difference-moisture-index-ndmi/)
