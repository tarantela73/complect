# Frontend instructions

Read the [repository map](../AGENTS.md) first. Apply [architecture section 7](../docs/Project_architecture.md#7-frontend-architecture) for frontend decisions, including Server Components, error boundaries and centralized design tokens. This file specializes implementation and verification for `frontend/`.

## Frontend architecture

Use feature-oriented Next.js modules. Keep server state in TanStack Query and small client/UI state in Zustand; do not use client storage for access tokens. API access goes through the generated SDK/client and feature hooks. Follow the frozen OpenAPI contract, invalidate appropriate query keys after mutations and verify changed visible flows in the browser when practical.

Frontend authorization consumes permissions returned by login and uses the project's permission checks. Do not infer permissions solely from Django superuser state. Maintain strict TypeScript and the `@/` path alias.

`/api/v1/` is frozen: a breaking change needs an ADR and a new major API version. Generate `frontend/src/shared/api/generated/` only with `make generate-sdk` from the repository root; never hand-edit it. Inspect SDK changes and affected callers together.

Authentication, permission, session or tenant-switch UI changes also require [IAM instructions](../backend/apps/users/AGENTS.md), ADR-0024 and its active plan. UI checks do not replace server authorization.

## Accessibility

[Architecture section 7.6](../docs/Project_architecture.md#76-accessibility-wcag-21-aa) is the accessibility authority. In changed flows:

- Use semantic HTML and native buttons, links and form controls before adding ARIA. Give controls accessible names and associate labels/errors with inputs.
- Preserve keyboard operation and visible focus. Manage focus on dialog open/close and after navigation or validation changes; restore it to a useful control and avoid keyboard traps.
- Announce asynchronous success, error and loading status with appropriate accessible status/alert semantics without stealing focus.
- Honor `prefers-reduced-motion` for nonessential animation.

## Performance and verification

Use server components by default and keep interactive client boundaries small per the architecture. Measure rendering, bundle or request costs before optimizing. For cache changes, identify query keys, ownership and invalidation triggers, including tenant/session changes; preserve authorization boundaries in cached data.

Use available `react-best-practices`, `shadcn` when applicable, and `frontend-testing-debugging` for rendered UI checks as routed in [capabilities](../docs/agents/capabilities.md). Prefer a functional Browser capability when required by that skill; otherwise use repository Playwright and record the limitation.

All development and test commands run through Docker from the repository root. Use `make tsc`, `make test-e2e`, and the current relevant lint/test targets in [Makefile](../Makefile); follow [SDK and E2E setup](../docs/development/workstations.md#sdk-frontend-checks-and-e2e). Exercise changed visible flows when practical, including keyboard/focus behavior, dynamic states, permissions and responsive layout; report unverified browser behavior. Use behavior-level regression checks for new or changed behavior rather than tests tied to component internals. Run `git diff --check`.
