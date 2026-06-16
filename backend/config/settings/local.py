# backend/config/settings/local.py
import os

import redis

from apps.common.security.secrets import get_secret

from .base import *  # noqa: F401, F403

ROOT_URLCONF = "config.urls"


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


DEBUG = _env_bool("DEBUG", default=True)

# ===========================================================================
# DATABASE
# ===========================================================================

DB_PASSWORD = get_secret(
    "POSTGRES_PASSWORD",
    secret_file_env="DATABASE_PASSWORD_FILE",
    default="dev_root_secure_postgres_pass_2026",
)

DB_NAME = os.getenv("POSTGRES_DB", "complect_db")
DB_USER = os.getenv("POSTGRES_USER", "complect_admin")
DB_PRIMARY_HOST = os.getenv("DATABASE_HOST", "postgres_primary")
DB_REPLICA_HOST = os.getenv("DATABASE_REPLICA_HOST", "postgres_replica")
DB_PORT = os.getenv("DATABASE_PORT", "5432")

_DB_OPTIONS = {
    "options": (
        "-c statement_timeout=30000 "
        "-c lock_timeout=10000 "
        "-c idle_in_transaction_session_timeout=15000"
    ),
}

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": DB_NAME,
        "USER": DB_USER,
        "PASSWORD": DB_PASSWORD,
        "HOST": DB_PRIMARY_HOST,
        "PORT": DB_PORT,
        "CONN_MAX_AGE": 600,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": _DB_OPTIONS,
    },
    "replica": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": DB_NAME,
        "USER": DB_USER,
        "PASSWORD": DB_PASSWORD,
        "HOST": DB_REPLICA_HOST,
        "PORT": DB_PORT,
        "CONN_MAX_AGE": 600,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": _DB_OPTIONS,
        "TEST": {"MIRROR": "default"},
    },
}

POSTGRES_REPLICA_ENABLED = _env_bool("POSTGRES_REPLICA_ENABLED", default=True)
POSTGRES_REPLICA_MAX_LAG = float(os.getenv("POSTGRES_REPLICA_MAX_LAG", "1.0"))

SECRET_KEY = os.getenv("SECRET_KEY", "local-dev-insecure-key-change-me-32chars")
ALLOWED_HOSTS = ["*"]

REDIS_DEFAULT_URL = os.getenv("REDIS_DEFAULT_URL", "redis://redis:6379/0")
REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", "redis://redis:6379/1")
REDIS_CELERY_URL = os.getenv("REDIS_CELERY_URL", "redis://redis:6379/2")
REDIS_STREAMS_URL = os.getenv("REDIS_STREAMS_URL", "redis://redis:6379/3")
REDIS_IDEMPOTENCY_URL = os.getenv("REDIS_IDEMPOTENCY_URL", "redis://redis:6379/4")
CHANNEL_LAYER_URL = os.getenv("CHANNEL_LAYER_URL", "redis://redis:6379/5")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/6")
REDIS_AUDIT_URL = os.getenv("REDIS_AUDIT_URL", "redis://redis:6379/7")

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

CELERY_BROKER_URL = REDIS_CELERY_URL
ENABLE_OTEL = _env_bool("ENABLE_OTEL", default=False)

REDIS_CLIENT = redis.Redis.from_url(REDIS_DEFAULT_URL, decode_responses=True)

EXPECTED_GATEWAY_TOKEN = os.getenv("EXPECTED_GATEWAY_TOKEN", "complect_internal_secure_mesh_token_2026")

# Public site / CMS (ADR-0023). Dev defaults — override via env in production.
# Use the first real tenant UUID from the DB, or keep this placeholder for fresh installs.
PUBLIC_SITE_TENANT_ID = os.getenv(
    "PUBLIC_SITE_TENANT_ID",
    "00000000-0000-0000-0000-000000000001",
)
# Cloudflare Turnstile always-pass test secret (dev only, not production).
CF_TURNSTILE_SECRET_KEY = os.getenv(
    "CF_TURNSTILE_SECRET_KEY",
    "1x0000000000000000000000000000000AA",
)

CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "idempotency-key",
]

