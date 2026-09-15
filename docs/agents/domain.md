# Domain Docs

How the engineering skills should consume this repository's domain documentation when exploring the codebase.

## Canonical sources

After the required CodeGraph impact pass, read [CONCEPTS.md](../../CONCEPTS.md)
for domain vocabulary and the applicable [ADRs](../adr/) for decisions.
`CONCEPTS.md` is Complect's canonical domain glossary. Do not create `CONTEXT.md`,
`CONTEXT-MAP.md` or per-context glossary files unless the user explicitly requests
that documentation change. A shared glossary does not imply one bounded context:
the apps retain the ownership boundaries defined by the architecture and ADRs.

## Updating terminology

Use `domain-modeling` when changing the model: challenge overloaded terms,
exercise concrete edge cases and compare statements with verified code.
Record an accepted domain term in `CONCEPTS.md`; do not turn the glossary into
an implementation specification. Use canonical terms in issues, proposals,
hypotheses and tests. If a concept is missing, first establish whether it belongs
to the domain. Architectural decisions remain in `docs/adr/` and require a real
trade-off; a vocabulary clarification alone does not require an ADR.

## Flag ADR conflicts

If output contradicts an existing ADR, surface the conflict explicitly instead of silently overriding the decision.
