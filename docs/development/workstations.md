# Development workstations: native Linux/Fedora and Windows/WSL2

Use an independent clone on each workstation's Linux filesystem. Keep developer
checkouts and review evidence on persistent storage, not a directory erased at
reboot. Docker data and local credentials belong to that workstation only.

This workflow is development-only. Production deployment/networking, IAM
business logic and PostgreSQL production roles are not changed. The supported
application target remains Linux amd64. Fedora with SELinux Enforcing requires
the separate Phase 3 verification on the real laptop; Ubuntu/WSL tests do not
establish that proof.

## Prerequisites

Install prerequisites yourself before bootstrap. The scripts never run sudo,
dnf, apt or a global Codex installer.

- Git, Bash, Make, jq, Python 3.10+ and curl. On Fedora the ordinary packages are
  `git bash make jq python3 curl`.
- A local Docker Linux daemon and the Compose plugin. Use the official
  [Docker Engine Fedora instructions](https://docs.docker.com/engine/install/fedora/)
  for `docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`.
  Docker Desktop with WSL integration remains supported for Windows/WSL.
- Permission to use the local Docker socket. The helper rejects remote Docker
  deployment contexts. Do not fix socket access with world-writable permissions.
- CodeGraph 1.6.0 on PATH. Install the pinned tool using its
  [upstream instructions](https://colbymchenry.github.io/codegraph/getting-started/installation/).
  Preserve existing agent configuration; the repository does not install or
  reconfigure global Codex/MCP servers.

`toolchain.json` distinguishes exact container/build pins from tested host
versions. Docker 29.7.2 / Compose 5.5.0 are tested baselines, not exact host pins.
Compose must support the declared features, including optional/raw env files,
health dependencies and `up --wait`. An unsupported feature fails configuration
validation; no guessed minimum/range is imposed.

Host Node/npm and host Chromium are not needed for application or E2E commands.
Use the container versions and dependency locks described in [dependencies.md](dependencies.md).

## New checkout

```bash
git clone <your-authorized-GitHub-remote> Complect
cd Complect
python3 -B infra/scripts/dev_environment.py configure
# Optionally edit public .env.dev and provide external keys in .dev/external.env.
make bootstrap
make doctor
```

`configure` is also performed by bootstrap if needed. Do not copy the example
verbatim over an existing local file: configuration tokens are expanded by the
helper, and existing values are never overwritten. `.env.example` remains the
separate production-oriented template; `.env.dev.example` is the development
contract. The public config parser accepts only its documented keys and never
executes shell text.

Each checkout gets its own Compose project identity. Existing containers,
networks or volumes with another checkout identity are rejected before lifecycle
operations. Fixed global container names are not used. Canonical ports mean that
only one stack can publish these ports on one host at a time. Stop the previous
stack explicitly from its own checkout; bootstrap never stops another project or
selects a random fallback port.

## Port and origin contract

| Endpoint | Host/browser | Inside Compose |
|---|---|---|
| Frontend | `http://localhost:8080` (bind `127.0.0.1`) | `frontend:3000` |
| Gateway | `http://localhost:8000` (bind `127.0.0.1`) | `api_gateway:8000` |
| Backend | No host publication | `backend:8010` |
| PostgreSQL primary | `127.0.0.1:15432` | `postgres_primary:5432` |
| PostgreSQL replica | `127.0.0.1:5433` | `postgres_replica:5432` |
| Redis | No host publication | `redis:6379` |
| MinIO API/console | `127.0.0.1:9000` / `127.0.0.1:9001` | `minio:9000` |

Use the literal browser origin `http://localhost:8080`. Django CORS/CSRF and
Kong agree on it; an arbitrary alternate origin is not silently accepted.
Container connections never use the host database ports. Optional observability
ports retain their existing values but bind to loopback.

## Configuration and credential sources

| Setting | Source and consumer |
|---|---|
| `COMPOSE_PROJECT_NAME` | Generated public `.env.dev`; used for checkout-local resources |
| `DEV_UID`, `DEV_GID` | Actual developer IDs in `.env.dev`; app/frontend/generators only |
| `DEV_FRONTEND_ORIGIN`, `DEV_API_ORIGIN` | Public dev template; validated against the canonical contract |
| `DEV_CHECKOUT_ID` | Derived from this checkout root for resource ownership labels |
| `DEV_SOURCE_DIGEST` | Derived from backend source/lock inputs; labels the built image |
| Django `SECRET_KEY` | Generated `.dev/secrets/django_secret_key`; read via `DJANGO_SECRET_KEY_FILE` |
| PostgreSQL password | Generated `.dev/secrets/postgres_password`; primary and app-specific runtime copies |
| Replication password | Generated `.dev/secrets/replication_password`; primary initializer and replica |
| MinIO password | Generated `.dev/secrets/minio_password`; MinIO and bucket initializer |
| Gateway trust token | Generated `.dev/secrets/gateway_token`; Kong and app-specific runtime copies |
| `DADATA_API_KEY` | Optional user-provided `.dev/external.env`; only this key is accepted |
| LiveKit values | Current dev settings are empty scaffolding; no LiveKit service/credential is required or faked |
| `E2E_USERNAME`, `E2E_PASSWORD` | Optional existing test account in private `.dev/e2e.env` |
| IAM merge execution | Forced `false`; not an external override |
| Redis URLs / DB hosts | Literal internal service endpoints in the shared app environment |

External env files use raw values: `KEY=value`, without shell interpolation or
`export`. Bootstrap creates empty private files for optional external credentials;
it does not invent API keys. Existing development file storage/scanning remain
`STUB`; the ClamAV service is optional infrastructure. MinIO runs locally with
the existing bucket names. Optional observability is in its own Compose profile.

## Secret and filesystem permissions

Generated host secret files are mode 0600 in a mode-0700 directory. Generation
uses secure randomness and exclusive, no-follow file creation. Existing values
are preserved; malformed/symlinked private state is rejected.

A one-shot filesystem stage materializes credentials into a private named Docker
volume. Reader UIDs/GIDs are discovered from the pinned PostgreSQL, Kong and
MinIO images. Runtime files are mode 0400; app-specific copies are owned by the
host developer UID. System service UIDs are not changed. A value or owner
mismatch on an existing runtime secret fails for manual review rather than
silently rotating it. Do not delete local secrets while retaining their volumes.

Shared bind mounts use SELinux `z`, with `ro` where writes are unnecessary.
Named volume roots for application caches are initialized only when empty.
There is no blanket checkout chown, chmod 777 or SELinux disabling. Doctor checks
contexts separately from Unix permissions on Fedora. Actual Fedora behavior is
validated in Phase 3.

App/frontend processes and SDK/E2E generators run as the developer UID/GID.
SDK files and test artifacts written into the checkout retain that ownership.
Backend caches/coverage and frontend modules/build cache use project volumes.

## Lifecycle and migration safety

`make bootstrap` prepares missing local state, verifies ownership/config/locks,
builds the locked backend only when its inputs differ, initializes runtime
permissions, starts infrastructure, applies an explicit safe forward plan,
generates the SDK, then starts app/worker/beat/frontend and runs doctor.

`make sync` is used after a successful Git update. It never pulls Git, changes
history, generates locks, runs dependency resolution, seeds business data or
resets a database. Changed npm inputs use `npm ci`; unchanged install receipts
are reused. Backend installation validates roots and the installed transitive
closure offline; full resolver regeneration remains an explicit CI/lock check.

Normal backend startup does not call migrate. A readonly readiness check prevents
backend/worker/beat from starting against a pending/incompatible schema. The
migration service is a tools-profile one-shot, invoked explicitly. The plan is
shown before application. No fake/reverse/drop/create recovery is attempted.

The dev migration receipt records applied source hashes. Unknown history,
changed applied files, an unreceipted existing schema, an incomplete batch or an
unreviewed future migration fails. Existing IAM foundation/fence updates require
their accepted release preflight; the pinned baseline is allowed automatically
only for a genuinely fresh development schema. New migration approval entries
must be reviewed with their ADR/release constraints; the allowlist is not an ADR
replacement. The development migration mutex does not replace IAM's own barriers.

A failed batch keeps an `in_progress` receipt for diagnosis. Do not delete that
receipt, fake migrations, erase volumes or rotate secrets to make a check green.

The default Granian process uses explicit restarts/sync after backend edits;
unbounded automatic reload is not the portable default. Health probes have their
own time limits and exec form, and app containers use an init process. These
changes avoid indefinite probe children when a worker stops replying.

## SDK, frontend checks and E2E

```bash
make generate-sdk
make tsc
make test-e2e
```

The optional host shortcut `npm --prefix frontend run generate:api` delegates to
`make generate-sdk`, as do the E2E npm shortcuts. These host shortcuts require
host npm; the canonical Make commands do not. Inside containers, use the SDK
one-shot service through the host helper rather than fetching a protected schema
without gateway authentication.

SDK generation uses the selected checkout's services and private scratch path,
not a fixed container name or shared `/tmp/openapi.json`. E2E requires the current
SDK and healthy services. The Playwright package and official browser image are
both pinned to 1.49.1.

The E2E runner is on the Compose network. Two transparent local TCP relays map
its browser's `localhost:8080` to frontend and `localhost:8000` to Kong. They
preserve browser Host/Origin and headers; API requests do not bypass the gateway.
Doctor independently checks the actual published host ports. This design does
not require host networking support, host Node/npm or a user's browser cache.

Clean-clone smoke tests exercise the login page and browser/gateway CORS path.
The existing authenticated upload tests require an explicitly supplied existing
test account and are reported as skipped when it is absent. Bootstrap never
creates a superuser. Traces are disabled by default to avoid recording credentials;
results go to ignored `.dev/test-artifacts`. The optional headed command runs in
container Xvfb, not a host browser window.

Vitest excludes Playwright specs. If no unit tests exist, run it with the explicit
`--passWithNoTests` option and report zero tests rather than claiming unit coverage.

## Doctor is read-only

Doctor performs version/config/ownership checks, Docker inspections, bounded HTTP
probes, Redis PING and readonly PostgreSQL migration/catalog queries. It does not
start/restart/build containers, generate secrets/SDK/locks/indexes, repair modes,
apply migrations or change Git. Read probes may produce normal service access
logs/metrics; they are not corrective operations or business-data writes.

Statuses are PASS/WARN/FAIL/SKIP. A mandatory failure returns nonzero. Missing
agent skill availability is reported separately from the tracked skill manifest.
A missing local CodeGraph index is reported; run `codegraph init` explicitly
before agent code analysis. The development database's current superuser/BYPASSRLS
role is reported as a limitation, never as production RLS enforcement evidence.

## Daily machine switch

On machine A, inspect Git status, run relevant tests, commit only intended paths,
then push. On machine B:

```bash
git status --short
git pull --ff-only
make sync
make doctor
```

Never synchronize `.venv`, `node_modules`, Docker volumes, PostgreSQL/Redis/MinIO
data, CodeGraph or Graphify indexes, Codex cache, credentials/tokens or generated
browser caches. Recreate dependencies locally; keep each workstation's development
data independent. Any data export/import is a separate reviewed operation.

## Codex and PostgreSQL MCP

Use [codex-mcp.example.toml](codex-mcp.example.toml) as a portable, credential-free
reference. It does not modify global configuration. PostgreSQL MCP profiles point
to `127.0.0.1:15432` / `complect_db` using a separately provisioned read-only role
and local credential storage. A failed MCP login must be fixed through the proper
credential flow, never by weakening database authentication. Bootstrap does not
reconfigure global Codex, create MCP credentials or alter production roles.

## Disposable acceptance verification

The `Workstation reproducibility` CI job starts from a clean committed checkout.
It runs the explicit lock check, `workstation_rehearsal.py`, containerized E2E
and Vitest isolation. CI execution is evidence only after the workflow actually
runs successfully; an added workflow alone is not a green hosted result.

For a separately authorized disposable checkout and local daemon, run:

```bash
python3 -B infra/scripts/workstation_rehearsal.py --output /path/outside-checkout/evidence
```

The harness refuses existing local configuration, secrets, SDK or module state.
It creates a uniquely named acceptance-only SQL schema/table and synthetic ledger
entry to test preservation and unsafe-history rejection. It deletes only its own
SQL fixtures; the checkout's containers/networks/volumes remain for inspection.
It never imports developer volumes or runs a global cleanup. The evidence covers
two bootstraps, sync, secret/data preservation, doctor file/index/ownership
immutability, Docker command tracing and negative cases. The explicit
`--allow-candidate-patch` flag is only for reviewing an uncommitted patch applied
to a clean baseline clone; this must be reported separately from committed-checkout
CI evidence. Native Fedora with SELinux Enforcing still requires Phase 3.

## Frozen MinIO image sourcing

The dev server and bucket initializer use the official `quay.io/minio/minio`
`RELEASE.2025-09-07T16-13-09Z` and `quay.io/minio/mc`
`RELEASE.2025-08-13T08-35-41Z` images. Each exact release tag is paired with its
multiarch manifest-list digest in Compose; both digests are unchanged from the
previous Docker Hub pins. Linux amd64 resolves to the corresponding child
manifest. Do not substitute a floating tag, community rebuild or newer release.

On 2026-09-14, anonymous Docker Hub tag and digest requests were denied while
Quay served the same manifest lists. Quay publication is confirmed by the
upstream release scripts for
[server](https://github.com/minio/minio/blob/RELEASE.2025-09-07T16-13-09Z/docker-buildx.sh)
and [client](https://github.com/minio/mc/blob/RELEASE.2025-08-13T08-35-41Z/docker-buildx.sh).
The Compose infrastructure tests check both exact references and the unchanged
MinIO ports, command, healthcheck and bucket initialization commands.

Supply-chain follow-up, outside this portability fix: Docker Hub marks both
repositories Archived; the GitHub [server](https://github.com/minio/minio) and
[client](https://github.com/minio/mc) repositories are also archived. The server
community distribution is source-only. The frozen server predates
[Security/CVE RELEASE.2025-10-15T17-29-55Z](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z).
Preserving this version is not a security-update certification. After Fedora
portability, separately decide whether to retain frozen community MinIO only
for local dev, adopt a maintained S3-compatible dev backend, or choose another
approved option. This patch does not make that decision or upgrade MinIO.
