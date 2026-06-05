from pathlib import Path

from kombu import Exchange, Queue

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
    "django.contrib.postgres",
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
    "apps.files",
    "apps.realtime",
    "apps.integration",
    "apps.notifications",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ===========================================================================
# MIDDLEWARE
# ===========================================================================
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
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

# rule 4.15 — queue topology (6 queues, high/normal/low priority tiers)
_default_exchange = Exchange("default", type="direct")
CELERY_TASK_QUEUES = (
    Queue("default",       _default_exchange,         routing_key="default"),
    Queue("outbox",        Exchange("outbox"),         routing_key="outbox"),
    Queue("notifications", Exchange("notifications"),  routing_key="notifications"),
    Queue("finance",       Exchange("finance"),        routing_key="finance"),
    Queue("files",         Exchange("files"),          routing_key="files"),
    Queue("retention",     Exchange("retention"),      routing_key="retention"),
)
CELERY_TASK_DEFAULT_QUEUE = "default"

# rule 4.15 / ADR-0003 — canonical Beat schedule, synced to DB by DatabaseScheduler on startup
CELERY_BEAT_SCHEDULE = {
    "outbox-polling-dispatcher": {
        "task": "apps.common.tasks.outbox_dispatcher.run_outbox_polling_dispatcher_task",
        "schedule": 10.0,  # every 10 seconds
        "options": {"queue": "outbox"},
    },
    "chain-completeness-monitor": {
        "task": "apps.common.tasks.chain_completeness_monitor.run_chain_completeness_monitor_task",
        "schedule": 60.0,  # every 60 seconds
        "options": {"queue": "outbox"},
    },
    "outbox-archive": {
        "task": "apps.common.tasks.retention_manager.archive_processed_outbox_records_task",
        "schedule": 86400.0,  # daily
        "options": {"queue": "retention"},
    },
    "causal-pair-slo-monitor": {
        "task": "apps.common.tasks.causal_pair_monitor.check_causal_pair_slo_task",
        "schedule": 60.0,  # every 60 seconds — observability only, no repair
        "options": {"queue": "notifications"},
    },
    "files-recovery-scanner": {
        "task": "apps.files.tasks.recovery_scanner.run_files_recovery_scan_task",
        "schedule": 60.0,  # every 60 seconds; skips internally if CB still open
        "options": {"queue": "files"},
    },
    "files-system-control-policy": {
        "task": "apps.files.tasks.policy_evaluator.run_system_control_policy_task",
        "schedule": 30.0,  # every 30 seconds; self-healing via PARAM_TTL on batch_size key
        "options": {"queue": "files"},
    },
    "notifications-consumer": {
        "task": "apps.notifications.consumer.notifications_consumer.run_notifications_consumer_task",
        "schedule": 10.0,  # every 10 seconds; same cadence as outbox dispatcher
        "options": {"queue": "notifications"},
    },
}

# Tunable per-environment; dispatcher marks records permanently failed after this many retries.
OUTBOX_MAX_PUBLISH_RETRIES = 5
# Exponential backoff: delay = min(BASE × 2^retry_count, CAP) + jitter(10%)
OUTBOX_BACKOFF_BASE_SECONDS = 30
OUTBOX_BACKOFF_MAX_SECONDS = 300
# Coalescing: at most one wake-up dispatch per this window (prevents apply_async storm on bulk writes).
OUTBOX_WAKEUP_COALESCE_TTL = 2
# Inflight guard TTL: if the dispatcher task crashes without cleanup, this is the
# silence window before the next write can trigger a new wake-up. Must exceed max task duration.
OUTBOX_INFLIGHT_GUARD_TTL = 30
# Consumer lease TTL (ADR-0011): default ownership window. reclaim() can take over
# after expiry. Set per-consumer via lease_ttl= arg for long-running handlers.
CONSUMER_LEASE_TTL_SECONDS = 300

# Outbox retention (ADR-0013).
# Normal-class records archived after this many days post-processing.
OUTBOX_ARCHIVE_WINDOW_DAYS: int = 30

# Aggregate ordering (ADR-0012).
# Mode A: aggregate types that require serial dispatch (delta semantics).
# Events for these types go to per-slot Redis Streams processed by a
# single-concurrency worker. Misclassifying delta→Mode B = silent data corruption.
SERIAL_DISPATCH_AGGREGATE_TYPES: frozenset[str] = frozenset({
    "finance.transaction",
    "finance.balance",
})
# Hash sharding for Mode A slots: hash(aggregate_id) % N. Limits Redis Stream
# proliferation while preserving per-aggregate FIFO within a slot.
OUTBOX_SERIAL_ROUTING_SLOTS: int = 256

# Chain Completeness SLO (ADR-0017).
# Records must have acked ProcessedEvent within this many seconds of OutboxRecord creation.
CHAIN_COMPLETENESS_SLO_WINDOW_SECONDS = 300
# Observation slack: how far back beyond the SLO deadline to look per measurement tick.
# Prevents false positives from scheduler jitter; must be >= beat interval.
CHAIN_COMPLETENESS_OBSERVATION_SLACK_SECONDS = 300
# SLO target (99.9%): fraction of chains that must be complete within the SLO window.
CHAIN_COMPLETENESS_SLO_TARGET = 0.999

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
# RBAC
# ===========================================================================
# OFF       — bypass all checks (use during initial rollout or in tests that don't need RBAC)
# LOG_ONLY  — evaluate and log denials, never block (default; safe for gradual rollout)
# ENFORCE   — raise PermissionDenied on denial
RBAC_ENFORCEMENT_MODE = "LOG_ONLY"
# Per-user permission set cache TTL in seconds. Short enough for security, long for perf.
RBAC_POLICY_CACHE_TTL = 60

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
# Unix socket path mounted from the ClamAV container (docker-compose: /var/run/clamav).
CLAMAV_SOCKET_PATH: str = "/var/run/clamav/clamd.ctl"
# Hard timeout for a single scan I/O round-trip. Timeout → ScanResult.ERROR → QUARANTINED.
CLAMAV_SCAN_TIMEOUT_MS: int = 120_000

S3_QUARANTINE_BUCKET = "complect-quarantine"
S3_PRODUCTION_BUCKET = "complect-production"

# StorageBackend selector: "STUB" (dev/test) or "S3" (production).
FILES_STORAGE_BACKEND: str = "STUB"
AWS_S3_BUCKET_NAME: str = S3_PRODUCTION_BUCKET
AWS_S3_REGION_NAME: str = "us-east-1"
# Presigned URL TTLs (seconds). Presigned PUT must complete before upload TTL expires.
FILES_PRESIGNED_UPLOAD_TTL: int = 900    # 15 min
FILES_PRESIGNED_DOWNLOAD_TTL: int = 3600  # 1 hr

# ScanEngine selector: "STUB" (dev/test) or "CLAMAV" (production).
FILES_SCAN_ENGINE: str = "STUB"

# Circuit breaker thresholds for ClamAV unix socket.
# FAILURE_THRESHOLD: consecutive ERROR results before opening the circuit.
# RECOVERY_TTL:      seconds the circuit stays OPEN before probing again.
CLAMAV_CB_FAILURE_THRESHOLD: int = 5
CLAMAV_CB_RECOVERY_TTL: int = 60

# Batch size for the CB recovery re-scan task.
FILES_RECOVERY_SCAN_BATCH_SIZE: int = 50

# System control policy — adaptive batch sizing.
FILES_CONTROL_BATCH_SIZE_DEFAULT: int = 100    # nominal
FILES_CONTROL_BATCH_SIZE_REDUCED: int = 25     # scan latency elevated
FILES_CONTROL_BATCH_SIZE_THROTTLED: int = 10   # CB rate or backlog high
FILES_CONTROL_CB_RATE_THRESHOLD: int = 3       # CB opens in window before throttle
FILES_CONTROL_CB_RATE_WINDOW_SECONDS: int = 300
FILES_CONTROL_SCAN_LATENCY_THRESHOLD_MS: float = 5000.0  # per-file ms EWMA
FILES_CONTROL_BACKLOG_THROTTLE_THRESHOLD: int = 100
FILES_CONTROL_SCAN_EWMA_ALPHA: float = 0.3     # α for scan_ms EWMA
FILES_CONTROL_SCAN_EWMA_TTL: int = 600         # EWMA key TTL (10 min)
FILES_CONTROL_PARAM_TTL: int = 120             # batch_size key TTL; expires → default

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
