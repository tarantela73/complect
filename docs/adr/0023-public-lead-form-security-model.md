# ADR-0023: Public Lead Form Security Model

**Status:** Accepted  
**Date:** 2026-06-12  
**Deciders:** Технический лид, Директор  
**Tags:** security, public-api, tenant-isolation

---

## Context

ERP «Комплект» добавляет публичный маркетинговый сайт. На сайте есть форма заявки, которая должна создавать `Request` в ERP без аутентификации посетителя. Это разрывает ключевую инварианту системы: все API-запросы требуют JWT + tenant context.

**Ключевые ограничения:**
- Посетитель анонимный — JWT невозможен
- Tenant известен (одна компания, один ERP-инстанс)
- Endpoint открыт в интернет — нужна защита от спама/ботов
- Существующий `AsgiTenantEnforcementMiddleware` блокирует всё без JWT

---

## Decision

Выделить новый URL-prefix `/api/v1/public/` с особым поведением в middleware:

1. **Gateway token пропускается** — публичный endpoint доступен напрямую из интернет через Cloudflare (сайт за CF proxy)
2. **Tenant injected from config** — `settings.PUBLIC_SITE_TENANT_ID` (UUID) устанавливается в `ContextVar` до вызова endpoint. Не из JWT, не из домена — из переменной окружения
3. **Anti-bot: Cloudflare Turnstile** — каждый POST `/api/v1/public/requests/` обязан содержать `cf_turnstile_token`. Backend верифицирует токен через `https://challenges.cloudflare.com/turnstile/v0/siteverify`. При неудаче — 422
4. **Rate limiting: CF WAF** — ограничение по IP на уровне Cloudflare, не Django. Django не держит состояние per-IP
5. **Actor_id = None** — публичные endpoints не устанавливают `user_id` в context. `get_user_id()` не должен вызываться из публичных handlers

---

## Consequences

**Положительные:**
- Минимальные изменения в существующем middleware — одна дополнительная ветвь
- `RequestSource.WEBSITE` уже существует в модели — Request создаётся через штатный `RequestCreateService`
- Tenant isolation сохранена: создаётся Request только в configured tenant, не в произвольный
- Turnstile invisible для пользователя (нет CAPTCHA-виджета), сайт за CF (native integration)

**Риски:**
- `PUBLIC_SITE_TENANT_ID` в env — если указывает на несуществующий tenant, Request создаётся в «orphan» tenant. Митигация: валидация UUID и существования tenant в `AppConfig.ready()`
- Turnstile timeout (CF API недоступен) блокирует форму. Митигация: httpx timeout=5s, 503 с user-facing сообщением
- В будущем при SaaS-эволюции потребуется заменить env-config на domain→tenant таблицу. Публичный API surface (`/api/v1/public/`) не меняется — меняется только механизм резолвинга tenant внутри middleware

**Отвергнутые альтернативы:**
- **Google reCAPTCHA v3** — сайт уже за CF, Turnstile нативно интегрирован с WAF и не отправляет данные в Google (GDPR)
- **Tenant из HTTP Host заголовка** — усложняет конфигурацию (нужна таблица доменов), нет выигрыша при single-tenant деплое
- **Отдельный Django-сервис для публичного сайта** — противоречит концепции «один портал», дублирует Request-модель
