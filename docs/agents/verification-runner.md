# Проверяемый запуск команды

GitHub #55. Общий runner для task-specific тестов, lint и types в процессе `/implement`. Он исполняет явно переданные argv, сохраняет факты запуска и отдельно проверяет их целостность. Квитанция не является защищённой аттестацией; родительская приёмка закрепляет её хеш и согласованный набор входов.

## Запуск

Из корня репозитория, с заранее согласованным полным набором исходников:

```bash
python3 -B -m infra.scripts.verification_runner run \
  --root /absolute/checkout --cwd /absolute/checkout \
  --source infra/scripts/verification_runner.py \
  --source infra/scripts/tests/test_verification_runner.py \
  --source infra/scripts/run_infra_quality.py \
  --source infra/mypy.ini \
  --output /absolute/evidence/check-001 \
  -- docker run --rm --pull=never --network=none --read-only \
     --tmpfs /tmp:rw,nosuid,nodev \
     -v /absolute/checkout:/workspace:ro -w /workspace \
     -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/workspace:/workspace/backend \
     --entrypoint python complect_backend:dev \
     -m unittest infra.scripts.tests.test_verification_runner -v
```

Команда — пример вызова, не универсальный список входов. Координатор включает исходники, тесты, конфигурацию, инструкции и зависимости, влияющие на конкретную проверку. `--source` повторяется для каждого относительного пути внутри `--root`; выборка не выводится из имеющегося receipt. Пустая выборка, повторы, выход за root, symlinks, каталоги и отсутствующие входы отклоняются до исполнения. Полный existing working-tree snapshot и commit-only требования продолжают действовать для допускающих/требующих их gates.

Действующие Docker, isolation и cleanup-команды сохраняются как argv. Runner сам не изолирует внешние сервисы, не меняет daemon/context и не управляет lifecycle их ресурсов. Он не запускает shell неявно; для переданного shell pipeline режим pipefail задаётся самой командой. Проверки должны быть неинтерактивными: stdin закрыт.

`--output` обозначает новый каталог попытки вне source root либо внутри его `.dev/test-artifacts/`, который исключён существующим working-tree snapshot. Другие внутренние каталоги отклоняются до исполнения. Родительские каталоги могут быть созданы; существующий каталог попытки не используется повторно. Каждый успешный старт записи получает отдельный `attempt_id`. Внутри сохраняются `command.log` и `receipt.json`. Запись один раз создаёт эти файлы; повторы и read-only проверка не меняют историю.

Для acceptance-запуска API `run_check` принимает `declared_checkout` и, при необходимости, `required_inputs`. При заданном `declared_checkout` он требует, чтобы `root` и `cwd` разрешались в тот же checkout; отсутствующий или symlink-вход отклоняется до создания output и старта процесса. `required_inputs` — только уже существующие requirements/receipts; будущий output producer туда не включается. Внешний output разрешён, если он не существует заранее. Без `declared_checkout` сохраняется совместимость общего runner.

## Факты и статусы

Receipt формата `complect-verification-run-v1` содержит attempt ID, реальные argv/cwd, source root, полный объявленный набор source_before/source_after с SHA-256 и mode, исходный exit процесса, expected_exit и путь/хеш полного очищенного журнала. Stdout и stderr объединены в порядке их поступления в общий pipe. Журнал и его хеш обрабатываются потоково; краткий stdout runner не заменяет полный файл.

Runner возвращает `receipt_sha256`, вычисленный из созданных им фактов до записи файла. Координатор закрепляет этот хеш вне пакета попытки, например в родительском индексе приёмки. Для последующей проверки нужен именно этот независимый хеш, а не пересчёт текущего непроверенного receipt. Так обнаруживается изменение argv, cwd, expected_exit и любых других байтов квитанции. Это якорь локальной целостности, не подпись внешнего исполнителя.

Статусы вычисляются заново, а не принимаются из флагов в файле:

| Поле | Значение |
| --- | --- |
| integrity | PASS только при полном контракте, доступном журнале с правильным хешем и одинаковых before/after/current исходниках из независимой выборки |
| behavior | PASS при исходном exit 0; FAIL при ненулевом exit; NOT_RUN при подтверждённом отказе старта; UNKNOWN при отсутствии достаточных фактов |
| expectation | PASS, если исходный exit точно совпал с целым expected_exit |

Для положительной приёмки нужны integrity=PASS, behavior=PASS и expectation=PASS. Успешная проверка квитанции сама по себе не подтверждает успешный тест.

Для RED передай `--expect-exit 1`, если именно этот exit ожидается. При фактическом exit 1 сохраняются integrity=PASS, expectation=PASS, behavior=FAIL; CLI также возвращает 1. Ошибка ожидания не превращается в RED. GREEN требует реального exit 0 и корректных доказательств.

`run` сохраняет ненулевой exit процесса в коде CLI; отрицательный Python returncode от сигнала сохраняется в receipt и представляется в CLI как 128 + номер сигнала. Если процесс дал 0, но evidence или ожидание не подтверждены, CLI возвращает 2. Ошибка подготовки/записи также возвращает 2. При удалении или изменении исходника исходный exit остаётся в receipt, но integrity не даёт PASS. Прерванная запись без полного receipt не принимается; этот runner не является recovery-controller.

## Проверка без исполнения

```bash
python3 -B -m infra.scripts.verification_runner verify \
  --root /absolute/checkout \
  --source infra/scripts/verification_runner.py \
  --source infra/scripts/tests/test_verification_runner.py \
  --source infra/scripts/run_infra_quality.py \
  --source infra/mypy.ini \
  --receipt /absolute/evidence/check-001/receipt.json \
  --sha256 "$CHECK_RECEIPT_SHA256"
```

Передавай тот же независимый набор входов и закреплённый хеш receipt; `CHECK_RECEIPT_SHA256` в примере берётся из сводки `run` и родительского индекса. Проверка никогда не исполняет команду из квитанции. Она аутентифицирует всю квитанцию и журнал через existing authenticated reader evidence bundles, проверяет обязательные поля и текущие исходники. Без независимого pin integrity не получает PASS. `verify` возвращает 0 только за integrity=PASS, иначе 2; behavior и исходный exit остаются отдельными полями stdout. Удаление обязательных метаданных, изменение журнала, другой source root/набор входов и source drift исключают PASS целостности.

## Секреты и совместимость

Каждый известный секрет, который команда может вывести, объявляется через `--redact-env ENV_NAME`; значение берётся из окружения runner и не записывается в receipt. Передавай секреты только через разрешённое окружение/secret files. Если зарегистрированное значение встречается в argv, cwd, source root, именах исходников или пути output, запуск отклоняется до исполнения, чтобы сохранить точные безопасные факты. Для внешнего Docker передача env/secret подчиняется действующим проектным правилам.

Зарегистрированные значения заменяются на `[REDACTED]` до записи журнала, включая совпадение на границе чтения pipe. Хеш относится к очищенному полному файлу. Механизм не обнаруживает неизвестные секреты или raw PII автоматически; действующие ограничения их вывода обязательны. Ни полный журнал, ни обязательные поля receipt не усекаются; штатный stdout содержит только attempt ID, три статуса, исходный exit, диагностические коды и путь.

Новый receipt выпускается рядом с существующими доказательствами. Он не меняет внешние форматы quality, Payments, legacy или fixed Harness recorder, не повышает исторические записи до нового формата и не восстанавливает отсутствующий mypy-журнал задним числом. Для обязательного Harness gate используется существующий фиксированный `agent_harness.py --record`; его receipt остаётся обязательным.

Локальная копия `/implement` адаптирована для этого процесса; исходные upstream hashes в skill provenance сохраняются как данные о происхождении.


## Использование в CI

[CI producer](ci-gate-producer.md) формирует привязанный пакет Infrastructure/Payments. Необязательный аргумент API `on_exit` получает наблюдённый returncode сразу после завершения процесса, до записи receipt, чтобы адаптер сохранил исходный nonzero exit при последующей ошибке evidence. Callback не создаёт доказательство успешной записи; формат receipt и CLI runner сохраняются.
