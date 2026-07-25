# Codex Agent Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe global Codex baseline and a Complect-specific agent layer without mixing task memory or replacing existing MCP configuration, then make the pinned local Supermemory server process the final task transcript through Qwen.

**Architecture:** User-wide defaults and add-only MCP guidance live in `~/.codex`; Complect rules, concurrency, agent roles, and Supermemory isolation live in the repository. Project config inherits global MCP entries instead of duplicating them. Self-hosted Supermemory keeps server `0.0.6` and its named volume but uses the built-in direct workflow engine because the packaged Rivet runtime cannot load its WASM module.

**Tech Stack:** Codex CLI 0.145.0, TOML, Markdown, Docker Compose, project-local Codex agents, Supermemory server 0.0.6, local llama.cpp/Qwen, Supermemory container tags.

## Global Constraints

- Preserve every existing MCP server and all unrelated global configuration.
- Keep `complect:*`, task keys, and project roles out of `~/.codex`; preserve
  the existing user-level trust entry for the repository path.
- Never store credentials or secret values in committed or global TOML.
- Use `sandbox_mode = "workspace-write"`, `approval_policy = "on-request"`,
  and disabled sandbox network access.
- Disable built-in Codex Memories without deleting stored data; tagged local
  Supermemory is the only persistent project-memory layer.
- Keep `SUPERMEMORY_SERVER_VERSION=0.0.6`, the `supermemory_data` volume, and
  the canonical task container
  `complect:task:20260725-codex-agent-configuration`.
- Do not create commits because the current worktree contains unrelated user changes.

---

### Task 1: Safe global Codex baseline

**Files:**
- Modify: `/home/sever/.codex/config.toml`
- Modify: `/home/sever/.codex/AGENTS.md`
- Modify: `/home/sever/.codex/supermemory.json`

**Interfaces:**
- Consumes: existing model, trust, memory, app, `openaiDeveloperDocs`, and `playwright` entries.
- Produces: portable sandbox/approval defaults, multi-agent settings, and global add-only MCP guidance.

- [x] **Step 1: Add only missing portable config keys**

Before editing, calculate a SHA-256 hash of the canonical JSON representation
of the complete `mcp_servers` table. Use it as the after-edit equality baseline.

Add top-level:

```toml
sandbox_mode = "workspace-write"
approval_policy = "on-request"
```

Extend the existing `[features]` table:

```toml
multi_agent = true
memories = false
hooks = true
```

Replace the legacy `codex_hooks = true` alias with canonical `hooks = true`.

Add:

```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 3

[sandbox_workspace_write]
network_access = false
writable_roots = []
```

Disable generation and injection without deleting existing memory files:

```toml
[memories]
generate_memories = false
use_memories = false
```

Do not change any existing MCP table or model/app setting. The only intentional
change to an existing feature is disabling built-in Codex Memories.

- [x] **Step 2: Add global working agreements**

Write a short `~/.codex/AGENTS.md` that requires preserving dirty worktrees,
scoping configuration narrowly, treating MCP changes as add-only, using
environment-variable credential references, and verifying configuration after
changes. It must also state that subagents may access persistent memory only
when the primary gives them an exact container. Do not mention Complect.

- [x] **Step 3: Parse the global files without printing secrets**

Before validation, remove `projectContainerTag`, `customContainers`,
`customContainerInstructions`, and `enableCustomContainers` from global
`supermemory.json`. Keep only local `baseUrl`, disabled automatic
recall/capture, and the similarity threshold.

Run:

```bash
python3 -c 'import tomllib; tomllib.load(open("/home/sever/.codex/config.toml", "rb"))'
rg -n "complect:|api[_-]?key[[:space:]]*=" /home/sever/.codex/config.toml /home/sever/.codex/AGENTS.md /home/sever/.codex/supermemory.json
```

Expected: TOML exits `0`; the search returns no project tags or inline key assignment.

### Task 2: Project-local Codex agents

**Files:**
- Create: `.codex/config.toml`
- Create: `.codex/agents/complect-explorer.toml`
- Create: `.codex/agents/complect-reviewer.toml`
- Create: `.codex/agents/complect-verifier.toml`

**Interfaces:**
- Consumes: root `AGENTS.md`, ADRs, solution docs, Docker Make targets, and inherited global MCP.
- Produces: three discoverable project-local roles with narrow responsibilities.

- [x] **Step 1: Configure project concurrency**

Create `.codex/config.toml`:

```toml
sandbox_mode = "workspace-write"
approval_policy = "on-request"

[agents]
enabled = true
max_concurrent_threads_per_session = 3

[sandbox_workspace_write]
network_access = false
writable_roots = []
```

Do not add project MCP tables; inherited global servers remain authoritative.

- [x] **Step 2: Add the read-only explorer**

Create a custom agent with `name = "complect_explorer"`,
`sandbox_mode = "read-only"`, and instructions to map code paths, ADRs,
domain boundaries, tenant filters, and evidence without editing or accessing
persistent memory. Set `[features] hooks = false`.

- [x] **Step 3: Add the read-only reviewer**

Create a custom agent with `name = "complect_reviewer"`,
`sandbox_mode = "read-only"`, and instructions to prioritize correctness,
security, tenant isolation, CAS-only mutations, legal hold, outbox ordering,
OpenAPI compatibility, migration safety, and missing tests, without accessing
persistent memory. Set `[features] hooks = false`.

- [x] **Step 4: Add the verifier**

Create a custom agent with `name = "complect_verifier"`,
`sandbox_mode = "workspace-write"`, and instructions to choose the smallest
relevant Docker-based checks, avoid source edits, report exact commands, and
distinguish product failures from environment failures. It must not access
persistent memory. Set `[features] hooks = false`.

- [x] **Step 5: Validate all project TOML**

Run:

```bash
python3 -c 'import glob,tomllib; [tomllib.load(open(path,"rb")) for path in glob.glob(".codex/**/*.toml", recursive=True)]'
```

Expected: exits `0`.

### Task 3: Durable project policy

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/development/supermemory.md`
- Modify (ignored, machine-local): `.claude/.supermemory-claude/config.json`

**Interfaces:**
- Consumes: `docs/development/supermemory.md` and the project-local role names.
- Produces: a concise, agent-visible contract for delegation, memory isolation, and MCP preservation.

- [x] **Step 1: Add a Codex agents section**

Document the three roles, require bounded independent delegation, and state
that the primary agent owns decisions, edits, and all persistent-memory
operations. Require `fork_turns="none"` plus a self-contained brief when
selecting a custom agent type.

- [x] **Step 2: Add the memory isolation summary**

Require one explicit `containerTag` per operation, separate recalls for
`complect:project` and `complect:task:<task-key>`, no recall without a tag, and
no secrets or PII.

- [x] **Step 3: Rotate the current task container**

Set machine-local `repoContainerTag` to:

```text
complect:task:20260725-codex-agent-configuration
```

Set `personalContainerTag=complect:project` in the same ignored project config.
This supplies the project read only when the hook cwd belongs to Complect.

Replace the stale concrete tag in `docs/development/supermemory.md` with a
generic rule that the machine-local value must match the current task and must
not be committed. Document that global `projectContainerTag` is intentionally
unset to prevent cross-repository recall.

- [x] **Step 4: Add the add-only MCP policy**

Require explicit user authorization before removing, disabling, renaming, or
replacing an existing MCP server. Place reusable additions globally,
project-only additions locally, and reference credentials through environment
variables.

- [x] **Step 5: Correct the OpenCode description**

Make the `AGENTS.md` description match the actual ignored `opencode.json`:
Superpowers is configured; do not claim compound-engineering is present.

### Task 4: Cross-layer verification

**Files:**
- Verify: `/home/sever/.codex/config.toml`
- Verify: `/home/sever/.codex/AGENTS.md`
- Verify: `.codex/config.toml`
- Verify: `.codex/agents/*.toml`
- Verify: `AGENTS.md`

**Interfaces:**
- Consumes: the final global and project layers.
- Produces: evidence that Codex loads the config, MCP entries are preserved, and no project memory leaked globally.

- [x] **Step 1: Check feature and MCP loading**

Run:

```bash
codex features list
codex mcp list
```

Expected: `multi_agent` is `true`, `memories` is `false`, and
`openaiDeveloperDocs` and `playwright` remain enabled with their original
transports. Recalculate the complete MCP table hash; it must equal the
pre-edit hash. `codex doctor --json` must not report a legacy feature alias.

- [x] **Step 2: Check required agent schema**

Run a TOML script that asserts every `.codex/agents/*.toml` has non-empty
`name`, `description`, and `developer_instructions`, and that explorer/reviewer
are read-only.

- [x] **Step 3: Check scope, secrets, and whitespace**

Run:

```bash
rg -n "complect:" /home/sever/.codex/config.toml /home/sever/.codex/AGENTS.md /home/sever/.codex/supermemory.json
rg -n "containerTag|add-only|complect_explorer|complect_reviewer|complect_verifier" AGENTS.md .codex
git diff --check -- AGENTS.md .codex docs/development/supermemory.md docs/superpowers/specs/2026-07-25-codex-agent-configuration-design.md docs/superpowers/plans/2026-07-25-codex-agent-configuration.md
git status --short
```

Expected: no global `complect:` match, all project policy terms are present,
no whitespace errors, and unrelated user changes remain untouched.

- [x] **Step 4: Verify runtime memory scope**

Run `session-start.js` against an isolated test home with debug logging and a
non-routable test endpoint. Assert:

- Complect canonical tag is
  `complect:task:20260725-codex-agent-configuration`;
- Complect all-reads contains `complect:project`;
- a cwd outside Complect contains no `complect:*`.

List memory counts without returning memory content. Expected: generated legacy
Complect containers contain `0` active memories; existing records remain only
in the explicit project and completed-task containers.

Start one ephemeral Codex session with `--strict-config`, hooks disabled, and a
read-only sandbox. Spawn `complect_explorer` with `fork_turns="none"` and verify
that it returns the expected marker.

### Task 5: Direct local Supermemory ingestion and final flush

**Files:**
- Modify: `docker-compose.dev.yml:124-134`
- Verify: `.claude/.supermemory-claude/config.json`
- Verify: `/home/sever/.codex/supermemory/flush.js`
- Verify: `/home/sever/.codex/sessions/2026/07/24/rollout-2026-07-24T22-49-57-019f95ad-6761-7091-89de-e51467a5f4f0.jsonl`

**Interfaces:**
- Consumes: host Qwen at `http://host.docker.internal:8090/v1`, server
  `0.0.6`, named volume `supermemory_data`, task container
  `complect:task:20260725-codex-agent-configuration`, and transcript custom ID
  `019f95ad-6761-7091-89de-e51467a5f4f0`.
- Produces: a healthy direct ingestion path and at least one searchable memory
  in the same task container.

- [x] **Step 1: Verify the config-level RED state**

The user approved the TDD exception for this configuration-only change. Use
the Compose model itself as the executable assertion:

```bash
docker compose -f docker-compose.dev.yml config --format json \
  | jq -e '.services.supermemory.environment.WORKFLOW_ENGINE == "direct"'
```

Expected: exit `1`, because `WORKFLOW_ENGINE` is absent.

Confirm the runtime symptom without returning document content:

```bash
docker compose -f docker-compose.dev.yml exec -T supermemory sh -c \
  'curl --silent --output /dev/null --write-out "%{http_code}\n" \
  http://127.0.0.1:6767/api/rivet/metadata'
```

Expected before the change: `500`; the final task document status is
`queued`.

- [x] **Step 2: Select the built-in direct workflow engine**

Add exactly one environment entry to the existing `supermemory` service:

```yaml
      WORKFLOW_ENGINE: direct
```

Place it beside `OPENAI_MODEL`. Do not change the pinned server version, Qwen
endpoint, API key reference, ports, volumes, security options, or any other
service.

- [x] **Step 3: Verify the Compose model is GREEN**

Run:

```bash
docker compose -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.dev.yml config --format json \
  | jq -e '
      .services.supermemory.environment.WORKFLOW_ENGINE == "direct"
      and ([.services | to_entries[]
            | select(.key != "supermemory")
            | .value.environment.WORKFLOW_ENGINE?] | all(. == null))
    '
git diff --check -- docker-compose.dev.yml
```

Expected: all commands exit `0`; only `supermemory` receives the variable.

- [x] **Step 4: Recreate only Supermemory**

Run:

```bash
docker compose -f docker-compose.dev.yml up -d --force-recreate --no-deps supermemory
docker compose -f docker-compose.dev.yml ps supermemory
```

Expected: `complect_supermemory` becomes healthy; no other service is
recreated and `supermemory_data` remains attached.

- [x] **Step 5: Verify the direct runtime and Qwen boundary**

Run:

```bash
docker compose -f docker-compose.dev.yml exec -T supermemory sh -c '
  test "$WORKFLOW_ENGINE" = direct
  curl --fail --silent --output /dev/null http://host.docker.internal:8090/health
  curl --fail --silent --output /dev/null http://host.docker.internal:8090/v1/models
'
```

Inspect only the new container logs with Supermemory-looking keys redacted.
Expected: no new `Cannot find module '@rivetkit/rivetkit-wasm'`; the direct
engine does not register the Rivet metadata route.

- [x] **Step 6: Wait for the existing document to retry and finish**

The queued document becomes eligible 30 minutes after its creation and the
pinned server retries queued documents every 30 minutes. Poll:

```bash
curl --silent http://127.0.0.1:6767/v3/documents/019f95ad-6761-7091-89de-e51467a5f4f0 \
  -H "Authorization: Bearer $SUPERMEMORY_CODEX_API_KEY" \
  | jq -r '.status'
```

Expected after the next eligible cron: transitions from `queued` through
processing stages to `done`. If it becomes `failed`, inspect redacted
Supermemory logs and stop; do not delete the document or its tracker.

- [x] **Step 7: Verify exact-container search**

Send exactly one `containerTag` to `/v4/search`:

```json
{
  "q": "local qwen supermemory codex agent configuration",
  "containerTag": "complect:task:20260725-codex-agent-configuration",
  "limit": 5,
  "searchMode": "hybrid"
}
```

Expected: HTTP `200` and at least one result. Print only the result count and
document status, not memory content. Re-run `make supermemory-status` and
`git diff --check -- docker-compose.dev.yml`; both must pass.

- [x] **Step 8: Preserve the dirty worktree**

Do not commit `docker-compose.dev.yml`: the file already contains the user's
larger uncommitted Supermemory service addition. Report the exact one-line
change, runtime evidence, task container, and final document status without
staging or modifying unrelated files.
