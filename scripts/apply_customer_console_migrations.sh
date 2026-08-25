#!/usr/bin/env bash
# Apply the Customer Console schema ladder to the Supabase Console database.
#
# WHY ITS OWN APPLIER: the Console's data plane is a SEPARATE Supabase project
# (D34), not the tenant Postgres. scripts/apply_migrations.sh is bolted to the
# local docker Postgres (`docker exec acb-postgres`, a tenant pre-migration
# backup, POSTGRES_* from .env) and cannot target a remote DSN — so the Console
# ladder gets this dedicated, DSN-driven applier instead.
#
# IDEMPOTENT: every file in infra/customer_console/ is additive / IF NOT EXISTS,
# so re-running is safe and is the intended way to apply new ladder files.
#
# Usage (on the box, or anywhere with psql reachable to the Supabase project):
#   CUSTOMER_CONSOLE_DATABASE_URL='postgresql://…supabase.co:5432/postgres' \
#     bash scripts/apply_customer_console_migrations.sh
#
# It reads the SAME env var the service uses, so on the box you can source the
# service env file first:
#   set -a; . /opt/acb/app/apps/services/customer_console/.env; set +a
#   bash scripts/apply_customer_console_migrations.sh
set -euo pipefail

: "${CUSTOMER_CONSOLE_DATABASE_URL:?set CUSTOMER_CONSOLE_DATABASE_URL to the Supabase Console DSN}"

# psql wants a libpq URL; the service's DSN carries a SQLAlchemy driver suffix
# (postgresql+psycopg://…). Strip it so psql accepts the URL.
DSN="${CUSTOMER_CONSOLE_DATABASE_URL/+psycopg2/}"
DSN="${DSN/+psycopg/}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LADDER_DIR="$REPO_ROOT/infra/customer_console"

command -v psql >/dev/null 2>&1 || { echo "psql not found; install postgresql-client" >&2; exit 1; }
[ -d "$LADDER_DIR" ] || { echo "ladder dir missing: $LADDER_DIR" >&2; exit 1; }

echo "==> Console DB: $(printf '%s' "$DSN" | sed -E 's#(//[^:]+):[^@]*@#\1:***@#')"
echo "==> Ladder:     $LADDER_DIR"

shopt -s nullglob
files=("$LADDER_DIR"/[0-9][0-9][0-9]_*.sql)
[ ${#files[@]} -gt 0 ] || { echo "no NNN_*.sql files found in $LADDER_DIR" >&2; exit 1; }

for f in "${files[@]}"; do
  echo "==> applying $(basename "$f")"
  psql "$DSN" -v ON_ERROR_STOP=1 -q -f "$f"
done

echo "==> Customer Console ladder applied (${#files[@]} files)."
