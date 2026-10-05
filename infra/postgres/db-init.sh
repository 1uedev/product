#!/bin/sh
# Idempotent role/database bootstrap. Runs as the PostgreSQL superuser in a short-lived
# container (service "db-init"). It never drops anything. Passwords are re-applied on every
# run so that rotated secrets take effect. Table-level privileges are granted by the Alembic
# migrations, not here (see docs/architecture/grant-matrix.md).
set -eu

: "${PGHOST:?}" "${PGUSER:?}" "${PGPASSWORD:?}"
: "${DB_NAME:=decision_evidence}"
: "${KEYCLOAK_DB_NAME:=keycloak}" "${KEYCLOAK_DB_USER:=keycloak}"
: "${DB_MIGRATOR_PASSWORD:?}" "${DB_AUTH_PASSWORD:?}" "${DB_APP_PASSWORD:?}"
: "${DB_WORKER_PASSWORD:?}" "${DB_OUTBOX_PASSWORD:?}" "${KEYCLOAK_DB_PASSWORD:?}"

psql_admin() { psql -v ON_ERROR_STOP=1 -X -q "$@"; }

# Roles. LOGIN roles for runtime are plain roles: no SUPERUSER, no CREATEDB, no CREATEROLE, no BYPASSRLS.
psql_admin -d postgres \
  -v migrator_pw="$DB_MIGRATOR_PASSWORD" -v auth_pw="$DB_AUTH_PASSWORD" -v app_pw="$DB_APP_PASSWORD" \
  -v worker_pw="$DB_WORKER_PASSWORD" -v outbox_pw="$DB_OUTBOX_PASSWORD" \
  -v kc_user="$KEYCLOAK_DB_USER" -v kc_pw="$KEYCLOAK_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', r)
  FROM unnest(ARRAY['de_migrator','de_auth','de_app','de_worker','de_outbox', :'kc_user']) AS r
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
\gexec
SELECT 'CREATE ROLE de_recovery NOLOGIN NOSUPERUSER NOBYPASSRLS'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'de_recovery')
\gexec
-- the migrator must be able to hand function ownership to the NOLOGIN recovery role (SET ROLE, no inheritance)
GRANT de_recovery TO de_migrator WITH INHERIT FALSE, SET TRUE;
SELECT format('ALTER ROLE %I PASSWORD %L', r, p)
  FROM (VALUES ('de_migrator', :'migrator_pw'), ('de_auth', :'auth_pw'), ('de_app', :'app_pw'),
               ('de_worker', :'worker_pw'), ('de_outbox', :'outbox_pw'), (:'kc_user', :'kc_pw')) AS v(r, p)
\gexec
SQL

psql_admin -d postgres -v dbname="$DB_NAME" -v kc_db="$KEYCLOAK_DB_NAME" -v kc_user="$KEYCLOAK_DB_USER" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER de_migrator', :'dbname')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'kc_db', :'kc_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'kc_db')
\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'dbname') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'kc_db') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO de_auth, de_app, de_worker, de_outbox', :'dbname') \gexec
SQL

# Lock down the public schema of the application database.
psql_admin -d "$DB_NAME" <<'SQL'
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
echo "db-init: roles and databases are in place"
