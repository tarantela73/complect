---
title: "Kong CORS failures — missing plugin or stale origin list"
date: 2026-06-08
last_updated: 2026-06-25
category: integration-issues
module: api_gateway
problem_type: integration_issue
component: authentication
symptoms:
  - Browser login form hangs or shows generic error with no CORS details in UI
  - "OPTIONS preflight returns 200, but no POST ever appears in Kong access log"
  - "fetch() throws TypeError (network error), not a JSON 4xx response"
  - Requests directly to Django on port 8010 succeed; requests via Kong fail
  - "Generic error ('Ошибка входа. Попробуйте позже.') instead of INVALID_CREDENTIALS after frontend port change"
root_cause: config_error
resolution_type: config_change
severity: high
tags:
  - kong
  - cors
  - api-gateway
  - authentication
  - preflight
  - port-change
---

Two distinct failure modes produce the same symptom cluster: `OPTIONS → 200`, no POST in Kong logs, `fetch()` throws TypeError, browser shows a generic error (not a server-side rejection).

---

## Failure Mode A: CORS plugin missing entirely

**Problem.** Браузерный логин зависал при отправке через API-gateway (Kong, порт 8000). Прямые запросы к Django-бэкенду (порт 8010) работали. Корень проблемы — CORS-плагин не был настроен в Kong, поэтому OPTIONS preflight не получал заголовков `Access-Control-Allow-*` и браузер блокировал последующий POST.

**Symptoms:**

- Форма логина зависает, нет ответа и нет ошибки в UI
- В DevTools Network: `OPTIONS /api/v1/auth/login` → `200` без CORS-заголовков → браузер блокирует запрос с `CORS policy` ошибкой
- `Failed to fetch` на POST `/api/v1/auth/login` при обращении к порту 8000
- `curl` напрямую к порту 8010 проходит; всё сломано только через Kong (8000)

**What Didn't Work:**

- Django `corsheaders` middleware (`CORS_ALLOWED_ORIGINS=["http://localhost:3000"]` в `local.py`) — не помогает: OPTIONS-preflight перехватывается Kong до того, как запрос достигает Django. `corsheaders` никогда не видит preflight.
- Проверка `django-cors-headers` настроек — трата времени, поскольку проблема на уровне Kong, а не Django.

**Solution.** Добавить CORS-плагин в `infra/api_gateway/kong.yml` на уровне service (не route):

```yaml
# infra/api_gateway/kong.yml
services:
  - name: backend
    url: http://backend:8010
    plugins:
      - name: cors
        config:
          origins:
            - "http://localhost:3000"
            - "http://localhost:8080"
          methods:
            - GET
            - POST
            - PUT
            - PATCH
            - DELETE
            - OPTIONS
          headers:
            - Accept
            - Authorization
            - Content-Type
            - Idempotency-Key
          credentials: true          # нужно для cookie-based auth (Refresh Token)
          preflight_continue: false  # Kong отвечает на OPTIONS сам, не пробрасывает в Django
          max_age: 3600
```

Ключевые флаги:

- `credentials: true` — обязательно, иначе браузер отклоняет ответ при `credentials: 'include'`
- `preflight_continue: false` — Kong возвращает `200` на OPTIONS немедленно; предотвращает 405/403 от Django
- `headers` — включать все кастомные заголовки фронтенда (`Idempotency-Key`)

---

## Failure Mode B: Stale origin in CORS plugin after frontend port change

**Problem.** После переноса фронтенда с порта 3000 на 8080 появилась ошибка "Ошибка входа. Попробуйте позже." — не "Неверный логин или пароль". Бэкенд работал исправно, curl к gateway возвращал токен. Причина: в `origins` был только `http://localhost:3000`. Kong вернул `Access-Control-Allow-Origin: http://localhost:3000`, браузер отправлял `Origin: http://localhost:8080` — несоответствие → браузер блокировал POST до его отправки.

**Symptoms:**

- Браузер показывает общую ошибку (не `INVALID_CREDENTIALS`) после смены порта фронтенда
- Kong логи: только `OPTIONS → 200`, ни одного POST не залогировано
- `curl` к gateway с теми же credentials возвращает токен — бэкенд исправен
- `fetch()` выбрасывает `TypeError` → catch-блок попадает в else-ветку generic error

**What Didn't Work:**

- Проверка backend логов для поиска ошибки логина — ничего, потому что POST никогда не доходит
- Предположение что `OPTIONS → 200` = CORS ОК — статус 200 отражает обработку запроса, а не разрешение origin
- Тест через `curl` — curl игнорирует CORS заголовки, возвращал корректный токен, маскируя проблему браузера
- `docker restart api_gateway` — fail с OCI runtime ошибкой (stale bind mount path Docker Desktop); нужен `--force-recreate`

**Solution.** Добавить новый origin в `infra/api_gateway/kong.yml`:

```yaml
# Before:
origins:
  - "http://localhost:3000"

# After:
origins:
  - "http://localhost:3000"
  - "http://localhost:8080"
```

Применить без `docker restart`:

```bash
docker compose up -d --force-recreate api_gateway
```

Верификация:

```bash
curl -sv -X OPTIONS http://localhost:8000/api/v1/auth/login \
  -H "Origin: http://localhost:8080" \
  -H "Access-Control-Request-Method: POST" 2>&1 | grep Access-Control
# Ожидаемо: Access-Control-Allow-Origin: http://localhost:8080
```

---

## Why This Works

Kong — reverse proxy перед Django. Браузер шлёт OPTIONS preflight **на Kong**. Если Kong не настроен отвечать CORS-заголовками или возвращает неправильный origin, `fetch()` выбрасывает `TypeError` без каких-либо данных от сервера.

`OPTIONS → 200` НЕ означает, что CORS пройден. Браузер проверяет значение заголовка `Access-Control-Allow-Origin`, а не HTTP-статус. Если значение не совпадает с `Origin` запроса — POST блокируется несмотря на 200.

Django `corsheaders` middleware никогда не видит preflight, перехваченный Kong.

## Prevention

- **При смене порта фронтенда** в `docker-compose.dev.yml` — всегда обновлять `origins` в `infra/api_gateway/kong.yml` в том же коммите. Kong делает точное строковое сравнение: `localhost:3000` ≠ `localhost:8080`.
- **Диагностический паттерн**: `OPTIONS → 200` + нет POST в логах + `fetch()` выбрасывает TypeError = несоответствие origin в CORS config, а не серверная ошибка.
- При добавлении нового API-gateway: **CORS настраивается на уровне gateway**, а не только в Django middleware.
- Проверять CORS через gateway-порт с конкретным Origin: `curl -sv -X OPTIONS http://localhost:8000/api/v1/auth/login -H "Origin: http://localhost:PORT"` — `Access-Control-Allow-Origin` должен содержать тот же PORT.
- Любой новый кастомный заголовок на фронтенде нужно добавлять в `headers` Kong CORS-плагина с точным именем (`Idempotency-Key` — без `X-` префикса).
- **`docker restart` не работает** для контейнеров с bind-mounted файлами в Docker Desktop WSL2 — использовать `docker compose up -d --force-recreate <service>`.

## Related Issues

- `infra/api_gateway/kong.yml` — текущая конфигурация
- `backend/config/settings/local.py` — Django CORS settings (для прямого доступа к порту 8010)
- `docs/solutions/developer-experience/docker-desktop-wsl2-ghost-port.md` — Docker Desktop WSL2 port proxy ghost (причина смены порта фронтенда)
