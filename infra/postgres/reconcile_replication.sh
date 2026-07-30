#!/usr/bin/env bash
set -euo pipefail

export PGPASSWORD
PGPASSWORD="$(cat /run/secrets/db_root_password)"
replication_password="$(cat /run/secrets/replication_password)"

psql \
  --host=postgres_primary \
  --username=complect_admin \
  --dbname=complect_db \
  --set=ON_ERROR_STOP=1 \
  --set=replication_password="$replication_password" <<'EOSQL'
ALTER ROLE replicator
WITH REPLICATION LOGIN
PASSWORD :'replication_password';
EOSQL

unset PGPASSWORD replication_password
