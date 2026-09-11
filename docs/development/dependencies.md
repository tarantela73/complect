# Reproducible development dependencies

`toolchain.json` records the Phase 1 Linux amd64 toolchain. Run application
commands in Docker from the repository root. Python 3.12.13, Node 22.22.3,
npm 10.9.8 and uv 0.11.21 match the observed development runtime. This change
does not upgrade application dependencies. Native Fedora ARM64 is not yet a
validated target; do not assume the amd64 lock certifies it.

## Backend

Keep editing `backend/requirements/base.txt` for runtime inputs and `dev.txt`
for development/test inputs (which include base.txt). There is no pyproject
migration. The generated `base.lock` and `dev.lock` include every transitive
application dependency with exact versions and SHA-256 distribution hashes.
The initial selection used the existing development installation as constraints;
that machine snapshot is not an installation input or a repository artifact.

- `make lock-backend`: compile both locks using the pinned toolchain in a
  disposable container. Existing selections are preferred; shared runtime/dev
  dependencies are constrained by base.lock. Review any version change.
- `make check-backend-lock`: verify source/lock fingerprints and regenerate in
  temporary storage with a read-only repository mount. A difference fails.
- `make check-toolchain`: reject drift between the manifest, Docker/Compose,
  npm declarations and the backend lock metadata.

Commit changed input files, both affected locks and `lock-metadata.json`
together. Metadata must contain exactly the declared input/lock keys and valid
SHA-256 values. The Docker build also independently recompiles and compares both
locks before installing the full dev.lock with `--require-hashes --no-deps`, then runs
`uv pip check`. The pinned base image contains pip; uv is pinned separately in
its toolchain stage. These two installer tools are not application dependencies.
Fingerprints detect accidental changes, not hostile edits to the whole repository.
Rehashing a stale input in metadata cannot make the full resolver comparison pass.
Trust in deliberate lock/version changes still comes from patch review; metadata
is not a signature. Regeneration has no timestamp/header and sorts metadata keys;
existing locks preserve reviewed version selections, independently of installed
packages, machine snapshots, caches or development volumes.

A normal regeneration does not request upgrades. To deliberately update a
package, change its input constraint to the reviewed version and regenerate.
For a transitive-only upgrade, add an explicit reviewed constraint to the
appropriate input. Never use an unconstrained blanket upgrade as maintenance.
Update Python/uv versions in the manifest and Dockerfile together and regenerate
locks with that toolchain; `check-toolchain` detects mismatches.

Lock generation needs the public Python package index. A network/index failure
is an error, not a successful freshness check. Hashes protect the selected
published distributions. This is dependency-set reproducibility, not bit-for-bit
Docker image reproducibility: Debian apt repositories and source-build tooling
are not snapshotted by this phase.

## Frontend

The frontend container is pinned to Node 22.22.3 / npm 10.9.8 by an immutable
image digest. Compose installs with `npm ci`; CI checks both actual versions
against the manifest before clean installs. Neither npm project adds
engine-strict or exact host engines. Both package manifests and lockfiles retain
their pre-Phase-1 package selections and metadata.

The existing host-run `make test-e2e` is a transitional legacy workflow. It uses
`npm ci` and remains compatible with the observed WSL host Node 24.20.0 /
npm 12.0.2 without changing the host installation. That legacy workflow will be
removed after E2E containerization in Phase 2. Its existing absolute browser
paths and Vitest/Playwright collection problem remain outside this phase.
Generated SDK files remain local artifacts; their existing generation workflow
is unchanged. Full frontend typecheck still needs that SDK.

## Host tooling policy

Exact pins apply to container Python/Node/npm, build uv and agent CodeGraph.
Docker Engine, Compose, Git, Bash, Make and jq use presence/functional
compatibility. Docker 29.7.2 and Compose 5.5.0 are tested baselines, not exact
requirements. No unsupported minimum/range is invented. `make check-toolchain`
reports different observed Docker/Compose versions as WARN, not a version
failure. A missing required tool or actual failed operation still fails.
Optional host Node/npm versions are also observations, not required downgrades.

## Local artifacts and agent settings

`.codegraph/`, virtual environments, node_modules, Next.js build outputs and
existing browser/test caches are local generated state covered by repository
ignore rules. Initialize CodeGraph 1.6.0 independently per clone. No Graphify or
Codex-cache path is guessed: neither has a verified project-local path here.
Never transfer indexes, caches, virtual environments, Docker volumes, database
storage or credentials between workstations.

`.claude/settings.json` was a machine-specific permission/plugin history and is
now untracked by design (existing `.gitignore` rules already cover it).
`CLAUDE.md` still points to the canonical `AGENTS.md`; no architecture instruction
was removed. Keep personal Claude permissions local.

`.gitattributes` normalizes detected text to LF while leaving binary content
untouched. Shell executable modes are preserved; sourced PostgreSQL init scripts
must not be made executable as a blanket normalization step.

## Scope

The dependency CI gate checks both locks, the pinned backend build and both npm
clean installs without starting the persistent development stack. No secrets,
ports, SELinux policy, IAM/migration behavior, database roles, bootstrap/sync/
doctor commands or Fedora installation are changed in Phase 1. Fixed dependency
versions are a compatibility baseline, not a security-update certification.
