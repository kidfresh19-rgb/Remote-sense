# Triage labels

The skills speak in terms of five canonical triage roles. This repo's tracker is local markdown
(see `docs/agents/issue-tracker.md`), so a "label" is the value of the `Status:` line at the top
of an issue or backlog file. The table maps each canonical role to the string used here.

| Role in mattpocock/skills | Status string in this repo | Meaning |
| ------------------------- | -------------------------- | ------- |
| `needs-triage`    | `needs-triage`    | Maintainer needs to evaluate this issue. |
| `needs-info`      | `needs-info`      | Waiting on the reporter for more information. |
| `ready-for-agent` | `ready-for-agent` | Fully specified, ready for an AFK agent to pick up. |
| `ready-for-human` | `ready-for-human` | Requires human implementation. |
| `wontfix`         | `wontfix`         | Will not be actioned. |

When a skill mentions a role (for example "apply the AFK-ready triage label"), set the `Status:`
line to the matching string from the middle column. Edit that column if the vocabulary changes.
