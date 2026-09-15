---
name: domain-modeling
description: Build and sharpen a project's domain model. Use when discussing codebase terminology, writing or editing a CONCEPTS.md, or recording or editing an ADR.
---

Before applying this method, read [Complect skill policy](../../../docs/agents/local-skills.md). It routes to root/scoped instructions and accepted decisions; this skill is methodology, not architectural authority.


# Domain Modeling

Actively build and sharpen the project's domain model as you design. This is the *active* discipline: challenging terms, inventing edge-case scenarios, and writing the glossary and decisions down the moment they crystallise. (Merely *reading* `CONCEPTS.md` for vocabulary is not this skill: that's a one-line habit any skill can do. This skill is for when you're changing the model, not just consuming it.)

## Complect sources

Use canonical [CONCEPTS.md](../../../CONCEPTS.md) and
[domain guidance](../../../docs/agents/domain.md). Existing bounded contexts
share this glossary. Do not create `CONTEXT.md` or `CONTEXT-MAP.md` unless the
user explicitly requests it. Decisions stay in the existing `docs/adr/`.

## During the session

### Challenge against the glossary

When the user uses a term that conflicts with the existing language in `CONCEPTS.md`, call it out immediately. "Your glossary defines 'cancellation' as X, but you seem to mean Y. Which is it?"

### Sharpen fuzzy language

When the user uses vague or overloaded terms, propose a precise canonical term. "You're saying 'account': do you mean the Customer or the User? Those are different things."

### Discuss concrete scenarios

When domain relationships are being discussed, stress-test them with specific scenarios. Invent scenarios that probe edge cases and force the user to be precise about the boundaries between concepts.

### Cross-reference with code

When the user states how something works, check whether the code agrees. If you find a contradiction, surface it: "Your code cancels entire Orders, but you just said partial cancellation is possible. Which is right?"

### Update CONCEPTS.md inline

When a domain term is accepted, update `CONCEPTS.md` right there. Don't batch these up: capture them as they happen. Use the format in [CONCEPTS-FORMAT.md](./CONCEPTS-FORMAT.md).

`CONCEPTS.md` should be totally devoid of implementation details. Do not treat `CONCEPTS.md` as a spec, a scratch pad, or a repository for implementation decisions. It is a glossary and nothing else.

### Offer ADRs sparingly

Only offer to create an ADR when all three are true:

1. **Hard to reverse**: the cost of changing your mind later is meaningful
2. **Surprising without context**: a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off**: there were genuine alternatives and you picked one for specific reasons

If any of the three is missing, skip the ADR. Use the format in [ADR-FORMAT.md](./ADR-FORMAT.md).
