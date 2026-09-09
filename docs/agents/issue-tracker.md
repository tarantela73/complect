# Issue tracker: GitHub

Issues and specs for this repository live in GitHub Issues for `tarantela73/complect`. Use the `gh` CLI for all operations.

## Authentication prerequisite

Before reading from or writing to GitHub Issues, require `gh auth status` to succeed for `github.com`. If it fails, stop the tracker operation and ask the user to run `gh auth login -h github.com`.

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
- **Child ticket**: an issue linked to the map as a GitHub sub-issue through `gh api`. Where sub-issues are unavailable, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels are `wayfinder:<type>` where type is `research`, `prototype`, `grilling`, or `task`.
- **Blocking**: use GitHub native issue dependencies. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the numeric database id from `gh api repos/<owner>/<repo>/issues/<n> --jq .id`. If dependencies are unavailable, use a `Blocked by: #<n>, #<n>` line at the top of the child body.
- **Frontier query**: list the map's open children, drop any with an open blocker or an assignee, and take the first in map order.
- **Claim**: `gh issue edit <n> --add-assignee @me`; this is the session's first write.
- **Resolve**: comment with the answer, close the ticket, then append a context pointer to the map's Decisions-so-far.
