# Локальный Payments gate

Из корня репозитория запустите:

```bash
make test-payments-gate
```

Команда использует уже доступные образы `complect_backend:dev`, `postgis/postgis:16-3.4` и `redis:7.2-alpine`. Другой backend-образ и каталог доказательств можно задать явно:

```bash
make test-payments-gate PAYMENTS_GATE_ARGS='--backend-image complect_backend:dev --output /tmp/payments-gate-evidence'
```

Каталог результата должен отсутствовать перед запуском. Docker endpoint должен совпадать с выбранным локальным Unix context. Стенд создаёт закрытую Docker network, PostgreSQL/PostGIS, Redis и физическую streaming replica без host-портов. Все ресурсы записываются с immutable ID и меткой запуска; удаление требует совпадения ID, labels, image и mounts. Database credentials временные; HTTP-процессы получают только ограниченную runtime-роль. Подготовка схемы использует штатный guarded migration runner.

`result.json` содержит независимые результаты всех фаз: подготовка, миграции, runtime/target identity, HTTP-контроль, вся текущая группа Payments, HTTP-конфликты, физическая replica, Docker pytest/unittest для lifecycle, JSON validation и cleanup. Для каждого pytest node сохраняются collected manifest и setup/call/teardown. Нулевой сбор, skips и ошибки любой фазы исключают общий `PASS`.

`PASS` требует успешных проверок и очистки. Нарушение поведения получает `FAIL`, ошибка стенда или неполные доказательства — `BLOCKED_STAND`; оба возвращают ненулевой exit. Проверки после отказа получают `NOT_RUN` с причиной. Первичная ошибка сохраняется отдельно от ошибок cleanup. Валидатор можно вызвать отдельно:

```bash
python3 -B infra/scripts/payments_gate_report.py /tmp/payments-gate-evidence/result.json
```

HTTP проходит через настоящий Ninja router и Django Client. Auth/RBAC и FinanceFinder используют явно обозначенные fixtures; queue wake подменяется, записи Transactional Outbox проверяются в PostgreSQL. Два конфликтных writer используют отдельные committed runtime-соединения. Два независимых replica-цикла фиксируют синхронизацию, остановленный replay, разные версии строки и WAL LSN, attach/detach с primary-read, затем resume, синхронизацию и положительные HTTP-контроли. HTTP-команды запускаются без внешней транзакции и без включённого `force_primary`.

Для проверки отказов используйте одноразовый stand-control режим:

```bash
python3 -B infra/scripts/run_payments_gate.py --scope stand --fault prepare --output /tmp/payments-fault-prepare
```

Поддержаны `prepare`, `foreign_target`, `socket`, `cleanup`. Последний намеренно сообщает отказ cleanup после попытки удалить принадлежащие запуску ресурсы. Регрессионные тесты дополнительно проверяют настоящий отказ Docker removal и запрет удаления чужой identity. Для параллельных запусков укажите разные свободные `/24` через `--subnet` в диапазоне `198.19.0.0/16`.

Этот локальный gate не подтверждает production RLS, ERP acceptance или Kong/JWT/RBAC E2E: их значения в отчёте — `NOT_RUN`. GitHub CI и required merge check относятся к задаче #21.

## Контракт доказательств

`infra/scripts/payments_gate_evidence.py` владеет нормализацией evidence, правилами полноты и вычислением итогового статуса. `PaymentsReport.record()` принимает факты и обновления незавершённой фазы; `snapshot()` возвращает независимую копию JSON; `finish()` закрывает отчёт после cleanup. После закрытия запись запрещена. Общие поля результата и индексы manifest/evidence строит модуль, а не caller.

Runner выполняет Docker/SQL/HTTP, сохраняет файлы и передаёт наблюдения. `payments_gate_report.py` остаётся совместимым CLI-адаптером. Формат JSON, сообщения существующего валидатора и коды выхода сохранены; успешный stand остаётся `PARTIAL`, полный gate требует `PASS` всех фаз. Новые контрактные тесты используют независимую JSON-фикстуру и входят в unittest-фазу полного gate.
