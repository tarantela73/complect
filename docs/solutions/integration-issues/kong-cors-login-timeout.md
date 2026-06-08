---
title: Kong CORS plugin missing — login hangs / CORS error via API gateway
date: 2026-06-08
category: integration-issues
module: api_gateway
problem_type: integration_issue
component: authentication
symptoms:
  - Browser login form hangs or times out with no error message
  - Network tab shows CORS policy error on OPTIONS preflight to port 8000
  - "Failed to fetch on POST /api/v1/auth/login via Kong (port 8000)"
  - Requests directly to Django on port 8010 succeed; requests via Kong fail
root_cause: config_error
resolution_type: config_change
severity: high
tags:
  - kong
  - cors
  - api-gateway
  - authentication
  - preflight
---

# Kong CORS plugin missing — login hangs / CORS error via API gateway

## Problem

Браузерный логин зависал при отправке через API-gateway (Kong, порт 8000). Прямые запросы к Django-бэкенду (порт 8010) работали. Корень проблемы — CORS-плагин не был настроен в Kong, поэтому OPTIONS preflight не получал заголовков `Access-Control-Allow-*` и браузер блокировал последующий POST.

## Symptoms

- Форма логина зависает, нет ответа и нет ошибки в UI
- В DevTools Network: `OPTIONS /api/v1/auth/login` → `200` без CORS-заголовков → браузер блокирует запрос с `CORS policy` ошибкой
- `Failed to fetch` на POST `/api/v1/auth/login` при обращении к порту 8000
- `curl` напрямую к порту 8010 проходит; всё сломано только через Kong (8000)

## What Didn't Work

- Django `corsheaders` middleware (`CORS_ALLOWED_ORIGINS=["http://localhost:3000"]` в `local.py`) — не помогает: OPTIONS-preflight перехватывается Kong до того, как запрос достигает Django. `corsheaders` никогда не видит preflight.
- Проверка `django-cors-headers` настроек — трата времени, поскольку проблема на уровне Kong, а не Django.

## Solution

Добавить CORS-плагин в `infra/api_gateway/kong.yml` на уровне service (не route), чтобы Kong самостоятельно обрабатывал preflight:

```yaml
# infra/api_gateway/kong.yml
services:
  - name: backend
    url: http://backend:8010
    routes:
      - name: all-routes
        paths:
          - /
        strip_path: false
    plugins:
      - name: cors
        config:
          origins:
            - "http://localhost:3000"
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
- `credentials: true` — обязательно, иначе браузер отклоняет ответ при `withCredentials: true` / `credentials: 'include'`
- `preflight_continue: false` — Kong возвращает `200` на OPTIONS немедленно, без проброса на backend; предотвращает 405 или 403 от Django
- `headers` — включать все кастомные заголовки, которые фронтенд посылает (`Idempotency-Key`)

## Why This Works

Kong — reverse proxy перед Django. Браузер шлёт OPTIONS preflight **на Kong**. Если Kong не настроен отвечать CORS-заголовками, preflight получает ответ без `Access-Control-Allow-Origin`, и браузер блокирует настоящий запрос. Django `corsheaders` работает только когда запрос добирается до Django — он никогда не видит preflight, перехваченный Kong.

`preflight_continue: false` критично: если Kong пробрасывает OPTIONS в Django и Django не ожидает этот HTTP-метод, он вернёт 405, что ломает CORS-handshake.

## Prevention

- При добавлении нового API-gateway (Kong, nginx, Traefik): **CORS настраивается на уровне gateway**, а не только в Django middleware.
- Проверять CORS через gateway-порт (8000), а не напрямую (8010): `curl -s -I -X OPTIONS http://localhost:8000/api/v1/auth/login -H "Origin: http://localhost:3000" -H "Access-Control-Request-Method: POST"` — ответ должен содержать `Access-Control-Allow-Origin`.
- Любой новый кастомный заголовок на фронтенде нужно добавлять в список `headers` Kong CORS-плагина с точным именем (например, `Idempotency-Key` — без `X-` префикса, как определено в rule 5.3). Несовпадение имени вызывает CORS-блокировку несмотря на наличие записи в конфиге.

## Related Issues

- `infra/api_gateway/kong.yml` — текущая конфигурация
- `backend/config/settings/local.py` — Django CORS settings (нужны для прямого доступа к порту 8010, не для gateway)
