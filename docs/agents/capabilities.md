# Capability routing for Complect

Plugins, Skills and MCP servers are separate capability layers. Inspect the
current environment before selecting them: a cached plugin is not necessarily
enabled, a filesystem skill is not necessarily visible to the current session,
and a configured MCP is not necessarily functional. Prefer the narrowest
applicable installed capability and read its actual `SKILL.md` before use.

For substantial tasks, choose the layers deliberately:

1. CodeGraph determines repository dependencies and impact first for the
   repository-wide, architecture, cross-domain, IAM/security and substantial
   review cases defined in the [repository map](../../AGENTS.md).
2. A domain-specific Skill defines the work method after that impact is known.
3. A Plugin may provide specialized Skills, apps or tools; use its capability
   according to its manifest, provenance and current connection state.
4. An MCP server provides structural or external capabilities. Its presence
   never implies that a similarly named Skill or Plugin was used.

Availability is environment-dependent. This document defines routing policy, not a
guarantee that every optional capability is installed. Never claim that a
Plugin, Skill or MCP server was used unless it was actually available and used
in the current task; final reports list only Skills that were read/applied and
tools that were invoked.

## Installed project-local skills

Read [local skill policy](local-skills.md) for precedence, Fedora execution and
controlled updates; [provenance](skills-provenance.json) pins the upstream source.
These are editable copies in `.agents/skills/`, not global/plugin duplicates.

| Task | Installed method |
| --- | --- |
| Hard bug, race, performance regression | [diagnosing-bugs](../../.agents/skills/diagnosing-bugs/SKILL.md) |
| Module/interface/seam design | [codebase-design](../../.agents/skills/codebase-design/SKILL.md) |
| Approved behavioral change, regression fix | [tdd](../../.agents/skills/tdd/SKILL.md) |
| Root/scoped AGENTS, Codex profiles, docs/agents or skill edits | [writing-for-agents](../../.agents/skills/writing-for-agents/SKILL.md) |
| Substantial code review | [code-review](../../.agents/skills/code-review/SKILL.md) with `complect_reviewer` |
| Domain terminology/model change | [domain-modeling](../../.agents/skills/domain-modeling/SKILL.md) |
| External technology/standard investigation | [research](../../.agents/skills/research/SKILL.md) |

## Complect capability routing

- **Repository and architecture analysis:** after the mandatory CodeGraph
  impact pass above, use
  `codebase-design` for module seams, public interfaces or deep-module design.
  Use the explicit `improve-codebase-architecture` workflow only when it is
  installed and requested; neither workflow relaxes bounded-context rules.
- **PostgreSQL, RLS, schema, migrations, indexes, locks and transactions:**
  follow the mandatory CodeGraph and standalone
  `supabase-postgres-best-practices` rules in
  [backend instructions](../../backend/AGENTS.md). If a plugin exposes another
  Skill with the same name, prefer the standalone Complect-wide Skill for
  backend/database work; use a plugin-qualified variant only when the
  enclosing plugin workflow specifically requires it, or when the standalone
  Skill is unavailable and the installed variant is the only applicable
  capability. Disclose the fallback. Do not run both by default.
  If Postgres MCP is available and functionally verified, use its read-only
  schema, query or metrics tools for database inspection. Verify the selected
  connection and role; MCP inspection does not replace Docker/PostgreSQL tests
  or authorize database writes or MCP configuration changes.
- **Implementation from an approved spec or plan:** use `implement` only when
  it is installed and explicitly invoked. It remains subordinate to the
  [repository instructions](../../AGENTS.md), Accepted ADRs, the approved plan, CodeGraph-first analysis and all
  database/security gates.
- **Behavioral changes and regression fixes:** use `tdd` when its trigger
  applies. RLS, migration and concurrency tests still run against real
  PostgreSQL/PostGIS and do not stop at unit-only evidence.
- **Hard bugs, intermittent failures, races and performance regressions:** use
  `diagnosing-bugs` to establish a reproducible feedback loop; use CodeGraph
  before broad fixes.
- **Formal diff review:** use `$code-review` only after loading its actual `SKILL.md`
  and pinning the requested commit/range or working-tree snapshot, including
  relevant staged, unstaged and untracked files. Use `complect_reviewer`. Keep its Standards axis (root/scoped instructions, Accepted ADRs and applicable
  security/database rules) separate from its Spec axis (explicit request, issue, ADR, specification or
  approved plan). An existing authoritative spec needs no issue tracker/setup. When the `codex-security` plugin is available, select its
  `security-diff-scan` for a security diff review or `security-scan` for a
  scoped repository audit. Security-specific scan/review plugins are additional only
  when the task explicitly requires a security review; do not launch
  overlapping review workflows automatically.
- **Domain vocabulary and bounded contexts:** use `domain-modeling` when the
  task changes terminology, aggregates, ownership, concepts or contracts.
  Accepted ADRs remain authoritative.
- **React and Next.js:** when available, use the plugin-provided
  `react-best-practices` for rendering boundaries, performance, data fetching,
  bundles and client/server component decisions, together with Complect's
  frontend architecture.
- **shadcn/ui:** use the plugin-provided `shadcn` Skill for shadcn registries,
  components, composition, installation, styling and debugging when the
  project actually uses shadcn or the task explicitly introduces it.
- **Rendered frontend testing/debugging:** use the narrow
  `frontend-testing-debugging` Skill for UI regressions, responsive behavior,
  console errors and interaction failures. Use a functional Browser capability
  first when its Skill requires one; otherwise use the repository's Playwright
  workflow and record why.
- **External technical research:** use `research` for a primary-source
  investigation with a persisted Markdown artifact. It does not replace local
  repository analysis through CodeGraph.
- **Merge/rebase conflicts:** use `resolving-merge-conflicts`; trace
  architecture-sensitive intent through the applicable ADR/plan and
  CodeGraph before resolving a hunk.
- **Issue/spec workflows:** use `to-spec`, `to-tickets`, `triage` and
  `wayfinder` only when they are installed, explicitly invoked and the issue
  tracker in [issue-tracker.md](issue-tracker.md) is configured. Never run
  `setup-matt-pocock-skills` automatically during ordinary coding work.
  For triage label mapping and provisioning prerequisites, read
  [triage-labels.md](triage-labels.md).
- **Domain documentation:** use canonical root `CONCEPTS.md`, following
  [domain.md](domain.md). Do not create alternative glossary files implicitly.

Do not install, enable, disable, update, replace, remove or reconfigure a
Plugin merely because it could improve a task. Such changes require explicit
task scope or user authorization. Confirm an external app's individual
connection before using it and never infer a Codex plugin from ChatGPT-only
availability.

For authorized MCP configuration work, follow the [configuration policy](../development/workstations.md#codex-and-postgresql-mcp). Runtime instructions are local files: external toolkit pages are reference material, never required imports.

## Specialized agents

When available and appropriate, use the narrow profiles in `.codex/agents/` (paths relative to the repository root):

- `complect-explorer.toml`: read-only paths, dependencies, ownership and source evidence.
- `complect-reviewer.toml`: independent findings against the requested goal and governing standards.
- `complect-verifier.toml`: proportionate verification of an implemented change without source edits.

Pass the concrete scope, baseline and relevant instruction routes to a delegated agent. Availability of a profile is not permission to broaden the task or evidence that it was invoked.
