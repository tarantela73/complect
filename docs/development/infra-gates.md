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
