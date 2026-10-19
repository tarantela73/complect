# Issue tracker: GitHub

Issues and specs for this repository live in GitHub Issues for `tarantela73/complect`. Use the `gh` CLI for all operations.

## Authentication prerequisite

Before reading from or writing to GitHub Issues, require `gh auth status` to succeed for `github.com`. If it fails, stop the tracker operation and ask the user to run `gh auth login -h github.com`.

## Verified issue links

For an authorized sub-issue or blocking edge, use the narrow helper from the repository root:

```bash
python3 -B -m infra.scripts.github_issue_links sub-issue \
  --repo tarantela73/complect --parent "$MAP_ISSUE_NUMBER" --child "$CHILD_ISSUE_NUMBER"
python3 -B -m infra.scripts.github_issue_links blocked-by \
  --repo tarantela73/complect --issue "$BLOCKED_ISSUE_NUMBER" --blocker "$BLOCKER_ISSUE_NUMBER"
```

Set these task-specific variables to the selected issue numbers. The helper authenticates with existing `gh` on `github.com`, reads both issues and verifies their repository, numbers and database IDs. Issues and PRs are distinct; this helper accepts issues only. It creates at most one edge per invocation and never creates an issue or replaces a different parent. Repeating an existing correct edge performs reads only.

The sub-issue endpoint requires `sub_issue_id`; `blocked_by` requires `issue_id`. The helper resolves database IDs itself and sends `replace_parent=false` for sub-issues. Use the helper instead of reconstructing these payloads. It reads every relationship page (100 items, up to 20 pages); an incomplete, duplicate or malformed page blocks confirmation instead of implying an absent edge.

Readback checks the edge, child parent where applicable, and the source issue's title/body/state/label names against the preflight fingerprint. It does not PATCH issue fields, update labels or perform automatic rollback. Concurrent metadata changes produce a failure for review. Raw issue bodies, auth output and API error text are not included in the JSON summary.

| Result | Exit | Meaning |
| --- | --- | --- |
| PASS | 0 | Relationship and unchanged source metadata confirmed; `write_attempted` distinguishes a new write from a verified no-op |
| FAIL | 1 | Invalid identity/input, parent conflict, HTTP write error such as 422, or observed metadata change |
| BLOCKED | 2 | Authentication or preflight read/schema/pagination cannot be established; no write attempted |
| UNKNOWN | 3 | A write may have applied, but its outcome/readback cannot be confirmed |

A timeout remains UNKNOWN even if the immediate readback sees the edge. A later explicit retry can confirm it without another POST. HTTP 422 remains a failed operation and is not silently converted into PASS. After an error, inspect `write_attempted`, `write_http_status`, `relation_verified` and the diagnostic code before retrying. The helper never guesses a new target or rewrites the parent body as a fallback; any separately authorized task-list/map editing remains a separate operation.

The public boundary is `ensure_link` and its CLI. Contract tests use controlled `gh` responses in network-disabled Docker and do not mutate real issues. Apply the project verification runner when collecting their test/lint/type evidence.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body-file <path>`. Prepare multi-line Markdown in that file before submitting it.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`.
- **Apply or remove labels**: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- **Close**: `gh issue close <number> --comment "..."`.

Infer the repository from `git remote -v`; `gh` does this automatically when run inside this clone.

## Pull requests as a triage surface

**PRs as a request surface: no.** Set this to `yes` only if the repository starts treating external PRs as feature requests; `triage` reads this flag.

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh api --paginate 'repos/tarantela73/complect/pulls?state=open&per_page=100'`, then keep only `author_association` values `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE`. Fetch comments separately for a selected PR when needed.
- **Comment, label, or close**: use `gh pr comment`, `gh pr edit --add-label` or `--remove-label`, and `gh pr close`.

GitHub shares one number space across issues and PRs. For a bare `#42`, resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `wayfinder`. The map is a single issue with child issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes, Decisions-so-far, and Fog body. Create it with `gh issue create --label wayfinder:map`.
- **Child ticket**: create the issue, then link it to the map using the verified `sub-issue` helper above. Where native sub-issues are unavailable, report that result; task-list editing of the map and a `Part of #<map>` line are separate operations under the authorized workflow. Labels are `wayfinder:<type>` where type is `research`, `prototype`, `grilling`, or `task`.
- **Blocking**: use GitHub native dependencies through the verified `blocked-by` helper above. If dependencies are unavailable, report that result; the workflow's `Blocked by: #<n>, #<n>` text fallback is a separate operation on the child body.
- **Frontier query**: list the map's open children, drop any with an open blocker or an assignee, and take the first in map order.
- **Claim**: `gh issue edit <n> --add-assignee @me`; this is the session's first write.
- **Resolve**: comment with the answer, close the ticket, then append a context pointer to the map's Decisions-so-far.
