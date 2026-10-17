# Приёмка изменений общей инфраструктуры

Для изменений рабочего дерева запустите из корня `make agent-infra-test`.
По умолчанию база — `HEAD`; учитываются staged, unstaged и новые файлы.
Для уже закоммиченных изменений укажите точную базу:

```bash
make agent-infra-test INFRA_TEST_ARGS="--base <SHA-базы> --output /tmp/complect-infra-acceptance"
```

Команда выполняет существующие quality/unittest/pytest проверки, отдельный
быстрый прогон того же pytest-набора через Payments runtime adapter,
проверку recovery новым процессом и отдельный отчёт покрытия lint/type.
При изменении общего набора или адаптеров управляющий процесс также
автоматически вызывает существующий `make test-payments-gate`.
Список условий хранится в исполняемом selection-модуле.

Быстрый Payments context использует настоящий adapter с его cwd и mounts,
с отключённой сетью, пустым runtime credential mount и состоянием только для чтения.
Он сохраняет `scope=shared-infra-context`, `payments_acceptance=NOT_RUN`.
Успех этой фазы не подтверждает полный Payments gate.

Ключевые артефакты находятся под выбранным `--output`:

- `result.json`: результат всех обязательных фаз;
- `coverage/coverage.json`: версионированное покрытие каждого изменённого infra Python-файла;
- `payments-context/`: общий набор через Payments adapter, manifests и cleanup;
- `delivery/selection.json`: база, выбранные gates и результат полного Payments;
- `recovery/`: ошибка, blocked cleanup, recovery и повторный recovery.

Отчёт покрытия связывает полный текущий inventory с базовым commit и конкретными
исходниками базы. Проверки выполняются в offline quality-контейнере; входной
пакет монтируется только для чтения. Host повторно сверяет пакет после исполнения.
`FAIL_BASELINE` допустим лишь при совпадении конкретных diagnostics реальных
проверок базовых и текущих исходников. Новая диагностика блокирует успех.
Удаление проверенного базового файла отражается как `REMOVED`.

Отдельный `pytest` или `unittest` режим runner остаётся диагностическим вызовом
одного существующего набора. Для общей приёмки используйте `all` через Make.
CI сохраняет прежний фактический checkout; отдельный fetch базы нужен только
для сравнения, он не переключает checkout. Обязательные main workflows не
ограничиваются path-фильтрами.

Запись snapshot поддерживает совместимый короткий stdout:

```bash
python3 -B infra/scripts/agent_harness.py --record-snapshot .dev/harness/review.snapshot.json --snapshot-summary
```

Результат содержит `status`, `path`, `sha256` полного manifest. Файл сохраняет
прежнюю схему, а без `--snapshot-summary` stdout сохраняет полный manifest.
Снимок принимается явно прежним процессом; короткий вывод не принимает новые
hashes автоматически. Проверка свежести выполняется прежним `--verify-snapshot`.
