# This file is sourced by the official Postgres entrypoint so docker_process_sql
# is available without placing the replication password in tracked SQL or Compose.
replication_password="$(cat /run/secrets/replication_password)"
docker_process_sql --set=replication_password="$replication_password" <<'EOSQL'
CREATE ROLE replicator
WITH REPLICATION LOGIN
PASSWORD :'replication_password';
EOSQL
unset replication_password
