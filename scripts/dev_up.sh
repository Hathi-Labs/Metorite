#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# dev_up.sh: the whole Metorite app on THIS machine, in one command (LD-1).
#
#   bash scripts/dev_up.sh                  start. The first run builds and seeds.
#   bash scripts/dev_up.sh --status         what runs, on which port
#   bash scripts/dev_up.sh --check          health, then the tenant-isolation check
#   bash scripts/dev_up.sh --logs [gateway|web|migrate|seed]
#   bash scripts/dev_up.sh --env            the URLs and DSNs, as export lines
#   bash scripts/dev_up.sh --stop           stop the gateway and the web app
#   bash scripts/dev_up.sh --down           stop, then remove the containers and the data
#   bash scripts/dev_up.sh --reset          --down, then a fresh start
#   bash scripts/dev_up.sh --refresh-flags  copy production's switches from the staging box
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
# Fence: tests/unit/test_dev_up.py.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${DEV_UP_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
STATE_DIR="$REPO/.dev_up"
STATE="$STATE_DIR/state"
FLAGS_FILE="$SCRIPT_DIR/dev_up/prod_flags.env"
ME="dev@fracktal.in"
PG_IMAGE="pgvector/pgvector:pg16"
REDIS_IMAGE="redis:7-alpine"

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

web_env() {
  base_env
  flag_env workbench
  printf '%s\n' \
    "GATEWAY_BASE_URL=http://127.0.0.1:$GW_PORT" \
    "GATEWAY_INTERNAL_TOKEN=$TOKEN" \
    "EXECUTIVE_EMAILS=$ME" \
    "NEXT_TELEMETRY_DISABLED=1"
}

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
  printf 'SLOT=%s\nNAME=%s\nTOKEN=%s\n' "$SLOT" "$NAME" "$TOKEN" > "$STATE"
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
  local mount_root="$REPO"
  [ "$WIN" = 1 ] && mount_root="$(cd "$REPO" && pwd -W)"
  if [ "$(dk inspect -f '{{.State.Running}}' "$NAME-pg" 2>/dev/null || true)" != true ]; then
    if dk inspect "$NAME-pg" >/dev/null 2>&1; then
      dk start "$NAME-pg" >/dev/null
    else
      dk run -d --name "$NAME-pg" --label "metorite.dev_up=$REPO" \
        -e POSTGRES_USER=acb -e POSTGRES_PASSWORD=acb -e POSTGRES_DB=acb_dev \
        -v "$mount_root/infra:/app/infra:ro" -v "$mount_root/scripts:/app/scripts:ro" \
        -p "127.0.0.1:$DB_PORT:5432" "$PG_IMAGE" >/dev/null
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
  for i in $(seq 1 60); do
    dk exec "$NAME-pg" pg_isready -U acb -d acb_dev >/dev/null 2>&1 && return 0
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
  # The deploy's own replayer, never a second loop (see scripts/dev_db.sh).
  if ! dk exec "$NAME-pg" bash -c 'APP_DIR=/app PG_MODE=local PG_USER=acb PG_DB=acb_dev SKIP_PRE_MIGRATION_BACKUP=1 bash /app/scripts/apply_migrations.sh' \
       >> "$STATE_DIR/migrate.log" 2>&1; then
    tail -20 "$STATE_DIR/migrate.log" >&2
    fail "a migration failed. The whole log: $STATE_DIR/migrate.log"
  fi
  say "  migrations: $(grep -oE '\([0-9]+ applied, [0-9]+ already recorded\)' "$STATE_DIR/migrate.log" | tail -1)"

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

# The LAST thing the seed writes, so a seed that failed half-way reads as not seeded.
seeded() {
  [ "$(dk exec "$NAME-pg" psql -X -U acb -d acb_dev -tAc \
    "SELECT count(*) FROM pm_projects WHERE name = 'Northwind Warehouse Move'" 2>/dev/null | tr -d '[:space:]')" != 0 ]
}

# ── Commands ────────────────────────────────────────────────────────────────

cmd_up() {
  refuse_dotenv
  need_tools
  load_state || new_state
  say "Metorite dev stack, slot $SLOT ($NAME)"
  start_containers
  build_database

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

  if ! seeded; then
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
  say "  App       http://127.0.0.1:$WEB_PORT   signed in as $ME (Kite & Co. Interiors)"
  say "  Gateway   http://127.0.0.1:$GW_PORT    Python edits reload by themselves"
  say "  Database  postgresql://acb:acb@127.0.0.1:$DB_PORT/acb_dev   (owner. The gateway connects as acb_app.)"
  say "  Logs      bash scripts/dev_up.sh --logs gateway|web"
  say "  Stop      bash scripts/dev_up.sh --stop    (--down also removes the data)"
  say ""
  say "This stack has no keys. Mail sync, sending, WhatsApp, CRM sync and model calls do not"
  say "work here. Test those on staging. A change to uv.lock or package.json needs --stop, then start."
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
  local s
  say "slot $SLOT ($NAME)"
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

cmd_refresh_flags() {
  local host="${DEV_UP_FLAGS_HOST:-metorite-dev}" tmp
  tmp="$(mktemp)"
  ssh -o ConnectTimeout=10 "$host" "sudo cat /var/lib/acb/staging-import/flags.env" \
    | grep -E '^(root|workbench):[A-Z0-9_]+=[A-Za-z0-9_.:-]{0,64}$' > "$tmp" || true
  [ -s "$tmp" ] || { rm -f "$tmp"; fail "no switches came back from $host"; }
  { flags_header "$(date -u +%F)"; cat "$tmp"; } > "$FLAGS_FILE"
  say "wrote $(wc -l < "$tmp" | tr -d ' ') switches to $FLAGS_FILE. Review the diff, then commit it."
  rm -f "$tmp"
}

case "${1:-up}" in
  up|--up|start)    cmd_up ;;
  --stop|stop)      cmd_stop ;;
  --down|down)      cmd_down ;;
  --reset|reset)    cmd_down; cmd_up ;;
  --status|status)  cmd_status ;;
  --check|check)    cmd_check ;;
  --env|env)        cmd_env ;;
  --logs|logs)      cmd_logs "${2:-gateway}" ;;
  --print-env)      cmd_print_env ;;
  --refresh-flags)  cmd_refresh_flags ;;
  -h|--help|help)   sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) fail "unknown option '$1'. Try --help." ;;
esac
