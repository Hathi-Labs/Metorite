#!/usr/bin/env bash
# One-time schema bootstrap for a MANAGED Postgres (e.g. Supabase).
#
# Why this exists: on the compose path, infra/postgres/00_create_databases.sql
# and 01_schema.sql reach Postgres only through the container's initdb hook,
# and apply_migrations.sh deliberately skips 00_*/01_*. A database that never
# ran initdb therefore has neither the extensions (uuid-ossp, vector) nor the
# nine core tables the numbered ladder FK-references — and the first numbered
# migration fails in a way that looks like a ladder bug.
#
# Run this ONCE before the first apply_migrations.sh against such a database,
# with libpq env exported:
#
#   PGHOST=... PGPORT=5432 PGUSER=postgres PGPASSWORD=... PGSSLMODE=require \
#     PG_DB=postgres bash scripts/bootstrap_external_db.sh
#
# 00_create_databases.sql is NOT applied: a managed provider hands you the
# database — CREATE DATABASE is its dashboard's job, not ours.
#
# ⚠️ Managed dashboards often pre-install extensions into an `extensions`
# schema. 01_schema.sql's `uuid_generate_v4()` column defaults resolve only
# if the function is reachable from `public`, so the extensions are created
# here WITH SCHEMA public explicitly and verified before the schema applies.
# If the verify step fails, the extension already lives elsewhere — run
#   ALTER EXTENSION "uuid-ossp" SET SCHEMA public;
# from the provider's SQL console and re-run this script.
#
# Idempotent: 01_schema.sql is IF NOT EXISTS throughout; re-running is safe.
set -euo pipefail
cd "$(dirname "$0")/.."

PG_DB="${PG_DB:-${PGDATABASE:-postgres}}"
command -v psql >/dev/null || { echo "ERROR: psql is not on PATH." >&2; exit 1; }

run() { psql -v ON_ERROR_STOP=1 -d "$PG_DB" "$@"; }

echo "==> Extensions (into public, explicitly)"
run -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;'
run -c 'CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;'
run -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;'

echo "==> Verifying uuid_generate_v4() resolves from public"
run -c "SELECT public.uuid_generate_v4();" >/dev/null

echo "==> Applying infra/postgres/01_schema.sql (core tables)"
run -f infra/postgres/01_schema.sql

echo "==> Done. Next: PG_MODE=local bash scripts/apply_migrations.sh"
