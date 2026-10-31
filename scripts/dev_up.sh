#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# dev_up.sh: the whole Metorite app on THIS machine, in one command (LD-1).
#
#   bash scripts/dev_up.sh                  start. The first run builds and seeds.
#   bash scripts/dev_up.sh --status         what runs, on which port
#   bash scripts/dev_up.sh --check          health, then the tenant-isolation check
#   bash scripts/dev_up.sh --logs [gateway|web|migrate|seed|restore]
#   bash scripts/dev_up.sh --env            the URLs and DSNs, as export lines
#   bash scripts/dev_up.sh --stop           stop the gateway and the web app
#   bash scripts/dev_up.sh --down           stop, then remove the containers and the data
#   bash scripts/dev_up.sh --reset          --down, then a fresh start (the same data)
#   bash scripts/dev_up.sh --refresh-flags  retired with the staging box (D144). Exits 2.
#
#   bash scripts/dev_up.sh --data prod --as <email>   production's copy (LD-8b)
#     --data demo|prod  the data of the stack. demo is the default. A switch needs --reset.
#     --copy DIR        the copy to restore. The default is the newest folder in
#                       $PROD_COPY_DIR (~/.metorite/prod-copy), from scripts/prod_copy.sh.
#     --as <email>      the member to sign in as: an active member in the copy.
#                       With no --as, it lists the active members and stops.
#     --db-only         build the database, then stop. No gateway and no web app.
#
# WHAT YOU GET, per worktree, on 127.0.0.1 only:
#   * Postgres (pgvector) with the whole ladder AND the RLS promotion, so FORCE
#     ROW LEVEL SECURITY binds as it binds in production;
#   * a Redis of its own, so two stacks never share a cache;
#   * the gateway as the NON-superuser role acb_app, with --reload;
#   * the web app (next dev), signed in as dev@fracktal.in;
#   * two made-up organizations from scripts/demo_seed/seed_demo.py: Kite & Co.
#     (yours) and Northwind (the other tenant, so a leak shows).
#
# WITH --data prod (engineering_practice.md §1.3.1):
#   * Postgres 17, because the copy is pg_dump 17 output (archive format 1.16);
#   * the restore of scripts/staging_refresh.sh steps 4 to 6, inside the container;
#   * the pg17 container runs with -c maintenance_work_mem=256MB. An IVFFlat
#     index on the embeddings of a large mailbox needs more than the 64 MB
#     default. That stopped production's nightly verify on 2026-10-31;
#   * DELETE FROM push_subscriptions on each start, before the gateway starts;
#   * no demo seed and no RLS promotion replay, because the copy holds both;
#   * production's switches from the copy's flags.env, with the outbound ones OFF;
#   * the web app signed in as the --as member.
#
# WHY THE LADDER RUNS INSIDE THE CONTAINER. Measured 2026-10-30 on the Windows
# PC: scripts/apply_migrations.sh from Git Bash took 515 s for 310 files, and
# 89 s with nothing to apply. Process start-up under Git Bash is the cost. The
# same script inside the Linux container took 38 s and 6 s.
#
# NOTHING LEAVES THIS MACHINE:
#   * each process starts with a CLEAN environment (env -i), so a key in your
#     shell never reaches the stack;
#   * the script refuses a checkout with a .env, because the gateway and
#     Next.js read .env files themselves;
#   * production's switches come across with the outbound ones held OFF, by
#     the same pattern as scripts/staging_refresh.sh.
#
# Fences: tests/unit/test_dev_up.py, and tests/unit/test_dev_up_prod_r8.py (R8).
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${DEV_UP_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
STATE_DIR="$REPO/.dev_up"
STATE="$STATE_DIR/state"
FLAGS_FILE="${DEV_UP_FLAGS_FILE:-$SCRIPT_DIR/dev_up/prod_flags.env}"
# The member the stack signs in as. --data prod replaces it with --as.
ME="dev@fracktal.in"
PG_IMAGE_DEMO="pgvector/pgvector:pg16"
# pg_dump 17 writes archive format 1.16, and pg_restore 16 cannot read it.
PG_IMAGE_PROD="pgvector/pgvector:pg17"
# Set per mode by set_mode.
PG_IMAGE="$PG_IMAGE_DEMO"
REDIS_IMAGE="redis:7-alpine"
# The folder of copies that scripts/prod_copy.sh writes (LD-8a). One folder for
# each copy, named by its UTC time, with these five files.
PROD_COPY_BASE="${PROD_COPY_DIR:-${HOME:-}/.metorite/prod-copy}"
COPY_FILES=(staging.dump roles.txt extensions.txt flags.env SHA256SUMS)

case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) WIN=1 ;; *) WIN=0 ;; esac

# The SAME pattern as scripts/staging_refresh.sh step 6. A switch whose name
# matches it reaches a person or a third party, so it stays OFF here.
# tests/unit/test_dev_up.py fails when the two copies differ.
HELD='(WHATSAPP|MEETING_BOT|EMAIL|REACH_|INVITE|CUSTOMER_CONSOLE|SELF_SERVE|SIGNUP|GMAIL|MSFT|SYNC|SEND|OUTBOUND|NOTIFY|PUSH|DIGEST|WEBHOOK|SMS|SCHEDULER)'
SWITCH='^(NEXT_PUBLIC_[A-Z0-9_]+|[A-Z0-9_]+_ENABLED|[A-Z0-9_]+_LENS|IDENTITY_CUTOVER|ACB_GRAPH_TENANT_BIND)$'
# Also OFF here: the Console bootstrap needs the Console database, and the
# local stack does not run one.
LOCAL_OFF='^(CONSOLE_BOOTSTRAP_ENABLED)$'
# Two loops start unless told not to (tests/unit/test_launch_defang_kill_switches.py).
# They stay off even when the flags file does not name them.
FORCED_OFF=(EMAIL_SYNC_ENABLED=false EMAIL_BACKGROUND_SYNC_ENABLED=false WORKFLOW_SCHEDULER_ENABLED=false)

say()  { printf '%s\n' "$*"; }
fail() { printf 'dev_up: %s\n' "$*" >&2; exit 1; }
dk()   { MSYS_NO_PATHCONV=1 docker "$@"; }
val()  { { grep -E "^$1=" "$STATE" 2>/dev/null || true; } | tail -1 | cut -d= -f2-; }
# set_state <KEY> <value>: replace one line of the state file.
set_state() {
  { grep -vE "^$1=" "$STATE" 2>/dev/null || true; printf '%s=%s\n' "$1" "$2"; } > "$STATE.tmp"
  mv -f "$STATE.tmp" "$STATE"
}

# ── The environment each process gets ──────────────────────────────────────

base_env() {
  local v names
  if [ "$WIN" = 1 ]; then
    names="PATH SYSTEMROOT WINDIR COMSPEC PATHEXT TEMP TMP USERPROFILE HOME APPDATA LOCALAPPDATA PROGRAMDATA USERNAME NUMBER_OF_PROCESSORS PROCESSOR_ARCHITECTURE"
  else
    names="PATH HOME USER LANG TMPDIR"
  fi
  for v in $names; do
    [ -n "${!v:-}" ] && printf '%s=%s\n' "$v" "${!v}"
  done
  printf 'PYTHONUTF8=1\nPYTHONIOENCODING=utf-8\n'
}

# flag_env <root|workbench>: production's switches for one scope, held ones off.
flag_env() {
  local scope="$1" line name value
  declare -A out=()
  if [ -f "$FLAGS_FILE" ]; then
    while IFS= read -r line; do
      # A Windows checkout can give the file CRLF endings. With the CR left on,
      # the strict test below dropped every line, and no switch reached the stack.
      line="${line%$'\r'}"
      case "$line" in "$scope":*) ;; *) continue ;; esac
      line="${line#"$scope":}"
      [[ "$line" =~ ^[A-Z0-9_]+=[A-Za-z0-9_.:-]{0,64}$ ]] || continue
      name="${line%%=*}"
      value="${line#*=}"
      [[ "$name" =~ $SWITCH ]] || continue
      [[ "$name" =~ (KEY|SECRET|TOKEN|PASSWORD|PASSWD|DSN|URL|PASS) ]] && continue
      if [[ "$name" =~ $HELD ]] || [[ "$name" =~ $LOCAL_OFF ]]; then
        case "$value" in 0|1) value=0 ;; *) value=false ;; esac
      fi
      out["$name"]="$value"
    done < "$FLAGS_FILE"
  fi
  if [ "$scope" = root ]; then
    for line in "${FORCED_OFF[@]}"; do out["${line%%=*}"]="${line#*=}"; done
  fi
  for name in "${!out[@]}"; do printf '%s=%s\n' "$name" "${out[$name]}"; done | sort
}

gateway_env() {
  base_env
  flag_env root
  printf '%s\n' \
    "ACB_ENV=dev" \
    "DATABASE_URL=postgresql+psycopg://acb_app:acb_app@127.0.0.1:$DB_PORT/acb_dev" \
    "REDIS_URL=redis://127.0.0.1:$REDIS_PORT/0" \
    "GATEWAY_INTERNAL_TOKEN=$TOKEN" \
    "EXECUTIVE_EMAILS=$ME"
}

# DEV_SIGNED_IN_EMAIL: the member that src/lib/gateway.ts signs in as when the
# web app has no sign-in provider. A production build ignores it.
web_env() {
  base_env
  flag_env workbench
  printf '%s\n' \
    "GATEWAY_BASE_URL=http://127.0.0.1:$GW_PORT" \
    "GATEWAY_INTERNAL_TOKEN=$TOKEN" \
    "EXECUTIVE_EMAILS=$ME" \
    "DEV_SIGNED_IN_EMAIL=$ME" \
    "NEXT_TELEMETRY_DISABLED=1"
}

# The demo seed only. --data prod never calls it, because the seed does not run.
seed_env() {
  base_env
  printf '%s\n' \
    "DEMO_DATABASE_URL=postgresql://acb:acb@127.0.0.1:$DB_PORT/acb_dev" \
    "DEMO_APP_DATABASE_URL=postgresql://acb_app:acb_app@127.0.0.1:$DB_PORT/acb_dev" \
    "DEMO_GATEWAY_URL=http://127.0.0.1:$GW_PORT" \
    "DEMO_GATEWAY_TOKEN=$TOKEN" \
    "DEMO_SIGNED_IN_EMAIL=$ME"
}

# ── Ports and state ─────────────────────────────────────────────────────────
# A slot from 0 to 49 comes from the worktree path. Each slot owns four ports,
# clear of the ones this repo already uses (5432-5436, 3001-3002, 3101, 8000,
# 8010-8011, 8080).

ports_for() { DB_PORT=$((6400 + $1)); REDIS_PORT=$((6450 + $1)); GW_PORT=$((8400 + $1)); WEB_PORT=$((3400 + $1)); }

# listener_pid <port>: the PID that listens on it, or nothing. awk reads ALL
# its input: an early `exit` breaks the pipe, and under pipefail the broken
# pipe ends the whole script.
listener_pid() {
  if [ "$WIN" = 1 ]; then
    { netstat -ano 2>/dev/null || true; } | tr -d '\r' \
      | awk -v p=":$1" '$4 == "LISTENING" && !hit && substr($2, length($2) - length(p) + 1) == p { hit = $5 } END { if (hit) print hit }'
  else
    { ss -ltnpH "sport = :$1" 2>/dev/null || true; } \
      | awk 'match($0, /pid=[0-9]+/) && !hit { hit = substr($0, RSTART + 4, RLENGTH - 4) } END { if (hit) print hit }'
  fi
}
port_busy() {
  if [ "$WIN" = 1 ]; then [ -n "$(listener_pid "$1")" ]
  else { ss -ltnH "sport = :$1" 2>/dev/null || true; } | grep -q .
  fi
}

load_state() {
  if [ -f "$STATE" ]; then
    SLOT="$(val SLOT)"; TOKEN="$(val TOKEN)"; NAME="$(val NAME)"
    ports_for "$SLOT"
    return 0
  fi
  return 1
}

new_state() {
  local slot tries=0
  slot=$(( $(printf '%s' "$REPO" | cksum | cut -d' ' -f1) % 50 ))
  while :; do
    ports_for "$slot"
    if ! port_busy "$DB_PORT" && ! port_busy "$REDIS_PORT" \
       && ! port_busy "$GW_PORT" && ! port_busy "$WEB_PORT"; then break; fi
    tries=$((tries + 1)); [ "$tries" -lt 50 ] || fail "no free port slot (6400-6499, 8400-8449, 3400-3449)"
    slot=$(( (slot + 1) % 50 ))
  done
  SLOT="$slot"
  NAME="metorite-dev-$(basename "$REPO" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9-\n' '-' | cut -c1-40)"
  TOKEN="dev-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  mkdir -p "$STATE_DIR"
  printf 'SLOT=%s\nNAME=%s\nTOKEN=%s\nDATA=%s\n' "$SLOT" "$NAME" "$TOKEN" "$DATA" > "$STATE"
}

# ── The data mode ───────────────────────────────────────────────────────────
# A stack keeps the data it was built with. A state file from before LD-8b has
# no DATA line, and it holds the demo seed.

set_mode() {
  local have=""
  if [ -f "$STATE" ]; then have="$(val DATA)"; have="${have:-demo}"; fi
  DATA="${DATA_ARG:-${have:-demo}}"
  if [ -n "$have" ] && [ "$DATA" != "$have" ]; then
    local as_hint=""
    [ "$DATA" = prod ] && as_hint=" --as <email>"
    fail "this stack holds the $have data, not $DATA. A switch needs a fresh database:
         bash scripts/dev_up.sh --reset --data $DATA$as_hint"
  fi
  if [ "$DATA" = prod ]; then PG_IMAGE="$PG_IMAGE_PROD"; else PG_IMAGE="$PG_IMAGE_DEMO"; fi
}

# check_as_format: --as must look like an email before anything starts. The
# membership check comes after the restore, in check_as.
check_as_format() {
  [ -z "$AS" ] && return 0
  [[ "$AS" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$ ]] || fail "--as '$AS' is not an email address"
  AS="$(printf '%s' "$AS" | tr 'A-Z' 'a-z')"
}

# ── The production copy (--data prod) ───────────────────────────────────────

# newest_copy: the name of the newest copy folder. A name that starts with "."
# is a pull that did not finish, and the glob never matches it.
newest_copy() {
  [ -d "$PROD_COPY_BASE" ] || return 0
  ( cd "$PROD_COPY_BASE" && for d in *; do [ -d "$d" ] && printf '%s\n' "$d"; done; true ) \
    | LC_ALL=C sort | tail -1
}

# check_copy: all five files are there, SHA256SUMS names the four data files,
# and each checksum matches. It runs before each restore.
check_copy() {
  local f
  for f in "${COPY_FILES[@]}"; do
    [ -f "$COPY/$f" ] || fail "$COPY has no $f. Pull the copy again: bash scripts/prod_copy.sh"
  done
  for f in staging.dump roles.txt extensions.txt flags.env; do
    tr -d '\r' < "$COPY/SHA256SUMS" | grep -qE "^[0-9a-f]{64} [ *]$f\$" \
      || fail "$COPY/SHA256SUMS has no line for $f. Pull the copy again: bash scripts/prod_copy.sh"
  done
  ( cd "$COPY" && sha256sum --quiet -c SHA256SUMS ) >&2 \
    || fail "the checksums of $COPY do not match. Pull the copy again: bash scripts/prod_copy.sh"
  COPY_CHECKED=1
}

# pick_copy: the copy of this run. --copy wins, then the copy that this stack
# restored, then the newest copy. The switches come from the same copy.
pick_copy() {
  local restored name
  restored="$(val COPY)"
  if [ -n "$COPY_ARG" ]; then
    [ -d "$COPY_ARG" ] || fail "--copy $COPY_ARG is not a folder"
    COPY="$(cd "$COPY_ARG" && pwd)"
  elif [ -n "$restored" ]; then
    COPY="$restored"
  else
    name="$(newest_copy)"
    [ -n "$name" ] || fail "no production copy in $PROD_COPY_BASE. Run \`bash scripts/prod_copy.sh\` first."
    COPY="$(cd "$PROD_COPY_BASE/$name" && pwd)"
  fi
  [[ "$(basename "$COPY")" =~ ^[A-Za-z0-9_-][A-Za-z0-9._-]*$ ]] \
    || fail "the copy folder name '$(basename "$COPY")' must be letters, digits, '.', '_' or '-'"
  if [ -n "$restored" ] && [ "$COPY" != "$restored" ]; then
    fail "this stack holds the copy $restored. Another copy needs a fresh database:
         bash scripts/dev_up.sh --reset --data prod --copy <DIR> --as <email>"
  fi
  if [ -n "$restored" ]; then
    [ -f "$COPY/flags.env" ] || fail "the copy that this stack restored is gone ($COPY).
         Restore the newest copy: bash scripts/dev_up.sh --reset --data prod --as <email>"
  else
    check_copy
  fi
  FLAGS_FILE="$COPY/flags.env"
}

# The restore of scripts/staging_refresh.sh steps 4 to 6, as ONE run inside
# the container: the roles from roles.txt, the schemas and extensions from
# extensions.txt, then pg_restore with the `SCHEMA - public` entry left out.
# The container's superuser is acb and its database is acb_dev, not postgres.
# Each name is checked by the same pattern as there, so no line of a copy
# becomes SQL.
IFS= read -r -d '' RESTORE_SH <<'SH' || true
set -euo pipefail
d=/tmp/prod-copy
trap 'rm -rf "$d"' EXIT
q() { psql -X -q -v ON_ERROR_STOP=1 -U acb -d acb_dev "$@"; }
while IFS= read -r role; do
  role="${role%$'\r'}"
  [[ "$role" =~ ^[A-Za-z0-9_]+$ ]] || continue
  q -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$role') THEN CREATE ROLE \"$role\" NOLOGIN; END IF; END \$\$" < /dev/null
done < "$d/roles.txt"
available="$(q -tA -c "select name from pg_available_extensions" < /dev/null)"
while IFS='|' read -r ext nsp; do
  nsp="${nsp%$'\r'}"
  # A name may hold a hyphen: uuid-ossp. The SQL below quotes the name.
  [[ "$ext" =~ ^[a-z0-9_-]+$ && "$nsp" =~ ^[a-z0-9_]+$ ]] || continue
  if printf '%s\n' "$available" | grep -qxF "$ext"; then
    q -c "CREATE SCHEMA IF NOT EXISTS \"$nsp\"; CREATE EXTENSION IF NOT EXISTS \"$ext\" WITH SCHEMA \"$nsp\" CASCADE" < /dev/null
  else
    echo "extension $ext is not in the image, so it is skipped"
  fi
done < "$d/extensions.txt"
# A dump of --schema=public creates the schema public, and the database has it
# already. Restore from a list without that one entry, so the restore has no error.
pg_restore -l "$d/staging.dump" | grep -vE "^[0-9]+; [0-9]+ [0-9]+ SCHEMA - public " > "$d/staging.list"
rc=0
# The server runs with maintenance_work_mem=256MB (start_containers), for the
# IVFFlat indexes that this restore builds.
pg_restore -U acb -d acb_dev --no-owner --no-acl -L "$d/staging.list" "$d/staging.dump" > "$d/restore.log" 2>&1 || rc=$?
cat "$d/restore.log"
if [ "$rc" != "0" ] || grep -qE '^pg_restore: error' "$d/restore.log"; then
  grep -E '^pg_restore: error' "$d/restore.log" | head -20 >&2 || true
  echo "pg_restore failed (exit $rc)" >&2
  exit 1
fi
SH

restore_copy() {
  local host_copy="$COPY" f
  [ "$WIN" = 1 ] && host_copy="$(cd "$COPY" && pwd -W)"
  dk exec "$NAME-pg" rm -rf /tmp/prod-copy
  dk exec "$NAME-pg" mkdir -p /tmp/prod-copy
  for f in staging.dump roles.txt extensions.txt; do
    # docker cp, not a pipe: pg_restore -L needs a file it can seek in.
    dk cp "$host_copy/$f" "$NAME-pg:/tmp/prod-copy/$f" >/dev/null \
      || fail "docker cp of $f into $NAME-pg failed"
  done
  dk exec "$NAME-pg" bash -c "$RESTORE_SH" > "$STATE_DIR/restore.log" 2>&1 \
    || { tail -20 "$STATE_DIR/restore.log" >&2; fail "the restore failed. Run --reset for a clean start. Log: $STATE_DIR/restore.log"; }
}

# The member to sign in as is live in the copy: an app_user with an active
# org_membership, the two rows that the gateway reads (acb_auth.access). Read
# as the superuser acb, before the gateway starts. The email never goes into
# the SQL: the list comes back, and the match happens here.
MEMBERS_SQL="SELECT DISTINCT lower(u.email) || '  ' || o.slug
  FROM app_user u
  JOIN user_identity ui ON lower(ui.email) = lower(u.email)
  JOIN org_membership m ON m.user_id = ui.id AND m.organization_id = u.organization_id
  JOIN organization o ON o.id = m.organization_id
 WHERE u.status = 'active' AND m.status = 'active'
 ORDER BY 1"

check_as() {
  local members
  members="$(dk exec -i "$NAME-pg" psql -X -q -v ON_ERROR_STOP=1 -U acb -d acb_dev -tA -c "$MEMBERS_SQL" < /dev/null | tr -d '\r')" \
    || fail "the member list of the copy could not be read"
  if [ -n "$AS" ] && printf '%s\n' "$members" | cut -d' ' -f1 | grep -qxF -- "$AS"; then
    ME="$AS"
    return 0
  fi
  {
    if [ -z "$AS" ]; then
      printf 'dev_up: --data prod needs --as <email>, the member to sign in as.\n'
    else
      printf 'dev_up: %s is not an active member in this copy.\n' "$AS"
    fi
    if [ -n "$members" ]; then
      printf 'The active members (email, then organization):\n'
      printf '%s\n' "$members" | sed 's/^/  /'
    else
      printf 'The copy has no active member.\n'
    fi
  } >&2
  exit 1
}

# ── Guards ──────────────────────────────────────────────────────────────────

refuse_dotenv() {
  local f
  for f in "$REPO/.env" "$REPO/workbench/control_plane/.env" \
           "$REPO/workbench/control_plane/.env.local" \
           "$REPO/workbench/control_plane/.env.development" \
           "$REPO/workbench/control_plane/.env.development.local"; do
    if [ -f "$f" ]; then
      fail "$f exists. The gateway and Next.js read it themselves, so its keys
         would reach this stack. Run dev_up in a worktree instead:
           bash scripts/worktree.sh new <slug>"
    fi
  done
}

need_tools() {
  local t
  for t in docker uv node curl; do command -v "$t" >/dev/null || fail "$t is not on PATH"; done
  docker info >/dev/null 2>&1 || fail "the Docker daemon is not running. Start Docker Desktop, then run this again."
}

# ── Processes ───────────────────────────────────────────────────────────────

# launch <name> <dir> <env-lines-fn> <cmd...>: start a process in the
# background with ONLY the environment that <env-lines-fn> prints.
launch() {
  local name="$1" dir="$2" envfn="$3"; shift 3
  local -a envs
  mapfile -t envs < <("$envfn")
  if [ "$WIN" = 1 ]; then
    ( cd "$dir" && exec env -i "${envs[@]}" "$@" ) > "$STATE_DIR/$name.log" 2>&1 &
  else
    ( cd "$dir" && exec setsid env -i "${envs[@]}" "$@" ) > "$STATE_DIR/$name.log" 2>&1 &
  fi
  local pid=$! winpid=""
  [ "$WIN" = 1 ] && winpid="$(cat "/proc/$pid/winpid" 2>/dev/null || true)"
  printf '%s %s\n' "$pid" "$winpid" > "$STATE_DIR/$name.pid"
}

# stop_one <name> <port>: end the process tree, then whatever still listens on
# the port. uvicorn --reload runs a parent and a worker, and a kill of the
# wrapper does not always reach both, so the port is the final test.
stop_one() {
  local name="$1" port="$2" pid="" winpid="" lp i
  if [ -f "$STATE_DIR/$name.pid" ]; then
    read -r pid winpid < "$STATE_DIR/$name.pid" || true
    if [ "$WIN" = 1 ]; then
      [ -n "$winpid" ] && taskkill //F //T //PID "$winpid" >/dev/null 2>&1 || true
    else
      [ -n "$pid" ] && { kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true; }
    fi
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    lp="$(listener_pid "$port")"
    [ -z "$lp" ] && break
    if [ "$WIN" = 1 ]; then taskkill //F //T //PID "$lp" >/dev/null 2>&1 || true
    else kill -TERM "$lp" 2>/dev/null || true
    fi
    sleep 1
  done
  rm -f "$STATE_DIR/$name.pid"
  [ -z "$(listener_pid "$port")" ] || fail "port $port still has a listener after the $name stop"
}

# wait_http <url> <seconds>: one try at once, then every 2 s until <seconds>.
wait_http() {
  local url="$1" secs="$2" waited=0
  while :; do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)" = 200 ] && return 0
    [ "$waited" -ge "$secs" ] && return 1
    sleep 2
    waited=$((waited + 2))
  done
}

# ── The database ────────────────────────────────────────────────────────────

pg_sql() { dk exec -i "$NAME-pg" psql -X -q -v ON_ERROR_STOP=1 -U acb -d acb_dev "$@"; }

start_containers() {
  local mount_root="$REPO" image
  local -a pg_args=()
  [ "$WIN" = 1 ] && mount_root="$(cd "$REPO" && pwd -W)"
  # --data prod: an IVFFlat index (lists = 100) on about 9,500 vector(1536)
  # rows needs 65 MB, and the default is 64 MB. Production's nightly verify
  # failed that way on 2026-10-31, and the copy keeps the largest mailboxes.
  # The flag goes AFTER the image: before it, -c is docker's --cpu-shares.
  if [ "$DATA" = prod ]; then pg_args=(-c maintenance_work_mem=256MB); fi
  # A container of the other mode holds the other data. Never build on it.
  image="$(dk inspect -f '{{.Config.Image}}' "$NAME-pg" 2>/dev/null || true)"
  if [ -n "$image" ] && [ "$image" != "$PG_IMAGE" ]; then
    fail "$NAME-pg runs $image, and the $DATA data needs $PG_IMAGE. Start again with a fresh database:
         bash scripts/dev_up.sh --reset --data $DATA"
  fi
  if [ "$(dk inspect -f '{{.State.Running}}' "$NAME-pg" 2>/dev/null || true)" != true ]; then
    if dk inspect "$NAME-pg" >/dev/null 2>&1; then
      dk start "$NAME-pg" >/dev/null
    else
      dk run -d --name "$NAME-pg" --label "metorite.dev_up=$REPO" \
        -e POSTGRES_USER=acb -e POSTGRES_PASSWORD=acb -e POSTGRES_DB=acb_dev \
        -v "$mount_root/infra:/app/infra:ro" -v "$mount_root/scripts:/app/scripts:ro" \
        -p "127.0.0.1:$DB_PORT:5432" "$PG_IMAGE" "${pg_args[@]}" >/dev/null
    fi
  fi
  if [ "$(dk inspect -f '{{.State.Running}}' "$NAME-redis" 2>/dev/null || true)" != true ]; then
    if dk inspect "$NAME-redis" >/dev/null 2>&1; then
      dk start "$NAME-redis" >/dev/null
    else
      dk run -d --name "$NAME-redis" --label "metorite.dev_up=$REPO" \
        -p "127.0.0.1:$REDIS_PORT:6379" "$REDIS_IMAGE" >/dev/null
    fi
  fi
  local i
  # Over TCP: the image's first-start server listens on the socket only, and
  # it stops again before the real server starts.
  for i in $(seq 1 60); do
    dk exec "$NAME-pg" pg_isready -h 127.0.0.1 -U acb -d acb_dev >/dev/null 2>&1 && return 0
    sleep 1
  done
  fail "Postgres in $NAME-pg did not become ready"
}

# The app role comes FIRST, before any migration. Some migrations grant to
# acb_app only when the role exists (222, 230), as on staging and production.
ensure_app_role() {
  pg_sql >/dev/null <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'acb_app') THEN CREATE ROLE acb_app; END IF;
END $$;
ALTER ROLE acb_app WITH LOGIN PASSWORD 'acb_app' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
GRANT CONNECT ON DATABASE acb_dev TO acb_app;
GRANT USAGE ON SCHEMA public TO acb_app;
SQL
}

# The same grants staging gives the app role (scripts/staging_refresh.sh step 5).
grant_app_role() {
  pg_sql >/dev/null <<'SQL'
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO acb_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO acb_app;
ALTER DEFAULT PRIVILEGES FOR ROLE acb IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acb_app;
ALTER DEFAULT PRIVILEGES FOR ROLE acb IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO acb_app;
SQL
}

build_database() {
  local tables marker started
  started=$(date +%s)
  ensure_app_role
  tables="$(dk exec "$NAME-pg" psql -X -U acb -d acb_dev -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'" | tr -d '[:space:]')"
  if [ "${tables:-0}" = 0 ]; then
    say "  empty database: base schema, then the whole ladder (about 40 s)"
    dk exec "$NAME-pg" bash -c 'set -e
      psql -X -q -U acb -d acb_dev -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; CREATE EXTENSION IF NOT EXISTS pgcrypto;" >/dev/null
      psql -X -q -v ON_ERROR_STOP=1 -U acb -d acb_dev -f /app/infra/postgres/01_schema.sql >/dev/null' \
      > "$STATE_DIR/migrate.log" 2>&1 || { tail -20 "$STATE_DIR/migrate.log" >&2; fail "01_schema.sql failed"; }
  fi
  run_migrations

  # The RLS promotion, once. The ladder does not carry it, and production
  # applied it once (tests/unit/test_h3_rls_promotion_rehearsal.py does the same).
  marker="$(dk exec "$NAME-pg" psql -X -U acb -d acb_dev -tAc \
    "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = 'acb_dev'" | tr -d '\r')"
  if [ "$marker" != "dev_up: rls promoted" ]; then
    dk exec "$NAME-pg" bash -c 'set -e
      for p in 01_add_columns 02_backfill 03_constraints 04_policies; do
        psql -X -q -v ON_ERROR_STOP=1 -U acb -d acb_dev -f /app/infra/postgres/generated/$p.sql
      done' >> "$STATE_DIR/migrate.log" 2>&1 || { tail -20 "$STATE_DIR/migrate.log" >&2; fail "the RLS promotion failed"; }
    pg_sql -c "COMMENT ON DATABASE acb_dev IS 'dev_up: rls promoted'" >/dev/null
    say "  RLS promoted: $(dk exec "$NAME-pg" psql -X -U acb -d acb_dev -tAc \
      "SELECT count(*) FILTER (WHERE relforcerowsecurity) || ' of ' || count(*) || ' tables force RLS' FROM pg_class WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace" | tr -d '\r')"
  fi
  grant_app_role
  say "  database ready in $(( $(date +%s) - started )) s"
}

# The deploy's own replayer, never a second loop (see scripts/dev_db.sh).
run_migrations() {
  if ! dk exec "$NAME-pg" bash -c 'APP_DIR=/app PG_MODE=local PG_USER=acb PG_DB=acb_dev SKIP_PRE_MIGRATION_BACKUP=1 bash /app/scripts/apply_migrations.sh' \
       >> "$STATE_DIR/migrate.log" 2>&1; then
    tail -20 "$STATE_DIR/migrate.log" >&2
    fail "a migration failed. The whole log: $STATE_DIR/migrate.log"
  fi
  say "  migrations: $(grep -oE '\([0-9]+ applied, [0-9]+ already recorded\)' "$STATE_DIR/migrate.log" | tail -1)"
}

# --data prod: the copy, restored once. It holds the ladder AND the RLS
# promotion of production, so neither the demo seed nor the promotion runs.
build_prod_database() {
  local tables marker started
  started=$(date +%s)
  tables="$(dk exec "$NAME-pg" psql -X -U acb -d acb_dev -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'" | tr -d '[:space:]')"
  marker="$(dk exec "$NAME-pg" psql -X -U acb -d acb_dev -tAc \
    "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = 'acb_dev'" | tr -d '\r')"
  if [ "${tables:-0}" = 0 ]; then
    [ "${COPY_CHECKED:-0}" = 1 ] || check_copy
    say "  empty database: restoring the copy $(basename "$COPY") (about a minute)"
    restore_copy
    set_state COPY "$COPY"
    pg_sql -c "COMMENT ON DATABASE acb_dev IS 'dev_up: prod copy $(basename "$COPY")'" >/dev/null
    say "  restored: $(dk exec "$NAME-pg" psql -X -U acb -d acb_dev -tAc \
      "SELECT count(*) FILTER (WHERE relforcerowsecurity) || ' of ' || count(*) || ' tables force RLS' FROM pg_class WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace" | tr -d '\r')"
  elif [ "${marker#dev_up: prod copy }" = "$marker" ]; then
    fail "this database has tables, but no restore of a copy finished. Start again with a fresh one:
         bash scripts/dev_up.sh --reset --data prod --as <email>"
  fi
  # A second defence after LD-8c: the live push keys of real devices never
  # reach a running stack. On each start, before the gateway starts.
  pg_sql -c "DELETE FROM push_subscriptions" >/dev/null
  # The same grants as the demo stack (NOSUPERUSER NOBYPASSRLS), then the
  # branch's pending migrations, then the grants again for their new tables.
  ensure_app_role
  grant_app_role
  run_migrations
  grant_app_role
  say "  database ready in $(( $(date +%s) - started )) s"
}

# The LAST thing the seed writes, so a seed that failed half-way reads as not seeded.
seeded() {
  [ "$(dk exec "$NAME-pg" psql -X -U acb -d acb_dev -tAc \
    "SELECT count(*) FROM pm_projects WHERE name = 'Northwind Warehouse Move'" 2>/dev/null | tr -d '[:space:]')" != 0 ]
}

# ── Commands ────────────────────────────────────────────────────────────────

cmd_up() {
  refuse_dotenv
  local had_state=0
  load_state && had_state=1
  set_mode
  if [ "$DATA" = prod ]; then
    check_as_format
    pick_copy
  fi
  need_tools
  [ "$had_state" = 1 ] || new_state
  say "Metorite dev stack, slot $SLOT ($NAME), $DATA data"
  start_containers
  if [ "$DATA" = prod ]; then
    build_prod_database
    check_as
  else
    build_database
  fi

  if [ "$DB_ONLY" = 1 ]; then
    say ""
    say "Database only. No gateway and no web app started."
    say "  Database  postgresql://acb:acb@127.0.0.1:$DB_PORT/acb_dev   (owner. The app role is acb_app.)"
    return 0
  fi

  if [ "$DATA" = prod ]; then
    # The gateway and the web app take the member when they start. Another
    # member needs a restart of both.
    local running_as
    running_as="$(val AS)"
    if [ -n "$running_as" ] && [ "$running_as" != "$ME" ] \
       && { wait_http "http://127.0.0.1:$GW_PORT/health" 0 || wait_http "http://127.0.0.1:$WEB_PORT/api/health" 0; }; then
      fail "the stack runs as $running_as. Run --stop, then start again with --as $ME."
    fi
    set_state AS "$ME"
  fi

  if wait_http "http://127.0.0.1:$GW_PORT/health" 0 2>/dev/null; then
    say "  gateway already running"
  else
    port_busy "$GW_PORT" && fail "port $GW_PORT is in use by another process"
    say "  starting the gateway"
    launch gateway "$REPO" gateway_env uv run uvicorn gateway.main:app \
      --host 127.0.0.1 --port "$GW_PORT" --reload --reload-dir apps/services --reload-dir packages
    wait_http "http://127.0.0.1:$GW_PORT/health" 240 \
      || { tail -30 "$STATE_DIR/gateway.log" >&2; fail "the gateway did not answer /health. Log: $STATE_DIR/gateway.log"; }
  fi

  # The demo seed only: a copy holds real organizations.
  if [ "$DATA" = demo ] && ! seeded; then
    say "  seeding Kite & Co. and Northwind (about 30 s)"
    local -a envs
    mapfile -t envs < <(seed_env)
    ( cd "$REPO" && env -i "${envs[@]}" uv run python scripts/demo_seed/seed_demo.py ) \
      > "$STATE_DIR/seed.log" 2>&1 \
      || { tail -20 "$STATE_DIR/seed.log" >&2; fail "the seed failed. Run --reset for a clean start. Log: $STATE_DIR/seed.log"; }
    { grep -E "isolation" "$STATE_DIR/seed.log" || true; } | sed 's/^ */  /'
  fi

  local web_dir="$REPO/workbench/control_plane"
  if wait_http "http://127.0.0.1:$WEB_PORT/api/health" 0 2>/dev/null; then
    say "  web app already running"
  else
    port_busy "$WEB_PORT" && fail "port $WEB_PORT is in use by another process"
    if [ ! -f "$web_dir/node_modules/next/dist/bin/next" ]; then
      # npm ci, not npm install: it never rewrites package-lock.json.
      say "  installing the web app's packages (npm ci, about 90 s)"
      ( cd "$web_dir" && npm ci --no-audit --no-fund --loglevel=error ) > "$STATE_DIR/npm.log" 2>&1 \
        || { tail -20 "$STATE_DIR/npm.log" >&2; fail "npm ci failed. Log: $STATE_DIR/npm.log"; }
    fi
    say "  starting the web app"
    launch web "$web_dir" web_env node node_modules/next/dist/bin/next dev -H 127.0.0.1 -p "$WEB_PORT"
    wait_http "http://127.0.0.1:$WEB_PORT/api/health" 240 \
      || { tail -30 "$STATE_DIR/web.log" >&2; fail "the web app did not answer. Log: $STATE_DIR/web.log"; }
  fi

  say ""
  say "Ready."
  if [ "$DATA" = prod ]; then
    say "  App       http://127.0.0.1:$WEB_PORT   signed in as $ME (the production copy $(basename "$COPY"))"
  else
    say "  App       http://127.0.0.1:$WEB_PORT   signed in as $ME (Kite & Co. Interiors)"
  fi
  say "  Gateway   http://127.0.0.1:$GW_PORT    Python edits reload by themselves"
  say "  Database  postgresql://acb:acb@127.0.0.1:$DB_PORT/acb_dev   (owner. The gateway connects as acb_app.)"
  say "  Logs      bash scripts/dev_up.sh --logs gateway|web"
  say "  Stop      bash scripts/dev_up.sh --stop    (--down also removes the data)"
  say ""
  say "This stack has no keys. Mail sync, sending, WhatsApp, CRM sync and model calls do not"
  say "work here. Test those on staging. A change to uv.lock or package.json needs --stop, then start."
  if [ "$DATA" = prod ]; then
    local newest
    newest="$(newest_copy)"
    say ""
    say "This is a copy of production with real people's data. It stays on this PC: never put"
    say "it in git, a log, a PR or a chat message."
    if [ -n "$newest" ] && [ "$newest" != "$(basename "$COPY")" ]; then
      say "A newer copy exists ($newest). To use it: bash scripts/dev_up.sh --reset --data prod --as $ME"
    fi
  fi
}

cmd_stop() {
  load_state || { say "no dev stack in this worktree"; return 0; }
  stop_one web "$WEB_PORT"
  stop_one gateway "$GW_PORT"
  say "stopped the gateway and the web app (slot $SLOT). The database and Redis keep running."
}

cmd_down() {
  load_state || { say "no dev stack in this worktree"; return 0; }
  stop_one web "$WEB_PORT"
  stop_one gateway "$GW_PORT"
  dk rm -f "$NAME-pg" "$NAME-redis" >/dev/null 2>&1 || true
  rm -rf "$STATE_DIR"
  say "removed the dev stack of this worktree (slot $SLOT), data included"
}

cmd_status() {
  load_state || { say "no dev stack in this worktree. Start one: bash scripts/dev_up.sh"; return 0; }
  local s data
  data="$(val DATA)"
  say "slot $SLOT ($NAME), ${data:-demo} data"
  for s in pg redis; do
    say "  $s: $(dk inspect -f '{{.State.Status}}' "$NAME-$s" 2>/dev/null || echo absent)"
  done
  wait_http "http://127.0.0.1:$GW_PORT/health" 0 && s=up || s=down
  say "  gateway: $s   http://127.0.0.1:$GW_PORT"
  wait_http "http://127.0.0.1:$WEB_PORT/api/health" 0 && s=up || s=down
  say "  web:     $s   http://127.0.0.1:$WEB_PORT"
}

cmd_check() {
  load_state || fail "no dev stack in this worktree"
  wait_http "http://127.0.0.1:$GW_PORT/health" 0 || fail "the gateway is down"
  wait_http "http://127.0.0.1:$WEB_PORT/api/health" 0 || fail "the web app is down"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/api/projects/nodes")"
  [ "$code" = 200 ] || fail "GET /api/projects/nodes through the web app answered $code"
  say "  gateway, web app and the projects API answer"
  if [ "$(val DATA)" = prod ]; then
    say "  the tenant-isolation check reads the demo seed, so a stack with --data prod skips it"
    return 0
  fi
  local -a envs
  mapfile -t envs < <(seed_env)
  ( cd "$REPO" && env -i "${envs[@]}" uv run python scripts/demo_seed/seed_demo.py --check )
}

cmd_env() {
  load_state || fail "no dev stack in this worktree"
  # Not DATABASE_URL: tests/conftest.py reads that name, and a suite that
  # resets its database must never reach this one (see engineering_practice.md §1.1).
  printf 'export DEV_UP_APP_URL=http://127.0.0.1:%s\n' "$WEB_PORT"
  printf 'export DEV_UP_GATEWAY_URL=http://127.0.0.1:%s\n' "$GW_PORT"
  printf 'export DEV_UP_GATEWAY_TOKEN=%s\n' "$TOKEN"
  printf 'export DEV_UP_DB_URL=postgresql://acb:acb@127.0.0.1:%s/acb_dev\n' "$DB_PORT"
  printf 'export DEV_UP_APP_DB_URL=postgresql://acb_app:acb_app@127.0.0.1:%s/acb_dev\n' "$DB_PORT"
}

cmd_logs() {
  load_state || fail "no dev stack in this worktree"
  local which="${1:-gateway}"
  [ -f "$STATE_DIR/$which.log" ] || fail "no $which log in $STATE_DIR"
  tail -n 50 -f "$STATE_DIR/$which.log"
}

# For tests and for a look before a start: what each process would get.
cmd_print_env() {
  load_state 2>/dev/null || { SLOT=0; ports_for 0; TOKEN=dev-preview; NAME=metorite-dev-preview; }
  set_mode
  if [ "$DATA" = prod ]; then
    [ -n "$AS" ] || AS="$(val AS)"
    [ -n "$AS" ] || fail "--data prod needs --as <email>, the member to sign in as"
    check_as_format
    ME="$AS"
    pick_copy
  fi
  say "[gateway]"; gateway_env | grep -vE '^(PATH|SYSTEMROOT|WINDIR|COMSPEC|PATHEXT|TEMP|TMP|USERPROFILE|HOME|APPDATA|LOCALAPPDATA|PROGRAMDATA|USERNAME|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE|USER|LANG|TMPDIR)='
  say "[web]";     web_env | grep -vE '^(PATH|SYSTEMROOT|WINDIR|COMSPEC|PATHEXT|TEMP|TMP|USERPROFILE|HOME|APPDATA|LOCALAPPDATA|PROGRAMDATA|USERNAME|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE|USER|LANG|TMPDIR)='
}

flags_header() {
  cat <<EOF
# Production's feature switches, for the local stack (scripts/dev_up.sh).
#
# Source: the flags.env that the nightly staging export carries
# (scripts/staging_export.sh). It holds switch values only, never a key, a
# token or a URL. Read from the staging box on $1.
#
# Refresh it with:  bash scripts/dev_up.sh --refresh-flags
#
# dev_up.sh does NOT apply this file as it is. It applies the same two rules
# that scripts/staging_refresh.sh applies, so a switch that reaches a person or
# a third party (mail, WhatsApp, push, invites, sign-up, sync) is held OFF.
# Format: <scope>:<NAME>=<value>. Scope \`root\` goes to the gateway, scope
# \`workbench\` goes to the web app.
EOF
}

# The staging box is retired (D144), so its default source is gone. The copy
# of --data prod carries production's switches instead.
cmd_refresh_flags() {
  local host="${DEV_UP_FLAGS_HOST:-metorite-dev}" tmp
  if [ "$host" = metorite-dev ]; then
    printf '%s\n' \
      "dev_up: the staging box metorite-dev is retired (D144), so --refresh-flags has no source." \
      "  Use --data prod. It reads production's switches from the copy that scripts/prod_copy.sh pulls." >&2
    exit 2
  fi
  tmp="$(mktemp)"
  ssh -o ConnectTimeout=10 "$host" "sudo cat /var/lib/acb/staging-import/flags.env" \
    | grep -E '^(root|workbench):[A-Z0-9_]+=[A-Za-z0-9_.:-]{0,64}$' > "$tmp" || true
  [ -s "$tmp" ] || { rm -f "$tmp"; fail "no switches came back from $host"; }
  { flags_header "$(date -u +%F)"; cat "$tmp"; } > "$FLAGS_FILE"
  say "wrote $(wc -l < "$tmp" | tr -d ' ') switches to $FLAGS_FILE. Review the diff, then commit it."
  rm -f "$tmp"
}

# ── Arguments ───────────────────────────────────────────────────────────────
# One command, and the options of the data mode in any position.

CMD=""; LOGS_WHICH=""; DATA_ARG=""; COPY_ARG=""; AS=""; DB_ONLY=0; DATA=demo; COPY=""; COPY_CHECKED=0
while [ $# -gt 0 ]; do
  case "$1" in
    --data)    [ $# -ge 2 ] || fail "--data needs demo or prod"; DATA_ARG="$2"; shift 2 ;;
    --copy)    [ $# -ge 2 ] || fail "--copy needs a folder"; COPY_ARG="$2"; shift 2 ;;
    --as)      [ $# -ge 2 ] || fail "--as needs an email address"; AS="$2"; shift 2 ;;
    --db-only) DB_ONLY=1; shift ;;
    *)
      [ -z "$CMD" ] || fail "unknown option '$1'. Try --help."
      CMD="$1"; shift
      if { [ "$CMD" = --logs ] || [ "$CMD" = logs ]; } && [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
        LOGS_WHICH="$1"; shift
      fi
      ;;
  esac
done
case "$DATA_ARG" in ""|demo|prod) ;; *) fail "--data takes demo or prod, not '$DATA_ARG'" ;; esac

case "${CMD:-up}" in
  up|--up|start)    cmd_up ;;
  --stop|stop)      cmd_stop ;;
  --down|down)      cmd_down ;;
  # A reset keeps the data mode of the stack, unless --data names another.
  --reset|reset)    [ -n "$DATA_ARG" ] || DATA_ARG="$(val DATA)"; cmd_down; cmd_up ;;
  --status|status)  cmd_status ;;
  --check|check)    cmd_check ;;
  --env|env)        cmd_env ;;
  --logs|logs)      cmd_logs "${LOGS_WHICH:-gateway}" ;;
  --print-env)      cmd_print_env ;;
  --refresh-flags)  cmd_refresh_flags ;;
  -h|--help|help)   sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) fail "unknown option '$CMD'. Try --help." ;;
esac
