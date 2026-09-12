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
SHA-256 values. Full resolver regeneration/comparison stays in
`make check-backend-lock` and CI. Docker installs validate input constraints
offline, install dev.lock with `--require-hashes --no-deps`, then validate the
installed transitive closure (including selected extras) and run `uv pip check`.
This keeps bootstrap/sync from invoking a dependency resolver while retaining
independent stale-root and missing-transitive-dependency checks. pip is in the
pinned base image; uv is pinned in the toolchain stage. Neither is an application
dependency. Fingerprints are integrity checks, not signatures over an untrusted
repository; deliberate code/lock updates still require review.

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

The frontend container uses the digest-pinned Node 22.22.3 / npm 10.9.8 image.
An explicit npm-ci stage records both package inputs and actual runtime versions.
It reuses the installed tree only when those inputs match. Host engines are not
constrained. Playwright 1.49.1 now runs in its matching pinned browser container;
the transitional host E2E target has been replaced. See
[workstations.md](workstations.md) for SDK, E2E, ports and lifecycle commands.

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
clean installs without starting the persistent development stack. Phase 2 adds the development lifecycle documented in workstations.md, without
changing application dependencies, IAM business logic or production deployment. Fixed dependency
versions are a compatibility baseline, not a security-update certification.
