#!/usr/bin/env bash
# Run from the repository root. All Python and services run in Docker.
set -euo pipefail
test_run_id=$(cat /proc/sys/kernel/random/uuid)
test_redis_name="complect-iam-test-${test_run_id}"
docker run --rm -d --name "$test_redis_name" --network erp_network redis:7.2-alpine >/dev/null
trap 'docker rm -f "$test_redis_name" >/dev/null' EXIT
docker compose -f docker-compose.dev.yml exec -T \
  -e REDIS_DEFAULT_URL="redis://${test_redis_name}:6379/0" \
  -e REDIS_CACHE_URL="redis://${test_redis_name}:6379/1" \
  -e REDIS_CELERY_URL="redis://${test_redis_name}:6379/2" \
  -e REDIS_STREAMS_URL="redis://${test_redis_name}:6379/3" \
  -e REDIS_IDEMPOTENCY_URL="redis://${test_redis_name}:6379/4" \
  -e REDIS_AUDIT_URL="redis://${test_redis_name}:6379/7" \
  backend python - "$@" <<'PY'
import os
import sys
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
import django
django.setup()
from django.conf import settings
from django.db import connections

database = "test_iam_fence_" + uuid.uuid4().hex
default = connections["default"]
params = default.get_connection_params()
params["dbname"] = "postgres"
admin = default.Database.connect(**params)
admin.autocommit = True
with admin.cursor() as cursor:
    cursor.execute("SET statement_timeout = '60s'")
    cursor.execute("SET lock_timeout = '10s'")
    cursor.execute(f'CREATE DATABASE "{database}"')
print("DISPOSABLE_DATABASE", database, flush=True)
try:
    # Even session-level fixtures opening default before django_db_setup can
    # touch only this disposable database. No fixture DDL reaches development.
    connections.close_all()
    for config in settings.DATABASES.values():
        config["NAME"] = database
        # Django TEST.MIRROR copies only NAME. Pin every test alias to primary;
        # the development replica is asynchronous and would make assertions stale.
        config["HOST"] = settings.DATABASES["default"]["HOST"]
        config["PORT"] = settings.DATABASES["default"]["PORT"]
    settings.DATABASES["default"]["TEST"]["NAME"] = database
    import pytest
    args = sys.argv[1:] or ["apps/users/tests/test_identity_audit_fence.py"]
    result = pytest.main([*args, "-q", "--reuse-db", "--tb=short"])
finally:
    connections.close_all()
    with admin.cursor() as cursor:
        cursor.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
    admin.close()
    print("DISPOSED_DATABASE", database, flush=True)
raise SystemExit(result)
PY
