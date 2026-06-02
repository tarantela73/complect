# backend/config/settings/local.py
import os

import redis

from apps.common.security.secrets import get_docker_secret

from .base import *  # noqa: F401, F403

ROOT_URLCONF = "config.urls"
DEBUG = os.getenv("DEBUG", "True").lower() in ("1", "true", "yes", "on")

# ===========================================================================
# DATABASE
# ===========================================================================

DB_PASSWORD = get_docker_secret(
    "POSTGRES_PASSWORD",
    secret_file_env="DATABASE_PASSWORD_FILE",
    default="dev_root_secure_postgres_pass_2026",
)

DB_NAME = os.getenv("POSTGRES_DB", "complect_db")
DB_USER = os.getenv("POSTGRES_USER", "complect_admin")
DB_HOST = os.getenv("DATABASE_HOST", "postgres_primary")
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
        "HOST": DB_HOST,
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
    },
}

POSTGRES_REPLICA_ENABLED = os.getenv("POSTGRES_REPLICA_ENABLED", "True").lower() in ("1", "true", "yes", "on")
POSTGRES_REPLICA_MAX_LAG = float(os.getenv("POSTGRES_REPLICA_MAX_LAG", "1.0"))

SECRET_KEY = os.getenv("SECRET_KEY", "local-dev-insecure-key-change-me-32chars")
ALLOWED_HOSTS = ["*"]

REDIS_DEFAULT_URL = os.getenv("REDIS_DEFAULT_URL", "redis://redis:6379/0")
REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", "redis://redis:6379/1")
REDIS_CELERY_URL = os.getenv("REDIS_CELERY_URL", "redis://redis:6379/2")
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
ENABLE_OTEL = os.getenv("ENABLE_OTEL", "false").lower() in ("1", "true", "yes")

REDIS_CLIENT = redis.Redis.from_url(REDIS_DEFAULT_URL, decode_responses=True)

EXPECTED_GATEWAY_TOKEN = os.getenv("EXPECTED_GATEWAY_TOKEN", "complect_internal_secure_mesh_token_2026")

DATABASES["replica"]["TEST"] = {"MIRROR": "default"}
