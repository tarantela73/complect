# Agent Guidelines Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `AGENTS.md` the authoritative repository guide and reduce `CLAUDE.md` to Claude-specific behavior and integrations.

**Architecture:** Project-wide rules remain in `AGENTS.md`; `CLAUDE.md` references those rules instead of copying them. Documentation-only validation protects the user's existing uncommitted edit and checks links, headings, duplicated facts, and whitespace.

**Tech Stack:** Markdown, Git, ripgrep

---

### Task 1: Complete the authoritative repository guide

**Files:**
- Modify: `AGENTS.md:1-383`

- [ ] **Step 1: Capture the existing user change**

Run:

```bash
git diff -- AGENTS.md
```

Expected: the existing `opencode.json` line links to Superpowers. Keep that line unchanged.

- [ ] **Step 2: Remove the brittle ADR count**

Replace:

```markdown
ADR (23 штуки): `docs/adr/`
```

with:

```markdown
Реестр архитектурных решений: `docs/adr/`
```

- [ ] **Step 3: Add Git and pull-request guidance**

Insert before `## Чего НЕ делать`:

```markdown
## Коммиты и Pull Requests

- Формат коммита: `type(scope): description`; scope можно опустить. Используйте
  принятые типы `feat`, `fix`, `docs`, `test`, `refactor`, `style`, `chore`.
- Один коммит должен описывать одно логическое изменение. Не включайте
  сгенерированные или посторонние файлы без необходимости.
- В PR укажите цель и границы изменения, связанную задачу (если есть), риски,
  миграции и выполненные проверки.
- Перед PR запустите `make lint`, `make types` и релевантный набор тестов. При
  изменении API выполните `make generate-sdk` и проверьте получившийся diff.
- Для видимых frontend-изменений приложите скриншоты или короткую запись
  основного и изменённого сценариев.
```

- [ ] **Step 4: Validate the guide**

Run:

```bash
rg -n "23 штуки|Коммиты и Pull Requests|type\\(scope\\)" AGENTS.md
git diff --check -- AGENTS.md
```

Expected: no fixed ADR count; the Git/PR section is present; `git diff --check` exits 0.

- [ ] **Step 5: Commit only this task**

```bash
git add AGENTS.md
git commit -m "docs(agents): define repository contribution workflow"
```

### Task 2: Reduce Claude-specific instructions

**Files:**
- Modify: `CLAUDE.md:1-354`

- [ ] **Step 1: Replace duplicated content with a focused document**

Rewrite `CLAUDE.md` with these exact sections and responsibilities:

```markdown
# Claude Development Instructions — ERP «Комплект»

## Источники правил

Перед работой прочитать `AGENTS.md`. Это основной источник архитектурных,
инфраструктурных, тестовых и security-правил проекта.

- `CONCEPTS.md` — доменный глоссарий.
- `docs/Project_architecture.md` — архитектурная спецификация.
- `docs/adr/` — реестр архитектурных решений.
- `docs/solutions/` — решения известных проблем и рабочие паттерны.

При расхождении следовать `AGENTS.md`, затем наиболее релевантному принятому ADR.

## Начало сессии

1. Проверить стек: `docker compose -f docker-compose.dev.yml ps`.
2. Если необходимые контейнеры не запущены — выполнить `make up`.
3. Если Supermemory MCP доступен, найти состояние проекта по
   `containerTag=complect-erp`. Недоступность MCP не блокирует работу.

## Project Memory

Supermemory с `containerTag=complect-erp` — основной внешний источник памяти,
когда интеграция доступна. После существенной реализации, исправления или
обнаружения неочевидного ограничения сохранить краткую запись с типом
`project`, `feedback` или `reference`. Файловую память Claude использовать
только как fallback.

## Роль и коммуникация

- Роль: Senior Software Architect и Technical Lead.
- Отвечать строго на русском; английский использовать для кода, идентификаторов
  и устоявшихся технических терминов.
- Писать профессионально, кратко и прямо, без приветствий и повторных итогов.
- Начинать с результата, кода или конкретного ответа.
- Не объяснять очевидный синтаксис; ориентироваться на senior-level аудиторию.

## Skills

Перед каждым ответом проверять применимость доступных skills и применять
подходящие автоматически. Инструкции фактически доступного skill имеют
приоритет над устаревшим перечнем инструментов в документации.

Особенно важны skills для:

- проектирования и планирования изменений;
- систематической диагностики ошибок;
- TDD и проверки результата перед заявлением о завершении;
- security review для auth, PII, пользовательского ввода и API;
- browser QA после видимых frontend-изменений;
- ADR и zero-downtime миграций.

## MCP

MCP использовать, когда задача не покрывается skill или требует внешней
интеграции:

- Supermemory — память проекта;
- PostgreSQL — диагностика данных и запросов;
- Docker — управление окружением;
- Playwright — browser QA;
- Serena — семантический поиск для крупных рефакторингов.

Использовать только реально доступные в текущей сессии инструменты.

## Формат ответа

- Ссылаться на файлы кликабельными Markdown-ссылками.
- Для сложных архитектурных потоков использовать Mermaid, если диаграмма
  действительно упрощает понимание.
- Указывать выполненные проверки и реальные ограничения окружения.
- Не заявлять об успешном завершении без свежей проверки результата.
```

- [ ] **Step 2: Check that duplicated project facts are gone**

Run:

```bash
rg -n "Celery Queue Topology|Coverage Thresholds|Architecture Laws|23 ADR|acks_late" CLAUDE.md
```

Expected: no matches.

- [ ] **Step 3: Validate Markdown and scope**

Run:

```bash
rg -n "^#|^##" CLAUDE.md
git diff --check -- CLAUDE.md
wc -l CLAUDE.md
```

Expected: only the planned top-level sections, no whitespace errors, and a substantially shorter document.

- [ ] **Step 4: Commit only this task**

```bash
git add CLAUDE.md
git commit -m "docs(claude): reference authoritative project guidelines"
```

### Task 3: Cross-document verification

**Files:**
- Verify: `AGENTS.md`
- Verify: `CLAUDE.md`

- [ ] **Step 1: Verify authoritative references and preserved edit**

Run:

```bash
rg -n "Superpowers|Реестр архитектурных решений|Коммиты и Pull Requests" AGENTS.md
rg -n "AGENTS.md|CONCEPTS.md|docs/adr/|docs/solutions/|Недоступность MCP не блокирует" CLAUDE.md
```

Expected: every required reference is found and the Superpowers link remains present.

- [ ] **Step 2: Check the final documentation diff**

Run:

```bash
git diff --check HEAD~2 -- AGENTS.md CLAUDE.md
git status --short
```

Expected: no whitespace errors. Pre-existing backend/frontend modifications may remain; no unrelated file is staged.

- [ ] **Step 3: Record verification**

No application test suite is required for Markdown-only changes. Report the two
documentation commits, the successful checks, and any untouched pre-existing
working-tree changes.
