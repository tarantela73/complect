# 0007 — API Gateway Trust Boundary и контекстные заголовки

## 1. Статус

| Поле     | Значение                        |
| -------- | ------------------------------- |
| Статус   | APPROVED                        |
| Дата     | 2026-06-01                      |
| Владелец | Core Platform Team / SRE Team   |

---

## 2. Контекст

Бэкенд работает за API Gateway (Kong/APISIX). Gateway валидирует JWT, определяет тенанта и пользователя, затем проксирует запрос на бэкенд. Бэкенд должен доверять только запросам, прошедшим через Gateway, и отклонять прямые обращения из внешней сети. Кроме того, auth-endpoint'ы (login, refresh, logout) по своей природе не имеют контекста тенанта/пользователя — они его устанавливают. Это противоречит общей модели middleware.

---

## 3. Решение

### Уточнение Release N: VERIFIED_AUTH_TENANT_CONTEXT_BOUNDARY (2026-09-17)

Для этого P0 repair slice следующие правила заменяют описанные ниже доверие
к identity headers и запрет повторной backend JWT verification. Историческая
модель ниже не является разрешением принимать неподтверждённый HTTP context.

- Kong удаляет клиентские `X-User-ID`, `X-Tenant-ID`, `X-CTX-User-ID` и
  `X-CTX-Tenant-ID`, устанавливает свой `X-Gateway-Token` и передаёт исходный
  `Authorization` backend. Gateway не извлекает identity из JWT payload.
- Backend `JWTAuth` проверяет подпись HS256, обязательный `exp` и `type=access`,
  затем вызывает действующий users auth-context validator через common Protocol.
  Tenant-qualified actor, canonical authority, disabled identity и применимые
  identity/version/epoch guards сохраняются. Новый issuer/audience contract,
  IdentitySession и tenant grants этим изменением не вводятся.
- Tenant middleware вызывает этот authenticator до idempotency, tenant ORM и
  остальных потребителей контекста. Только успешный verified result одновременно
  устанавливает `request.jwt_*` и user/tenant ContextVar. Проверка gateway secret
  остаётся дополнительной защитой; даже при её отключении headers не дают identity.
- `require_permission` требует тот же verified result независимо от `auth=None`
  или отсутствия router auth. Повторная проверка в одном request использует его
  только при совпадении bearer, request attributes и ContextVar; mismatch
  отклоняется, а не исправляется перезаписью контекста.
- Users adapter регистрируется в `UsersConfig.ready()` до первого HTTP request;
  отсутствие adapter закрывает доступ. Common не импортирует users implementation.
- Context-free auth allowlist содержит только точные POST login, refresh, logout
  и MFA verify. MFA setup/confirm и merge-convergence защищены access JWT уже в
  middleware. GET/HEAD docs/OpenAPI, health/static/public и OPTIONS сохраняют
  явные исключения. Public API по ADR-0023 получает tenant только из конфигурации
  и не получает authenticated actor. Очистка выполняется и при исключениях.
- Внутренние propagation serializers/parsers сохраняют формат и поведение.
  Они не удостоверяют отправителя: доверие обязан установить внутренний transport
  caller. Внешний HTTP не вызывает их для назначения actor/tenant. Наличие helper
  не доказывает production wiring или аутентификацию внутреннего транспорта.

Компромисс: криптографическая проверка и users validation выполняются на backend
до чтения tenant-контекста; это устраняет зависимость authority от sanitation Kong.
Повторные router/RBAC проверки переиспользуют один результат в пределах request.
Rollback не должен возвращать доверие к внешним identity headers.

Проверки: `test_verified_auth_boundary.py`, `test_verified_auth_http.py`,
`test_endpoint_coverage.py`, context/RBAC regressions и
`infra/scripts/tests/test_gateway_runtime.py`. Статическая проверка конфигурации
не заменяет runtime Kong evidence; локальная БД не доказывает production RLS role.

Трёхуровневая модель доверия в `AsgiTenantEnforcementMiddleware`.

### Уровень 1 — Публичные маршруты (PUBLIC_PREFIXES)

Полный bypass middleware. Не требуют ни токена шлюза, ни контекстных заголовков.

```
/public/, /static/, /metrics/, /health/, /favicon.ico
```

### Уровень 2 — Context-Free маршруты (CONTEXT_FREE_PREFIXES)

Требуют `X-Gateway-Token`, но **не** требуют `X-Tenant-ID` / `X-User-ID`. Используются для auth-flow, где контекст ещё не установлен, и для OpenAPI-документации.

```
/api/v1/auth/, /api/v1/docs, /api/v1/openapi.json
```

### Уровень 3 — Защищённые бизнес-маршруты (все остальные)

Требуют `X-Gateway-Token` + `X-Tenant-ID` (UUID v4) + `X-User-ID` (UUID v4). Gateway добавляет эти заголовки после успешной валидации JWT. Nil UUID (`00000000-...`) и non-v4 UUID отклоняются с HTTP 400.

### Idempotency Exempt

Auth-endpoint'ы (`/api/v1/auth/`) исключены из `IdempotencyEnforcementMiddleware` — каждый вызов login/refresh генерирует новые токены и по определению не идемпотентен.

### Правила

- `EXPECTED_GATEWAY_TOKEN` задаётся через Docker Secret / env; пустое значение отключает проверку (только для unit-тестов).
- Gateway-токен — служебный секрет, не JWT пользователя; ротируется по расписанию (раздел 5.9).
- Бэкенд-endpoint'ы **не валидируют JWT самостоятельно**; доверяют контексту, установленному middleware из заголовков Gateway.

---

## 4. Рассмотренные альтернативы

| Альтернатива                  | Причина отказа                                                              |
| ----------------------------- | --------------------------------------------------------------------------- |
| mTLS между Gateway и бэкендом | Избыточно для monolith; усложняет локальную разработку и тестирование.      |
| Повторная валидация JWT на бэкенде | Двойная работа; при rotation secret нужно обновлять два места.         |
| Network-level isolation only  | Недостаточно: не защищает от компрометации внутренней сети.                 |

---

## 5. Последствия

### Плюсы

- Чёткая граница доверия: бэкенд никогда не получает необработанный JWT.
- Auth-flow не требует специальной обработки в каждом endpoint'е.
- Тестирование middleware изолировано от auth-логики.

### Минусы

- Зависимость от корректной конфигурации Gateway: ошибка в Kong-конфиге → все запросы получают 403.
- Локальная разработка требует знания `X-Gateway-Token` для curl/Postman.

---

## 6. Связанные ADR и разделы

- [ADR-0006](0006-jwt-auth-token-rotation.md) — JWT auth и токены
- [ADR-0005](0005-bounded-context-ownership.md) — изоляция контекстов
- Раздел 5.1 архитектурной спецификации — Authentication
- Раздел 5.3 — Idempotency Policy
