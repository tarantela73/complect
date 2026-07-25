# Supermemory: изоляция памяти Complect

## Назначение

Supermemory — необязательная локальная память агентов. Интеграция хранит только
краткие знания, полезные между сессиями; `AGENTS.md`, ADR и код остаются
авторитетными источниками.

Для Complect используется self-hosted Memory API на
`http://127.0.0.1:6767`. Порт доступен только с loopback-интерфейса.
Supermemory работает в Docker, использует встроенные локальные embeddings
`Xenova/bge-base-en-v1.5` (768d), а извлечение фактов выполняет host-процесс
`llama.cpp` с локальной моделью `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf`.
Телеметрия отключена.

Pinned server `0.0.6` пока не поддерживает переменные
`SUPERMEMORY_EMBEDDING_*`, появившиеся в документации после его выпуска.
Поэтому `bge-m3` нельзя включить декларативно в этой версии. Для русских
запросов использовать стабильные ASCII-маркеры (`project`, `scope`, `task`,
`kind`) и hybrid/keyword search. Переход на multilingual embeddings требует
отдельного обновления после выпуска совместимого server binary и полной
переиндексации локальной памяти.

Self-hosted server не предоставляет managed Supermemory MCP. Codex и Claude
Code подключаются официальными плагинами с
`baseUrl=http://127.0.0.1:6767`.

`containerTag` является границей изоляции. Теги в тексте и metadata помогают
аудиту и поиску, но не заменяют отдельный контейнер.

## Контейнеры

| Scope | `containerTag` | Содержимое |
|---|---|---|
| Проект | `complect:project` | Проверенные архитектурные решения, устойчивые ограничения и переиспользуемые решения |
| Задача | `complect:task:<task-key>` | Гипотезы, прогресс, локальные решения и handoff только одной задачи |

`task-key` выбирается один раз в начале сессии и не меняется до завершения
задачи:

1. `issue-<number>` или `pr-<number>`, если задача связана с GitHub;
2. идентификатор из трекера, приведённый к lowercase ASCII;
3. `branch-<branch-slug>` для отдельной feature-ветки;
4. `<YYYYMMDD>-<short-slug>`, если внешнего идентификатора нет.

Допустимы только `a-z`, `0-9` и `-`. Пример:
`complect:task:issue-142`.

## Начало сессии

Если локальная Supermemory доступна и нужен межсессионный контекст:

1. Определить `task-key` и зафиксировать
   `TASK_CONTAINER=complect:task:<task-key>`.
2. Выполнить отдельный `recall` в `complect:project` с запросом по затронутым
   доменам и путям.
3. Выполнить отдельный `recall` в `TASK_CONTAINER` по цели и текущему состоянию
   задачи.
4. Не выполнять `recall` без `containerTag` и не использовать контейнер другой
   задачи.

Недоступность Supermemory не блокирует работу и не требует создания
произвольных fallback-файлов.

## Запись

- Промежуточное состояние, гипотезы, task-specific решения и handoff сохранять
  только в `TASK_CONTAINER`.
- В `complect:project` сохранять только проверенное знание, которое с высокой
  вероятностью пригодится в другой задаче.
- Продвижение знания из задачи в проект выполнять новой краткой записью в
  `complect:project`, без копирования журнала задачи.
- Для операций памяти всегда передавать ровно один `containerTag`.
- При прямом API-вызове task-запись не должна одновременно иметь project- и
  task-container tags: иначе она станет доступна из проектного контекста.
- Использовать устойчивый `customId`, когда клиент его поддерживает, чтобы
  повторная запись обновляла контекст, а не создавала дубликаты.
- Не сохранять секреты, токены, пароли, PII, полные логи, дампы БД и
  неподтверждённые предположения как факты.

## Формат записи

Не все плагины предоставляют отдельные поля metadata в операции `memory`,
поэтому минимальные метки дублируются в содержимом.

Project scope:

```text
[project:complect] [scope:project] [kind:decision]
Факт: ...
Основание: ADR/path/commit ...
Проверка: ...
```

Task scope:

```text
[project:complect] [scope:task] [task:issue-142] [kind:handoff]
Цель: ...
Состояние: ...
Следующий шаг: ...
Связанные пути: ...
```

Допустимые `kind`: `decision`, `finding`, `feedback`, `handoff`, `reference`.

## Завершение задачи

1. Сохранить в task-контейнер только короткий handoff, если работа будет
   продолжена в другой сессии.
2. Продвинуть в проектный контейнер лишь проверенные переиспользуемые выводы.
3. Не читать завершённый task-контейнер в новых несвязанных задачах.

## Локальный запуск

До запуска Supermemory должен работать `llama-server` на `0.0.0.0:8090` с
OpenAI-compatible alias `qwen3-coder`. Compose обращается к нему через
`host.docker.internal`; порт не публикуется из контейнера.

Первый запуск Supermemory скачивает серверный образ и встроенную
embedding-модель объёмом около 106 MB. Он может быть заметно дольше
последующих запусков.

```bash
make supermemory-up
make supermemory-status
make supermemory-logs
make supermemory-down
```

`supermemory-up` сначала проверяет `/health` у host `llama-server`.
`supermemory-down` останавливает Supermemory, но не останавливает
`llama-server` и сохраняет именованные volumes.
`supermemory-logs` редактирует строки, похожие на Supermemory API key, перед
выводом в терминал.
Команда `make destroy` удаляет все volumes dev-окружения, включая локальную
память, и не должна использоваться без актуального backup.

## Секреты и данные

- API key генерируется сервером при первом старте и хранится только в
  `/data/api-key` внутри volume `supermemory_data`.
- Клиентские переменные `SUPERMEMORY_CODEX_API_KEY` и
  `SUPERMEMORY_CC_API_KEY` находятся в `/home/sever/.local/bin/env` с mode
  `0600`.
- В репозиторий нельзя добавлять API key, содержимое volume, дампы памяти или
  plugin-конфигурацию с секретами.
- Корневая HTML-страница локального сервера показывает API key для первичной
  настройки. Автоматические health-check должны отбрасывать response body
  (`--output /dev/null`); страницу нельзя сохранять в логи или вставлять в
  отчёты.
- Для ротации локального ключа сначала остановить клиентов, сделать backup
  volume и следовать процедуре текущей версии Supermemory; ручная замена
  `/data/api-key` не считается безопасной ротацией.

Проверить наличие ключа без его вывода:

```bash
docker compose -f docker-compose.dev.yml exec -T supermemory \
  sh -c 'test -s /data/api-key'
```

## Настройка агентов

Codex:

- глобальный `projectContainerTag` намеренно не задан: иначе Complect-контекст
  попадал бы в read-набор Codex при работе в других репозиториях;
- `captureEveryNTurns=0` и `autoRecallEveryPrompt=false`;
- plugin `Stop` flush hook включён; canonical write tag берётся из
  машинно-локального `repoContainerTag` и должен быть равен текущему
  `complect:task:<task-key>`;
- `personalContainerTag=complect:project` задаётся в том же project-local
  config как дополнительный read-контейнер, а не как место автоматической
  записи task-диалога;
- task-контекст сохраняется и запрашивается явно через custom container
  `complect:task:<task-key>`.

Claude Code:

- `repoContainerTag` должен быть равен контейнеру текущей задачи;
- автоматические summary сохраняются только в этот task-контейнер;
- при начале новой задачи `repoContainerTag` обязательно меняется;
- переиспользуемые знания читаются отдельно из `complect:project`.

Машинно-локальный `repoContainerTag` хранится в игнорируемой Git
`.claude/.supermemory-claude/config.json`. Его значение должно совпадать с
текущей задачей, не должно коммититься и обязательно меняется перед первым
flush следующей задачи. Там же хранится
`personalContainerTag=complect:project`, чтобы Codex читал переиспользуемые
знания только при работе внутри Complect.

## Backup и restore

Перед backup остановить запись:

```bash
make supermemory-down
mkdir -p backups
docker run --rm \
  --volume complect_supermemory_data:/source:ro \
  --volume "$PWD/backups":/backup \
  busybox:1.37.0 \
  tar -C /source -czf /backup/supermemory-$(date +%Y%m%d-%H%M%S).tar.gz .
```

Каталог `backups/` должен оставаться локальным и не коммититься. Для restore
использовать новый пустой volume, распаковать archive, проверить владельца
`10001:10001`, затем запустить сервис. Нельзя распаковывать backup поверх
работающего экземпляра.

## Проверка изоляции

Project и task контейнеры проверяются двумя независимыми запросами
`POST /v4/search` с разными `containerTag`. Результат запроса к одному
контейнеру не должен содержать записи с `scope` другого контейнера.

Облачный endpoint `api.supermemory.ai` и hosted MCP не используются. Их
конфигурацию разрешено удалять только после успешной локальной проверки и
переноса нужных записей.
