#!/bin/sh
# 仅在全新数据库卷初始化时执行；已有卷的角色调整需管理员另行确认。
set -eu
: "${APP_DB_PASSWORD:?缺少应用角色密码}"
: "${MIGRATION_DB_PASSWORD:?缺少迁移角色密码}"
psql --no-psqlrc --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv app_password APP_DB_PASSWORD
\getenv migration_password MIGRATION_DB_PASSWORD
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT format('CREATE ROLE mmr_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'migration_password') \gexec
SELECT format('CREATE ROLE mmr_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'app_password') \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO mmr_migrator;
GRANT USAGE ON SCHEMA public TO mmr_app;
GRANT SELECT ON spatial_ref_sys TO mmr_app, mmr_migrator;
ALTER DEFAULT PRIVILEGES FOR ROLE mmr_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mmr_app;
ALTER DEFAULT PRIVILEGES FOR ROLE mmr_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO mmr_app;
SQL
