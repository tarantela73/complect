# ADR-0020: Bank Statement Reconciliation Model

**Date:** 2026-06-09
**Status:** Accepted
**Author(s):** sever
**Deciders:** Engineering Team

---

## Context

Импорт банковских выписок требует сопоставления строк выписки с транзакциями модуля finance для закрытия дебиторской/кредиторской задолженности. Необходима формальная модель: какие статусы существуют, как алгоритм присваивает confidence, в каком порядке применяются стратегии, что разрешено делать в каждом статусе, и каковы правила ручного вмешательства. Без явного контракта изменение любого порогового значения или порядка стратегий становится несогласованным изменением финансовой логики.

**Key constraints:**

- ADR-0005: `apps/payments` не импортирует модели `apps/finance` — связь только через Protocol `FinanceTransactionFinder`
- Правило 4.3: каждое изменение статуса пишется в Outbox внутри `transaction.atomic()`
- Финансовый аудит обязателен: кто, когда, из какого статуса и в какой перевёл строку

---

## MatchStatus Definition

Строка выписки находится в одном из четырёх состояний:

| Статус | Значение | Ручная работа нужна? |
| --- | --- | --- |
| `UNMATCHED` | Ни одна стратегия не нашла кандидата | Да — если платёж должен быть разнесён |
| `PARTIAL` | Единственный кандидат найден по сумме + ИНН (низкое доверие) | Желательно — подтвердить совпадение |
| `AMBIGUOUS` | 2+ кандидата с близкими параметрами | Да — выбрать один из кандидатов |
| `MATCHED` | Однозначное совпадение (алгоритм) или ручное подтверждение | Нет |

---

## Confidence Scoring Rules (бизнес-инварианты)

Эти значения закреплены здесь как авторитетный источник. Изменение требует обновления ADR.

| Стратегия | Условие результата | Статус | Confidence |
| --- | --- | --- | --- |
| Exact doc_number → external_ref | 1 совпадение | `MATCHED` | **100** |
| Exact doc_number → external_ref | 2+ совпадения | `AMBIGUOUS` | **50** |
| Amount + date window (±3 дня) | 1 совпадение | `MATCHED` | **85** |
| Amount + date window (±3 дня) | 2+ совпадения | `AMBIGUOUS` | **50** |
| Amount + counterparty INN | 1 совпадение | `PARTIAL` | **60** |
| Amount + counterparty INN | 2+ совпадения | `AMBIGUOUS` | **40** |
| Нет сигнала | — | `UNMATCHED` | **0** |

---

## Precedence Order (порядок применения стратегий)

Алгоритм останавливается при первом ненулевом результате:

```text
1. doc_number → external_ref   (exact match, highest confidence)
2. amount + date ± 3 days      (если doc_number не дал результат)
3. amount + counterparty INN   (если предыдущие не дали результат)
4. UNMATCHED                   (нет кандидатов)
```

**Важно**: стратегии не комбинируются и не накапливают confidence. Первая сработавшая стратегия определяет итоговый статус и confidence. Это предотвращает ложное повышение уверенности при частичных совпадениях.

---

## Manual Override Rules

`ManualMatchService.attach()` — единственная точка, где человек подтверждает или переопределяет алгоритм:

- Применима из любого статуса (UNMATCHED, PARTIAL, AMBIGUOUS, MATCHED)
- Устанавливает `match_status=MATCHED`, `match_confidence=100`, `transaction_id=<uuid>`
- Требует `confirmed_by: uuid.UUID` — кто выполнил действие
- Пишет `BankStatementLineMatchedEvent` в Outbox с полями `previous_status` и `confirmed_by`
- Anti-IDOR guard: `transaction_id` проверяется через `find_by_id(tenant_id, ...)` до UPDATE — транзакция обязана принадлежать тому же тенанту (ADR-0021)

`ManualMatchService.detach()` — снятие привязки:

- Переводит строку в `UNMATCHED`, `match_confidence=0`, `transaction_id=NULL`
- Пишет Outbox-событие с `previous_status`

---

## Immutability Rules

| Статус | Разрешено | Запрещено |
| --- | --- | --- |
| `UNMATCHED` | attach (manual), повторный auto-match | — |
| `PARTIAL` | attach (manual), detach → UNMATCHED | — |
| `AMBIGUOUS` | attach (manual) — выбор одного кандидата | auto-posting |
| `MATCHED` (confidence < 100) | attach (manual override), detach | auto-posting |
| `MATCHED` (confidence=100, ручное) | detach | Повторный attach без detach |

**Auto-posting разрешён только при `MATCHED` + `confidence=100`** — т.е. только при ручном подтверждении или exact doc_number match. Реализация auto-posting — отдельное решение, не входит в текущую версию.

---

## Lifecycle

```text
              [import]
                 |
           UNMATCHED (0)
          /      |      \
[doc_ref×1] [amt+dt×1] [amt+inn×1]
        |       |           |
   MATCHED(100) MATCHED(85) PARTIAL(60)
         \         \          /
          \    [2+ hits]      /
           \       |         /
            --> AMBIGUOUS(40-50)
                   |
              [manual attach]
                   |
             MATCHED(100) <-- [manual attach from any state]
                   |
              [manual detach]
                   |
             UNMATCHED(0)
```

---

## Audit Requirement

Каждый переход статуса — как алгоритмический (при `confirm_import`), так и ручной — должен порождать `BankStatementLineMatchedEvent` в Transactional Outbox (правило 4.3). Обязательные поля события:

- `line_id`, `statement_id`, `tenant_id`
- `transaction_id` (None при detach)
- `match_status` (новый), `previous_status` (старый)
- `match_confidence`
- `confirmed_by` (user UUID; для алгоритмического — UUID сервисного аккаунта)
- `correlation_id`, `occurred_at`, `event_id`

**Исключение**: текущая реализация `MatchingService.match_statement()` использует `bulk_update` без Outbox-событий per-line (только финальные счётчики). При переходе к Celery-задаче необходимо добавить per-line события.

---

## Options Considered

### Option 1: Binary match (matched / unmatched)

**Description:** Только два состояния. Нет промежуточных.

**Pros:** Максимальная простота.

**Cons:** Не отражает AMBIGUOUS — бухгалтер не знает, что нужно вмешаться. Нет способа задать приоритет ручной работы.

**Why this was ruled out:** В bank reconciliation AMBIGUOUS — основной рабочий кейс. Binary модель не моделирует реальность.

---

### Option 2: 4-state model + confidence score (выбрано)

**Description:** UNMATCHED / PARTIAL / MATCHED / AMBIGUOUS с полем confidence 0–100.

**Pros:** Полное моделирование реальности. Приоритизация очереди ручной работы. Confidence как вход для auto-posting и ML.

**Cons:** Пороги 85/60 — эмпирические значения, требуют мониторинга в production.

---

### Option 3: Score-only (без дискретных статусов)

**Description:** Только числовое confidence, без TextChoices enum.

**Pros:** Гибко.

**Cons:** Каждый слой (UI, отчёты, Celery) должен самостоятельно интерпретировать пороги — логика размазывается по кодовой базе.

**Why this was ruled out:** Нарушает принцип единственного источника правды. Дискретные статусы — это контракт.

---

## Consequences

### Positive Consequences

- Бухгалтер видит приоритизированную очередь: AMBIGUOUS > PARTIAL > UNMATCHED
- confidence=100 — однозначный триггер для auto-posting (будущее)
- Полный аудит-след через Outbox

### Negative Consequences / Accepted Tradeoffs

- Пороги 85/60/50/40 — эмпирические, нет ML-backing на текущем объёме данных
- `MATCHED` алгоритм (confidence=85) неотличим от `MATCHED` ручного (confidence=100) без поля `confirmed_by`
- `bulk_update` в `MatchingService.match_statement()` не пишет per-line Outbox события

### Risks

- При смене алгоритма старые значения confidence несравнимы с новыми — нужен `matcher_version` при переходе на ML
- Порог 85 для amount+date может давать ложные MATCHED при большом объёме транзакций с одинаковой суммой

---

## Implementation Notes

- `MatchingService` — `backend/apps/payments/services/matching_service.py`
- `ManualMatchService` — `backend/apps/payments/services/manual_match_service.py`
- `MatchStatus` enum — `backend/apps/payments/models/bank_statement_line.py:14`
- `BankStatementLineMatchedEvent` — `backend/apps/payments/events.py`
- При добавлении per-line Outbox событий в `bulk_update` — использовать `batch_size=200`, queue `finance`

---

## Review Date

Пересмотреть при: переходе на ML-scoring для confidence, добавлении нового поля в `FinanceTransactionFinder`, или если доля AMBIGUOUS в production превысит 20%.
