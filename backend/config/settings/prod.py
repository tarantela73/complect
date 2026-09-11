import json
import os

from apps.common.security.secrets import get_secret

from .base import *  # noqa: F401, F403

DEBUG = False
ENVIRONMENT = "production"

RBAC_ENFORCEMENT_MODE = "ENFORCE"

SECRET_KEY = os.environ["SECRET_KEY"]
ALLOWED_HOSTS = os.environ["ALLOWED_HOSTS"].split(",")
EXPECTED_GATEWAY_TOKEN = os.environ["EXPECTED_GATEWAY_TOKEN"]

IAM_DEFERRED_MERGE_EXECUTION_ENABLED = os.getenv(
    "IAM_DEFERRED_MERGE_EXECUTION_ENABLED",
    "false",
).lower() in {"1", "true", "yes", "on"}
IAM_DEFERRED_MERGE_COVERAGE_DIGEST = os.getenv("IAM_DEFERRED_MERGE_COVERAGE_DIGEST", "")
IAM_DEFERRED_MERGE_COVERAGE_KEY_ID = os.getenv("IAM_DEFERRED_MERGE_COVERAGE_KEY_ID", "")
IAM_DEFERRED_MERGE_COVERAGE_SIGNATURE = os.getenv("IAM_DEFERRED_MERGE_COVERAGE_SIGNATURE", "")
IAM_DEFERRED_MERGE_COVERAGE_COMMIT = os.getenv("IAM_DEFERRED_MERGE_COVERAGE_COMMIT", "")
IAM_DEFERRED_MERGE_COVERAGE_APPROVED_AT = os.getenv("IAM_DEFERRED_MERGE_COVERAGE_APPROVED_AT", "")
IAM_DEFERRED_MERGE_COVERAGE_EXPIRES_AT = os.getenv("IAM_DEFERRED_MERGE_COVERAGE_EXPIRES_AT", "")
IAM_DEFERRED_MERGE_COVERAGE_NONCE = os.getenv("IAM_DEFERRED_MERGE_COVERAGE_NONCE", "")
BUILD_COMMIT = os.getenv("BUILD_COMMIT", "")
IAM_RECONCILIATION_APPROVAL_PUBLIC_KEYS = json.loads(
    os.getenv("IAM_RECONCILIATION_APPROVAL_PUBLIC_KEYS", "{}"),
)
IAM_DEFERRED_MERGE_COVERAGE_PUBLIC_KEYS = json.loads(
    os.getenv("IAM_DEFERRED_MERGE_COVERAGE_PUBLIC_KEYS", "{}"),
)
IAM_MFA_CURRENT_KEY_ID = os.getenv("IAM_MFA_CURRENT_KEY_ID", "")
IAM_MFA_ENCRYPTION_KEYS = json.loads(os.getenv("IAM_MFA_ENCRYPTION_KEYS", "{}"))

DB_PASSWORD = get_secret(
    "POSTGRES_PASSWORD",
    secret_file_env="DATABASE_PASSWORD_FILE",
)
DB_NAME = os.environ["POSTGRES_DB"]
DB_USER = os.environ["POSTGRES_USER"]
DB_PRIMARY_HOST = os.environ["DATABASE_HOST"]
DB_REPLICA_HOST = os.environ["DATABASE_REPLICA_HOST"]
DB_PORT = os.getenv("DATABASE_PORT", "5432")

_DB_OPTIONS = {
    "options": ("-c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=15000"),
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

POSTGRES_REPLICA_ENABLED = True
POSTGRES_REPLICA_MAX_LAG = float(os.getenv("POSTGRES_REPLICA_MAX_LAG", "1.0"))

REDIS_DEFAULT_URL = os.environ["REDIS_DEFAULT_URL"]
REDIS_CACHE_URL = os.environ["REDIS_CACHE_URL"]
REDIS_CELERY_URL = os.environ["REDIS_CELERY_URL"]
REDIS_STREAMS_URL = os.environ["REDIS_STREAMS_URL"]
REDIS_IDEMPOTENCY_URL = os.environ["REDIS_IDEMPOTENCY_URL"]
CHANNEL_LAYER_URL = os.environ["CHANNEL_LAYER_URL"]
CELERY_RESULT_BACKEND = os.environ["CELERY_RESULT_BACKEND"]
REDIS_AUDIT_URL = os.environ["REDIS_AUDIT_URL"]

import redis  # noqa: E402

REDIS_CLIENT = redis.Redis.from_url(REDIS_DEFAULT_URL, decode_responses=True)

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

PUBLIC_SITE_TENANT_ID = os.environ["PUBLIC_SITE_TENANT_ID"]
CF_TURNSTILE_SECRET_KEY = os.environ["CF_TURNSTILE_SECRET_KEY"]

CORS_ALLOWED_ORIGINS = os.environ["CORS_ALLOWED_ORIGINS"].split(",")
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

DADATA_API_KEY = os.environ["DADATA_API_KEY"]
MAP_ZOUIT_ENABLED = True
ENABLE_OTEL = True
