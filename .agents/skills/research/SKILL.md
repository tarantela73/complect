---
name: research
description: Investigate external technologies and standards against primary sources, preserving cited findings and separating them from Complect repository facts and decisions.
---

Follow root/scoped instruction hierarchy. For skill installation, provenance or updates, see [local skill policy](../../../docs/agents/local-skills.md); ordinary skill use does not require loading it.

# Research for Complect

Use for external technology and standard investigations: Django, PostgreSQL,
PostGIS, Redis, Celery, Kong, Granian, Next.js, React, OAuth/OIDC, browser security,
and migration/locking semantics. Repository impact analysis still uses CodeGraph
first when the root trigger applies; external research does not replace it.

Delegate a bounded investigation to a background agent when available and useful
alongside independent work; otherwise investigate locally and disclose the scope.

1. Prefer official documentation, specifications and first-party source code.
   Check current documentation for changing technologies and match the version
   actually used by Complect. Record source version/date and citations.
2. Distinguish **repository fact** (verified local evidence), **external source
   fact** (cited primary source) and **inference/recommendation**. Resolve conflicts
   explicitly rather than presenting an external recommendation as project policy.
3. Before proposing adoption, check applicable ADRs, Project_architecture.md,
   root/scoped AGENTS.md and the active plan. A best practice becomes a Complect
   rule only through the project's decision process.
4. Create a durable research artifact when the user requests one or the result
   must support an accepted decision/plan; reuse a relevant note where appropriate.
   Otherwise a source-backed answer is sufficient. Preserve provenance and
   uncertainty. If network access fails, state which claims remain unverified;
   do not turn inaccessible or stale sources into confirmed facts.
