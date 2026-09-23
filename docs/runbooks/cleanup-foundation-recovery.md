# Cleanup foundation: interrupted development bootstrap

Статус: candidate / только изолированный disposable rehearsal. Это восстановление
DDL-миграции, а не Q2 recovery, executor recovery или разрешение production/adoption.
`CLEANUP_FINDING` остаётся `OPEN`; runtime cleanup и `ACTIVE` запрещены.

## Разрешённая граница

Host entrypoint `python3 infra/scripts/cleanup_foundation_recovery.py` вызывает
изолированное ядро `infra/scripts/cleanup_foundation_recovery_core.py`. Оба находятся отдельно от
`config.dev_database`. Штатные `apply`, startup и `assess_migrations()` не вызывают
её и сохраняют отказ при `in_progress`, неверном receipt и pending
`bootstrap_only`. Existing-environment upgrade этим runbook не разрешается.

Поддержан только исходно пустой disposable PostgreSQL/PostGIS, в котором
**до запуска штатного apply** создан certificate `prepare`. Нужны:

- отдельный task-owned Compose PostgreSQL с `network_mode: none`, PGDATA в tmpfs,
  без published ports и подключений к рабочим сервисам;
- read-only bind файла `/run/complect-foundation-task/owner.json` в PostgreSQL.
  Он создаётся при выделении нового стенда и содержит `scope` со значением
  `cleanup-foundation-v2-disposable`, точный Docker `task_owner` и случайный
  32-символьный hex `cluster_nonce`; это marker ресурса, не credential;
- отключённый replica path, `config.settings.local`, выключенный IAM executor;
- один и тот же PostgreSQL server/role и те же версии расширений для reference и
  target; точные hashes всех Django migrations, policy и recovery helper;
- отсутствие любых иных client sessions и новых клиентов на всё окно работ;
- сохранённый исходный пустой ledger/отсутствующий receipt, полный forward plan,
  identity сервера/БД/схемы/роли и host-verified ownership evidence.

Присутствие индекса с известным именем само по себе не доказывает ownership.
Ownership здесь выводится из certificate пустой схемы до apply, изоляции стенда,
точного approved плана и полной проверки фактических определений. Без этих
сведений процедура отказывает, включая старые сбои, для которых `prepare`
никогда не выполнялся. Не создавайте certificate задним числом.

## Операторская последовательность

1. На отдельной reference DB с теми же extensions выполните обычные
   `python -m config.dev_database apply` и `python -m config.dev_database ready`.
   Reference должна содержать полный approved ledger и пустые foundation tables.
2. Сохраните reference в новом приватном файле:

   ```bash
   python3 infra/scripts/cleanup_foundation_recovery.py \
     --compose "$COMPOSE_FILE" --owner "$TASK_OWNER" --database "$REFERENCE_DB" \
     --evidence "$EVIDENCE_DIR" --state-dir /evidence/state-reference \
     reference --output /evidence/reference.json
   ```

3. Для **пустой target DB** получите live Docker inspection: container ID,
   task-owner label, `NetworkMode=none`, PGDATA tmpfs, отсутствие published ports
   и persistent PGDATA mounts. Сверьте `pg_control_system().system_identifier`, имя БД
   и текущую роль. JSON evidence содержит `container_id`, `network_mode`,
   `owned_by_harness`, `postgres_tmpfs`, `no_published_ports`, `system_identifier`,
   `database_name`, `current_role`, `task_token`, `task_owner`, `cluster_nonce`,
   `marker_sha256` и `server_hostname`. Host entrypoint создаёт этот файл сам.
   Boolean-поля — результат этих
   проверок, не разрешение пропустить их. Автоматизированный rehearsal ниже
   выполняет реальные проверки перед созданием evidence.
4. С `DEV_STATE_DIR`, указывающим на новый target-specific каталог, выполните:

   ```bash
   python3 infra/scripts/cleanup_foundation_recovery.py \
     --compose "$COMPOSE_FILE" --owner "$TASK_OWNER" --database "$TARGET_DB" \
     --evidence "$EVIDENCE_DIR" --state-dir /evidence/state-target \
     prepare --reference /evidence/reference.json \
     --task-token "$TASK_TOKEN" --output /evidence/target-plan.json
   docker compose -f "$COMPOSE_FILE" run --rm --no-deps \
     -e "POSTGRES_DB=$TARGET_DB" -e DEV_STATE_DIR=/evidence/state-target \
     migrate -m config.dev_database apply
   ```

   `prepare` не выполняет migration DDL. Он записывает уникальный ownership marker
   в COMMENT target DB и создаёт certificate; существующие файлы не перезаписываются.
   Reference, certificate и host evidence должны оставаться приватными, неизменными
   и недоступными для записи другим пользователям.
5. После сбоя сохраните stdout/exit code, receipt, фактический `django_migrations`,
   catalog и оставшийся plan. Обычный повтор `apply` должен отказать. Не удаляйте
   `schema.json`, не используйте `--fake` и не редактируйте ledger.
6. После повторной проверки изоляции и quiescence явно разрешите rebuild запуском:

   ```bash
   python3 infra/scripts/cleanup_foundation_recovery.py \
     --compose "$COMPOSE_FILE" --owner "$TASK_OWNER" --database "$TARGET_DB" \
     --evidence "$EVIDENCE_DIR" --state-dir /evidence/state-target \
     recover --reference /evidence/reference.json --plan /evidence/target-plan.json \
     --task-token "$TASK_TOKEN"
   docker compose -f "$COMPOSE_FILE" run --rm --no-deps \
     -e "POSTGRES_DB=$TARGET_DB" -e DEV_STATE_DIR=/evidence/state-target \
     migrate -m config.dev_database ready
   ```

   Этот запуск — явное операторское продолжение **того же** доказанного fresh
   bootstrap. Он не преобразует непустой ledger в новый bootstrap и не снимает
   `bootstrap_only`. Применяется исключительно singleton остаток
   `users.0003b1_cleanup_admission_indexes` через Django MigrationExecutor.

## Проверки до первого DROP

Перед **каждой** операцией host verifier заново проверяет local Docker endpoint,
живой container ID/owner label, network-none, tmpfs, отсутствие published ports,
read-only marker и разрешённые mounts. Socket Docker не передаётся контейнерам.

Ядро независимо читает через PostgreSQL `pg_read_file` только фиксированные
`/proc/self/mountinfo`, `/proc/self/net/dev`, `/etc/hostname` и task marker.
Оно проверяет mount именно фактического `data_directory`, только интерфейс `lo`,
read-only mount marker, его nonce/digest и совпадение hostname с Docker ID.
Пользовательский JSON не заменяет эти live-проверки. Отсутствие права чтения или
неоднозначный формат означает отказ; дополнительных grants и SECURITY DEFINER
функций инструмент не создаёт. Этот административный disposable-role path не
является доказательством production RLS/ACL.

Дополнительно обязательны штатные `DEV_SCHEMA_MANAGEMENT=1` и
`HOST=postgres_primary`. Все artifacts/receipts ограничены task mount `/evidence`;
путь наружу, включая symlink escape, отвергается до действий.

Helper получает тот же advisory mutex, что штатный migration stage. Другие client
sessions вызывают отказ, включая idle sessions. DDL выполняется в autocommit с
`lock_timeout=2s`, `statement_timeout=30s`; исходные session settings восстанавливаются.

Ledger должен равняться точному завершённому префиксу исходного плана; receipt
остаётся исходным `{"state":"in_progress","applied":{}}`. Это receipt начала
пустого bootstrap, поэтому его applied snapshot намеренно предшествует текущему
ledger. Такая разница допускается только здесь, на основании certificate.

Semantic catalog application-объектов сверяется с reference: отношения/колонки/constraints,
индексы, guards/functions/triggers, RLS policies/flags, owner/ACL, extensions и
dependencies. Extension-owned внутренности исключены из сравнения; для расширений
сверяются имя, версия, схема и owner. Исключение допускается только для точного набора объектов последней
index migration. Для существующего индекса проверяются schema, owning table,
definition, access method, owner, validity/readiness/liveness, constraint attachment,
replica identity, options/tablespace и dependencies. В foundation tables не должно
быть protocol rows. Неизвестное отличие означает отказ до DDL.

## Состояния и действия

| Состояние | Действие |
| --- | --- |
| Ожидаемый индекс отсутствует | Ничего удалять не требуется; после всех проверок forward migration создаст его. |
| Точный INVALID индекс | Явный bounded `DROP INDEX CONCURRENTLY`, затем forward rebuild. |
| Точный valid-but-unrecorded индекс | Тот же явный rebuild в доказанно пустом quiescent foundation; имя без definition/provenance недостаточно. |
| Часть набора уже создана | Сначала проверяется весь набор и весь оставшийся catalog, затем rebuild точного набора. |
| Уже attached ожидаемый UNIQUE constraint | Проверяется exact constraint; он удаляется явно без `CASCADE`, удаляя owned index. Остальные индексы удаляются CONCURRENTLY. |
| Одноимённый чужой/неожиданный объект | Отказ, объект сохраняется. |
| Повторный сбой или timeout | Receipt остаётся `in_progress`; сохраняются реальные частичные эффекты. Новый recovery заново проверяет их. |
| Все миграции записаны, но запись ready receipt прервалась | Только после полной проверки catalog/ledger допускается восстановление receipt. |
| Уже согласованные catalog/ledger/ready receipt | Read-only no-op. |

Выбран rebuild, поскольку новые foundation tables ещё не используются и обязаны
быть пустыми. Это сохраняет обычный forward migration и Django state без сложного
reuse и ручных ledger rows. Для существующего production state это обоснование
неприменимо: такой target запрещён.

До DROP helper сохраняет отдельный `cleanup-recovery-before-*.json` с catalog,
ledger, receipt, hashes, identity и pending plan. После forward migration он
повторно проверяет полный catalog и ledger и только затем атомарно записывает
`ready` через штатный receipt writer. Никакой безусловной замены состояния нет.

## Воспроизводимый regression rehearsal

В candidate входят host orchestrator
`infra/scripts/tests/rehearse_cleanup_foundation_recovery.py` и subprocess driver
`backend/apps/users/tests/cleanup_foundation_rehearsal_driver.py`.

```bash
python3 infra/scripts/tests/rehearse_cleanup_foundation_recovery.py \
  --compose /absolute/task-owned/compose.yml \
  --evidence /absolute/task-owned/evidence --owner task-owner-label
```

Compose должен предоставлять `postgres_primary`, `migrate`, роль `foundation`,
read-only backend mount `/app` и evidence mount `/evidence`; локальный runner
использует штатный `config.settings.local`. Каталог evidence должен принадлежать
UID runner. PostgreSQL изолирован `network_mode: none`; migrate разделяет только
его namespace. Orchestrator отказывается от другого Docker endpoint, persistent
PGDATA, published ports или неверного owner label. Read-only task-marker bind
обязателен; другие PostgreSQL bind mounts запрещены.

Каждый сценарий создаёт отдельную БД, без reset существующих БД. INVALID получают
отменой настоящего concurrent build, ожидающего ROW EXCLUSIVE transaction; нет
изменений system catalog, подмены hash-pinned migrations или synthetic INVALID
fixtures. После проверки удаляются только подтверждённые task-owned контейнеры,
а evidence и candidate сохраняются.
