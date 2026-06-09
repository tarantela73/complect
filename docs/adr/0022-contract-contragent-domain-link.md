# ADR-0022: Contract ↔ Contragent Domain Link via UUID Reference

**Date:** 2026-06-10
**Status:** Accepted
**Author(s):** sever
**Deciders:** Engineering Team

---

## Context

`Contract` не имеет прямой связи с `Contragent`. Единственный существующий cross-domain линк — `Transaction.counterparty_inn` → `Contragent.inn`: транзакция знает ИНН плательщика, но договор не знает, к какому контрагенту он относится как к сущности. Это блокирует любую агрегированную аналитику уровня «сущность-контрагент»: количество договоров по клиенту, оборот, задолженность, lifecycle (first contract → last payment). Без явной связи такая аналитика реализуется только через INN-join через транзакции — медленнее, неточнее (ИНН не гарантирует entity identity) и не покрывает договоры без транзакций. Паттерн UUID-референса без FK уже применён в проекте (`SupplyObject.contragent_id`) и является established bounded context pattern.

**Key constraints:**
- ADR-0005: прямой ORM FK (`ForeignKey`) между доменами `contracts` и `contragents` запрещён — создаёт coupling на уровне схемы и CASCADE-зависимости
- ADR-0002: изменение схемы требует Expand & Contract — `NOT NULL` без default на populated таблице запрещён
- INN как entity identity ненадёжен: один ИНН может принадлежать нескольким юрлицам при реорганизации; backfill через INN даёт только probabilistic match, не authoritative

---

## Options Considered

### Option 1: Оставить INN-join (статус-кво)

**Description:** Продолжить агрегировать данные по контрагенту через `Transaction.counterparty_inn` → `Contragent.inn`. Договорная статистика — отдельным запросом с `JOIN` через ИНН контрагента.

**Pros:**
- Нет изменений схемы
- Нет миграции

**Cons:**
- ИНН ≠ entity identity: при реорганизации, переименовании или дублях в справочнике агрегаты смешиваются
- Не покрывает договоры без транзакций (DRAFT-договоры, новые клиенты)
- JOIN через строковое поле — медленнее UUID-lookup даже с индексом
- Невозможно построить Contragent 360° как authoritative entity graph — только inferred view

**Why this was ruled out:** ИНН-based join — производная связь (derived), не авторитетная (authoritative). ERP-система уровня «учёт договоров» не может строить клиентскую аналитику на probabilistic identity.

---

### Option 2: Django ORM ForeignKey (contracts → contragents)

**Description:** Добавить `contragent = models.ForeignKey('contragents.Contragent', on_delete=models.PROTECT, ...)` в модель `Contract`.

**Pros:**
- Декларативная referential integrity на уровне ORM
- Автоматические JOIN-хелперы Django (`select_related`, `prefetch_related`)

**Cons:**
- Нарушает ADR-0005: ORM FK между доменами создаёт hard coupling на уровне схемы и migration dependencies
- `CASCADE` / `PROTECT` semantics перетекают через доменную границу: удаление контрагента становится обязанностью `contracts`
- Миграции `contragents` начинают влиять на `contracts` (shared migration dependency graph)
- При horizontal scaling доменов — FK блокирует независимый deploy

**Why this was ruled out:** Прямое нарушение ADR-0005. FK между доменами — это именно то coupling, от которого ADR-0005 защищает.

---

### Option 3: UUID reference без FK (выбрано)

**Description:** Добавить `contragent_id = models.UUIDField(null=True, blank=True, db_index=True)` в модель `Contract`. Это data reference, не ORM dependency: `contracts`-домен хранит UUID, но не импортирует модель `Contragent` и не создаёт migration dependency на `contragents`. Паттерн идентичен `SupplyObject.contragent_id` (уже в проекте).

**Pros:**
- Полное соответствие ADR-0005: нет импорта моделей, нет migration coupling
- Authoritative entity link: договор явно знает «мой контрагент — этот UUID»
- Composite index `(tenant_id, contragent_id)` покрывает агрегации для Contragent 360°
- Expand & Contract безопасен: nullable → backfill → optional NOT NULL
- Не требует изменений в `contragents`-домене

**Cons:**
- Нет DB-level referential integrity: удалённый контрагент оставит orphan UUID в договорах (обрабатывается soft-delete + is_legal_hold)
- Backfill через INN даёт только probabilistic coverage для исторических данных

---

## Decision

**Добавляем `contragent_id = UUIDField(null=True, blank=True, db_index=True)` в модель `Contract`.**

Поле хранит UUID контрагента как authoritative domain reference без ORM FK — по уже установленному паттерну `SupplyObject.contragent_id`. Связь ADR-0005 compliant: `contracts`-домен знает UUID, но не импортирует модели из `contragents`. Это переход от «финансовой идентичности через ИНН» к «доменной идентичности через UUID» и является необходимым условием для Contragent 360° и любой клиентской аналитики.

---

## Consequences

### Positive Consequences
- Contragent 360° становится buildable: агрегации договоров по контрагенту через `Contract.objects.filter(tenant_id=..., contragent_id=...)`
- Lifecycle клиента (first contract → last payment) реализуем без INN-join
- Client analytics query pattern: один composite index покрывает все агрегаты
- Новые договоры создаются уже с заполненным `contragent_id` — историческая неполнота только у legacy-данных

### Negative Consequences / Accepted Tradeoffs
- Нет DB referential integrity: приложение отвечает за консистентность (soft-delete + is_legal_hold обеспечивают это)
- Backfill исторических данных через INN — probabilistic, не 100% точный. Записи без match остаются nullable.
- До завершения backfill Contragent 360° показывает неполные данные для legacy-договоров

### Risks
- INN-backfill может дать неверные matches при дублях ИНН в справочнике контрагентов (решается: backfill только при `COUNT(DISTINCT contragent.id WHERE inn = ?) == 1`)
- Если в будущем добавится multi-contragent per contract (консорциум, субподряд) — это поле станет недостаточным. Триггер для ревизии: появление бизнес-требования «один договор — несколько контрагентов».

---

## Implementation Notes

**Expand & Contract (ADR-0002):**

| Release | Действие |
|---|---|
| N | `AddField(contragent_id, null=True)` + `AddIndexConcurrently(tenant_id, contragent_id)` |
| N+1 | Celery backfill: `Contract.objects.filter(contragent_id=None)` → match через `Contragent.objects.filter(inn=counterparty_inn)`. Batch 5000, `SKIP LOCKED`. Только однозначные matches (1 контрагент на ИНН в рамках тенанта). |
| N+2 | Опционально: `NOT NULL` + `NOT VALID` → `VALIDATE CONSTRAINT` — только после достижения 100% coverage (мониторинг через `Contract.objects.filter(contragent_id=None).count()`) |

**Backfill источник INN:** `Transaction.counterparty_inn` — поле уже populated для всех проведённых транзакций. Для договоров без транзакций (DRAFT) — backfill невозможен без ручного заполнения.

**Composite index для Contragent 360°:**
```sql
CREATE INDEX CONCURRENTLY idx_contract_tenant_contragent
ON contracts_contracts (tenant_id, contragent_id, status, is_deleted, id DESC);
```
Покрывает: `COUNT(*)`, `COUNT(*) WHERE status='ACTIVE'`, `ORDER BY id DESC LIMIT N`.

**Service layer:** Новые договоры должны принимать `contragent_id` в `ContractCreateRequest`. Поле nullable → существующие create-ендпоинты не сломаются.

---

## Review Date

Пересмотреть если: появится требование «один договор — несколько контрагентов», или backfill-coverage через 90 дней после Release N+1 останется ниже 80% (пересмотр стратегии backfill).
