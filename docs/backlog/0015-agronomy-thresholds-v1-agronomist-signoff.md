# Backlog 0015 — Agronomist sign-off of v1 interpretation thresholds + colormaps

- Status: ready-for-human
- Type: HITL (requires an agronomist; an engineer can propose, only an agronomist can sign off)
- Parent: `docs/agronomy-thresholds-v1-review.md` (PLAN §10 step 4)
- Blocks: nothing (the layer ships on provisional engineering approval); closing this converts
  "provisional" to "agronomist-confirmed"
- Driver: best run with the `agronomy-scientist` agent for a literature-backed recommendation
  first, then a human agronomist confirms.

## Why this exists

On 2026-06-19 the v1 interpretation bands (`packages/rs_interpret/thresholds.py` §1/§2) and the
display colormap ranges (`packages/rs_analysis/colormaps.py` + mirror
`frontend/src/lib/indices.ts` §3) received **provisional engineering approval only** so the
interpretation layer could be unblocked. The `# ⚑ CONFIRM` markers were replaced with provenance
notes that explicitly say these are NOT agronomist-confirmed and point here. This item keeps the
real agronomist review a tracked, recoverable TODO rather than a lost marker.

The numeric defaults were verified unchanged from the 2026-06-03 literature proposal at the time
of provisional approval, so **no `formula_version` / `PROMPT_VERSION` bump was needed** then.

## Already resolved (do not re-open)

- **Q1 — WATER (SCL 6) in the clear set** and **Q6 — confidence cutoffs (0.5 / 0.8)** were
  ratified 2026-06-18 in commit `2f63404` ("SCL ratification"). `scl.py` and `engine.py` carry
  the completed wording. Out of scope here.

## Open questions for the agronomist (verbatim from the review doc, §1/§2/§3 only)

- [ ] **Q2 — Generic NDMI "dry" cutoff at 0.0.** The literature commonly puts water stress below
  ~0.1-0.2. Is 0.0 too conservative for the generic (non-sorghum) crops? (Sorghum already uses
  -0.1.)
- [ ] **Q3 — Separate SAVI / EVI2 per-crop edges.** They currently reuse the NDVI numbers. SAVI
  especially peaks lower (L=0.5). Worth calibrating distinct edges, or accept "indicative near a
  boundary"?
- [ ] **Q4 — Growth-stage awareness.** Bands are season-agnostic thresholds against a single pass;
  stage context is passed to the model via notes, not encoded. Should there be stage-specific
  tables (e.g. maize pre-V6 vs silking)?
- [ ] **Q5 — Per-crop coverage gaps.** cotton has no NDRE/NDMI override; sorghum has NDMI but not
  NDRE; maize/tobacco have no NDMI override. Are the generic bands acceptable for those, or add
  overrides?
- [ ] **§3 display colormap ranges.** Display-only (a colour stretch, never classification or
  stored values), so lower stakes, but still confirm the per-index `vmin`/`vmax` are right for the
  Zimbabwe cropping window. Any change here is display-only and needs no version bump.

## Acceptance criteria (closing this item)

- [ ] A named agronomist reviews §1 base bands, §2 per-crop overrides, and §3 display ranges and
  resolves Q2-Q5.
- [ ] If any **band edge** changes: amend `thresholds.py`, bump `PROMPT_VERSION` in
  `packages/rs_interpret/prompts.py`, and let the worker re-draft. Re-draft is idempotent
  (`insert_interpretation` is `on_conflict_do_nothing` on `uq_interpretation_identity`, which
  includes `prompt_version`), so prior reviewed reads keep their identity and are not overwritten.
  Never a manual SQL migration.
- [ ] If any **display range** changes: amend `colormaps.py` and the `indices.ts` mirror together;
  no version bump (display-only).
- [ ] Replace the provenance notes in `thresholds.py`, `colormaps.py`, and `indices.ts` with
  "agronomist-confirmed <name>, <date>".
- [ ] Record the agronomist name + date in `docs/agronomy-thresholds-v1-review.md` Sign-off
  section (the `agronomist_signoff:` field), flip its status from PENDING, and close this item.
