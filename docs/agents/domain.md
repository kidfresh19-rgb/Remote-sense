# Domain docs

How the engineering skills consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root: the domain glossary and module map.
- **`docs/adr/`**: read the ADRs that touch the area you are about to work in. This repo is
  single-context, so all ADRs live here. There is no `CONTEXT-MAP.md` and no per-context
  `src/*/docs/adr/`.

If any of these are missing for a given topic, proceed silently. Do not flag their absence or
suggest creating them upfront. The producer skill (`/grill-with-docs`) creates and sharpens them
lazily as terms and decisions actually get resolved.

## Layout (single-context)

```
/
├── CONTEXT.md            <- domain glossary + module map
├── CLAUDE.md             <- binding build rules (Section 6 routes work to agents/skills)
├── PLAN.md               <- product spec
└── docs/
    ├── adr/              <- 0001-ports-and-adapters ... 0008-ordered-reimplementation
    ├── prd/              <- PRDs (issue tracker)
    ├── backlog/          <- implementation slices (issue tracker)
    └── process/WORKFLOW.md
```

## Use the glossary's vocabulary

When your output names a domain concept (an issue title, a refactor proposal, a hypothesis, a
test name), use the term as defined in `CONTEXT.md`. Do not drift to synonyms the glossary
avoids. If the concept is not in the glossary yet, that is a signal: either you are inventing
language the project does not use (reconsider), or there is a real gap (note it for
`/grill-with-docs`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding
it. For example: "Contradicts ADR-0006 (AgriTrack sync contract), but worth reopening because
...". Anything touching a Section 1 architecture invariant needs its own ADR.
