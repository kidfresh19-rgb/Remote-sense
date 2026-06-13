# Working process: idea to merge

This is the standard path a unit of work travels in remote-sense. It exists because agents have no
memory between sessions: each phase leaves a durable artifact so the next session (or the next
agent) reads the artifact instead of re-deriving context. Follow it to keep work linear. Use the
fast lane when a change is too small to deserve the full path.

The phases are backed by the installed skill catalog (grill-with-docs, to-prd, to-issues, tdd,
prototype, handoff, triage, improve-codebase-architecture, review). Binding rules are in `CLAUDE.md`
(Section 6 is the short, binding summary of this file). The product spec is `PLAN.md`. The domain
map and shared language are `CONTEXT.md`. Decisions are in `docs/adr/`.

## The pipeline at a glance

| # | Phase | Skill / agent | Durable artifact |
|---|-------|---------------|------------------|
| 0 | Intake | you | a decision: fast lane or full pipeline |
| 1 | Shape | `/grill-with-docs` (+ `architect` for invariants, `/prototype` if uncertain) | sharpened intent + shared language, updated CONTEXT.md / ADR |
| 2 | Specify | `/to-prd` | a PRD (issue) |
| 3 | Slice | `/to-issues` | vertical tracer-bullet issues, with blocking order |
| 4 | Plan | `architect` (read-only) | interface + file-by-file change list + test strategy |
| 5 | Build | owning specialist + `/tdd` | code + tests, red-green-refactor |
| 6 | Verify | `qa-engineer`, `/verify`, `/review`, `/code-review`, `/security-review` | green suite, review notes, matrix row |
| 7 | Land | you | Conventional Commit, ADR if an invariant moved |
| 8 | Hygiene | `/improve-codebase-architecture` | refactor candidates (periodic) |

Two tools run across phases rather than inside one: `/triage` grooms the backlog that feeds Intake,
and `/handoff` carries context to the next session. Both are covered under Backlog and continuity
below.

## Phases

**0. Intake.** Is this a small change (typo, one-file fix, obvious bug, copy tweak, dependency bump)?
Take the fast lane: jump to Build with a test, then Verify. Otherwise run the full pipeline. When
unsure, run the pipeline. The early phases are cheap.

**1. Shape: align before you build.** Run `/grill-with-docs` to resolve the whole design tree before
any code. It interviews you against `CONTEXT.md` and `docs/adr/`, sharpens the shared domain
language, and updates those docs inline so the next slice reuses established terms instead of
reinventing them (this is what keeps the codebase consistent and low-repetition). For anything
touching an architecture invariant (CLAUDE.md Section 1), bring in the `architect` agent and expect
an ADR. When a UI, state, or product decision is genuinely uncertain, spike it with `/prototype`
first: a throwaway you can react to beats arguing in the abstract. Leave Shape only when the design
tree has no open branches.

**2. Specify.** Run `/to-prd` to turn the shaped intent into a PRD with user stories. The PRD is the
persistent record of what and why. It survives context loss.

**3. Slice.** Run `/to-issues` to cut the PRD into vertical tracer-bullet slices. Each slice cuts
through every layer and is independently shippable, with explicit blocking relationships so
independent slices can run in parallel. The anti-pattern is horizontal slicing ("all the schema,
then all the API").

**4. Plan.** Hand one slice to the `architect` agent (read-only). It returns the interface/contract,
a file-by-file change list, the test strategy, the risks, and the sequencing: an executable hand-off
the specialist runs without re-deriving context.

**5. Build.** The owning specialist (see the routing table in CLAUDE.md Section 6) implements the
slice via `/tdd`: confirm the interface, write one failing test, make it pass, refactor. One slice at
a time. Stop when the slice is green, not when most of it works.

**6. Verify.** Three reviews, each answering a different question. `/review` checks the work against
the repo's standards and, when the slice came from a PRD or issue, the originating spec (parallel
sub-agents). `/code-review` checks the diff
for correctness bugs and cleanups. `/security-review` is additionally required for auth, RBAC,
secrets, and sync changes (CLAUDE.md Section 4). Alongside them, `qa-engineer` hardens edge cases
and, for any index, adds the validation-matrix row against the Copernicus Browser, and `/verify`
runs the thing and observes behaviour. A change is not done until it has tests, ruff is clean, and
pytest is green (Section 3).

**7. Land.** Conventional Commit on a short-lived branch. If an invariant moved, the ADR written in
Shape lands with it.

**8. Hygiene (periodic).** Weekly, or after a development surge, run `/improve-codebase-architecture`
to find shallow modules, tight coupling, and unclear test boundaries. A clean codebase is what keeps
agent output good; a messy one compounds.

## Fast lane

For changes too small to justify the pipeline, skip phases 1 to 4. Still write or update a test
(Build) and still run Verify (`/code-review` at minimum). The fast lane skips ceremony, never tests.

## Backlog and continuity

These two skills hold the process together between units of work and between sessions.

- **`/triage`** turns messy ideas, bug reports, and feature requests into actionable, grabbable
  issues and grooms the backlog (including preparing work for an unattended agent). It is the front
  door: a groomed issue is what Intake picks up.
- **`/handoff`** compacts the current conversation into a handoff document when context fills or a
  session ends, so the next session or agent resumes without re-deriving. It is the persistence
  principle applied to the conversation itself.

## Why this is linear (the persistence principle)

"Haphazard and non-linear" is what you get when each session re-derives context and re-decides
direction. The fix is not more planning in the moment. It is leaving a durable artifact at every
phase: PRD, issue, ADR, memory file, validation-matrix row, test, handoff doc. The artifact is the
memory. Re-entry becomes "read the artifact," not "reconstruct the reasoning."

## Skills catalog (phase map)

Every skill in the catalog, and where it lands in the pipeline:

| Skill | Phase | Role |
|-------|-------|------|
| `/triage` | Backlog | Groom messy input into grabbable issues |
| `/grill-with-docs` | Shape | Interview + domain-driven design; align and sharpen shared language |
| `/prototype` | Shape / Plan | Throwaway spike for uncertain UI, state, or product calls |
| `/to-prd` | Specify | Conversation + codebase into a PRD |
| `/to-issues` | Slice | PRD into vertical tracer-bullet issues |
| `/tdd` | Build | One behaviour at a time, red-green-refactor |
| `/handoff` | Continuity | Compact context across sessions |
| `/review` | Verify | Work vs standards and spec, parallel sub-agents |
| `/improve-codebase-architecture` | Hygiene | Refactor for testability and agent navigation |

## Worked example

Task: fill `CROP_BANDS` for maize / tobacco / sorghum / cotton in
`packages/rs_interpret/thresholds.py` (a known `# CONFIRM` gap).

- Intake: domain-science change with correctness risk, so full pipeline.
- Shape: `/grill-with-docs` pins down which crops, which season stages, and the source of truth for
  the values, sharpening the threshold vocabulary in `CONTEXT.md`; `agronomy-scientist` verifies them
  with WebSearch. Result: sharpened intent.
- Specify / Slice: `/to-prd` then `/to-issues`, one slice per crop, maize first.
- Plan: `architect` confirms `classify()` already prefers a crop override, so this is config plus
  tests, no interface change. Result: a file-by-file list.
- Build: `agronomy-scientist` via `/tdd` fills the maize bands, bumps `PROMPT_VERSION` if prompt copy
  changes, and tests classification at the band edges.
- Verify: `qa-engineer` covers boundary values; `/review` checks it against the spec; `/code-review`.
  Result: green.
- Land: `feat: tune maize CROP_BANDS`. No invariant moved, so no ADR.
