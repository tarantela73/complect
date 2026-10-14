#!/bin/bash
set -euo pipefail

# The official entrypoint runs this after the database owner exists. The
# runtime password is the checkout-local secret materialized for the app.
runtime_password="$(cat /run/complect-secrets/bootstrap_app_postgres_password)"
runtime_role="${POSTGRES_APP_RUNTIME_ROLE:-complect_app_runtime}"
runtime_login="${POSTGRES_APP_RUNTIME_LOGIN:-complect_app_local}"

psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --set=ON_ERROR_STOP=1 \
  --set=runtime_role="$runtime_role" \
  --set=runtime_login="$runtime_login" \
  --set=runtime_password="$runtime_password" <<'EOSQL'
SELECT format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION', :'runtime_role') \gexec
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION',
  :'runtime_login', :'runtime_password'
) \gexec
SELECT format('GRANT %I TO %I', :'runtime_role', :'runtime_login') \gexec
SELECT bool_or(rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR
               rolcreaterole OR rolreplication) AS unsafe
  FROM pg_roles WHERE rolname = :'runtime_role' \gset group_
\if :group_unsafe
\quit 1
\endif
SELECT bool_or(NOT rolcanlogin OR NOT rolinherit OR rolsuper OR rolbypassrls OR
               rolcreatedb OR rolcreaterole OR rolreplication) AS unsafe
  FROM pg_roles WHERE rolname = :'runtime_login' \gset login_
\if :login_unsafe
\quit 1
\endif
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'runtime_role') \gexec
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'runtime_role') \gexec
EOSQL
