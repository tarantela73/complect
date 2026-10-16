# Приёмка работы агента

Контракт спецификации #37: родитель фиксирует требования до dispatch, принимает существующие receipts через read-only CLI и отдельно проверяет публикацию. При недостаточных доказательствах CLI возвращает ненулевой exit и причины; команды из receipts не выполняются.

## Требования до dispatch

Сохранить JSON requirements версии 1. В `checks` перечислить уникальные `id`, `producer`, ожидаемое `execution` и заранее известные пути результатов. Порядок `--receipt` соответствует порядку checks. Поддержаны `infrastructure`, `payments`, `harness`, `legacy`; неизвестные форматы блокируются. Путь `path` в check, если задан, должен совпасть с переданным receipt.

`source` задаёт известную до запуска identity исходников. Infrastructure и Payments используют `producer_sha256`, соответствующий действующему `Stand.source_identity()`. Harness использует точную `sources` map и сохранённый лог с проверяемым SHA256. Legacy требует `source_commit` и `source_tree` выбранного immutable commit и путь `attempt` в check. Существующие producer JSON не переписываются.

`execution` обязателен. Для полного Infrastructure gate он имеет `kind: docker`, `command: make agent-infra-test`; для Payments — `kind: docker`, `command: make test-payments-gate`; для Harness — `kind: docker`, `command: bash infra/scripts/check_agent_harness.sh`. Для Infrastructure leaf `mode` и `image` проверяются по результату, `kind: docker`. Legacy positive использует `kind: committed`, `command: legacy-stand-qualify`.

Infrastructure check содержит `artifacts.selection.path` до запуска. Leaf проверяет sibling manifest и cleanup. Полный gate дополнительно проверяет полный набор фаз, quality, coverage/input/after, downstream Payments, контекст и recovery. Отдельный source-proof sidecar связывает snapshots всего дерева до/после запуска с обязательными artifacts; проверка снимков включает Makefile, workflows и Dockerfiles. Изменение дерева или отсутствие обязательного artifact блокирует приёмку, сохраняя первичный exit. Для проверки source inventory и baseline diagnostics полный check задаёт `source_root` выбранного checkout. Ссылки `artifacts` могут иметь SHA256 для уже существующих входов; хеш будущего результата до запуска не требуется. Если хеш задан, он обязателен к совпадению.

Legacy positive задаёт `artifacts.manifest.path` на original133 manifest внутри выбранной попытки. Принимаются только manifest, привязанный к её identity, и полный исходный 133/399 контракт. Отрицательный сценарий использует `expected_product` с точным исходным status/primary_exit и `artifacts.scenario.path`; сценарий должен подтвердить исходный результат, recovery и repeat без повышения verdict. Для него command — `legacy-recovery-scenario`. Для вложенной early matrix задать `scenario_case`: `success`, `blocked` или `tamper`. Успешная ранняя очистка подтверждает исходный FAIL/137 и cleanup PASS без выдуманных recovery-записей; остальные кейсы требуют distinct recovery/repeat bindings текущей попытки.

```bash
python3 -B -m infra.scripts.delivery_evidence accept \
  --requirements /absolute/path/parent-requirements.json \
  --receipt /absolute/path/result.json
```

Обязательные skips/NOT_RUN, неполные manifests и cleanup блокируют. Product limits вроде ERP_ACCEPTANCE=NOT_RUN сохраняют свой смысл и не подменяют обязательные проверки. FAIL_BASELINE требует согласованных source/base hashes, исходных baseline tool diagnostics и их проверяемого соответствия текущим строкам; одной надписи MATCHED недостаточно.

## Статус попытки

```bash
python3 -B -m infra.scripts.run_legacy_qualification status \
  --directory /absolute/path/attempt
```

Новые source commits объявляют identity protocol отдельно от qualification protocol v1. В fresh attempt создаётся immutable attempt_id; отдельный sidecar связывает source, стадии, JSON и логи. Записи сериализованы между parent/supervisor; активный незапечатанный лог не принимается за завершённое доказательство. После остановки работы запечатываются bounded original133 outputs.

Вывод разделяет исходный verdict, phase и binding. Повреждение, отсутствие identity у нового source, несовпадающие ID, чужие manifests и изменённые логи блокируют новую приёмку. Старый source сохраняет чтение/recovery и обозначается LEGACY; он не подтверждает новый identity-контракт. История не мигрируется, ID по именам файлов или времени не назначаются.

## Проверка публикации

Запускать из выбранного implementation checkout. Указать `repository` в requirements: identity выбранного remote (для GitHub owner/repository). Для sha-only/working-tree proof перед dispatch сохранить полный Harness snapshot, его путь и SHA256 в `source.snapshot`; файлы, режимы и удаления должны соответствовать публикуемому commit. Допустимый pre-commit receipt не ослабляет commit-only original133.

```bash
python3 -B -m infra.scripts.publication_preflight \
  --repository /absolute/path/checkout --remote origin --head FULL_SHA \
  --target main --requirements /absolute/path/parent-requirements.json \
  --receipt /absolute/path/result.json
```

Для существующего PR вместо `--target` использовать `--pr NUMBER_OR_URL`. Preflight читает актуальную базу выбранного remote; неизвестная/устаревшая база, неверный repository/target, несовпадение receipt comparison_base и проверенных исходников блокируют приёмку. Локальный быстрый HEAD-режим gates сохраняется.

## Доставка

Инфраструктурная ветка зависит от PR31. Локальные skills/profile передаются отдельными патчами с before/after hashes; они не входят автоматически в infrastructure commit. После двух полных gates и независимого Standards/Spec review предъявить точный commit и patch.

Push/draft PR требует отдельной команды пользователя. Затем выполнить hosted Infrastructure/Payments CI и на фактическом CI checkout original133/399 и native faults. До этого hosted CI и actual-checkout acceptance остаются NOT_RUN. Merge требует отдельной команды. Локальные receipts не являются подписанной внешней аттестацией и не доказывают production RLS/ERP/Kong acceptance.
