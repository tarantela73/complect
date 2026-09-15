# Complect local skill policy

Read this before applying any skill under `.agents/skills/`. These are editable,
versioned Codex skills; each installed directory contains its supporting resources.

## Authority and runtime

Follow explicit task → Accepted ADR → approved active implementation plan →
root/scoped AGENTS.md → project/domain docs → skill methodology. The
[repository map](../../AGENTS.md) and its mandatory impact-based routes apply
regardless of the current directory. Skills do not change architectural decisions.

Run CodeGraph first for architecture, cross-domain, IAM/security and substantial
review/analysis, then targeted reads. Follow [backend guidance](../../backend/AGENTS.md)
for database work and [IAM guidance](../../backend/apps/users/AGENTS.md) when
its security graph is affected, including changes outside users.

Preserve bounded-context ownership, protected CAS mutations, tenant isolation,
FORCE RLS, Legal Hold and canonical graph locks, Expand/Contract, Transactional
Outbox, frozen `/api/v1/`, generated SDK and PII/audit restrictions. Keep domain
vocabulary in canonical [CONCEPTS.md](../../CONCEPTS.md), following
[domain.md](domain.md). Skills must not create a competing glossary.

Development/test execution uses repository Docker commands from the repository
root and real PostgreSQL/PostGIS where database semantics are under test.
Follow [workstations.md](../development/workstations.md) for current tooling.
On native Fedora use Linux-native paths and Docker; do not substitute Podman or
assume Docker Desktop/WSL. Diagnose SELinux evidence without disabling enforcement
or changing permanent policy. Missing tools are reported, not automatically
installed or reconfigured. Preserve user work and explicit commit/push scope.

## Discovery and updates

The seven skill directories in `.agents/skills/` are the single project-local
copies. Codex discovers this location; no second global copy, external runtime
include or Claude-specific configuration is needed. Supporting files are local;
research may need network access for external evidence and must report its absence.

[skills-provenance.json](skills-provenance.json) records the upstream commit,
source paths and original SHA-256 values for comparison. The included
[upstream license](../../.agents/skills/LICENSE) accompanies the vendored material.
The initial install used the official `skills@1.5.26` CLI with Codex, seven named
skills and editable copies. Direct `npx skills@latest` timed out; the same published
installer and dependencies were downloaded over HTTPS with npm integrity checks
and run against the recorded upstream archive. The generated temporary-source
lock was discarded; repository files plus provenance are the portable artifact.

Updates require explicit scope. Do not use automatic `npx skills update` as an
ordinary overwrite workflow for these adapted skills. Do not run
`npx skills update --help` assuming it is guaranteed read-only; inspect already
available documentation or source for command semantics instead.

1. Begin with read-only comparison against provenance: repository, pinned previous
   upstream commit, candidate current upstream commit and original upstream hashes.
2. Fetch the old and candidate new upstream snapshots to temporary storage outside
   the installed skill directories. Verify the old snapshot against recorded hashes.
3. Compare old upstream → new upstream separately from old upstream → current
   Complect-adapted local files, including added, removed and renamed resources.
4. Merge relevant upstream changes while preserving Complect overrides. Never
   automatically replace `.agents/skills/<skill>/` with a new upstream snapshot.
   If an adaptation conflict cannot be resolved unambiguously, stop the affected
   update and report a finding; do not choose an authority by guesswork.
5. Validate frontmatter, JSON, local links, discovery and complete independent
   Standards/Spec review. Update `skills-provenance.json` to the reviewed upstream
   base only after all required checks succeed. Keep the prior provenance on failure.

Do not run `setup-matt-pocock-skills` automatically.

## Adaptation boundaries

- `diagnosing-bugs`: database defects need real database feedback; capture only redacted evidence.
- `codebase-design`: accepted ownership/transaction/test contracts justify single-implementation Protocols.
- `tdd`: existing approved interfaces establish seams; database assertions can be contract checks.
- `writing-for-agents`: preserve critical guardrails and intentional local reminders while keeping root short.
- `code-review`: use the existing reviewer profile for Standards/Spec and the exact requested snapshot, including relevant untracked work.
- `domain-modeling`: update accepted terms in CONCEPTS.md and use the existing ADR template.
- `research`: separate repository facts, external facts and recommendations; adoption follows project decisions.
