#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# Rebuild the STAGING database from last night's reduced copy of production
# (WS-38 T-9, project-docs/specs/staging_and_daily_release.md §5.11).
#
# WHERE IT RUNS: on the staging box only, as root, from
# acb-staging-refresh.service (each night) or by hand:
#
#   sudo bash /opt/acb/app/scripts/staging_refresh.sh            # if the copy is new
#   sudo bash /opt/acb/app/scripts/staging_refresh.sh --force    # always
#
# WHAT IT DOES:
#   1. Refuses unless /opt/acb/app/.env says DEPLOY_ENV=staging. It drops a
#      database, so it must never run on production.
#   2. Pulls the export from the production box with the staging link key.
#      That key can read the export folder and nothing else (rrsync -ro).
#   3. Checks the checksums and the age (36 hours at most).
#   4. Drops and rebuilds the database `postgres` in acb-staging-db: the roles
#      and extensions the dump names, then pg_restore.
#   5. Gives the gateway the non-owner role acb_app (handover §H3), so RLS binds
#      staging as it binds production (R5).
#   6. Copies production's feature switches into the two env files, except the
#      switches that reach a person or a third party, which staging keeps off.
#   7. Runs the normal deploy path, which applies the pending migrations of the
#      `staging` ref, rebuilds and restarts.
#
# Config: /etc/acb/staging.env (root, 0600), written by bootstrap_staging.sh:
#   STAGING_PROD_HOST   the production box
#   STAGING_APP_DB_PW   acb_app's password, the one in DATABASE_URL
# and /etc/acb/staging-db.env holds POSTGRES_PASSWORD of the container.
#
# Fence: tests/unit/test_staging_box.py.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

app_dir="${STAGING_APP_DIR:-/opt/acb/app}"   # a test points this at a scratch tree
import_dir=/var/lib/acb/staging-import
state=/var/lib/acb/staging-refresh.sha
ctr=acb-staging-db
force=0
[ "${1:-}" = "--force" ] && force=1

say()  { printf '==> %s\n' "$*"; }
fail() { printf '!! %s\n' "$*" >&2; exit 1; }
val()  { { grep -E "^$1=" "$2" 2>/dev/null || true; } | tail -1 | cut -d= -f2-; }

# ── 1. Staging only ─────────────────────────────────────────────────────────
# The staging check comes FIRST, before anything else, the root check included.
[ "$(val DEPLOY_ENV "$app_dir/.env")" = "staging" ] \
  || fail "$app_dir/.env does not say DEPLOY_ENV=staging. This drops a database: refusing."
[ "$(id -u)" = "0" ] || fail "run as root"
prod_host="$(val STAGING_PROD_HOST /etc/acb/staging.env)"
app_pw="$(val STAGING_APP_DB_PW /etc/acb/staging.env)"
[ -n "$prod_host" ] && [ -n "$app_pw" ] || fail "/etc/acb/staging.env is missing a value"
[[ "$app_pw" =~ ^[A-Za-z0-9]+$ ]] || fail "STAGING_APP_DB_PW must be letters and digits"

# ── 2. Pull ─────────────────────────────────────────────────────────────────
say "Pulling the export from $prod_host"
install -d -m 0750 -o acb -g acb "$import_dir"
sudo -u acb rsync -a --delete \
  -e "ssh -i /home/acb/.ssh/staging_link -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/acb/.ssh/known_hosts_prod" \
  "acb@$prod_host:./" "$import_dir/" \
  || fail "the pull failed. Is the staging link key in the production box's authorized_keys?"

# ── 3. Check ────────────────────────────────────────────────────────────────
(cd "$import_dir" && sha256sum --quiet -c SHA256SUMS) || fail "the export's checksums do not match"
age_h=$(( ( $(date +%s) - $(stat -c %Y "$import_dir/staging.dump") ) / 3600 ))
[ "$age_h" -le 36 ] || fail "the export is ${age_h}h old. Staging keeps its current data."
sum="$(sha256sum "$import_dir/staging.dump" | cut -d' ' -f1)"
if [ "$force" = "0" ] && [ -f "$state" ] && [ "$(cat "$state")" = "$sum" ]; then
  say "the export has not changed since the last rebuild, so nothing is done"
  exit 0
fi

# ── 4. Rebuild ──────────────────────────────────────────────────────────────
say "Rebuilding the staging database (export ${age_h}h old)"
dpsql() { docker exec -i "$ctr" psql -X -q -v ON_ERROR_STOP=1 -U postgres "$@"; }
systemctl stop acb-gateway acb-workbench acb-agent-edge 2>/dev/null || true
dpsql -d template1 -c "DROP DATABASE IF EXISTS postgres WITH (FORCE)" < /dev/null
dpsql -d template1 -c "CREATE DATABASE postgres" < /dev/null
while IFS= read -r role; do
  [[ "$role" =~ ^[A-Za-z0-9_]+$ ]] || continue
  dpsql -d postgres -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$role') THEN CREATE ROLE \"$role\" NOLOGIN; END IF; END \$\$" < /dev/null
done < "$import_dir/roles.txt"
available="$(dpsql -d postgres -tA -c "select name from pg_available_extensions" < /dev/null)"
while IFS='|' read -r ext nsp; do
  [[ "$ext" =~ ^[a-z0-9_]+$ && "$nsp" =~ ^[a-z0-9_]+$ ]] || continue
  if printf '%s\n' "$available" | grep -qxF "$ext"; then
    dpsql -d postgres -c "CREATE SCHEMA IF NOT EXISTS \"$nsp\"; CREATE EXTENSION IF NOT EXISTS \"$ext\" WITH SCHEMA \"$nsp\" CASCADE" < /dev/null
  else
    say "extension $ext is not in the image, so it is skipped"
  fi
done < "$import_dir/extensions.txt"
log="$import_dir/restore.log"
rc=0
docker exec -i "$ctr" pg_restore -U postgres -d postgres --no-owner --no-acl \
  < "$import_dir/staging.dump" > "$log" 2>&1 || rc=$?
if [ "$rc" != "0" ] || grep -qE '^pg_restore: error' "$log"; then
  grep -E '^pg_restore: error' "$log" | head -20 >&2 || true
  fail "pg_restore failed (exit $rc). See $log"
fi

# ── 5. The gateway's role: not owner, not superuser, no RLS bypass ───────────
dpsql -d postgres <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'acb_app') THEN CREATE ROLE acb_app; END IF;
END \$\$;
ALTER ROLE acb_app WITH LOGIN PASSWORD '$app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
GRANT CONNECT ON DATABASE postgres TO acb_app;
GRANT USAGE ON SCHEMA public TO acb_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO acb_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO acb_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acb_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO acb_app;
SQL
echo "$sum" > "$state"

# ── 6. Production's switches, with the outbound ones held off ────────────────
# A switch that reaches a person or a third party never comes across: mail,
# WhatsApp, the meeting bot, invites, Reach, sign-in through the Console, and
# self-serve sign-up. Staging keeps the value that bootstrap_staging.sh wrote.
held='^(WHATSAPP_|MEETING_BOT_|EMAIL_|REACH_|MEMBER_INVITE_|CUSTOMER_CONSOLE_|SELF_SERVE_|GMAIL_|MSFT_)|(SYNC|SEND|OUTBOUND|NOTIFY|PUSH)'
apply_flags() {
  local scope="$1" file="$2" line name
  [ -f "$file" ] || return 0
  while IFS= read -r line; do
    case "$line" in "$scope":*) ;; *) continue ;; esac
    line="${line#"$scope":}"
    name="${line%%=*}"
    [[ "$line" =~ ^[A-Z0-9_]+=[A-Za-z0-9_.:-]{0,64}$ ]] || continue
    [[ "$name" =~ $held ]] && continue
    ( umask 077; { grep -v "^$name=" "$file" || true; } > "$file.tmp" )
    echo "$line" >> "$file.tmp"
    cat "$file.tmp" > "$file"; rm -f "$file.tmp"
  done < "$import_dir/flags.env"
}
apply_flags root "$app_dir/.env"
apply_flags workbench "$app_dir/workbench/control_plane/.env.local"

# ── 7. The normal deploy path ───────────────────────────────────────────────
say "Applying the staging ref (pending migrations, build, restart)"
sudo -u acb env MODE=force bash "$app_dir/scripts/vps_pull.sh"
say "Staging rebuilt from the production copy"
