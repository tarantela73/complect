# 0006 — JWT Authentication и ротация Refresh Token через Redis

## 1. Статус

| Поле     | Значение                        |
| -------- | ------------------------------- |
| Статус   | APPROVED                        |
| Дата     | 2026-06-01                      |
| Владелец | Core Platform Team              |

---

## 2. Контекст

ERP требует аутентификации для всех бизнес-endpoint'ов. Стандартные Django-сессии основаны на базе данных и не масштабируются в ASGI-среде с множеством воркеров. Нужен stateless механизм с возможностью принудительного отзыва токенов (logout, смена устройства, security event).

---

## 3. Решение

Двухтокенная схема: **Access Token** (краткоживущий, in-memory) + **Refresh Token** (долгоживущий, HttpOnly Cookie, хранится в Redis).

### Правила

#### Access Token

- Алгоритм: HS256, библиотека PyJWT.
- TTL: 15 минут.
- Payload: `user_id`, `tenant_id`, `username`, `iat`, `exp`, `type: "access"`.
- Хранение на клиенте: **исключительно в памяти** (не localStorage, не sessionStorage).
- Передача: заголовок `Authorization: Bearer <token>`.

#### Refresh Token

- TTL: 7 дней.
- Payload: `user_id`, `jti` (UUID v4), `iat`, `exp`, `type: "refresh"`.
- Хранение: HttpOnly, Secure, SameSite=Strict Cookie + запись в Redis (`auth:refresh:{user_id}:{jti}`).
- При каждом использовании токен **ротируется**: старый `jti` удаляется, выпускается новый.
- Хранение на сервере позволяет мгновенно отозвать токен при logout или security event.

#### MFA Flow

- После успешного login с `mfa_enabled=True` выдаётся partial token (`type: "mfa_pending"`, TTL 5 минут).
- Partial token принимается только на endpoint `/auth/mfa/verify`.
- После успешной TOTP-проверки выдаются полноценные access + refresh токены.

### Запреты

- Хранить Access Token в localStorage или sessionStorage.
- Выдавать полноценный Access Token без прохождения MFA при `mfa_enabled=True`.
- Принимать просроченный partial MFA token.

---

## 4. Рассмотренные альтернативы

| Альтернатива          | Причина отказа                                                                    |
| --------------------- | --------------------------------------------------------------------------------- |
| Django Sessions (DB)  | Требует DB-запроса на каждый запрос; не масштабируется в multi-worker ASGI.       |
| Только Access Token   | Нет механизма отзыва; при компрометации токена нельзя принудительно выйти.       |
| RS256 вместо HS256    | Асимметричная подпись избыточна для monolith; HS256 достаточен при SECRET_KEY rotation. |

---

## 5. Последствия

### Плюсы

- Stateless Access Token: нет DB-запросов при проверке.
- Мгновенный отзыв через Redis при компрометации.
- MFA не нарушает основной flow — partial token изолирован.

### Минусы

- Зависимость от Redis для Refresh Token; при недоступности Redis logout не работает.
- Ротация refresh token требует атомарного обновления Cookie на клиенте.

---

## 6. Связанные ADR и разделы

- [ADR-0007](0007-api-gateway-trust-boundary.md) — trust boundary и контекстные заголовки
- Раздел 5.1 архитектурной спецификации — Authentication
- Раздел 5.9 — Secrets Rotation Policy
