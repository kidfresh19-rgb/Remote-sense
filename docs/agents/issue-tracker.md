# Issue tracker: local markdown (docs/)

Issues, PRDs, and the backlog for this repo live as markdown under `docs/`, not in a hosted
tracker. The Azure DevOps remote carries code; work items are tracked here in the repo so the
backlog travels with the code and both machines share it. The board's hosted-tracker target is
deferred, so this stays local for now.

## Where things live

- **PRDs.** `docs/prd/NNNN-<slug>.md`, numbered from `0001`. Produced by `/to-prd`.
- **Implementation issues and slices.** `docs/backlog/NNNN-<slug>.md`, numbered from `0001`.
  Produced by `/to-issues` as vertical tracer-bullet slices, each recording its blocking order
  inline.
- **Open backlog and quick items.** `TODO.md` at the repo root, groomed with `/triage`.
- **Decisions.** `docs/adr/NNNN-<slug>.md` (consumer rules in `docs/agents/domain.md`).

## Conventions

- Number files monotonically per directory (`0001`, `0002`, and so on). Never reuse a number.
- Record triage state as a `Status:` line near the top of each backlog or issue file, using the
  role strings in `docs/agents/triage-labels.md`.
- For a multi-slice feature, list its slices and their blocking order in a short lead section of
  the PRD, then cross-link each `docs/backlog/` file back to that PRD.
- Append conversation and comments to the bottom of the file under a `## Comments` heading.

## When a skill says "publish to the issue tracker"

Create a new numbered file in `docs/prd/` for a PRD or `docs/backlog/` for an issue or slice.
Write nothing outside `docs/`. Add a one-line pointer to `TODO.md` when it is an open item that
should surface in the groomed backlog.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user normally passes the path or the NNNN number
directly, for example "backlog 0001" resolves to `docs/backlog/0001-*.md`.
