from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Переопределяются в local.py / prod.py
SECRET_KEY = "django-insecure-replace-in-env"
DEBUG = False
ALLOWED_HOSTS: list[str] = []

# ===========================================================================
# INSTALLED APPS
# ===========================================================================
DJANGO_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django.contrib.gis",
]

THIRD_PARTY_APPS = [
    "ninja",
    "channels",
    "django_celery_beat",
    "corsheaders",
]

LOCAL_APPS = [
    "apps.common",
    "apps.users",
    "apps.contracts",
    "apps.contragents",
    "apps.finance",
    "apps.objects",
    "apps.realtime",
    "apps.integration",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ===========================================================================
# MIDDLEWARE
# ===========================================================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.middleware.context_isolation.ContextIsolationMiddleware",
    "apps.common.middleware.kill_switch.SreGlobalKillSwitchMiddleware",  
    "apps.common.tenancy.middleware.AsgiTenantEnforcementMiddleware",
    "apps.common.middleware.idempotency.IdempotencyEnforcementMiddleware",
    "apps.common.middleware.lifecycle.read_your_own_writes.ReadYourOwnWritesStickyMiddleware",
    "apps.common.middleware.lifecycle.lifecycle.StructlogTracingContextMiddleware",
    "apps.common.middleware.lifecycle.lifecycle.PostgresRlsEnforcementMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

# ===========================================================================
# ROUTING
# ===========================================================================
ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"

# ===========================================================================
# TEMPLATES
# ===========================================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

# ===========================================================================
# DATABASE ROUTER
# ===========================================================================
DATABASE_ROUTERS = ["apps.common.db.router.EnterpriseClusterRouter"]

# ===========================================================================
# AUTH
# ===========================================================================
AUTH_USER_MODEL = "users.User"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
]

# ===========================================================================
# REDIS — базовые URL, переопределяются в local.py/prod.py из env
# ===========================================================================
REDIS_DEFAULT_URL = "redis://redis:6379/0"
REDIS_CACHE_URL = "redis://redis:6379/1"
REDIS_CELERY_URL = "redis://redis:6379/2"
REDIS_STREAMS_URL = "redis://redis:6379/3"
REDIS_IDEMPOTENCY_URL = "redis://redis:6379/4"
CHANNEL_LAYER_URL = "redis://redis:6379/5"
CELERY_RESULT_BACKEND = "redis://redis:6379/6"
REDIS_AUDIT_URL = "redis://redis:6379/7"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [CHANNEL_LAYER_URL]},
    }
}

# ===========================================================================
# CELERY
# ===========================================================================
CELERY_BROKER_URL = REDIS_CELERY_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_MAX_RETRIES = 5

# ===========================================================================
# INTERNATIONALIZATION
# ===========================================================================
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ===========================================================================
# STATIC / MEDIA
# ===========================================================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ===========================================================================
# SECURITY
# ===========================================================================
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# ===========================================================================
# UPLOAD LIMITS
# ===========================================================================
MAX_UPLOAD_SIZE_MB = 50
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800
FILE_UPLOAD_MAX_MEMORY_SIZE = 52428800

# ===========================================================================
# SRE / MIDDLEWARE CONSTANTS
# ===========================================================================
IDEMPOTENCY_TTL_SECONDS = 86400
IDEMPOTENCY_PROCESSING_TTL_SECONDS = 60
SRE_KILL_SWITCH_CACHE_KEY = "sre:kill_switch:domains"
FEATURE_FLAGS_PREFIX = "flags:"
DLQ_STREAM_NAME = "infrastructure:dlq:stream"
PUBLIC_URL_PREFIXES = ["/public/", "/health/", "/metrics/"]

# ===========================================================================
# RLS
# ===========================================================================
ENABLE_RLS = True
RLS_NAMESPACE = "app.current_tenant_id"
POSTGRES_REPLICA_ENABLED = True
POSTGRES_REPLICA_MAX_LAG = 1.0

# ===========================================================================
# AUDIT / RETENTION
# ===========================================================================
AUDIT_RETENTION_DAYS = 3650
DLQ_RETENTION_DAYS = 30
LOG_RETENTION_DAYS = 90
AUDIT_LOG_HASH_ALGORITHM = "sha256"

# ===========================================================================
# FILE PIPELINE / CLAMAV / S3
# ===========================================================================
CLAMAV_HOST = "clamav"
CLAMAV_PORT = 3310
CLAMAV_TIMEOUT = 120
S3_QUARANTINE_BUCKET = "complect-quarantine"
S3_PRODUCTION_BUCKET = "complect-production"

# ===========================================================================
# LIVEKIT
# ===========================================================================
LIVEKIT_URL = ""
LIVEKIT_API_KEY = ""
LIVEKIT_API_SECRET = ""

# ===========================================================================
# OBSERVABILITY
# ===========================================================================
ENABLE_OTEL = False
OTEL_SERVICE_NAME = "complect-backend"
OTEL_EXPORTER_OTLP_ENDPOINT = "http://otel_collector:4317"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
