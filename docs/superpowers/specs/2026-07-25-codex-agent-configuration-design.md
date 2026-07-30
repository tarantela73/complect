# Codex Agent Configuration Design

## Цель

Настроить Codex для Complect так, чтобы общие пользовательские правила не
смешивались с проектными, project-local агенты имели узкие роли, существующие
MCP не повреждались, а Supermemory всегда использовала явную границу
`containerTag`.

## Разделение ответственности

### Глобальный слой `~/.codex`

- `config.toml` хранит только переносимые пользовательские defaults:
  `workspace-write`, `on-request`, отключённый sandbox network, включённый
  multi-agent и ограничение параллельности тремя дочерними потоками.
- Существующие model, trust, app и MCP-настройки сохраняются без
  переименования, удаления и дублирования. Встроенная Codex Memories
  отключается целенаправленно, потому что не обеспечивает task isolation через
  `containerTag`; сохранённые данные при этом не удаляются. Устаревший
  feature alias `codex_hooks` заменяется каноническим `hooks` без изменения
  поведения hook.
- `AGENTS.md` содержит общие рабочие соглашения: сохранение dirty worktree,
  безопасные изменения конфигурации, add-only MCP и запрет секретов в TOML.
- Глобальный `supermemory.json` хранит только transport/automation defaults и
  не содержит `projectContainerTag`, `customContainers` или `complect:*`.
- В глобальном слое нет `complect:*`, task-key или локальных проектных ролей.
  Единственное допустимое project-specific значение там — существующая запись
  trust для пути репозитория, поскольку Codex хранит trust на user level.

### Проектный слой Complect

- Корневой `AGENTS.md` остаётся авторитетным кратким руководством по проекту и
  ссылается на `docs/development/supermemory.md`.
- `.codex/config.toml` задаёт только проектный лимит параллельных агентов.
  Также он явно сохраняет `workspace-write`, `on-request` и запрет sandbox
  network. Глобальные MCP наследуются и не копируются в репозиторий.
- `.codex/agents/*.toml` определяет три узкие роли:
  - `complect_explorer` — read-only карта кода, ADR и границ доменов;
  - `complect_reviewer` — read-only ревью correctness, security и архитектурных
    инвариантов;
  - `complect_verifier` — запускает релевантные проверки, не изменяя код.

Основной агент принимает решения и выполняет изменения. Project-local роли
используются только для независимых ограниченных подзадач и возвращают
доказательства с путями и командами. Подагенты не читают и не записывают
Supermemory самостоятельно: только основной агент координирует память и
передаёт ровно один заранее выбранный `containerTag`. В custom agent files
`features.hooks=false`, поэтому их `Stop` не создаёт автоматические summaries.
Custom roles запускаются с `fork_turns="none"` и самодостаточным brief, потому
что full-history fork наследует parent agent type.

## Supermemory

- Проектные знания читаются и пишутся только с
  `containerTag=complect:project`.
- Контекст текущей задачи использует ровно один
  `containerTag=complect:task:<task-key>`.
- `task-key` выбирается один раз на задачу по правилам
  `docs/development/supermemory.md`.
- Машинно-локальный Claude `repoContainerTag`, используемый общим Stop hook,
  устанавливается в `complect:task:20260725-codex-agent-configuration` для
  текущей задачи; завершённый tag от 2026-07-24 больше не используется.
- Машинно-локальный `personalContainerTag=complect:project` даёт
  дополнительный project read только внутри Complect. Глобальный plugin config
  не содержит Complect tags, поэтому другие репозитории используют собственный
  сгенерированный project tag.
- Recall без `containerTag`, чтение контейнера другой задачи и автоматическая
  запись task-диалога в project container запрещены.
- Секреты, PII, полные логи и неподтверждённые гипотезы не сохраняются.

### Локальный ingestion engine

Self-hosted Supermemory `0.0.6` запускается с
`WORKFLOW_ENGINE=direct`. Этот режим обходит встроенный Rivet runtime, который
в packaged binary не загружает `@rivetkit/rivetkit-wasm` и оставляет принятые
документы в `queued`, хотя host Qwen и её OpenAI-compatible API доступны.
Версия сервера, модель, volume и границы контейнеров при этом не меняются.

Рассмотренные альтернативы:

- обновление pinned server может исправить bundled Rivet, но одновременно
  меняет API и формат локального хранилища и требует отдельной миграционной
  проверки;
- ожидание cron с Rivet сохраняет известный неработающий ingestion path и не
  гарантирует переход документа в `done`.

Изменение ограничивается environment сервиса `supermemory`. Пересоздаётся
только этот контейнер; именованный volume `supermemory_data`, host
`llama-server` и остальные сервисы не останавливаются. Уже принятый документ
текущей задачи повторно обрабатывается штатным retry, без записи в другой
`containerTag`.

## Add-only MCP

Add-only означает сохранение каждого существующего MCP entry и его параметров.
Новый MCP можно добавить в подходящий слой, но удалять, отключать,
переименовывать или заменять существующий сервер разрешено только по явному
запросу пользователя с указанием сервера.

- Переиспользуемый MCP добавляется глобально.
- MCP, нужный только Complect, добавляется в `.codex/config.toml`.
- Credentials задаются через environment variable references, не статическими
  значениями в TOML.
- После изменения проверяются TOML, загрузка конфигурации и `codex mcp list`.

## Безопасность и ошибки

- Базовый режим: `sandbox_mode = "workspace-write"` и
  `approval_policy = "on-request"`; sandbox network остаётся выключенной.
- Project-local explorer и reviewer принудительно read-only.
- Ни один агент не получает `danger-full-access`.
- Встроенные Codex Memories не генерируют и не подмешивают глобальный контекст;
  долговременная память Complect идёт только через тегированную Supermemory.
- Если project config не загружается, сначала проверяется trust проекта.
- Ошибка одного необязательного MCP или Supermemory не блокирует локальную
  разработку; агент сообщает ограничение и продолжает без выдуманного
  fallback-контекста.

## Проверка

1. Оба `config.toml` и все agent TOML разбираются `tomllib`.
2. `codex features list` видит `multi_agent=true`.
3. `codex features list` показывает `memories=false`.
4. `codex mcp list` по-прежнему содержит исходные `openaiDeveloperDocs` и
   `playwright` с прежними параметрами; hash всей таблицы `mcp_servers`
   совпадает с baseline.
5. В глобальных `AGENTS.md`, `config.toml` и `supermemory.json` нет
   `complect:` и project-only ролей; существующая project trust entry
   сохранена.
6. В project-local agent files нет секретов и все обязательные поля
   `name`, `description`, `developer_instructions`; hooks отключены.
7. `git diff --check` не находит whitespace errors.
8. `codex doctor --json` не сообщает legacy feature alias.
9. Изолированный hook dry-run показывает для Complect canonical task tag плюс
   `complect:project`, а для cwd вне репозитория не содержит `complect:*`.
10. Все автоматически добавляемые legacy read-контейнеры Complect имеют `0`
    активных записей.
11. Ephemeral Codex session с `--strict-config` запускает
    `complect_explorer` при `fork_turns="none"`.
12. Compose config передаёт `WORKFLOW_ENGINE=direct` только сервису
    `supermemory`.
13. Из контейнера `supermemory` доступны Qwen `/health` и `/v1/models`.
14. Финальный Codex transcript переходит из `queued` в `done`, а точечный
    `/v4/search` с
    `containerTag=complect:task:20260725-codex-agent-configuration`
    возвращает хотя бы одну память.
