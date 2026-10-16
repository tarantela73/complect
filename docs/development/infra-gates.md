# Локальные инфраструктурные проверки

Из корня репозитория подготовьте locked backend и тестовый образ:

```bash
make agent-infra-image
make agent-infra-test INFRA_TEST_ARGS="--output /absolute/path/to/infra-results"
```

Сборка — отдельное действие с загрузкой зависимостей. Сам gate работает без
сети и проверяет наличие Ruff, mypy и actionlint. Версии Ruff и mypy заданы
существующим backend lock; actionlint закреплён версией и SHA-256 в
`infra/Dockerfile.tests`. Scope проверок явно задан в `run_infra_quality.py`,
конфигурация типов находится в `infra/mypy.ini`.
Любая ошибка quality, unittest, pytest или cleanup блокирует общий результат.
CI вызывает ту же команду и всегда сохраняет evidence. Локальный PASS команды
не доказывает успешное выполнение GitHub Actions.

## Отдельная подготовка полного стенда

Стенд создаётся только в новом каталоге внутри `.dev/test-artifacts`.
Рабочие исходники берутся из явно записанного snapshot, включая незакоммиченные
файлы. Секреты, данные Docker и конфигурация существующего стенда не копируются.

```bash
task_dir="$PWD/.dev/test-artifacts/my-infra-validation"
mkdir -p "$task_dir"
python3 -B infra/scripts/agent_harness.py --record-snapshot "$task_dir/source.snapshot.json"
make legacy-stand-prepare LEGACY_STAND_ARGS="--snapshot $task_dir/source.snapshot.json --directory $task_dir/stand"
```

Подготовка создаёт отдельные checkout, Compose project, сеть, volumes, секреты,
тестовую базу и Redis с совпадающим UUID. Она использует штатную подготовку
PostgreSQL/PostGIS, guarded migrations и MinIO buckets. Runtime остаётся без
TEMP и без членства в migrator. Обычные Celery consumers не запускаются.
Незавершённая подготовка останавливается, сохраняет причину отказа, пытается
очистить подтверждённые собственные ресурсы и сообщает об остатках. Новый
запуск требует нового каталога; восстановление частичного стенда не выполняется.

При успешной подготовке стенд остаётся доступен для явной проверки. Его
`ownership.json` содержит идентичность ресурсов и хеш точного Compose override.
Override находится рядом с checkout и не меняет файлы source snapshot.

## Проверка и исходные 133 теста

`infra/legacy133.json` хранит исходный список тестов в исходном порядке;
его фиксированный digest проверяет существующий preflight. Укажите явный
патч проверяемых изменений; файл патча должен существовать до запуска.
Патч не должен содержать секреты или постороннюю работу.

```bash
run_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_id"])' "$task_dir/stand/ownership.json")
python3 -B -m infra.scripts.run_legacy_regressions \
  --source-root "$PWD" --root "$task_dir/stand/checkout" \
  --snapshot "$task_dir/source.snapshot.json" \
  --ownership "$task_dir/stand/ownership.json" \
  --manifest "$PWD/infra/legacy133.json" --run-id "$run_id" \
  --review-patch "$task_dir/review.patch" --output "$task_dir/stand/run" --preflight-only
```

Для запуска всех 133 тестов выполните ту же команду без `--preflight-only`.
Preflight проверяет стенд без исправлений. До его успешного завершения
контроллер и тесты не запускаются. После прогона snapshot и source copy
проверяются повторно перед cleanup. Изменение исходников блокирует приёмку,
а безопасная очистка продолжается. Повторный прогон требует явной подготовки
согласованной версии. Ошибки cleanup также блокируют приёмку.

После проверки явно удалите стенд:

```bash
make legacy-stand-cleanup LEGACY_STAND_ARGS="--ownership $task_dir/stand/ownership.json"
```

Очистка повторно проверяет записанные ID, labels и fingerprint. Чужой или
изменившийся ресурс не удаляется; отказ сохраняется в receipt. Эти проверки
не дают production RLS, ERP/release или Kong/JWT/RBAC acceptance.

## Точные артефакты для ревью

Ctrl+C и обрабатываемый SIGTERM сохраняют причину остановки и запускают
инвентаризацию и очистку подтверждённых ресурсов. Неизвестная полнота inventory
блокирует успешный cleanup даже при пустом списке. После обрыва выполните явное
восстановление, затем очистку:

```bash
python3 -B -m infra.scripts.prepare_legacy_stand recover-inventory --ownership "$task_dir/stand/ownership.json"
make legacy-stand-cleanup LEGACY_STAND_ARGS="--ownership $task_dir/stand/ownership.json"
```

Восстановление проверяет идентичность, метки, fingerprint и конфигурацию стенда,
сохраняет подтверждённые ID и не возобновляет подготовку или тесты.

Приёмка требует `result_contract=complect-legacy-results-v1` и семь результатов:
`manifest.json`, `post-run-sources.json`, `privilege-probes.json`, `lifecycle.jsonl`,
`target.json`, `run-cleanup.json`, `result.json`. Дополнительная диагностика также
проверяется по хешам. Receipt `run-cleanup.json` относится к временным ресурсам
прогона; полный стенд очищается отдельной командой.

Успешный manifest содержит исходные 133 ID и ровно 399 успешных фаз
`setup/call/teardown`, без пропусков, skipped и дубликатов. Runner сохраняет
исходный manifest и ненулевой код pytest. При нарушении контракта код 0 становится
ненулевым, terminal result получает `BLOCKED_STAND` и диагностику ID/фазы.
Terminal result записывается и хешируется перед публикацией envelope.

Старые envelope доступны только для явной проверки целостности:
`python3 -B -m infra.scripts.evidence_bundle verify --envelope PATH --root ROOT --integrity-only`.
Этот режим не назначает профиль приёмки автоматически.

Начинайте ревью с envelope данного прогона. Проверяйте явно указанный файл
через `python3 -B -m infra.scripts.evidence_bundle verify --envelope PATH --root ROOT`.
Envelope связывает snapshot, патч, image ID, run ID и точные результаты с
SHA-256. Отсутствие или несовпадение обязательных данных блокирует приёмку;
поиск последнего успешного результата не выполняется.

Сжатый или opaque вывод инструмента не доказывает содержимое исходника.
Сохраняйте оригинал локально и читайте небольшими фрагментами:

```bash
python3 -B -m infra.scripts.evidence_bundle read \
  --path /absolute/path/to/artifact --sha256 EXPECTED_SHA256 --offset 0 --count 256
```

Хеш подтверждает версию файла, но не заменяет чтение. Если оригинал недоступен
или фрагмент не удалось прочитать точно, вывод о корректности заблокирован.
Изменения настроек MCP и плагинов требуют отдельной авторизации.


## Полная квалификация зафиксированного commit

Для нового положительного original133 используйте одну команду:

```bash
make legacy-stand-qualify SOURCE_COMMIT=<полный-SHA> DIRECTORY=/absolute/new/attempt
```

Выбранный commit должен содержать protocol v1 и совместимые orchestration/helpers.
Bootstrap создаёт независимую Git-копию, затем передаёт ей исполнение. Все стадии
работают из выбранной версии; неподдерживаемый commit отклоняется до Docker.
Каталог попытки должен быть новым. Внутри сохраняются frozen source, snapshot,
журнал процессов, старые runner receipts и отдельный qualification.json v1.

PASS требует ровно 133 исходных узла и 399 успешных фаз, полной очистки своих
Docker-ресурсов и credentials, сохранности идентичностей baseline. Новые чужие
ресурсы допустимы. При ошибке/SIGINT/SIGTERM сначала останавливается собственная
дочерняя работа, затем выполняется guarded cleanup. Первичная причина и exit
сохраняются. Например, тесты exit1 плюс blocked cleanup возвращают 1 со статусом
BLOCKED_STAND; тесты PASS плюс blocked cleanup возвращают 2.

После SIGKILL или перезапуска машины выполните cleanup-only recovery:

```bash
make legacy-stand-recover DIRECTORY=/absolute/existing/attempt
```

Recovery проверяет сохранённую идентичность и остановку прежней работы,
восстанавливает inventory и очищает только подтверждённые ресурсы. Неясный
журнал/ownership блокирует очистку. Восстановление не возобновляет тесты,
не меняет исходный verdict и пишет отдельный recovery receipt. Повторный
recovery безопасен. Для новых тестов создайте новую попытку.

Frozen source и доказательства сохраняются до отдельного явного удаления.
Runtime credentials удаляются отдельным guarded шагом даже при blocked Docker
cleanup. Не подменяйте каталоги попытки и не редактируйте frozen source: это
делает доказательства недостоверными и блокирует trusted recovery.

Быстрые регрессии входят в инфраструктурный CI gate; полный Docker original133
и fault/signal квалификация выполняются локально. Эти результаты не заменяют
production RLS, ERP/release или Kong E2E acceptance.
