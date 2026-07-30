# Agent Operational Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить project-owned lifecycle задач, doctor, secret lint и безопасный wrapper локальной Supermemory без изменения установленных плагинов, MCP и ERP-кода.

**Architecture:** Новые Python 3.12 utilities живут в `infra/scripts/agent_tools/` и используют только стандартную библиотеку. Чистые функции принимают пути, environment и runner как зависимости, поэтому unit-тесты не требуют Docker, сети или credentials; Makefile предоставляет стабильный CLI, а live probes включаются отдельно.

**Tech Stack:** Python 3.12 standard library (`argparse`, `dataclasses`, `json`, `pathlib`, `subprocess`, `tomllib`, `urllib`), `unittest`, GNU Make, локальные Codex/Supermemory JSON/TOML interfaces.

## Global Constraints

- Не изменять `~/.codex/config.toml`, `~/.codex/hooks.json`, plugin cache или существующие MCP entries.
- Не устанавливать новые плагины, MCP или Python dependencies.
- Не читать `.env`, volume, Git history или credential store из secret linter.
- Никогда не печатать API keys, passwords, response bodies или найденные secret values.
- Каждая memory operation получает ровно один явный `containerTag`.
- Допустимы только `complect:project` и `complect:task:<task-key>`, где task-key соответствует `^[a-z0-9]+(?:-[a-z0-9]+)*$`.
- Сохранять все неизвестные поля машинно-локального Supermemory config.
- Не изменять шесть существующих незакоммиченных backend/frontend request-файлов.
- Unit-тесты выполняются без сети; live probes запускаются отдельной командой.

---

## File Map

- `infra/scripts/agent_tools/__init__.py` — package marker и публичная версия tooling.
- `infra/scripts/agent_tools/common.py` — task/container validation, JSON load и atomic write.
- `infra/scripts/agent_tools/task.py` — `start`, `status`, `close` для local task lifecycle.
- `infra/scripts/agent_tools/secret_lint.py` — redacted tracked-file credential scanner.
- `infra/scripts/agent_tools/doctor.py` — offline checks, optional live probes и итоговый exit code.
- `infra/scripts/agent_tools/memory.py` — explicit-container search/save client.
- `infra/scripts/agent_tools/tests/test_task.py` — lifecycle и atomic-write tests.
- `infra/scripts/agent_tools/tests/test_secret_lint.py` — positive/negative scanner tests.
- `infra/scripts/agent_tools/tests/test_doctor.py` — PASS/WARN/FAIL и config checks.
- `infra/scripts/agent_tools/tests/test_memory.py` — container/payload/redaction tests.
- `Makefile` — стабильные `agent-*` targets.
- `docs/development/supermemory.md` — operational workflow и ограничения.

---

### Task 1: Task lifecycle and atomic local state

**Files:**
- Create: `infra/scripts/agent_tools/__init__.py`
- Create: `infra/scripts/agent_tools/common.py`
- Create: `infra/scripts/agent_tools/task.py`
- Create: `infra/scripts/agent_tools/tests/__init__.py`
- Create: `infra/scripts/agent_tools/tests/test_task.py`

**Interfaces:**
- Consumes: ignored config path `.claude/.supermemory-claude/config.json` and state path `.claude/.supermemory-claude/task-state.json`.
- Produces:
  - `CommandResult(returncode: int, stdout: str, stderr: str)`
  - `CommandRunner = Callable[[Sequence[str], Path | None], CommandResult]`
  - `validate_task_key(value: str) -> str`
  - `validate_container_tag(value: str) -> tuple[str, str | None]`
  - `atomic_write_json(path: Path, payload: Mapping[str, object]) -> None`
  - `start_task(config_path: Path, state_path: Path, task_key: str, now: datetime) -> dict[str, object]`
  - `close_task(config_path: Path, state_path: Path, now: datetime) -> dict[str, object]`
  - `read_task_status(config_path: Path, state_path: Path) -> dict[str, object]`

- [x] **Step 1: Create package markers and failing task validation tests**

```python
class TaskKeyValidationTests(unittest.TestCase):
    def test_accepts_canonical_key(self):
        self.assertEqual(validate_task_key("issue-142"), "issue-142")

    def test_rejects_non_canonical_keys(self):
        for value in ("Issue-142", "issue_142", "-issue", "issue-", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_task_key(value)

    def test_container_validation_returns_scope_and_task(self):
        self.assertEqual(validate_container_tag("complect:project"), ("project", None))
        self.assertEqual(
            validate_container_tag("complect:task:issue-142"),
            ("task", "issue-142"),
        )
```

- [x] **Step 2: Run the focused tests and confirm the import failure**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_task.TaskKeyValidationTests -v
```

Expected: `ModuleNotFoundError` for `infra.scripts.agent_tools.common`.

- [x] **Step 3: Implement validation and atomic JSON helpers**

`common.py` must define:

```python
TASK_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROJECT_CONTAINER = "complect:project"
TASK_CONTAINER_PREFIX = "complect:task:"


def validate_task_key(value: str) -> str:
    if not TASK_KEY_PATTERN.fullmatch(value):
        raise ValueError("task-key must match ^[a-z0-9]+(?:-[a-z0-9]+)*$")
    return value


def validate_container_tag(value: str) -> tuple[str, str | None]:
    if value == PROJECT_CONTAINER:
        return ("project", None)
    if value.startswith(TASK_CONTAINER_PREFIX):
        task_key = validate_task_key(value.removeprefix(TASK_CONTAINER_PREFIX))
        return ("task", task_key)
    raise ValueError("container must be complect:project or complect:task:<task-key>")
```

`atomic_write_json` creates a temporary file in the target directory, writes
UTF-8 JSON with trailing newline, flushes and `os.fsync()` calls, then uses
`os.replace()`. Temporary file is removed in `finally` after errors.

`common.py` also defines:

```python
@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], Path | None], CommandResult]
```

- [x] **Step 4: Run validation tests and confirm they pass**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_task.TaskKeyValidationTests -v
```

Expected: all tests `OK`.

- [x] **Step 5: Add failing lifecycle tests**

```python
def test_start_preserves_unknown_config_fields(self):
    self.write_json(
        self.config_path,
        {
            "baseUrl": "http://127.0.0.1:6767",
            "personalContainerTag": "complect:project",
            "signalExtraction": True,
        },
    )
    result = start_task(self.config_path, self.state_path, "issue-142", self.now)
    config = self.read_json(self.config_path)
    self.assertEqual(config["repoContainerTag"], "complect:task:issue-142")
    self.assertTrue(config["signalExtraction"])
    self.assertEqual(result["status"], "active")


def test_closed_task_cannot_be_reused(self):
    start_task(self.config_path, self.state_path, "issue-142", self.now)
    close_task(self.config_path, self.state_path, self.now)
    with self.assertRaisesRegex(ValueError, "already closed"):
        start_task(self.config_path, self.state_path, "issue-142", self.now)


def test_close_does_not_change_repo_container_tag(self):
    start_task(self.config_path, self.state_path, "issue-142", self.now)
    close_task(self.config_path, self.state_path, self.now)
    config = self.read_json(self.config_path)
    state = self.read_json(self.state_path)
    self.assertEqual(config["repoContainerTag"], "complect:task:issue-142")
    self.assertEqual(state["status"], "closed")
```

- [x] **Step 6: Run lifecycle tests and confirm they fail**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_task.TaskLifecycleTests -v
```

Expected: failures because `start_task`, `close_task` and `read_task_status`
are not implemented.

- [x] **Step 7: Implement lifecycle functions and CLI**

State schema:

```json
{
  "version": 1,
  "taskKey": "issue-142",
  "containerTag": "complect:task:issue-142",
  "status": "active",
  "startedAt": "2026-07-25T00:00:00+00:00",
  "closedAt": null,
  "closedTaskKeys": []
}
```

`close_task` appends the active key to `closedTaskKeys` exactly once and leaves
`repoContainerTag` unchanged. CLI:

```text
python3 -m infra.scripts.agent_tools.task start issue-142
python3 -m infra.scripts.agent_tools.task status
python3 -m infra.scripts.agent_tools.task close
```

Default paths derive from the Git root found with
`git rev-parse --show-toplevel`; tests pass explicit paths and never invoke Git.
`start_task` устанавливает отсутствующий
`personalContainerTag=complect:project`, но сохраняет существующее значение и
все неизвестные поля. CLI output contains only task key, container and status.

- [x] **Step 8: Run all task tests**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_task -v
```

Expected: all tests `OK`.

- [x] **Step 9: Commit lifecycle tooling**

```bash
git add infra/scripts/agent_tools/__init__.py \
  infra/scripts/agent_tools/common.py \
  infra/scripts/agent_tools/task.py \
  infra/scripts/agent_tools/tests/__init__.py \
  infra/scripts/agent_tools/tests/test_task.py
git commit -m "feat(agents): add task container lifecycle"
```

---

### Task 2: Redacted tracked-file secret lint

**Files:**
- Create: `infra/scripts/agent_tools/secret_lint.py`
- Create: `infra/scripts/agent_tools/tests/test_secret_lint.py`

**Interfaces:**
- Consumes: explicit `Path` values or `git ls-files` output.
- Produces:
  - `Finding(path: Path, line: int, rule: str)`
  - `scan_text(path: Path, text: str) -> list[Finding]`
  - `tracked_candidate_paths(root: Path, runner: CommandRunner) -> list[Path]`
  - `run_secret_lint(paths: Sequence[Path]) -> list[Finding]`

- [x] **Step 1: Write failing scanner tests**

```python
def test_reports_rule_without_secret_value(self):
    secret = "sm_" + "A" * 32
    findings = scan_text(Path("settings.json"), f'{{"apiKey":"{secret}"}}')
    self.assertEqual([(f.line, f.rule) for f in findings], [(1, "supermemory-token")])
    self.assertNotIn(secret, "\n".join(str(f) for f in findings))


def test_allows_environment_references_and_placeholders(self):
    text = """
API_KEY=${SUPERMEMORY_API_KEY}
SECRET_KEY=REQUIRED_FROM_VAULT_HIGH_ENTROPY_STRING
EXPECTED_GATEWAY_TOKEN=$EXPECTED_GATEWAY_TOKEN
"""
    self.assertEqual(scan_text(Path(".env.example"), text), [])


def test_ignores_unsupported_binary_and_local_env(self):
    self.assertFalse(is_candidate_path(Path(".env")))
    self.assertFalse(is_candidate_path(Path("image.png")))
```

- [x] **Step 2: Run tests and confirm they fail**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_secret_lint -v
```

Expected: import failure for `secret_lint`.

- [x] **Step 3: Implement scanner with redacted findings**

Rules include:

```python
RULES = (
    ("supermemory-token", re.compile(r"\bsm_[A-Za-z0-9_-]{20,}\b")),
    ("context7-token", re.compile(r"\bctx7sk-[A-Za-z0-9_-]{16,}\b")),
    ("generic-bearer", re.compile(r"Authorization:\s*Bearer\s+(?!\$\{?[A-Z_][A-Z0-9_]*\}?)[^\s\"']+")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
```

Candidate suffixes:

```python
{".md", ".toml", ".json", ".yaml", ".yml", ".sh", ".env", ".example"}
```

Special names include `Dockerfile`, `Makefile` and `docker-compose.dev.yml`.
`.env` and names matching `.env.*` are excluded except `.env.example`.
`Finding.__str__()` returns only `path:line:rule`.

- [x] **Step 4: Run scanner tests**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_secret_lint -v
```

Expected: all tests `OK`.

- [x] **Step 5: Add tracked-file enumeration test**

Use an injected runner returning:

```text
AGENTS.md
.codex/config.toml
.env
frontend/public/logo.png
```

Expected candidates: `AGENTS.md`, `.codex/config.toml`; `.env` and PNG are
excluded without reading them.

- [x] **Step 6: Implement `git ls-files -z` enumeration and CLI**

CLI:

```text
python3 -m infra.scripts.agent_tools.secret_lint [path ...]
```

No explicit paths means tracked candidates from `git ls-files -z`. Exit `1`
when findings exist, otherwise print `PASS agent-secret-check files=<count>`
and exit `0`.

- [x] **Step 7: Run all secret-lint tests and a repository scan**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_secret_lint -v
python3 -m infra.scripts.agent_tools.secret_lint \
  .codex AGENTS.md Makefile docker-compose.dev.yml docs/development/supermemory.md
```

Expected: tests pass and repository scan exits `0` without printing secret
values.

- [x] **Step 8: Commit secret lint**

```bash
git add infra/scripts/agent_tools/secret_lint.py \
  infra/scripts/agent_tools/tests/test_secret_lint.py
git commit -m "feat(agents): add redacted secret lint"
```

---

### Task 3: Offline and live Agent Doctor

**Files:**
- Create: `infra/scripts/agent_tools/doctor.py`
- Create: `infra/scripts/agent_tools/tests/test_doctor.py`

**Interfaces:**
- Consumes: project root, Codex home, environment mapping, injected command
  runner and injected HTTP probe.
- Produces:
  - `Status` enum: `PASS`, `WARN`, `FAIL`
  - `Check(name: str, status: Status, detail: str)`
  - `HttpResponse(status: int, json_body: Mapping[str, object] | None)`
  - `HttpProbe` protocol with keyword-only `method`, `url`, `headers`,
    `payload`, `timeout`
  - `run_offline_checks(context: DoctorContext) -> list[Check]`
  - `run_live_checks(context: DoctorContext) -> list[Check]`
  - `exit_code(checks: Sequence[Check]) -> int`

- [x] **Step 1: Write failing result-classification tests**

```python
def test_exit_code_fails_only_on_fail(self):
    self.assertEqual(exit_code([Check("a", Status.PASS, "ok")]), 0)
    self.assertEqual(exit_code([Check("a", Status.WARN, "optional")]), 0)
    self.assertEqual(exit_code([Check("a", Status.FAIL, "broken")]), 1)


def test_closed_task_is_fail(self):
    context = self.context_with_task_state(status="closed")
    checks = run_offline_checks(context)
    task_check = next(check for check in checks if check.name == "task-state")
    self.assertEqual(task_check.status, Status.FAIL)
    self.assertIn("agent-task-start", task_check.detail)
```

- [x] **Step 2: Run doctor tests and confirm they fail**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_doctor -v
```

Expected: import failure for `doctor`.

- [x] **Step 3: Implement result model and task/config checks**

`DoctorContext` fields:

```python
@dataclass(frozen=True)
class HttpResponse:
    status: int
    json_body: Mapping[str, object] | None = None


class HttpProbe(Protocol):
    def __call__(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        payload: Mapping[str, object] | None = None,
        timeout: float = 5.0,
    ) -> HttpResponse: ...


@dataclass(frozen=True)
class DoctorContext:
    project_root: Path
    codex_home: Path
    environ: Mapping[str, str]
    runner: CommandRunner
    http_probe: HttpProbe
```

Offline checks parse:

- `/home/.../.codex/config.toml`;
- `.codex/config.toml`;
- `.codex/agents/*.toml`;
- `~/.codex/hooks.json`;
- `~/.codex/supermemory.json`;
- `.claude/.supermemory-claude/config.json`;
- `.claude/.supermemory-claude/task-state.json`.

Expected invariants are copied from the design. Detail strings name fields and
paths but never include values matching `key|token|secret|password`.

- [x] **Step 4: Add and pass cadence/status regression test**

Test fixture:

```json
{"autoRecallEveryPrompt": false, "captureEveryNTurns": 0}
```

Expected doctor detail:

```text
autoRecallEveryPrompt=false captureEveryNTurns=0 stop_hook=present
```

The implementation must not use the hard-coded prose from upstream
`status.js`.

- [x] **Step 5: Add MCP, plugin and GitHub warning tests**

Injected `codex mcp list` output contains `playwright` and
`openaiDeveloperDocs`; both produce `PASS`. Missing `gh` produces:

```python
Check("github-cli", Status.WARN, "gh is not installed; connector availability is session-scoped")
```

Missing optional Product Design manifest is `WARN`; missing GitHub or
Superpowers manifest is `FAIL`.

- [x] **Step 6: Implement remaining offline checks and CLI**

CLI:

```text
python3 -m infra.scripts.agent_tools.doctor
python3 -m infra.scripts.agent_tools.doctor --live
python3 -m infra.scripts.agent_tools.doctor --json
```

Text output is one line per check:

```text
PASS task-state active complect:task:issue-142
WARN github-cli gh is not installed; connector availability is session-scoped
```

JSON output contains `name`, `status`, `detail`, but no environment values.

- [x] **Step 7: Write failing live-probe tests**

Injected probes assert:

- Qwen GET `http://127.0.0.1:8090/health`;
- Supermemory GET `http://127.0.0.1:6767/` discards the body;
- two POST searches use separate payloads, one with `complect:project`, one
  with the active task container;
- only result counts enter check detail.

- [x] **Step 8: Implement live probes**

Use `urllib.request` with five-second timeout. Search body:

```python
{
    "q": "project scope task kind",
    "containerTag": container_tag,
    "limit": 1,
    "searchMode": "hybrid",
}
```

`Authorization` reads `SUPERMEMORY_CODEX_API_KEY`; missing key is `FAIL` only
for `--live`. `docker compose -f docker-compose.dev.yml ps supermemory` is a
separate check.

- [x] **Step 9: Run doctor unit tests and offline CLI**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_doctor -v
python3 -m infra.scripts.agent_tools.doctor
```

Expected: tests pass. Offline doctor may report `WARN github-cli`; no `FAIL`.

- [x] **Step 10: Commit Agent Doctor**

```bash
git add infra/scripts/agent_tools/doctor.py \
  infra/scripts/agent_tools/tests/test_doctor.py
git commit -m "feat(agents): add configuration doctor"
```

---

### Task 4: Explicit-container Supermemory wrapper

**Files:**
- Create: `infra/scripts/agent_tools/memory.py`
- Create: `infra/scripts/agent_tools/tests/test_memory.py`

**Interfaces:**
- Consumes: `SUPERMEMORY_CODEX_API_KEY`, active local task state, content from
  file/stdin, local `/v4/search` and `/v4/memories`.
- Produces:
  - `build_search_payload(container_tag: str, query: str, limit: int) -> dict[str, object]`
  - `build_save_payload(container_tag: str, custom_id: str, kind: str, content: str) -> dict[str, object]`
  - `SupermemoryClient.search(...) -> int`
  - `SupermemoryClient.save(...) -> str`

- [x] **Step 1: Write failing validation and payload tests**

```python
def test_search_payload_has_exactly_one_container(self):
    payload = build_search_payload("complect:project", "agent doctor", 5)
    self.assertEqual(payload["containerTag"], "complect:project")
    self.assertNotIn("containerTags", payload)


def test_task_save_requires_active_matching_task(self):
    with self.assertRaisesRegex(ValueError, "active task"):
        validate_save_scope(
            "complect:task:issue-142",
            {"taskKey": "issue-143", "status": "active"},
        )


def test_save_requires_custom_id(self):
    with self.assertRaisesRegex(ValueError, "custom-id"):
        build_save_payload("complect:project", "", "decision", "content")
```

- [x] **Step 2: Run tests and confirm they fail**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_memory -v
```

Expected: import failure for `memory`.

- [x] **Step 3: Implement validation and payload builders**

Save memory object:

```python
{
    "content": content,
    "customId": custom_id,
    "isStatic": scope == "project",
    "metadata": {
        "project": "complect",
        "scope": scope,
        "kind": kind,
        **({"task": task_key} if task_key else {}),
    },
}
```

Request body:

```python
{"containerTag": container_tag, "memories": [memory]}
```

Allowed kinds: `decision`, `finding`, `feedback`, `handoff`, `reference`.

- [x] **Step 4: Add mock HTTP redaction tests**

Mock response bodies contain a fake token and memory content. Assert stdout
contains only:

```text
saved custom_id=agent-doctor-v1 container=complect:project
search container=complect:project count=1
```

and does not contain response body, content or fake token.

- [x] **Step 5: Implement `SupermemoryClient` and CLI**

CLI:

```text
python3 -m infra.scripts.agent_tools.memory search \
  --container complect:project --query "agent doctor" --limit 5

python3 -m infra.scripts.agent_tools.memory save \
  --container complect:project \
  --custom-id complect-agent-doctor-v1 \
  --kind decision \
  --content-file /tmp/memory.txt
```

`--content-file -` reads stdin. HTTP failures print endpoint path, status and
container only. Client base URL defaults to `http://127.0.0.1:6767` and may be
overridden by `SUPERMEMORY_API_URL`.

- [x] **Step 6: Run all memory tests**

Run:

```bash
python3 -m unittest infra.scripts.agent_tools.tests.test_memory -v
```

Expected: all tests `OK`.

- [x] **Step 7: Run safe live customId contract test**

Create a short temporary content file without secrets and save to the active
task container using custom ID
`complect-agent-tools-contract-20260725`. Repeat the same save once.

Then search the exact task container and print only count. Expected:

- both saves return success;
- response confirms the same custom ID or stable memory identity;
- search succeeds;
- if stability cannot be confirmed, CLI exits non-zero with
  `customId contract is not confirmed` and save functionality remains
  disabled.

The contract test does not delete memories.

- [x] **Step 8: Commit memory wrapper**

```bash
git add infra/scripts/agent_tools/memory.py \
  infra/scripts/agent_tools/tests/test_memory.py
git commit -m "feat(agents): add scoped memory wrapper"
```

---

### Task 5: Makefile, documentation and integrated verification

**Files:**
- Modify: `Makefile`
- Modify: `docs/development/supermemory.md`
- Modify: `docs/superpowers/plans/2026-07-25-agent-operational-hardening.md`

**Interfaces:**
- Consumes: CLIs from Tasks 1–4.
- Produces: stable `make agent-*` workflow and checked-off implementation
  record.

- [x] **Step 1: Add Makefile targets**

```make
agent-task-start:
	@test -n "$(TASK_KEY)" || (echo "TASK_KEY is required" >&2; exit 2)
	python3 -m infra.scripts.agent_tools.task start "$(TASK_KEY)"

agent-task-status:
	python3 -m infra.scripts.agent_tools.task status

agent-task-close:
	python3 -m infra.scripts.agent_tools.task close

agent-doctor:
	python3 -m infra.scripts.agent_tools.doctor

agent-doctor-live:
	python3 -m infra.scripts.agent_tools.doctor --live

agent-secret-check:
	python3 -m infra.scripts.agent_tools.secret_lint

agent-tools-test:
	python3 -m unittest discover -s infra/scripts/agent_tools/tests -v
```

- [x] **Step 2: Document start/close/doctor workflow**

Add to `docs/development/supermemory.md`:

```text
1. Before the first memory operation of a new task:
   make agent-task-start TASK_KEY=<key>
2. Verify:
   make agent-doctor
3. Before handoff:
   save with one explicit container, then make agent-task-close.
4. A closed state intentionally makes doctor fail until the next unique task
   key is started.
```

Document that upstream `supermemory-status` displays hard-coded cadence and
is not authoritative; `agent-doctor` reads actual config.

- [x] **Step 3: Run focused tooling verification**

Run:

```bash
make agent-tools-test
make agent-secret-check
make agent-task-status
make agent-doctor
```

Expected: tests and secret check pass. If the current task was previously
closed, start the already selected hardening task key once before doctor; do
not reuse the completed `20260725-codex-agent-configuration` key.

- [x] **Step 4: Run live verification**

Load credentials into the current process without printing them, then run:

```bash
make agent-doctor-live
make supermemory-status
```

Expected: Qwen and Supermemory health pass; project/task searches return
counts; missing `gh` remains `WARN`.

- [x] **Step 5: Run configuration and repository checks**

Run:

```bash
python3 -c 'import pathlib,tomllib; [tomllib.loads(p.read_text()) for p in pathlib.Path(".codex").rglob("*.toml")]'
codex mcp list
POSTGRES_REPLICATION_PASSWORD=verification-only \
  docker compose -f docker-compose.dev.yml config --quiet
git diff --check
git status --short
```

Expected:

- four project TOML files parse;
- `playwright` and `openaiDeveloperDocs` remain enabled;
- Compose config is valid;
- only agent-tooling files plus the six pre-existing request files are dirty.

- [x] **Step 6: Verify no ERP test suite is required**

Run:

```bash
git diff --name-only 03be6f5..HEAD
```

Expected: no paths under `backend/` or `frontend/` are introduced by this
plan. The six pre-existing dirty request files remain unstaged. Therefore
`make test-all` is not required; the prior verified application suite remains
unaffected.

- [x] **Step 7: Mark plan checkboxes and commit integration**

Update only completed checkboxes in this plan, then:

```bash
git add Makefile docs/development/supermemory.md \
  docs/superpowers/plans/2026-07-25-agent-operational-hardening.md
git commit -m "docs(agents): document operational workflow"
```

- [x] **Step 8: Final review**

Review:

```bash
git log --oneline 03be6f5..HEAD
git diff --stat 03be6f5..HEAD
git status --short
```

Expected: one focused commit per task, no staged files, and no changes to the
user's request workflow files.
