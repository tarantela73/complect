# Agent Operational Hardening Design

## Цель

Сделать уже установленную инфраструктуру Codex, Supermemory, MCP и project-local
агентов диагностируемой и безопасной в ежедневной работе. Улучшение не добавляет
новые внешние плагины и не меняет архитектуру приложения Complect.

Успешный результат:

- новая задача не может незаметно продолжить запись в завершённый
  `complect:task:<task-key>`;
- одна команда показывает фактическое состояние Codex, hooks, MCP, Qwen и
  Supermemory без вывода секретов;
- ручные операции памяти всегда требуют ровно один допустимый `containerTag`;
- tracked-конфигурация проверяется на литеральные credentials до публикации;
- отсутствие `gh`, внешнего коннектора или необязательного MCP выводится как
  отдельное предупреждение и не маскируется под ошибку конфигурации.

## Границы

В scope входят:

- project-local lifecycle текущего `task-key`;
- project-owned doctor и memory wrapper;
- проверка tracked-конфигурации на секреты;
- Makefile-команды и документация;
- unit-тесты без сети плюс отдельная opt-in live-проверка.

В scope не входят:

- установка новых ChatGPT-плагинов или MCP;
- изменение либо удаление существующих MCP entries;
- автоматическая авторизация GitHub;
- изменение установленных файлов Supermemory, Context7 или других плагинов в
  `~/.codex`;
- переписывание Git-истории и ротация API-ключей;
- изменения backend/frontend ERP.

## Рассмотренные подходы

### 1. Project-owned wrappers — выбран

Репозиторий получает небольшие стандартно-библиотечные утилиты в
`infra/scripts/`. Они читают существующую конфигурацию, валидируют её и
обращаются к установленным программам и локальному API через стабильные
интерфейсы.

Преимущества:

- обновление стороннего плагина не перезаписывает исправления;
- поведение можно тестировать без сети;
- project-specific правила остаются в проекте;
- не нарушается add-only MCP policy.

Недостаток: появляется тонкий project-owned operational layer, который надо
поддерживать вместе с `docs/development/supermemory.md`.

### 2. Патчить установленные плагины

Можно исправить `status.js`, `flush.js` и права Context7 непосредственно в
`~/.codex`. Это быстрее, но любое обновление уничтожит изменения, а
project-specific логика окажется в пользовательском слое. Подход отклонён.

### 3. Только документация и ручные команды

Не требует кода, но оставляет уже обнаруженные проблемы: status показывает
ложный cadence, завершённый task tag остаётся активным, а ручное сохранение
может выбрать неверный контейнер. Подход отклонён.

## Компоненты

### `agent_task.py`

Управляет только машинно-локальными файлами внутри игнорируемого
`.claude/.supermemory-claude/`.

Команды:

- `start <task-key>` — валидирует ключ, запрещает повторное использование
  закрытого ключа, атомарно устанавливает
  `repoContainerTag=complect:task:<task-key>` и сохраняет локальное состояние
  `active`;
- `status` — выводит task-key, container tag и состояние без чтения памяти;
- `close` — помечает текущий ключ завершённым, не удаляя его tag и не вызывая
  destructive memory operations.

Допустимый ключ соответствует `^[a-z0-9]+(?:-[a-z0-9]+)*$`. История закрытых
ключей хранится локально и не коммитится. `start` сохраняет существующие
`baseUrl`, `personalContainerTag` и signal-настройки.

`close` не отключает глобальный Stop hook: финальный session flush всё ещё
может завершить запись текущей задачи. После закрытия doctor сообщает ошибку,
пока перед следующей работой не выполнен новый `start`.

### `agent_doctor.py`

Doctor имеет два режима:

- по умолчанию — детерминированные локальные проверки без сети;
- `--live` — дополнительно проверяет loopback Qwen/Supermemory и запускает
  безопасные read-only probes.

Категории результата:

- `PASS` — проверка подтверждена;
- `WARN` — необязательная возможность недоступна;
- `FAIL` — нарушен инвариант, способный смешать память, сломать конфигурацию
  или раскрыть секрет.

Локальные проверки:

- parsing глобального/project TOML и agent TOML;
- `multi_agent=true`, лимит три и наличие трёх project roles;
- отсутствие project MCP duplication;
- наличие hooks `SessionStart`, `UserPromptSubmit`, `Stop`;
- фактические `autoRecallEveryPrompt=false` и `captureEveryNTurns=0`;
- активный незакрытый task state и совпадение `repoContainerTag`;
- `personalContainerTag=complect:project`;
- структурный результат `codex mcp list`;
- наличие GitHub, Superpowers и Product Design manifests;
- наличие `gh` и состояние его авторизации как `WARN`, а не `FAIL`;
- secret lint.

Live-проверки:

- Qwen `/health`;
- Supermemory root health без чтения response body;
- `docker compose ps supermemory`;
- отдельный точечный search для project и текущего task container, выводящий
  только количество результатов.

Doctor не утверждает, что ChatGPT App connector работоспособен, если локального
API для такой проверки нет. Он различает «manifest установлен»,
«MCP enabled», «CLI authenticated» и «live probe passed».

### `supermemory_memory.py`

Project wrapper для явных ручных операций.

Поддерживаются:

- `search --container <tag> --query <text> [--limit N]`;
- `save --container <tag> --custom-id <id> --kind <kind> --content-file <path>`;

Ограничения:

- ровно один `--container`;
- только `complect:project` или
  `complect:task:<валидный-task-key>`;
- task save разрешён только в активный task container;
- project save требует `scope=project`, task save — `scope=task`;
- content читается из файла или stdin, но не обязателен в argv;
- API key читается только из `SUPERMEMORY_CODEX_API_KEY`;
- response body с памятью не печатается;
- search печатает только статус и count;
- повторный save использует обязательный стабильный `custom-id`.

Перед реализацией live contract test должен подтвердить поддержку `customId`
локальным server `0.0.6`. Если endpoint его не подтверждает, save остаётся
заблокированным с понятной ошибкой; обход через delete/reinsert не используется.

### `agent_secret_linter.py`

Линтер проверяет только tracked или явно переданные файлы. Он не читает `.env`,
volume, credentials store и Git history.

Проверяемые форматы: Markdown, TOML, JSON, YAML/YML, Compose и shell scripts.
Находки выводятся как `path:line:rule`, без совпавшего значения.

Разрешены:

- `$VARIABLE` и `${VARIABLE}`;
- явно документированные placeholder-значения в `.env.example`;
- тестовые маркеры, которые не похожи на реальные токены.

Литеральные bearer keys, provider token prefixes, приватные ключи и
credentials в конфигурационных полях дают `FAIL`.

## Команды

Makefile получает:

```text
make agent-task-start TASK_KEY=<key>
make agent-task-status
make agent-task-close
make agent-doctor
make agent-doctor-live
make agent-secret-check
make agent-tools-test
```

Существующие `supermemory-*` команды сохраняются без переименования.
`agent-doctor-live` загружает credentials только из текущего environment;
проект не открывает и не печатает credential files.

## Данные и безопасность

Локальное состояние:

```text
.claude/.supermemory-claude/
├── config.json
└── task-state.json
```

Оба файла уже находятся под существующим `.claude/*` ignore rule. В state
хранятся только task keys, status и timestamps. Секретов, памяти или transcript
там нет.

Записи конфигурации обновляются через temporary file в том же каталоге,
`fsync` и atomic replace. Ошибка записи не должна оставлять частичный JSON.

Утилиты:

- не изменяют `~/.codex/config.toml`, `hooks.json` или plugin cache;
- не удаляют память и контейнеры;
- не выполняют GitHub login;
- не выводят API keys, passwords, response bodies или найденные secret values.

## Обработка ошибок

- Невалидный или закрытый task-key — exit non-zero без изменения файлов.
- Отсутствующая локальная task-конфигурация — doctor `FAIL` с точной командой
  `agent-task-start`.
- Qwen/Supermemory недоступны в offline doctor — не проверяются; в live doctor
  это отдельные `FAIL`.
- `gh` отсутствует или не авторизован — `WARN`; остальные проверки
  продолжаются.
- Необязательный MCP enabled, но live tool не доступен текущей сессии —
  `WARN`, а не предположение о причине.
- Supermemory search с HTTP error — выводится только endpoint, status code и
  container tag.
- Secret lint никогда не печатает обнаруженное значение.

## Тестирование

Unit-тесты используют `unittest`, temporary directories и mock HTTP server.
Сеть, Docker и реальные credentials не нужны.

Проверяются:

1. принятие и отклонение task-key;
2. атомарное сохранение и сохранение неизвестных полей config;
3. запрет повторного запуска закрытого task-key;
4. `close` и doctor failure для закрытого state;
5. отсутствие project/task scope mixing;
6. запрет save без container или custom ID;
7. redaction: секрет никогда не появляется в stdout/stderr;
8. secret-linter positives и разрешённые env references;
9. классификация `PASS/WARN/FAIL`;
10. mock search/save payload содержит ровно один `containerTag`.

Интеграционная проверка после unit-тестов:

1. `make agent-tools-test`;
2. `make agent-secret-check`;
3. `make agent-doctor`;
4. `make agent-doctor-live`;
5. `codex mcp list`;
6. `docker compose -f docker-compose.dev.yml config --quiet`;
7. `git diff --check`;
8. повторный doctor не изменяет tracked worktree.

Полный `make test-all` не требуется для чисто agent-tooling изменений, если
backend/frontend не затронуты. При любом изменении приложения запускается
соответствующий Docker test target.

## Этапы

1. Lifecycle и unit-тесты `agent_task.py`.
2. Secret linter и его тесты.
3. Offline doctor, затем opt-in live probes.
4. Memory wrapper: сначала mock contract, затем безопасный live contract test.
5. Makefile и документация.
6. Финальная проверка без изменения несвязанных файлов заявок.

## Критерии готовности

- закрытый task-key нельзя использовать повторно;
- doctor обнаруживает текущий завершённый tag до следующей задачи;
- upstream `supermemory-status` больше не используется как источник cadence;
- doctor различает конфигурацию и недоступность внешнего инструмента;
- ручной memory wrapper не может обратиться без одного явного container tag;
- secret check проходит на всех изменённых tracked agent-конфигурациях;
- все новые unit-тесты и live-проверки проходят;
- изменения backend/frontend заявок остаются нетронутыми.
