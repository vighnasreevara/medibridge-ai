#!/bin/sh
# Runs once, when the data volume is first initialised.
# Creates the least-privilege application role. The owner role (POSTGRES_USER) runs migrations;
# the app role only receives the grants that migrations give it (no UPDATE/DELETE on audit tables).
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_user="$MEDIBRIDGE_APP_USER" -v app_password="$MEDIBRIDGE_APP_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT', :'app_user', :'app_password')
\gexec
REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user')
\gexec
SQL
