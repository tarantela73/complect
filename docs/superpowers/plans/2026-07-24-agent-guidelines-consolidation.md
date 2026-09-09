# Superseded — Agent Guidelines Consolidation Plan

**Status:** Superseded on 2026-09-10.

This July plan described a transitional split between `AGENTS.md` and a
separate local Claude instruction file. It must not be used as a current
implementation guide: its proposed hierarchy and integration-specific details
were superseded by the repository-wide consolidation.

## Current decision

`AGENTS.md` is the only canonical source of repository agent instructions.
It defines the instruction hierarchy, mandatory workflow, security boundaries,
verification expectations and pointers to authoritative ADRs and active plans.
The repository-tracked Claude compatibility file contains a pointer only and
establishes no project policy.

Accepted ADRs remain authoritative for their decisions; this instruction
consolidation does not alter them. If an instruction conflicts with an
Accepted ADR, record it as a finding and resolve it explicitly.

## Historical record

The original plan remains available in Git history for audit purposes. Do not
copy its former wording, tool inventory, memory integration or Claude-specific
guidance into current repository instructions.

## Replacement validation

The current consolidation is complete only when:

- root `AGENTS.md` has the named instruction hierarchy and canonical workflow;
- all active repository references point to `AGENTS.md`, the ADR register or
  the relevant architecture document rather than an obsolete local instruction
  file;
- `git diff --check` passes and unrelated working-tree changes remain intact;
- a final standards and specification review of the resulting tracked diff has
  been completed.
