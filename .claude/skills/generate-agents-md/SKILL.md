---
name: generate-agents-md
description: "Generate or update AGENTS.md by investigating the codebase. Reads config files, Makefile, settings, ADRs, and known gotchas to produce a compact instruction file for future agents."
---

# Generate AGENTS.md

Systematic investigation of the codebase to produce a high-signal AGENTS.md.

## Steps

### 1. Read root config files
- `Makefile` — all make targets with comments
- `docker-compose.dev.yml` — services, ports, env vars, volume mounts
- `backend/Dockerfile.dev` — base image, package manager
- `backend/requirements/base.txt` and `dev.txt` — key dependencies
- `backend/pytest.ini` — test config
- `backend/ruff.toml` — linter config
- `backend/config/settings/base.py` — LOCAL_APPS list, middleware chain, Celery config, Redis DB mapping
- `frontend/package.json` — scripts, dependencies

### 2. Read architectural docs
- `docs/Project_architecture.md` — full architecture spec
- `CONCEPTS.md` — domain vocabulary
- `docs/adr/*.md` — all ADRs (22 in this project)

### 3. Read solution docs
- `docs/solutions/patterns/*.md` — outbox, tenant isolation, cursor pagination
- `docs/solutions/developer-experience/*.md` — known gotchas
- `docs/solutions/database-issues/*.md` — migration pitfalls

### 4. Investigate backend structure
- `backend/apps/` — list all apps
- `backend/apps/common/contracts/` — Protocol interfaces
- `backend/apps/common/middleware/` — middleware chain
- `infra/scripts/` — custom linters

### 5. Investigate frontend structure
- `frontend/src/app/dashboard/` — all dashboard pages
- `frontend/src/shared/api/` — API hook files
- `frontend/src/shared/auth/` — auth flow (store, hooks, api)

### 6. Identify gotchas from trajectory
- RBAC `_pk_to_uuid4` encoding (critical)
- Frontend port mapping (container 3000 → host 8080)
- JWT doesn't contain permissions (separate in login response)
- SQLite forbidden in tests
- Cross-domain model imports forbidden

### 7. Write AGENTS.md
Structure:
1. Overview (stack, links to docs)
2. Commands (Makefile targets)
3. Architecture rules (12 key rules with code examples)
4. Backend structure (apps tree, Celery queues, Redis DB)
5. Testing (Docker-only, no SQLite, conftest)
6. Services and ports (table)
7. Code style (ruff, TS strict)
8. Causal model (ADR-0015–0019)
9. Key patterns (outbox, tenant, cursor pagination)
10. Dev environment (quick start, login, auth flow, known issues)
11. Don'ts (9 anti-patterns)

### 8. Verify
- Check all make commands match Makefile
- Check architecture rules match Project_architecture.md
- Check port numbers match docker-compose.dev.yml
