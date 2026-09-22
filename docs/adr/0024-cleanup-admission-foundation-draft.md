# ADR-0024 draft: DB foundation для durable cleanup admission

**Статус: DRAFT / NOT ACCEPTED.** Этот файл не изменяет действующий ADR-0024,
не утверждает production-допустимость и не разрешает rollout или enablement.
Он фиксирует только кандидатский DB-foundation slice для направления
`DURABLE_ADMISSION_AND_HOLD_DRAIN` из design-пакета от 2026-09-21.

Исходный `CLEANUP_FINDING` остаётся открытым. Production acceptance остаётся
`PENDING`: у этого draft нет срока, Q2-решения или права объявить систему
готовой. Точный baseline design-пакета — `ff59bf82e83ffd54cd426aa44dff3a828017565b`.

## Контекст и граница

Design-пакет описывает протокол, в котором PostgreSQL хранит durable admission,
immutable scope и lifecycle evidence, а Redis не является fencing-хранилищем.
В этом draft принят к локальной реализации только Slice 1 из
`IMPLEMENTATION_PLAN.md`, «additive durable admission и ownership»:

* новые global IAM security metadata для admission, request subjects и
  append-only lifecycle;
* DB constraints и focused versioned repositories для единственного live owner,
  immutable scope и exact expected-version CAS;
* forward migration для этих новых сущностей и локальные behavioral tests;
* доказательство отсутствия runtime callers через caller inventory.

Новые сущности должны быть disabled и не подключаться к существующим
cleanup/hold entrypoints. Прототип design-пакета не является доказательством того, что production
реализация уже соответствует этому документу.

В этот slice не входят executor/worker, Redis I/O, hold writer, authority или
topology integrations, Celery scheduling, admin/API, automatic recovery,
all-source enforcement guards, tenant-RLS changes, role/ACL changes,
report-path repair или изменение существующих migrations. Не создаётся новый
публичный endpoint и не меняется frozen OpenAPI.

`ACTIVE` в этом slice недоступен: не существует перехода, который возвращает
успех установки действующего hold без реальных hold flags и полного enforcement.
Q2 recovery и certificate ingestion также недоступны. Нельзя добавлять
административный force-terminal shortcut.

## Нормативные решения кандидата

Этот раздел описывает local candidate, а не принятые production norms.

### S1: REQUESTED и самостоятельный drain

`REQUESTED` закрывает новые пересекающиеся admissions, но не блокирует узкий
lifecycle-путь уже допущенной immutable attempt. Такой путь может записать
evidence и завершить собственный drain; он не получает разрешение на authority
mutation и не устанавливает ACTIVE. Lifecycle acknowledgement должен идти через
отдельный разрешённый путь и не должен ждать тот же REQUESTED blocker.

Запрос в состоянии `REQUESTED` не означает `ACTIVE` и не возвращается caller как
успех установки hold. При этом этот foundation slice только хранит состояние и
evidence; он не реализует executor, физическое прекращение Redis-команд или activation.

### S2: запрет пересекающихся изменений

С commit `REQUESTED` запрещаются новые overlapping admissions и изменения
защищённой authority/topology области, которые могли бы скрыть или расширить
immutable scope. Узкое завершение ранее admitted attempt, lifecycle evidence и
будущая проверенная activation являются отдельными действиями. Existing
cleanup authority record нельзя менять для имитации drain: его
`COMPLETED`, version, attempt count и failure code не переписываются этим
lifecycle.

All-source guards и конкретные authority/topology callers — следующий slice;
их отсутствие здесь не является разрешением обходить будущий barrier.

### UNKNOWN: fail closed

`UNKNOWN` не является terminal и не снимается TTL, heartbeat, возрастом записи,
отсутствием ключей, Celery redelivery или повторным owner claim. Без отдельного
утверждённого Q2 certificate blocker сохраняется. Bounded time-to-ACTIVE не
обещается.

## Модель данных и доказательства

Имена ниже взяты из `DESIGN.md` и `SCOPE.md`; они являются candidate schema,
не утверждённым публичным API:

* `IdentityCleanupAdmission` хранит attempt UUID, cleanup UUID, exact expected
  cleanup version, operation UUID/version snapshot, owner incarnation UUID,
  hash capability, protocol/build/resource epoch, immutable payload digest и
  state `OPEN | UNKNOWN | TERMINAL`;
* `IdentityCleanupAdmissionSubject` хранит immutable typed subjects для
  `IDENTITY`, `ACTOR` и `TENANT`, а также source/canonical mappings и frozen
  subject scope;
* `IdentityHoldRequest` и immutable request subjects хранят request UUID,
  idempotency key, authorized target rows, requester/reason/request/correlation
  references и состояние `REQUESTED | ACTIVE | RELEASED`, но не PII, токены,
  refresh keys или credential material;
* `IdentityCleanupLifecycleEvent` и `IdentityHoldLifecycleEvent` являются
  append-only evidence и не заменяют authority records или active hold evidence.

Arrays scope и subject — frozen snapshots. Для запросов, которым нужна
нормализация, используются DB-maintained normalized subject projections;
повторное вычисление scope после admission не может добавить новый actor,
identity или tenant. В candidate не допускается два live admission одной
cleanup-записи либо пересекающегося identity/actor scope; disjoint scopes одного tenant могут идти
параллельно. Tenant subject участвует в hold overlap, а не в исключении независимых
owners. `actor_pks` точно равен набору существующего cleanup record; полный
connected identity closure и tenant snapshot проверяются отдельно.

Owner proof — capability, выданная одной executor incarnation, но в БД хранится
только её hash. Raw capability не сохраняется, не передаётся через Celery и не
восстанавливается после restart. Task-id/ACK, `updated_at`, heartbeat и TTL не
являются ownership proof. Дубликат доставки видит существующий blocker.

Terminal admission не равен business `COMPLETED` cleanup. Terminal lifecycle
acknowledgement означает только, что будущий executor представил допустимое
receipt для exact attempt/owner/lifecycle version; оно не является business CAS
успехом и не устанавливает ACTIVE.

Business CAS conflict — отдельный outcome и не равен UNKNOWN Redis outcome.
Stale expected-version receipt не может переписать конкурирующий результат.
Повтор идентичного уже сохранённого receipt от того же владельца возвращает
подтверждение без UPDATE и без нового события; другое evidence отвергается.
`COMPLETED` допускается только отдельному будущему executor после успешного
exact business CAS; admission terminal сам по себе этого не означает.

Будущий receipt должен быть structural attestation поведения sole executor:
необратимый emitter seal, definitive replies для всех sent commands,
отсутствие in-flight/queued/retry buffers и закрытое dedicated connection.
Owner hash/version или boolean `drained` не заменяют доказательство прекращения
отправок и разрешения in-flight команд. Синтетические DB fixtures и успешный
DB CAS не доказывают `no-more-sends` в Redis. В этом slice такой executor receipt
не принимается как production proof; его acceptance фиксируется для следующего
slice.

Будущий executor обязан иметь actor/command/key budgets. `SCAN COUNT` — hint,
а не hard limit. При исчерпании безопасный partial terminal может оставить
cleanup `PENDING`; это не UNKNOWN, если отсутствие дальнейших sends доказано.
При неизвестном исходе команды blocker остаётся, а terminal запрещён.

## Lifecycle и lock-order contract

Candidate lifecycle допускает только явные transitions, проверенные владельцем,
expected version и append-only event. `OPEN`/`UNKNOWN` нельзя закрыть TTL или
чужим owner. `RELEASED` request нельзя снова активировать; release не возобновляет
cleanup автоматически. Новый signed requeue, если он когда-нибудь будет
разрешён source-specific workflow, создаёт новую attempt identity.

Короткая DB-фаза должна получать locks в следующем порядке:

```text
existing shared retirement barrier
  -> catalog fence (draft key 6507031379205739152)
  -> existing graph/coordinator order
  -> lifecycle/request rows
```

Acknowledgement обновляет только admission и его событие. Он не вызывает
coordinator, не читает заново business version ради успешного CAS и не требует
отсутствия REQUESTED. Полная graph/hold-проверка будущего business finalize
остаётся следующему slice. Request repository принимает только заранее
авторизованный/resolved snapshot: service, который доказывает authorization
и разрешает все target bindings, в foundation отсутствует.

`release_requested` — только запись заранее авторизованного решения об отмене
ещё не активированного request. Она не снимает реальные hold flags, не очищает
resume barrier и не реализует release действующего ACTIVE hold. Для ACTIVE и
Q2 нет ни успешного repository transition, ни runtime/operator entrypoint.

Все изменения существующих строк новых сущностей сериализованы catalog fence.
Если будущая операция затрагивает несколько parent rows, порядок: admission по UUID,
затем request по UUID. Subject/event rows создаются только как производные INSERT
своего parent и больше не блокируются для изменения. Admission INSERT получает
существующие bootstrap/identity fences до operation/cleanup rows. Raw путь
использует try-lock и NOWAIT; lifecycle UPDATE использует try-retirement/catalog
после implicit row lock и при contention откатывается.

Scope arrays ограничены 1000 элементами каждый; targets — 1000 ссылками. Это
граница DB snapshot, а не бюджет будущих Redis-команд. Поля metadata содержат
ограниченные references; target JSON не принимает произвольные payload-ключи.
Payload digest вычисляет БД: SHA-256 UTF-8 представления PostgreSQL 16 JSONB
с фиксированным набором immutable полей. Receipt version 1 связывается с этим
digest, обеими expected business versions, attempt/owner, build и resource epoch.
Raw capability передаётся только в transient proof field и очищается BEFORE
storage; передать её вместо seal digest запрещено.

Catalog namespace — ASCII `iam-cleanup-hold-catalog:v1`; draft key вычисляется
как signed big-endian int64 из первых 8 байт SHA-256 namespace. Локальный namespace/key audit входит в проверку patch; deployment equality
check остаётся отдельным release gate.

Этот порядок не переупорядочивает существующий canonical graph. Raw try-only
writers после уже полученных row locks могут выполнить retirement/catalog/identity
try-lock и NOWAIT для binding rows; при contention они делают полный rollback.
Они никогда не получают блокирующий catalog fence в обратном порядке и не
создают reverse blocking order. Triggers не вызывают Redis.

## Reverse и сохранность evidence

Reverse миграция допустима только для пустого ledger: ни одной admission,
request, subject или lifecycle evidence, а также при подтверждённой quiescence.
После первого admission или request удаление evidence запрещено, включая
попытку пройти rollback через удаление ledger. Historical migrations не
переписываются. `0003b_cleanup_admission_expand` атомарно создаёт только новые
таблицы/functions/guards; `0003b1_cleanup_admission_indexes` создаёт secondary
индексы CONCURRENTLY. Таким образом, проектный номер `0003c` остаётся свободным
для будущего enforcement slice. Порядок downstream migrations при их реализации
должен зависеть от актуального leaf этого expansion.

DDL: lock timeout 2s, statement timeout 30s; repository phase: 2s/5s. Concurrent
index migration восстанавливает прежние session timeouts и не выполняется внутри
atomic block. Если concurrent build оборван и оставил invalid index, повтор
не маскирует ошибку через `IF NOT EXISTS`: требуется проверка migration-owned
index и отдельная безопасная процедура его удаления/повтора в quiescent window.
Reverse index phase также отказывает при непустом ledger; atomic schema reverse
получает retirement/catalog и locks новых таблиц до проверки и любого DROP.
Ни успешный empty-ledger rehearsal, ни эти отказы не разрешают production rollback. После появления live protocol state возврат возможен только на
protocol-aware build с сохранёнными barriers/evidence и отдельным утверждённым
rollback gate.

## Caller boundary

До следующего slice новые admission/request rows не создаются существующими
runtime entrypoints. Caller inventory обязан подтвердить отсутствие подключения
к существующему cleanup worker, supported hold writer, authority/topology paths,
Celery scheduling, admin/API и recovery. Наличие только model/repository/migration
кода не является caller.

Это также означает, что local candidate не изменяет existing hold guards и не
встраивает новый request service в `latch_identity_audit_hold` или другие
существующие writers. Если caller audit не может доказать эту границу, slice
остаётся `NEEDS_VERIFICATION`.

## Отложенные границы и release gates

| Этап | Допустимо сейчас | Что обязательно доказать позже |
|---|---|---|
| **NOW** | Additive DB schema, immutable arrays/snapshots и normalized projections, owner hash, focused CAS/lifecycle repositories, migrations/tests без callers | Caller inventory; fresh PostgreSQL/PostGIS migration rehearsal; raw bypass/immutability checks; no ACTIVE, no executor, no Q2 |
| **NEXT_SLICE** | Отдельно авторизованные integration slices из исходного плана | REQUESTED service, 15 relations/16 resolutions, topology/auth guards, реальный sole-owner emitter, CLOSED/no-more-sends, command/actor/key/reply budgets, exact business CAS и typed pending result |
| **RELEASE_GATE** | В этом draft не пройден | Production-допустимость indefinite pending, old executor/resource quiescence, production roles/RLS/ACL, Q2 certificate/recovery и coordinated enablement; проверка полного протокола после следующих slices |

`ACTIVE` и production rollout остаются недоступны до всех release gates. Q2
остаётся `NOT_RUN`/неутверждённым; unknown blocker нельзя снимать операторской
кнопкой, timeout или отсутствием ключей.

## Сверка с исходным design

Проверенный design-пакет расположен в
`/home/sever/artifacts/hold-cleanup-protocol-design-20260921/`; его SHA256SUMS
проверен до работы. Это source provenance, не runtime dependency.

| Уточнение | Исходный контракт | Уточнение foundation |
|---|---|---|
| REQUESTED не блокирует свой drain | DESIGN §2 (S2), §5 (Seal/ack/finalize), §6 | Отдельный lifecycle CAS; никакой authority mutation |
| Terminal admission не равен COMPLETED; CAS conflict не UNKNOWN | DESIGN §5, §7 I5, §8 | Receipt не вызывает business completion/failure CAS; synthetic DB tests отдельно проверяют неизменность чужого результата |
| Receipt подтверждает реальное поведение executor | DESIGN §3, §7 I1/I2/I5, §13 | Здесь только структурная проверка attestation; реальные CLOSED/no-more-sends доказательства — NEXT_SLICE |
| Ограниченный объём работы и partial | DESIGN §5 (command/actor/key budgets), §10, §13 | ACKED_PARTIAL terminal может оставить PENDING; SCAN COUNT не hard limit; unknown outcome не terminal |
| Нет TTL/recovery shortcut | DESIGN §8; RECOVERY «Обязательный certificate contract» | UNKNOWN не закрывается текущим repository; Q2 не реализован |

Сохранённые результаты design-прототипа не переносятся сюда как PASS реализации.
Команды, актуальные RED→GREEN результаты, migration rehearsal, caller inventory
и независимый review foundation patch публикуются в отдельном evidence bundle.
Executor tests, all-source integration и production gates не могут быть
объявлены PASS результатами этого DB-foundation набора.

**Статус нормативного принятия: DRAFT / NOT ACCEPTED.** Локальный проверенный
patch может получить `HOLD_CLEANUP_FOUNDATION_READY_FOR_REVIEW`, но это не
production acceptance. `CLEANUP_FINDING` остаётся открытым; rollout и enablement
не разрешены.
