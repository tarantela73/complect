# Consolidation of Agent Guidelines

## Goal

Remove contradictory and duplicated instructions while preserving the useful
tool-specific guidance. `AGENTS.md` becomes the authoritative contributor and
architecture guide for every coding agent. `CLAUDE.md` contains only behavior
and integrations specific to Claude.

## Scope

### `AGENTS.md`

- Preserve all existing project and architecture rules.
- Preserve the current uncommitted Superpowers link change.
- Add a concise Git and pull-request section based on repository history.
- Require Conventional Commits in the form `type(scope): description`, with
  the scope optional when it adds no useful context.
- Require a clear PR description, linked issue when applicable, relevant
  lint/type/test results, migration notes for schema changes, and screenshots
  for visible UI changes.
- Replace brittle documentation where appropriate, such as a fixed ADR count,
  with a reference to the authoritative directory.

### `CLAUDE.md`

- Keep communication style, session initialization, Supermemory, skills/MCP,
  response format, and links to authoritative project documentation.
- Make Supermemory initialization conditional on MCP availability.
- Remove duplicated architecture, testing, queue, security, and coding rules.
- Direct Claude to `AGENTS.md`, `CONCEPTS.md`, `docs/adr/`, and
  `docs/solutions/` for project-wide requirements.

## Safety and Validation

Only `AGENTS.md` and `CLAUDE.md` are implementation targets. Existing changes
in backend and frontend files remain untouched. Before completion:

1. Inspect both diffs to ensure no project rule was accidentally lost.
2. Search for stale fixed ADR counts and duplicated queue definitions.
3. Confirm Markdown headings, links, and command examples remain valid.
4. Confirm the existing user edit in `AGENTS.md` is preserved.

No application tests are required because the change affects documentation
only.
