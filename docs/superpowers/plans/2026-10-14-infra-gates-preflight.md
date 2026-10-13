# Локальный infra gate и ранняя квалификация legacy-стенда

План согласован пользователем 14 октября 2026 года в текущей сессии
`grill-with-docs`, решения Q1–Q6.

## Объём первого этапа

- `make agent-infra-test`: текущие unittest и выбранные pytest-тесты через
  изолированный Docker; новый lifecycle interface входит в постоянный набор.
- Один источник выбора тестов, переиспользуемый Payments infra phases.
- Legacy runner проверяет подготовленный disposable checkout до Redis flush,
  создания lifecycle controller и запуска pytest.
- Подключение той же команды к GitHub CI — отдельный следующий этап.

## Контракт preflight

Явный manifest полного working tree проверяется на host и сопоставляется с
подготовленным checkout. Snapshot, hashes и исходные receipts автоматически
не обновляются. Источник manifest — существующий `agent_harness.py`.

Проверки охватывают Docker/resource identity, исходный 133-node manifest,
согласованные UUID-имена Redis/БД, AOF/WAITAOF, фактическую DB identity,
ограниченные runtime-права и готовность ClamAV.

Preflight подтверждает готовность, не исправляет стенд. Подготовка — отдельное
действие. Marker/DDL и любые очищающие операции остаются после успешной
квалификации в существующем orchestration.

Результат сохраняется в `preflight.json`: `PASS` либо `BLOCKED_STAND` с безопасной
причиной. Блокировка возвращает ненулевой exit и не допускает Redis `FLUSHALL`,
создание controller или pytest. Credentials, tokens и raw PII не входят в
диагностику. Режим только preflight не подтверждает выполнение тестов.

## Сохраняемые контракты

Фиксированный исходный манифест 133 тестов и digest не меняются. SQL-защиты,
socket protocol, controller и разделение runtime/migrator сохраняются.
Payments JSON/status/exit/schema остаются совместимыми; состав test collection
расширяется новым покрытием, counts определяются фактическим сбором.

## Приёмка

1. RED/GREEN: collection/shared selection и отрицательные preflight cases.
2. Единая Docker-команда собирает новые tests и показывает явные conditional
   integration skips существующего локального unittest набора.
3. Отказы snapshot/identity/permissions/ClamAV доказывают отсутствие mutations
   и pytest, сохраняют redacted JSON и ненулевой exit.
4. Подготовленный полный working-tree stand: preflight PASS, исходные 133 узла,
   все 399 setup/call/teardown phases PASS без skips/errors.
5. Полный Payments gate, focused lint/types и независимое review.
6. Cleanup только записанных owned resources; preservation текущего HEAD/index
   и исходных Docker resources. Production/ERP/Kong/CI acceptance — NOT_RUN.
