MIDDLEWARE = [
    # 0. Django Security Middleware (FIRST GATE FOR NET HARDENING)
    "django.middleware.security.SecurityMiddleware",
    
    # 1. HARD CONTEXT ISOLATION (Превентивный сброс ContextVar + PostgreSQL RESET сессии)
    "apps.common.middleware.context_isolation.ContextIsolationMiddleware",
    
    # 2. TENANT ENFORCEMENT (Авторизационный барьер, токен-безопасный RequestContext)
    "apps.common.tenancy.middleware.AsgiTenantEnforcementMiddleware",
    
    # 3. GLOBAL KILL SWITCH (SRE экстренное аварийное отключение деградирующих доменов)
    "apps.common.middleware.kill_switch.SreGlobalKillSwitchMiddleware",
    
    # 4. IDEMPOTENCY (Защита мутирующих POST/PUT операций через Redis lock)
    "apps.common.middleware.idempotency.IdempotencyEnforcementMiddleware",
    
    # 5. READ/WRITE STRATEGY (Липкий CQRS роутинг с контролируемым Redis TTL)
    "apps.common.middleware.read_your_own_writes.ReadYourOwnWritesStickyMiddleware",
    
    # 6. OBSERVABILITY (Инжекция tracing ID, structlog логгер)
    "apps.common.middleware.lifecycle.StructlogTracingContextMiddleware",
    
    # 7. DB RLS (Взвод локальной переменной app.current_tenant_id в сессию Postgres)
    "apps.common.middleware.lifecycle.PostgresRlsEnforcementMiddleware",
    
    # Системные middlewares Django
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]
