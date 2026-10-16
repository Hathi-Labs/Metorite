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
# The deploy lock, for as long as services are stopped and the database is
# away. acb-pull.timer runs every 5 minutes on this box, and an apply in flight
# would migrate a database that this then drops. Wait for it, up to 30 minutes.
lock="$(dirname "$app_dir")/acb-deploy.lock"
[ -e "$lock" ] || install -m 0644 -o acb -g acb /dev/null "$lock"
exec 9<"$lock"
flock -w 1800 9 || fail "another deploy held the lock for 30 minutes. Nothing was changed."
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
# Matched ANYWHERE in the name, not only at its start: PROJECT_REPORT_EMAIL_ENABLED
# mails real people, and an anchored `^EMAIL_` missed it.
held='(WHATSAPP|MEETING_BOT|EMAIL|REACH_|INVITE|CUSTOMER_CONSOLE|SELF_SERVE|SIGNUP|GMAIL|MSFT|SYNC|SEND|OUTBOUND|NOTIFY|PUSH|DIGEST|WEBHOOK|SMS)'
# The export's own name rule, checked again here: a line that names anything
# else (DEPLOY_ENV, a URL, a key) is never applied.
switch='^(NEXT_PUBLIC_[A-Z0-9_]+|[A-Z0-9_]+_ENABLED|[A-Z0-9_]+_LENS|IDENTITY_CUTOVER|ACB_GRAPH_TENANT_BIND)$'
apply_flags() {
  local scope="$1" file="$2" line name
  [ -f "$file" ] || return 0
  while IFS= read -r line; do
    case "$line" in "$scope":*) ;; *) continue ;; esac
    line="${line#"$scope":}"
    name="${line%%=*}"
    [[ "$line" =~ ^[A-Z0-9_]+=[A-Za-z0-9_.:-]{0,64}$ ]] || continue
    [[ "$name" =~ $switch ]] || continue
    [[ "$name" =~ (KEY|SECRET|TOKEN|PASSWORD|PASSWD|DSN|URL|PASS) ]] && continue
    [[ "$name" =~ $held ]] && continue
    ( umask 077; { grep -v "^$name=" "$file" || true; } > "$file.tmp" )
    echo "$line" >> "$file.tmp"
    cat "$file.tmp" > "$file"; rm -f "$file.tmp"
  done < "$import_dir/flags.env"
}
apply_flags root "$app_dir/.env"
apply_flags workbench "$app_dir/workbench/control_plane/.env.local"

# ── 7. The normal deploy path ───────────────────────────────────────────────
# Release the lock, then deploy. vps_pull.sh takes the lock itself, and exits 0
# with "nothing to do" when the timer holds it, so retry until it ran.
flock -u 9
exec 9<&-
say "Applying the staging ref (pending migrations, build, restart)"
ran=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  # The PATH of acb-pull.service, so the build finds acb's Node 20.20.2 and
  # uv. sudo's secure_path would otherwise give the box's own Node.
  out="$(sudo -u acb env PATH=/home/acb/.local/bin:/usr/local/bin:/usr/bin:/bin HOME=/home/acb MODE=force bash "$app_dir/scripts/vps_pull.sh" 2>&1)" || {
    printf '%s\n' "$out" | tail -30 >&2
    fail "the staging deploy failed. The database is rebuilt, and the services may be down."
  }
  printf '%s\n' "$out" | tail -5
  if printf '%s\n' "$out" | grep -q "another deploy holds the lock"; then sleep 60; continue; fi
  ran=1
  break
done
[ "$ran" = "1" ] || fail "the deploy lock stayed busy for 10 minutes. Run this again."

# Proof by the serving commit, never by an exit code (CLAUDE.md §3 rule 8).
want="$(sudo -u acb git -C "$app_dir" rev-parse HEAD)"
for _ in $(seq 1 30); do
  got="$(curl -s -m 10 http://127.0.0.1:8080/version 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("sha",""))' 2>/dev/null || true)"
  [ "$got" = "$want" ] && break
  sleep 10
done
[ "$got" = "$want" ] || fail "the gateway serves '${got:-nothing}', not ${want:0:12}"
say "Staging rebuilt from the production copy, serving ${want:0:12}"
