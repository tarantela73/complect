# Triage Labels

The skills speak in terms of five canonical triage roles. This file defines
their intended label mapping for this repository's issue tracker; it does not
provision labels or assert that they already exist on GitHub.

| Label in mattpocock/skills | Label in our tracker | Meaning                                   |
| -------------------------- | -------------------- | ----------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue   |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information  |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent   |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation             |
| `wontfix`                  | `wontfix`            | Will not be actioned                       |

When a skill mentions a role, use the corresponding label string from this table.

Before applying a label, verify it exists in the configured tracker. If a
mapped label is missing, report the missing prerequisite and create it only
within explicitly authorized tracker-configuration work. Do not silently
substitute another label or treat an unlabelled issue as ready.

Edit the right-hand column if the repository adopts a different label vocabulary.
