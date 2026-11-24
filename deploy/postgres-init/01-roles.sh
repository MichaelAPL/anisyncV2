#!/usr/bin/env bash
# Runs once, when the Postgres accessory starts on an empty data directory (docker-entrypoint-initdb.d).
# Creates the three roles AniSyncHub uses so that no long-running app container holds schema rights:
#   anisync_owner   owns the schema and runs migrations (only the pre-deploy migrator uses it)
#   anisync_app     what the app connects as: read and write data, no DDL
#   anisync_backup  read-only, for the nightly dump
# Passwords come from the accessory's secret env and are passed as psql variables, never written to a file.
set -euo pipefail

: "${ANISYNC_OWNER_PASSWORD:?}" "${ANISYNC_APP_PASSWORD:?}" "${ANISYNC_BACKUP_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" \
  -v owner_pw="$ANISYNC_OWNER_PASSWORD" \
  -v app_pw="$ANISYNC_APP_PASSWORD" \
  -v backup_pw="$ANISYNC_BACKUP_PASSWORD" <<'SQL'
CREATE ROLE anisync_owner LOGIN PASSWORD :'owner_pw';
-- Old and new containers overlap during a deploy, so leave room for two sets of pools.
CREATE ROLE anisync_app LOGIN PASSWORD :'app_pw' CONNECTION LIMIT 60;
CREATE ROLE anisync_backup LOGIN PASSWORD :'backup_pw' CONNECTION LIMIT 2;
GRANT pg_read_all_data TO anisync_backup;

REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO anisync_owner, anisync_app, anisync_backup;

ALTER SCHEMA public OWNER TO anisync_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO anisync_app;

-- Everything the owner creates later (migrations, the Procrastinate schema) is readable and writable by the app.
ALTER DEFAULT PRIVILEGES FOR ROLE anisync_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anisync_app;
ALTER DEFAULT PRIVILEGES FOR ROLE anisync_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO anisync_app;
ALTER DEFAULT PRIVILEGES FOR ROLE anisync_owner IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO anisync_app;
SQL
