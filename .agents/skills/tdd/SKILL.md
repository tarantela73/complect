---
name: tdd
description: Behavior-first RED/GREEN development for approved behavioral changes, regression fixes and integration tests in Complect.
---

Before applying this method, read [Complect skill policy](../../../docs/agents/local-skills.md). It routes to root/scoped instructions and accepted decisions; this skill is methodology, not architectural authority.


# Test-Driven Development

TDD is the red → green loop. This skill is the reference that makes that loop produce tests worth keeping: what a good test is, where tests go, the anti-patterns, and the rules of the loop. Every section applies on every cycle: consult them before and during the loop, not after.

When exploring the codebase, read canonical `CONCEPTS.md` so test names and interface vocabulary match the project's domain language, and respect ADRs in the area you're touching.

## What a good test is

Tests verify behavior through public interfaces, not implementation details. Code can change entirely; tests shouldn't. A good test reads like a specification: "user can checkout with valid cart" tells you exactly what capability exists, and it survives refactors because it doesn't care about internal structure.

See [tests.md](tests.md) for examples and [mocking.md](mocking.md) for mocking guidelines.

## Seams: where tests go

A **seam** is the public boundary you test at: the interface where you observe behavior without reaching inside. Tests live at seams, never against internals.

**Use established seams without repeated confirmation.** Record the seam and its source: Accepted ADR, approved implementation plan, existing public service/interface, API contract or established test architecture. These already establish the test boundary. Ask only when the boundary is genuinely unresolved and the answer would change the work; continue independent work meanwhile.

PostgreSQL is itself part of the contract for RLS, constraints, CAS, row/advisory locks, migrations, indexes, Outbox transaction atomicity, isolation and concurrency. Direct database assertions for these are valid contract checks, using real PostgreSQL/PostGIS in Docker; they are not automatically implementation-coupled.

When the shape of that interface is itself in question (how deep the module is, where the seam belongs, what the interface should expose), read the installed [codebase-design](../codebase-design/SKILL.md) skill for the vocabulary. It is the shared source of the module, interface, depth, seam, adapter, leverage and locality terms, and it is a reference to consult, not a session to run.

## Anti-patterns

- **Implementation-coupled**: mocks internal collaborators, tests private methods, or verifies irrelevant internal state instead of the contract. Database-side assertions are appropriate when database semantics are the contract, as described above. The tell: the test breaks when you refactor but behavior hasn't changed.
- **Tautological**: the assertion recomputes the expected value the way the code does (`expect(add(a, b)).toBe(a + b)`, a snapshot derived by hand the same way, a constant asserted equal to itself), so it passes by construction and can never disagree with the code. Expected values must come from an independent source of truth: a known-good literal, a worked example, the spec.
- **Horizontal slicing**: writing all tests first, then all implementation. Bulk tests verify _imagined_ behavior: you test the _shape_ of things rather than user-facing behavior, the tests go insensitive to real changes, and you commit to test structure before understanding the implementation. Work in **vertical slices** instead: one test → one implementation → repeat, each test a **tracer bullet** that responds to what the last cycle taught you.

## Rules of the loop

- **Red before green.** Write the failing test first, then only enough code to pass it. Don't anticipate future tests or add speculative features.
- **One slice at a time.** One seam, one test, one minimal implementation per cycle.
- **Refactoring is not part of the loop.** It belongs to the review stage (see the `code-review` skill), not the red → green implementation cycle.
