---
name: harness-creator
description: Build or audit Complect agent instructions, task state, verification gates and session handoff; use for unreliable startup, scope drift or unsupported completion claims.
license: MIT
---

# Harness Creator for Complect

Follow root/scoped guidance, Accepted ADRs and current task scope. Read the
[project workflow](../../../docs/agents/harness.md); this skill governs agent work.

## Complete workflow

1. Record Git status and establish CodeGraph impact before substantial work.
   Inspect existing instructions, task state, commands and package manifests.
2. Assess instructions, state, verification, scope and lifecycle with
   `python3 -B infra/scripts/agent_harness.py`. Generic scores are structural only.
3. Select one feature with owning files, dependencies and acceptance criteria in
   `feature_list.json`. Read relevant patterns below; make the smallest change.
4. Run `./init.sh` for isolated harness/tool tests, then required task-specific
   Docker/PostgreSQL/API/browser checks. Missing tests or failed gates are not PASS.
5. Attach command, exit, log and source hashes before marking done. Update
   `progress.md` and `session-handoff.md`, review final diff and `git diff --check`.
6. Render an assessment if useful. Measure real effectiveness separately through
   repeated representative before/after agent sessions; a template score is not proof.

## Complete pattern routing

- Cross-session preferences/results: [memory](references/memory-persistence-pattern.md).
- Reusable workflow packaging: [skill runtime](references/skill-runtime-pattern.md).
- Permissions and concurrent calls: [tool safety](references/tool-registry-pattern.md).
- Large sessions and context loading: [context](references/context-engineering-pattern.md).
- Independent work needing delegation: [coordination](references/multi-agent-pattern.md).
- Startup, hooks and background work: [lifecycle](references/lifecycle-bootstrap-pattern.md).
- Audit recurring failure modes against [gotchas](references/gotchas.md).

These references illustrate multiple runtimes. Their example precedence, budgets,
auto-extraction, hooks and performance claims do not override Complect or Codex.
Local files never outrank explicit user instructions or Accepted ADRs. Create
runtime machinery, hooks, extractors or plugin configuration only within an
explicitly required and authorized task.

## Complete bundled tools

Scripts, templates, evals and seven references are available locally. Use
`node .agents/skills/harness-creator/scripts/<script>.mjs --help` for
`create-harness`, `validate-harness`, `render-assessment-html` and `run-benchmark`.
Generator templates are for disposable/new projects. Never run the generator
with `--force` over Complect's established root instructions or state.

Use the richer project validator for Complect; its evidence format intentionally
differs from generic templates. The English/Ukrainian entry translations retain
upstream reference text; this adapted entry is active. Source and adaptations:
[provenance](../../../docs/agents/harness-skill-provenance.json).
