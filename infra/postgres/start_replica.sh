#!/usr/bin/env bash
set -euo pipefail
if [[ ! -s /var/lib/postgresql/data/PG_VERSION ]]; then
  if [[ -n "$(ls -A /var/lib/postgresql/data)" ]]; then
    echo 'Replica directory is incomplete; refusing to erase existing data' >&2
    exit 1
  fi
  export PGPASSWORD
  PGPASSWORD=$(cat /run/complect-secrets/replication_password)
  pg_basebackup -h postgres_primary -p 5432 -D /var/lib/postgresql/data -U replicator -R -X stream
  unset PGPASSWORD
fi
exec docker-entrypoint.sh postgres -c hot_standby=on
