#!/usr/bin/env bash
# Run from the repository root. All Python and services run in Docker.
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$project_root"
if [[ "${GITHUB_ACTIONS:-}" == "true" && "${CI:-}" == "true" ]]; then
  project=${COMPOSE_PROJECT_NAME:-}
  subnet=${COMPLECT_DOCKER_SUBNET:-}
  if [[ ! "$project" =~ ^iam-ci-[0-9]+-[0-9]+$ ]]; then
    echo "Invalid IAM CI Compose project; refusing arbitrary Compose targeting" >&2
    exit 1
  fi
  if [[ "$subnet" != "198.19.0.0/24" ]]; then
    echo "Invalid IAM CI disposable subnet; refusing arbitrary network targeting" >&2
    exit 1
  fi
  compose=(docker compose -f docker-compose.dev.yml)
  backend_id=$("${compose[@]}" ps -q backend)
  test -n "$backend_id" || { echo "IAM CI backend is not running" >&2; exit 1; }
  ownership=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}:{{ index .Config.Labels "com.docker.compose.service" }}' "$backend_id")
  if [[ "$ownership" != "$project:backend" ]]; then
    echo "Backend does not belong to the IAM CI Compose project" >&2
    exit 1
  fi
  expected_network="${project}_erp_network"
  backend_network=$(docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$backend_id")
  if [[ "$backend_network" != "$expected_network" ]]; then
    echo "Backend is not attached only to the expected IAM CI project network" >&2
    exit 1
  fi
  network_ownership=$(docker network inspect --format '{{ index .Labels "com.docker.compose.project" }}:{{ index .Labels "com.docker.compose.network" }}' "$expected_network")
  if [[ "$network_ownership" != "$project:erp_network" ]]; then
    echo "Expected IAM CI project network has invalid Compose ownership" >&2
    exit 1
  fi
else
  compose=(python3 -B infra/scripts/dev_environment.py compose)
  backend_id=$("${compose[@]}" ps -q backend)
fi
test -n "$backend_id" || { echo 'Run make bootstrap first' >&2; exit 1; }
project_network=$(docker inspect --format '{{range $name, $network := .NetworkSettings.Networks}}{{$name}}{{end}}' "$backend_id")
test -n "$project_network"
test_run_id=$(cat /proc/sys/kernel/random/uuid)
test_redis_name="complect-iam-test-${test_run_id}"
docker run --rm -d --name "$test_redis_name" --network "$project_network" redis:7.2-alpine@sha256:dfa18828cbc07b3ae6a95ec7343f6c214fdee2d836197b4be8e9904420762cd8 >/dev/null
trap 'docker rm -f "$test_redis_name" >/dev/null' EXIT
"${compose[@]}" exec -T \
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
