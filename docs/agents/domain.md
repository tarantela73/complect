# Domain Docs

How the engineering skills should consume this repository's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repository root when it exists.
- **`docs/adr/`**: read ADRs that touch the area you are about to work in.
- **`CONCEPTS.md`**: use the existing project glossary until `CONTEXT.md` is created or when it contains additional project vocabulary.

If `CONTEXT.md` does not exist, proceed silently. Do not create it merely because it is absent. The `domain-modeling` skill, reached through `grill-with-docs` and `improve-codebase-architecture`, creates it lazily when terms or decisions are resolved.

## File structure

This repository uses a single shared domain-documentation layout. Its business
architecture still has multiple bounded contexts as defined by the ADRs:

```text
/
├── CONTEXT.md
├── CONCEPTS.md
└── docs/adr/
    ├── 0001-database-partitioning-strategy.md
    └── ...
```

Do not introduce `CONTEXT-MAP.md` or per-context `CONTEXT.md` files unless the repository is deliberately migrated to a multi-context documentation layout.

## Use the glossary's vocabulary

When output names a domain concept in an issue title, refactor proposal, hypothesis, or test name, use the term as defined in `CONTEXT.md` or, while it is absent, `CONCEPTS.md`. Do not drift to synonyms the glossary explicitly avoids.

If a needed concept is missing, reconsider whether it belongs to project vocabulary or note the gap for `domain-modeling`.

## Flag ADR conflicts

If output contradicts an existing ADR, surface the conflict explicitly instead of silently overriding the decision.
