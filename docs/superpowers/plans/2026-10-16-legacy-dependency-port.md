# Перенос зависимостей исходного legacy133 на main

Статус: предложение для согласования. Пользователь 16 октября запросил точный
план до продолжения legacy. Перенос бизнес-кода и миграций не начат;
legacy-стенды не запускались.

Исходная задача: [infra reassessment](../specs/2026-10-16-retro-infra-reassessment.md).
База: `20bb9fc0846a13e550f3023d47ddbf286bcbc65f` (`origin/main` на момент проверки).
Источник: рабочее дерево `/home/sever/projects/Complect`, HEAD
`6ac57d3a5043ab76df0316cac17aac86e4f9bd90` вместе с явно перечисленными
незакоммиченными файлами. HEAD сам по себе не описывает этот источник.

## Точный состав и границы

[Инвентаризация](2026-10-16-legacy-dependency-inventory.json) содержит 57 путей:
40 отсутствуют в базе, 17 уже существуют. Для каждого записаны SHA-256
исходника, наличие в базе и хеш базовой версии. Это проверенный список
кандидатов переноса, а не разрешение заменить существующие файлы целиком.
Дополнительные зависимости, выявленные проверками, требуют обновления
инвентаризации и отдельного согласования при выходе из этих границ.

В исходном manifest отсутствуют в main 12 тестовых файлов. Часть тестов
проверяет бизнес-поведение, отсутствующее в main; добавления одних тестов
недостаточно. Manifest сохраняет исходные 133 node ID и 399 фаз.

| Этап | Новые файлы и изменения | Проверка перед следующим этапом |
|---|---|---|
| Common contracts и recovery | `common/contracts/legacy_document_contracts.py`; `common/models/legacy_task_recovery.py`; `common/repositories/legacy_task_recovery_repository.py`; `common/services/legacy_recovery_parking.py`; `common/services/file_pipeline/clamav_result.py`; регистрация модели в `common/apps.py`; `common.0028` | Регистрация Django model; parser cases; scoped tenant/CAS/hold и recovery tests; реальная миграция PostgreSQL/PostGIS |
| Contracts | `contracts/events/legacy_document_mapping.py`; `contracts/models/legacy_document_link.py`; `contracts/repositories/legacy_document_link_repository.py`; `contracts/services/legacy_document_mapping_service.py`; экспорт модели; `contracts.0010–0012` | Tenant/RLS, CAS, неизменяемый replay, конфликт replay, Legal Hold; событие и состояние фиксируются атомарно |
| Files и связывание провайдеров | `files/repositories/legacy_file_proof_adapter.py`; `files/services/legacy_document_scan_adapter.py`; `files/services/legacy_document_compatibility_service.py`; `files/tasks/legacy_document_compat.py`; `config/legacy_document_compatibility.py`; `FilesConfig.ready`; точечные registration functions общих file/finance protocols | Реальный ClamAV/S3 clean/infected/checksum flow; confirmed-copy/delete/retry semantics; отсутствие междоменных model imports |
| PII / ContrAgents | `common/contracts/pii_read_context.py`; `contragents/services/person_profile_read_service.py`; точечные изменения auth/context isolation, audit/vault, queries и DTO | Доверенный actor/tenant; committed append-only audit до PII query/decrypt; отказ при сбое аудита; очистка context; сохранение API v1 |
| Integration | Согласованная версия `integration/pkk_client.py` и её исходные тесты | Контракт клиента, ошибки и timeout; внешние обращения заменяются контролируемой тестовой границей |
| Локальный IAM provisioning | `users/repositories/identity_creation_repository.py`; `users/services/local_files_provisioning.py`; `common/services/local_rbac_service.py`; `common/repositories/local_rbac_repository.py`; точечная адаптация `create_test_users`, membership и tenant registry repositories | Operator authorization, явный scope, tenant registry/RLS, tenant-local actor mapping, атомарность, идемпотентность, foreign-tenant refusal |

Все пути в таблице относятся к `backend/apps/`, кроме явно указанного
`backend/config/legacy_document_compatibility.py`. Точные полные пути находятся
в JSON. Текущие рабочие версии могут содержать посторонние изменения:
переносятся только необходимые функции и регистрация, с отдельным RED/GREEN.

## Существующие места интеграции

Обязательные узкие изменения, кроме новых файлов:

- `backend/apps/contracts/models/__init__.py`: экспорт новой модели.
- `backend/apps/files/apps.py`: регистрация legacy providers.
- `backend/apps/common/contracts/file_service_protocol.py`: `register_file_service`.
- `backend/apps/common/contracts/finance_contracts.py`: `register_finance_reader`.
- `backend/apps/common/contracts/auth_context.py`: совместимый rollout helper
  `get_auth_rollout_mode`, без изменения принятого режима авторизации.
- `backend/apps/common/api/auth.py` и `middleware/context_isolation.py`:
  создание доверенного PII context и гарантированная очистка.
- `backend/apps/common/security/audit.py` и `pii_vault.py`: durable audit/read
  boundary и совместимость ciphertext; raw PII не попадает в диагностику.
- `backend/apps/contragents/queries/contragent_queries.py` и
  `schemas/person_dto.py`: query seam и совместимость DTO.
- `backend/apps/users/management/commands/create_test_users.py`,
  `repositories/membership_repository.py`, `repositories/iam_tenant_registry_repository.py`:
  owning-layer provisioning на существующей схеме и tenant-local actor mapping.
- `backend/conftest.py`, `backend/apps/users/tests/conftest.py`,
  `backend/apps/users/tests/iam_factories.py`,
  `backend/apps/common/tests/legacy_compat_fixtures.py`: тестовые фикстуры
  с внешним privileged lifecycle, без расширения runtime grants.

Существующие `common/models/__init__.py` и `files/tasks/__init__.py`
не требуют предполагаемого ранее экспорта: регистрация находится в AppConfig.
Изменять другие auth endpoints, API schema или IAM authorities план не разрешает.

## Миграции и политика

| Миграция | Уже существующий предок | Назначение |
|---|---|---|
| `common.0028_legacy_task_recovery` | `common.0027_seed_director_role` | Recovery state и его tenant/RLS contract |
| `contracts.0010_runtime_admission_policy` | `contracts.0009_contracts_cursor_index` | Runtime admission policy |
| `contracts.0011_legacy_document_links` | `contracts.0010_runtime_admission_policy` | Legacy mapping state и RLS |
| `contracts.0012_attachment_runtime_admission` | `contracts.0011_legacy_document_links` | Attachment runtime admission |

После ревью DDL в `backend/config/dev_migration_policy.json` добавляются только
необходимые записи этих четырёх миграций с проверенными hashes. Весь файл
рабочего дерева не заменяется. Если предок или историческая модель расходится
с базой, применение останавливается до согласования точного изменения.

Для указанных тестов provisioning/PII хватает существующих IAM tables в main.
`users.0004`, `users.0004a` и `users.0005` в перенос не входят. В частности,
retirement `users.0005` не является способом подготовить legacy133.
Новые IAM DDL, irreversible operations и ослабление runtime/RLS не разрешаются.

Миграции проверяются по ADR-0002 и ADR-0024: исторические модели, краткие
транзакции, применимость вперёд/назад и ограничения rollback. Сначала свежая
disposable PostgreSQL/PostGIS, затем upgrade с исходного main migration state.
До DDL сохраняются схема/данные тестового стенда и инструкция восстановления;
восстановление проверяется. Повторное применение и migration ledger проверяются
отдельно. Production rollout и persistent development DB не входят в план.

## Приёмка переноса

Тестовые файлы из исходного manifest:

- `common/tests/test_legacy_clamav_result.py`, `test_legacy_compatibility_worker.py`,
  `test_legacy_task_recovery.py`, `test_sync_redis_metrics.py`.
- `contracts/tests/test_legacy_document_mapping.py`.
- `files/tests/test_legacy_document_scan.py`.
- `contragents/tests/test_person_profile_audit_regression.py`,
  `test_person_profile_existing_contracts.py`, `test_person_profile_read_policy.py`.
- `integration/tests/test_pkk_client.py`.
- `users/tests/test_create_test_users_command.py`, `test_local_files_provisioning.py`.

Полные пути начинаются с `backend/apps/`. Список node ID не сокращается.
Для каждого этапа сначала import/collection и один публичный RED/GREEN slice,
затем focused Docker tests, lint/types и затронутые security/data checks.
RLS и concurrency доказываются ограниченным runtime на реальной БД;
privileged migrator используется только для подготовки и lifecycle.

После `common.0028` и каждой миграции Contracts отдельно проверяется
двухтенантная отрицательная матрица. Соединение использует ограниченный login
`complect_app_local`, наследующий `complect_app_runtime`, без SUPERUSER,
BYPASSRLS и членства в migrator. В scope tenant A строки tenant B невидимы;
чужие UPDATE/DELETE не затрагивают строки, INSERT с tenant B отклоняется
и не оставляет записи. Отсутствующий tenant context также не допускает утечки
или записи. Tenant scope и SQL GUC восстанавливаются после исключения.

Проверяются фактические catalog-предикаты и FORCE RLS затронутых таблиц:
`USING`/`WITH CHECK`, роль policy, permissive/restrictive composition.
Наличие runtime policy с `(true)` допустимо только вместе с действующей
restrictive tenant policy; название policy не является доказательством
изоляции. Проверка выполняется на реальной PostgreSQL/PostGIS и сохраняет
безопасный результат без PII или credentials.

До full133 выполняются все focused этапы, migration graph/policy checks,
DDD-boundary/OpenAPI проверки и полный Payments gate. Затем один итоговый
кандидат включает dependency-коммиты и infra-коммиты: exact133/399,
четыре изолированных refusal-стенда и обычный положительный infra CI проверяют
его общий SHA/source identity. Отрицательный CI-коммит отличается только
контрольным падающим тестом и в итоговый PR не включается.

Финальная приёмка требует независимых Standards и Spec, hashes полного
пакета, cleanup own resources, сохранности общей Docker-среды и точного
патча перед публикацией. Зелёный сбор тестов не заменяет runtime proof.

## Текущий infra-черновик и отдельный обнаруженный пробел

Черновик находится в managed worktree
`/home/sever/.codex/worktrees/infra-retro-candidate/Complect`, ветка
`codex/infra-retro-candidate`. Workflow, legacy template и refusal driver
не опубликованы. Реальные legacy-стенды не создавались.

Выполнены Docker static checks и 68 pytest. Полный unittest discovery пока
FAIL: существующий в main `MakeRootSelectionTests` требует отсутствующие
Make targets `doctor`/`test-e2e` и checkout routing. Это отдельный baseline
integration gap, не разрешение переносить весь локальный lifecycle/toolchain.
Перед публикацией infra-кандидата требуется либо согласованный перенос
реальных target entrypoints с зависимостями, либо отдельное согласованное
изменение контракта этого теста. Пропуск теста ради зелёного gate не предлагается.

## Решения для согласования

1. Разрешить отдельный dependency-перенос перечисленных доменов и четырёх
   миграций по inventory, с узкими изменениями существующих seams и no-DDL IAM.
2. Отдельно определить объём восстановления локальных Make entrypoints;
   его полный dependency inventory пока не установлен и не включён в 57 путей.

## Последовательность после согласования

1. Закрепить hashes источника и базы; сформировать отдельные dependency-коммиты
   от main, сохраняя infra-черновик и исходное dirty tree.
2. Перенести Common и Contracts с четырьмя reviewed migrations; проверить
   fresh/upgrade/rollback на собственных PostgreSQL/PostGIS ресурсах.
3. Перенести Files/ClamAV/S3 и PII/ContrAgents/Integration по публичным
   behavior-first slices; проверить security/API contracts.
4. Перенести локальный provisioning и RBAC на существующей IAM-схеме;
   проверить operator/tenant/hold/idempotency и необходимые concurrency cases.
5. Закрыть отдельно согласованный Make entrypoint gap, собрать окончательный
   dependency + infra кандидат и выполнить focused/static/Payments проверки.
6. Выполнить original133/399 и четыре real refusals, независимое review;
   предъявить патч, затем получить положительный/отрицательный CI и проверить cleanup.
