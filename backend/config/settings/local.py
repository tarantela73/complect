import os
from urllib.parse import urlparse
import redis

# Автономный безопасный парсер логических ENV-переменных
def env_bool(name: str, default: bool = False) -> bool:
    """Гарантирует strict-типизацию строковых флагов из .env в булево значение."""
    return os.getenv(name, str(default)).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    
# [ИСПРАВЛЕНО]: Явное выравнивание инварианта маршрутизации для внешних тест-раннеров
ROOT_URLCONF = "config.urls"    


from .base import *  # Накатываем остальные базовые инварианты Django

# [ИСПРАВЛЕНО]: Теперь NameError физически невозможен
DEBUG = env_bool("DEBUG", True)

# ==========================================
# 🛡️ DJANGO SECURITY HARDENING GATES (ИБ-Комплаенс)
# ==========================================
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
CSRF_TRUSTED_ORIGINS = os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:3000").split(",")

SECURE_PROXY_SSL_HEADER = None  # Tuple-формат вынесен строго в prod.py
USE_X_FORWARDED_HOST = env_bool("USE_X_FORWARDED_HOST", False)

# Современные политики безопасности контекстов и заголовков браузера
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_CONTENT_TYPE_NOSNIFF = env_bool("SECURE_CONTENT_TYPE_NOSNIFF", True)
X_FRAME_OPTIONS = os.getenv("X_FRAME_OPTIONS", "DENY")

# [ИСПРАВЛЕНО]: Намертво запечатываем куки флагами HTTPONLY против XSS-атак
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", False)
SESSION_COOKIE_HTTPONLY = env_bool("SESSION_COOKIE_HTTPONLY", True)
CSRF_COOKIE_HTTPONLY = env_bool("CSRF_COOKIE_HTTPONLY", True)
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
CSRF_COOKIE_SAMESITE = os.getenv("CSRF_COOKIE_SAMESITE", "Lax")

# Переносим защиту CSRF под контроль сессий и включаем подписанные куки
CSRF_USE_SESSIONS = True
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)

# ==========================================
# 🧱 ОГРАНИЧЕНИЯ ПАМЯТИ И АСИНХРОННОСТИ
# ==========================================
if not env_bool("DJANGO_ALLOW_ASYNC_UNSAFE", False):
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "false"

DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", "52428800"))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE", "52428800"))


# ==========================================
# 🗄️ POSTGRES CONFIGURE С ПРОМЫШЛЕННЫМИ ТАЙМАУТАМИ
# ==========================================
def _parse_db_url(env_key: str) -> dict:
    raw = os.getenv(env_key)
    # [ИСПРАВЛЕНО]: Защита от падения urlparse при пустом окружении
    if not raw:
        raise RuntimeError(f"CRITICAL_CONFIGURATION_ERROR: Database env {env_key} is not configured.")

    url = urlparse(raw)
    return {
        "ENGINE": "django.contrib.gis.db.backends.postgis",  # Нативное ГИС-ядро PostGIS
        "NAME": url.path[1:],
        "USER": url.username,
        "PASSWORD": url.password,
        "HOST": url.hostname,
        "PORT": str(url.port or 5432),
        "CONN_MAX_AGE": 60,
        # [ИСПРАВЛЕНО]: Жесткие лимиты против дедлоков и зависших транзакций
        "OPTIONS": {
            "options": ("-c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=15000")
        },
    }


DATABASES = {
    "default": _parse_db_url("DATABASE_URL"),
    "postgres_replica": _parse_db_url("DATABASE_REPLICA_URL"),
}

ENABLE_RLS = env_bool("ENABLE_RLS", True)
RLS_NAMESPACE = os.getenv("RLS_NAMESPACE", "app.current_tenant_id")
POSTGRES_REPLICA_ENABLED = env_bool("POSTGRES_REPLICA_ENABLED", True)
POSTGRES_REPLICA_MAX_LAG = float(os.getenv("POSTGRES_REPLICA_MAX_LAG", "1.0"))

# ==========================================
# ⚡ МУЛЬТИ-БАЗОВЫЕ REDIS СТРОКИ URL (Для ленивой фабрики)
# ==========================================
# [ИСПРАВЛЕНО]: Переменные вычитываются, но СИНГЛТОНЫ ТУТ БОЛЬШЕ НЕ СОЗДАЮТСЯ!
REDIS_DEFAULT_URL = os.getenv("REDIS_DEFAULT_URL")
REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL")
REDIS_CELERY_URL = os.getenv("REDIS_CELERY_URL")
REDIS_STREAMS_URL = os.getenv("REDIS_STREAMS_URL")
REDIS_IDEMPOTENCY_URL = os.getenv("REDIS_IDEMPOTENCY_URL")
CHANNEL_LAYER_URL = os.getenv("CHANNEL_LAYER_URL")
CELERY_RESULT_BACKEND_URL = os.getenv("CELERY_RESULT_BACKEND")
REDIS_AUDIT_URL = os.getenv("REDIS_AUDIT_URL")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [CHANNEL_LAYER_URL]},  # Выделенная база под Channels
    },
}

# ==========================================
# ⚙️ INDUSTRIAL CELERY & RETRY POLICIES
# ==========================================
CELERY_BROKER_URL = REDIS_CELERY_URL
CELERY_RESULT_BACKEND = CELERY_RESULT_BACKEND_URL
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_ACKS_LATE = env_bool("CELERY_TASK_ACKS_LATE", True)
CELERY_WORKER_PREFETCH_MULTIPLIER = int(os.getenv("CELERY_WORKER_PREFETCH_MULTIPLIER", "1"))

EVENT_BUS_BACKEND = os.getenv("EVENT_BUS_BACKEND", "redis")
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "3650"))
DLQ_RETENTION_DAYS = int(os.getenv("DLQ_RETENTION_DAYS", "30"))
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "90"))

PLATFORM_READONLY_MODE = env_bool("PLATFORM_READONLY_MODE", False)
ENABLE_PUBLIC_API = env_bool("ENABLE_PUBLIC_API", True)
ENABLE_REALTIME = env_bool("ENABLE_REALTIME", True)
ENABLE_FINANCE_EXPORTS = env_bool("ENABLE_FINANCE_EXPORTS", True)
ENABLE_ANALYTICS = env_bool("ENABLE_ANALYTICS", True)

# ==========================================
# 📊 INSTANT OBSERVABILITY TUNING
# ==========================================
STRUCTLOG_JSON = env_bool("STRUCTLOG_JSON", True)
ENABLE_OTEL = env_bool("ENABLE_OTEL", True)

# ==========================================
# 🛡️ ИБ И ОГРАНИЧЕНИЯ ХРАНИЛИЩА
# ==========================================
CLAMAV_HOST = os.getenv("CLAMAV_HOST")
CLAMAV_PORT = int(os.getenv("CLAMAV_PORT", "3310"))
CLAMAV_TIMEOUT = int(os.getenv("CLAMAV_TIMEOUT", "120"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))

EXPECTED_GATEWAY_TOKEN_BYTES = os.getenv("EXPECTED_GATEWAY_TOKEN", "").encode("utf-8")
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN")
TRUSTED_SERVICE_NETWORK = os.getenv("TRUSTED_SERVICE_NETWORK", "127.0.0.1/32")

IDEMPOTENCY_TTL_SECONDS = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "86400"))
IDEMPOTENCY_PROCESSING_TTL_SECONDS = int(os.getenv("IDEMPOTENCY_PROCESSING_TTL_SECONDS", "60"))
DLQ_STREAM_NAME = os.getenv("DLQ_STREAM_NAME", "infrastructure:dlq:stream")
FEATURE_FLAGS_PREFIX = os.getenv("FEATURE_FLAGS_PREFIX", "flags:")
PUBLIC_URL_PREFIXES = os.getenv("PUBLIC_URL_PREFIXES", "/public/,/health/,/metrics/").split(",")
