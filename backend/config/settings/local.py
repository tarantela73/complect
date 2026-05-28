import os
from pathlib import Path

# --- 1. ПЕРВЫЙ СЛОЙ ОРКЕСТРАЦИИ (Загрузка ядра Django) ---
from .base import *  # ruff: noqa: E402, F403, F401

# --- 2. CONFIGURATION & SRE LOGIC ---
ROOT_URLCONF = "config.urls"
DEBUG = os.getenv("DEBUG", "True").lower() in ("1", "true", "yes", "on")


def read_secret_file(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if p.is_file():
        return p.read_text().strip()
    return None


def get_db_password() -> str:
    """Безопасно считывает сырой пароль из Docker Secrets периметра."""
    secret_path = (
        os.getenv("DATABASE_PASSWORD_FILE") or os.getenv("DB_PASSWORD_FILE") or "/run/secrets/db_root_password"
    )
    password = read_secret_file(secret_path)
    if password:
        return password
    return os.getenv("POSTGRES_PASSWORD", "dev_root_secure_postgres_pass_2026")


# --- 3. ENTERPRISE ZERO-TRUST DATABASE CONFIGURATION ---
DB_NAME = os.getenv("POSTGRES_DB", "complect_db")
DB_USER = os.getenv("POSTGRES_USER", "complect_admin")
DB_HOST_PRIMARY = os.getenv("DATABASE_HOST", "postgres_primary")
DB_HOST_REPLICA = os.getenv("DATABASE_REPLICA_HOST", "postgres_replica")
DB_PORT = os.getenv("DATABASE_PORT", "5432")
DB_PASSWORD = get_db_password()


def base_db_config(host: str) -> dict:
    return {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": DB_NAME,
        "USER": DB_USER,
        "PASSWORD": DB_PASSWORD,
        "HOST": host,
        "PORT": DB_PORT,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "options": ("-c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=15000")
        },
    }


DATABASES = {
    "default": base_db_config(DB_HOST_PRIMARY),
    "replica": base_db_config(DB_HOST_REPLICA),
}

POSTGRES_REPLICA_ENABLED = os.getenv("POSTGRES_REPLICA_ENABLED", "True").lower() in ("1", "true", "yes", "on")
POSTGRES_REPLICA_MAX_LAG = float(os.getenv("POSTGRES_REPLICA_MAX_LAG", "1.0"))

SECRET_KEY = os.getenv("SECRET_KEY", "local-dev-insecure-key-change-me")
ALLOWED_HOSTS = ["*"]

# Redis из env
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

SECRET_KEY = os.getenv("SECRET_KEY", "local-dev-insecure-key-change-me-32chars")
ALLOWED_HOSTS = ["*"]

# Redis
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
