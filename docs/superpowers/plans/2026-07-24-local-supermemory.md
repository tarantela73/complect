# Local Supermemory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Complect agent memory fully locally and switch Codex and Claude Code from hosted Supermemory to the local API without losing the four existing scoped memories.

**Architecture:** A pinned Supermemory server binary runs as an unprivileged Docker service with encrypted embedded storage in a named volume. The user's host `llama.cpp` server provides the local `qwen3-coder` extraction model, while Supermemory `0.0.6` uses its built-in local `Xenova/bge-base-en-v1.5` embeddings. Codex and Claude Code use their official plugins against `http://127.0.0.1:6767`; the hosted MCP is removed only after local recall is verified.

**Tech Stack:** Docker Compose, Supermemory server `0.0.6`, host `llama.cpp`, `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf`, `Xenova/bge-base-en-v1.5`, Codex Supermemory plugin `1.0.9`.

## Global Constraints

- Bind the Supermemory API only to `127.0.0.1:6767` on the host.
- Keep the generated API key and all memory data outside Git in Docker volumes and user-local configuration.
- Set `SUPERMEMORY_DISABLE_TELEMETRY=1`.
- Use `complect:project` for reusable facts and `complect:task:<task-key>` for task-local state.
- Do not read, write, or delete hosted memories without an explicit container tag.
- Do not remove hosted memories until all four local records are searchable in the expected containers.
- Preserve every unrelated worktree change and do not create a commit unless requested.

---

### Task 1: Reproducible local runtime

**Files:**
- Create: `infra/supermemory/Dockerfile`
- Modify: `docker-compose.dev.yml`
- Modify: `Makefile`

**Interfaces:**
- Consumes: official server release `server-v0.0.6` and its published SHA-256 checksums.
- Produces: `http://127.0.0.1:6767`, Docker services
  `supermemory_data_init` and `supermemory`.

- [x] **Step 1: Add the pinned Supermemory image**

Create a multi-architecture Dockerfile that downloads
`supermemory-server-linux-{x64|arm64}` from release `server-v0.0.6`, verifies
the matching checksum, installs `curl` for health checks, and runs as numeric
UID/GID `10001`.

- [x] **Step 2: Add local model and memory services**

Add named volume `supermemory_data`. Connect the Supermemory container to the
host `llama-server` through Docker's `host-gateway` mapping:

```yaml
supermemory:
  build:
    context: ./infra/supermemory
  extra_hosts:
    - "host.docker.internal:host-gateway"
  ports:
    - "127.0.0.1:6767:6767"
  environment:
    OPENAI_BASE_URL: http://host.docker.internal:8090/v1
    OPENAI_API_KEY: local-llama-cpp
    OPENAI_MODEL: qwen3-coder
    SUPERMEMORY_DISABLE_TELEMETRY: "1"
```

Use a one-shot root init service to set ownership on the memory volume; the
runtime service itself must stay unprivileged.

- [x] **Step 3: Add lifecycle commands**

Add `supermemory-up`, `supermemory-logs`, `supermemory-status`, and
`supermemory-down` Make targets. `supermemory-status` must call the server
root health route without printing the API key.

- [x] **Step 4: Validate Compose**

Run:

```bash
docker compose -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.dev.yml build supermemory
```

Expected: both commands exit `0`; the build verifies the release checksum.

### Task 2: Agent integration and operational documentation

**Files:**
- Modify: `docs/development/supermemory.md`
- Modify: `CLAUDE.md` (machine-local and Git-ignored)

**Interfaces:**
- Consumes: local API URL and generated key in the `supermemory_data` volume.
- Produces: exact setup, backup, restore, migration, and rollback instructions.

- [x] **Step 1: Document local-only behavior**

Document that self-hosted Supermemory exposes the Memory API but not managed
Supermemory MCP, so Codex and Claude Code must use their official plugins with
`baseUrl=http://127.0.0.1:6767`.

- [x] **Step 2: Document secret handling and recovery**

Record that the API key lives at `/data/api-key`, memory state lives in the
`supermemory_data` volume, and backups use a volume archive without committing
the key or data.

- [x] **Step 3: Point local Claude instructions at the local API**

Replace references to hosted MCP availability with the local service and
official plugin behavior while keeping the project/task container protocol.

- [x] **Step 4: Validate documentation**

Run:

```bash
git diff --check -- AGENTS.md docs/development/supermemory.md docker-compose.dev.yml Makefile infra/supermemory/Dockerfile
rg -n "complect:project|complect:task:<task-key>|127.0.0.1:6767" docs/development/supermemory.md CLAUDE.md
```

Expected: no whitespace errors and all container/API references are present.

### Task 3: Start and connect the local service

**Files:**
- Modify outside repository: `~/.codex/` through the official `codex-supermemory` installer.
- Modify outside repository: Claude Code plugin/config through its official CLI.
- Modify outside repository: `/home/sever/.local/bin/env` with local-only API URL and generated key.

**Interfaces:**
- Consumes: `/data/api-key` generated on first boot.
- Produces: local project recall plus task-scoped save behavior for Codex and
  Claude Code.

- [x] **Step 1: Start the local services**

Run:

```bash
make supermemory-up
```

Expected: the host `llama-server` health check succeeds, Supermemory reaches a
healthy state, and port `6767` is exposed only on loopback.

- [x] **Step 2: Capture the generated key without printing it**

Read `/data/api-key` inside the container, store it as
`SUPERMEMORY_CODEX_API_KEY` and `SUPERMEMORY_CC_API_KEY` in the existing
user-local environment file with mode `0600`, and never include the value in
logs or committed files.

- [x] **Step 3: Install and configure official plugins**

Install `codex-supermemory@1.0.9`, set:

```json
{
  "baseUrl": "http://127.0.0.1:6767",
  "projectContainerTag": "complect:project",
  "similarityThreshold": 0.6,
  "autoRecallEveryPrompt": false,
  "captureEveryNTurns": 0,
  "enableCustomContainers": true
}
```

Configure the Claude Code project plugin with the same URL and
`repoContainerTag=complect:task:20260724-supermemory-isolation`. Claude's
automatic summaries then stay in the current task while its Codex-compatible
read tags still include `complect:project`. Change the task tag when starting
a different task. Do not place API keys in project files.

Keep the official Codex `Stop` hook after the Claude project config is in
place. The installed plugins use Claude's `repoContainerTag` as the canonical
write tag, so automatic summaries go to the current task, while Codex
`projectContainerTag` remains an additional read scope.

- [x] **Step 4: Verify integrations**

Run the official Codex plugin status command and query `/`. Verify no
request is sent to `api.supermemory.ai` during the status/search check.

### Task 4: Migrate, verify, and retire hosted copies

**Files:**
- No repository files.

**Interfaces:**
- Consumes: the four previously created hosted records and their explicit container tags.
- Produces: three local records in `complect:project` and one in `complect:task:20260724-supermemory-isolation`.

- [x] **Step 1: Write the four exact records locally**

Use `POST /v4/memories` against `http://127.0.0.1:6767` with the generated
local key. Preserve content, `isStatic`, metadata, and container scope.

- [x] **Step 2: Verify isolation**

Search `complect:project` and
`complect:task:20260724-supermemory-isolation` independently. Expected totals:
project `3`, task `1`; result scopes must match their queried container.

- [x] **Step 3: Remove the hosted MCP configuration**

Run `codex mcp remove supermemory` only after local plugin status and searches
succeed. Keep the official developer-docs MCP unchanged.

- [x] **Step 4: Forget hosted copies**

Soft-forget only the four known hosted memory IDs, each with its original
container tag. Verify both hosted container searches return zero matching
active records. Do not issue a container-wide or semantic bulk delete.

- [x] **Step 5: Final verification**

Run:

```bash
docker compose -f docker-compose.dev.yml ps supermemory
curl -fsS http://127.0.0.1:6767/ >/dev/null
git diff --check
git status --short
```

Expected: local services healthy, local records isolated, hosted MCP absent,
no diff whitespace errors, and unrelated user changes unchanged.

Implementation runs inline in the current session because the user already
approved execution. Subagents are intentionally not used by the active
collaboration policy. Commit steps are intentionally omitted because the
worktree is already dirty and no commit was requested.
