# Bounded-Context Ownership & Cross-Domain Interaction Rules

## 1. STATUS
Статус: APPROVED FOR IMPLEMENTATION
Дата: 2026-05-24
Владелец: Principal Enterprise Architect / Lead Governance Engineer
## 2. CONTEXT
ERP «КОМПЛЕКТ» развернута как Modular Monolith в едином монорепозитории Complect/ [Раздел 18 ТЗ]. Высокая скорость разработки и параллельная работа команд над доменами contracts, finance, users и realtime создают критический риск перекрестного зацепления (High Coupling). Если изменения в таблицах пользователей начнут ломать логику закрытия договоров или балансы леджера из-за прямых связей в коде, платформа потеряет свойство модульности и не сможет эволюционировать.
## 3. DECISION & ENGINEERING LAW (ЗАКОНЫ ВЛАДЕНИЯ ДОМЕНАМИ)
Установить жесткую персональную и технологическую привязку владения каждым ограниченным контекстом (Bounded Context):

А. Матрица владения контекстами (Ownership Matrix)
1. Context: apps/users & apps/workflows (Identity & Process)
Владелец (Owner): Core Platform Team.
Сфера ответственности: Сессии, MFA, Argon2, Device Fingerprint, RBAC, движок FSM-клиента.
2. Context: apps/contracts & apps/contragents (Contracts & Compliance)
Владелец (Owner): Legal Tech Feature Team.
Сфера ответственности: Жизненный цикл договоров, PII-шифрование OpenPGP, конвейер ClamAV/OCR, Legal Hold.
3. Context: apps/finance & apps/analytics & apps/integration (Finance Ledger)
Владелец (Owner): FinTech & Integrations Team.
Сфера ответственности: Двойная запись леджера, расчет задолженностей, BFF комбо-аналитика, ACL мапперы 1С.
4. Context: apps/realtime & apps/chat & apps/notifications (Realtime Mesh)
Владелец (Owner): Realtime Infrastructure Team.Сфера ответственности: Redis Streams лог событий, сокет-рукопожатие v1, Event Replay, LiveKit WebRTC.

Б. Закон кросс-доменных взаимодействий (Inter-Module Interaction Law)
Правило 1 (Полная изоляция моделей): Прямой импорт Django-моделей из чужого домена (например, импорт from apps.finance.models.ledger import LedgerTransaction внутри apps/contracts/) категорически запрещен. Нарушение карается падением CI/CD Quality Gate.
Правило 2 (Стена Internal SDK): Межмодульное синхронное общение идет исключительно через абстрактные контракты (интерфейсы typing.Protocol), расположенные в центральном слое backend/apps/common/contracts/ [4.4 ТЗ]. Домен-потребитель импортирует только Protocol. Реализация (Class) регистрируется в контейнере зависимостей при старте приложения.
Правило 3 (Асинхронная развязка): Если домену нужно уведомить систему об изменении своего состояния, он выбрасывает иммутабельное dataclass-событие в Transactional Outbox [Раздел 4.5 ТЗ]. Другие домены подписываются на события асинхронно через Celery-очереди, полностью исключая зацепление в рантайме.
## 4. CONSEQUENCES
Pros: Абсолютная изоляция доменов. Любой контекст (например, apps/finance/) можно безболезненно вырезать и превратить в независимый микросервис за 1 день без изменения кода других приложений. Полная защита от спагетти-архитектуры.
Cons: Разработчикам требуется писать дополнительные интерфейсы-протоколы и Pydantic-схемы маппинга DTO на границах доменов. Velocity снижается ради абсолютной расширяемости.
