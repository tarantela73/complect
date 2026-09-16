---
name: code-review
description: Review scoped commits, branches or working-tree changes on separate Standards and Spec axes using the existing Complect reviewer profile.
---

Follow root/scoped instruction hierarchy. For skill installation, provenance or updates, see [local skill policy](../../../docs/agents/local-skills.md); ordinary skill use does not require loading it.


Always assess both axes using the existing [complect_reviewer profile](../../../.codex/agents/complect-reviewer.toml):

- **Standards**: root/scoped instructions, Accepted ADRs, applicable plan and database/security contracts.
- **Spec**: the explicit task and its authoritative requirements.

This is a review method, not a second reviewer architecture. No edits, commit or push.

## Process

### 1. Pin the requested snapshot

Use the scope already specified by the user; ask only if the intended baseline
is ambiguous. Record resolved commit IDs, file scope, commands and exclusions.
For working-tree review, record status and hashes of scoped file contents so
subagents review the same snapshot; recheck for changes before reporting.

| Requested scope | Evidence |
| --- | --- |
| Branch against merge-base | Resolve refs, then `git diff <base>...HEAD` and relevant commit list |
| Exact committed range | `git diff <start> <end>`; for a single commit include its patch |
| Staged | `git diff --cached` against the declared base, normally HEAD |
| Unstaged | `git diff` against the index |
| Current working tree | Inspect staged and unstaged diffs plus `git diff HEAD` for net tracked changes; enumerate `git ls-files --others --exclude-standard` and read relevant untracked files |

An empty committed diff does not mean the working tree is empty. Include only
requested/relevant files; preserve unrelated changes. Do not stage untracked
files merely to review them. A changed snapshot requires refreshing affected
evidence, never silently combining different states.

### 2. Identify the spec source

Use the explicit user request and any named ADR, approved plan, repository
specification, issue or document. A known authoritative spec needs no issue
tracker. Read `docs/agents/issue-tracker.md` only when fetching an issue is
necessary and it exists; do not run setup because tracker files are missing.
If no spec is available, report that limitation for the Spec axis while
continuing Standards review.

### 3. Identify the standards sources

Read root/scoped AGENTS.md by impact, applicable Accepted ADRs, approved active plans and relevant project docs. CodeGraph precedes targeted reads for substantial review. Keep code-smell heuristics subordinate to these sources.

Raise code smells only when they materially affect correctness, architecture, security, maintainability or the requested scope. Explain the concrete impact and label heuristic judgments as such; documented Complect standards take precedence. Skip observations already enforced by tooling.

### 4. Run the two axes through complect_reviewer

Use independent reviewer instances when change size, risk or domain separation justifies an additional pass. One `complect_reviewer` may cover both axes for a small bounded change. Never install another reviewer profile. Pass each reviewer the fixed snapshot/diff, acceptance criteria, relevant Complect standards and specific risk areas.

**Standards sub-agent prompt** should include:

- The full snapshot manifest, diff commands and relevant commit list.
- Relevant standards-source files, acceptance criteria and specific risk areas; no full generic code-smell catalogue.
- The brief: "Report material violations of documented standards with the file, rule and affected hunk. Distinguish hard violations from heuristic judgments; explain concrete impact. Keep findings concise without truncating material security or correctness evidence."

**Spec sub-agent prompt** should include:

- The snapshot manifest, diff commands and relevant commit list.
- The path or fetched contents of the spec.
- The brief: "Report: (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for (scope creep); (c) requirements that look implemented but where the implementation looks wrong. Quote the spec line for each finding. Keep findings concise without truncating material security or correctness evidence."

If the spec is missing, skip the Spec sub-agent and note this in the final report.

### 5. Aggregate

Present the two reports under `## Standards` and `## Spec` headings, verbatim or lightly cleaned. Do **not** merge or rerank findings, because the two axes are deliberately separate (see _Why two axes_).

End with a one-line summary: total findings per axis, and the worst issue _within each axis_ (if any). Don't pick a single winner across axes: that's the reranking the separation exists to prevent.

## Why two axes

A change can pass one axis and fail the other:

- Code that follows every standard but implements the wrong thing → **Standards pass, Spec fail.**
- Code that does exactly what the issue asked but breaks the project's conventions → **Spec pass, Standards fail.**

Reporting them separately stops one axis from masking the other.
