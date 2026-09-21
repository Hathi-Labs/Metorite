#!/usr/bin/env bash
# Bring up the local scratch databases the R8 suites need.
#
# 🔴 **Why this script exists.** `engineering_practice.md` §1 has described the
# "`mt-scratch` pattern (:5433, full ladder applied)" for weeks, and a pattern
# is not a command. Measured 2026-08-30: a local `pytest` over the 26 Console
# suites ran **123 passed, 843 skipped** — every skip reading "R8 requires a
# REAL Postgres". A green run had proven about one test in eight, and nothing
# said so loudly enough to notice.
#
# Usage:
#     bash scripts/dev_db.sh            # start, apply ladders, print the DSNs
#     eval "$(bash scripts/dev_db.sh --export)"   # ...and set them in this shell
#     bash scripts/dev_db.sh --down     # throw both away
#
# ⚠️ **These are THROWAWAY databases and nothing here touches production.** The
# containers are named `metorite-scratch-*`, they bind to loopback only, and
# the credentials are `cc/cc` and `acb/acb` on purpose — a scratch database
# that shares a password with anything real is a scratch database somebody will
# eventually point at the wrong host.
set -euo pipefail

# Where the repo is, resolved from THIS script rather than the caller's cwd:
# `apply_migrations.sh` is reached by path below, and `bash scripts/dev_db.sh`
# from a subdirectory would otherwise not find it.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CC_CONTAINER="metorite-scratch-console"
TENANT_CONTAINER="metorite-scratch-tenant"
CC_PORT="${METORITE_SCRATCH_CONSOLE_PORT:-5433}"
TENANT_PORT="${METORITE_SCRATCH_TENANT_PORT:-5434}"

CC_DSN="postgresql+psycopg://cc:cc@127.0.0.1:${CC_PORT}/cc_platform"
TENANT_DSN="postgresql+psycopg://acb:acb@127.0.0.1:${TENANT_PORT}/acb_tenant"

# ⚠️ The tenant ladder needs pgvector, and it fails DEEP inside 01_schema.sql
# as a migration error rather than as "the image is wrong". Same trap CI's own
# comment warns about.
CC_IMAGE="postgres:16"
TENANT_IMAGE="pgvector/pgvector:pg16"

say() { printf '%s\n' "$*" >&2; }

if [ "${1:-}" = "--down" ]; then
    docker rm -f "$CC_CONTAINER" "$TENANT_CONTAINER" >/dev/null 2>&1 || true
    say "scratch databases removed"
    exit 0
fi

EXPORT_ONLY=""
[ "${1:-}" = "--export" ] && EXPORT_ONLY=1

if ! docker info >/dev/null 2>&1; then
    say "ERROR: the Docker daemon is not running."
    say "       On Windows, start Docker Desktop and run this again."
    exit 1
fi

# ── Start, or reuse what is already up ──────────────────────────────────────
#
# ⚠️ Reused, never recreated. Re-applying the ladder over an existing database
# is the point: every migration is written to be idempotent, and replaying it
# against a database that already holds rows is a closer rehearsal of the real
# deploy than a fresh one.
start_one() {
    local name="$1" image="$2" port="$3" user="$4" db="$5"
    if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" = "true" ]; then
        say "  reusing $name on :$port"
        return
    fi
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" \
        -e POSTGRES_USER="$user" -e POSTGRES_PASSWORD="$user" -e POSTGRES_DB="$db" \
        -p "127.0.0.1:${port}:5432" "$image" >/dev/null
    say "  started $name on :$port"
}

wait_one() {
    local name="$1" user="$2"
    for _ in $(seq 1 30); do
        if docker exec "$name" pg_isready -U "$user" >/dev/null 2>&1; then return; fi
        sleep 1
    done
    say "ERROR: $name did not become ready"
    exit 1
}

say "Scratch databases:"
start_one "$CC_CONTAINER" "$CC_IMAGE" "$CC_PORT" cc cc_platform
start_one "$TENANT_CONTAINER" "$TENANT_IMAGE" "$TENANT_PORT" acb acb_tenant
wait_one "$CC_CONTAINER" cc
wait_one "$TENANT_CONTAINER" acb

# ── Apply the Console ladder ────────────────────────────────────────────────
#
# ⚠️ **Through the CONTAINER's psql, not the host's.** A Windows dev box has no
# `psql`, which is what `scripts/apply_customer_console_migrations.sh` needs —
# so on the primary development platform that script cannot run at all. Piping
# each file into the container removes the dependency entirely.
#
# ⚠️ **`ON_ERROR_STOP=1`, and the loop breaks.** Without it psql reports success
# after a failed statement, and the ladder "applies" while the schema is half
# built — the same shape as the deploy that reported success while shipping
# nothing.
say "Console ladder:"
applied=0
for f in infra/customer_console/[0-9][0-9][0-9]_*.sql; do
    if ! docker exec -i "$CC_CONTAINER" psql -U cc -d cc_platform \
            -v ON_ERROR_STOP=1 -q < "$f" 2>/tmp/dev_db_err; then
        say "  FAILED $(basename "$f")"
        sed 's/^/    /' /tmp/dev_db_err >&2
        exit 1
    fi
    applied=$((applied + 1))
done
say "  applied $applied files"

# Every file must have landed, not merely most. The applier keeps no ledger, so
# a file it silently skipped leaves no trace for anyone to find later.
want="$(ls infra/customer_console/[0-9][0-9][0-9]_*.sql | wc -l | tr -d ' ')"
if [ "$applied" != "$want" ]; then
    say "ERROR: applied $applied of $want Console migrations"
    exit 1
fi

# ── Apply the TENANT ladder ─────────────────────────────────────────────────
#
# 🔴 **This script started the tenant database and never built it (H-96).** Its
# own header promised the "mt-scratch pattern (:5433, full ladder applied)",
# and a reader took the printed DSN as proof. Measured 2026-09-02: the Console
# database got every file and the tenant database got ZERO tables. An R8 suite
# needing a tenant table then failed, or skipped, against a database this
# script had just called ready.
#
# ⚠️ **THREE steps, and the ladder alone is only the third.** A replay of
# 02..209 onto a fresh container fails at 03, then again at 95. Both read as a
# broken migration. Neither is one. They are missing prerequisites.
#
# ⚠️ **The ladder goes through scripts/apply_migrations.sh**, the same replayer
# the deploy runs, and never a second loop written here. That script already
# carries the numeric sort, the init-only skips and ON_ERROR_STOP. A plain glob
# puts 100_ before 10_ and the ladder dies at once, so a copy here would be a
# second opinion about the order of 209 files.
#
# ⚠️ **SKIP_PRE_MIGRATION_BACKUP=1 is correct HERE and nowhere else.** A
# scratch container that was empty a minute ago has nothing to restore.
say "Tenant ladder:"

# 1. The extensions. 01_schema.sql creates uuid-ossp and vector itself, but
#    03_pending_commits.sql wants uuid_generate_v4() and this costs nothing
#    when they are already there.
docker exec "$TENANT_CONTAINER" psql -U acb -d acb_tenant -q \
    -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; CREATE EXTENSION IF NOT EXISTS pgcrypto;' \
    >/dev/null 2>&1 || true

# 2. The base schema — ONLY on an empty database.
#
# ⚠️ 01_schema.sql is what initdb lays down on a fresh volume, and it is NOT
# re-runnable. start_one above REUSES a running container, so replaying it over
# a built database fails on the first CREATE. The table count is the test,
# because it is the one fact that tells the two cases apart.
tenant_tables="$(docker exec "$TENANT_CONTAINER" psql -U acb -d acb_tenant -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" \
    2>/dev/null | tr -d '[:space:]')"
if [ "${tenant_tables:-0}" = "0" ]; then
    if ! docker exec -i "$TENANT_CONTAINER" psql -U acb -d acb_tenant \
            -v ON_ERROR_STOP=1 -q < infra/postgres/01_schema.sql 2>/tmp/dev_db_err; then
        say "  FAILED 01_schema.sql"
        sed 's/^/    /' /tmp/dev_db_err >&2
        say '  !! If that named the "vector" type, the IMAGE is wrong — the'
        say '     tenant container must be pgvector, not stock postgres.'
        exit 1
    fi
    say "  applied 01_schema.sql (the database was empty)"
else
    say "  01_schema.sql skipped — $tenant_tables tables already here"
fi

# 3. The ladder itself. The replayer keeps a ledger, so a re-run costs seconds
#    rather than replaying all 209 files.
if ! APP_DIR="$REPO_ROOT" PG_CONTAINER="$TENANT_CONTAINER" \
        PG_USER=acb PG_DB=acb_tenant SKIP_PRE_MIGRATION_BACKUP=1 \
        bash "$REPO_ROOT/scripts/apply_migrations.sh" \
        >/tmp/dev_db_tenant.log 2>&1; then
    say "  FAILED — the last 20 lines of the replay:"
    tail -20 /tmp/dev_db_tenant.log | sed 's/^/    /' >&2
    exit 1
fi
tenant_note="$(grep -oE '\([0-9]+ applied, [0-9]+ already recorded\)' \
    /tmp/dev_db_tenant.log | tail -1)"
say "  ${tenant_note:-replayed}"

if [ -n "$EXPORT_ONLY" ]; then
    printf 'export CUSTOMER_CONSOLE_DATABASE_URL=%s\n' "$CC_DSN"
    printf 'export TENANT_LADDER_DATABASE_URL=%s\n' "$TENANT_DSN"
    exit 0
fi

say ""
say "Ready. Export these, then run the suite:"
say ""
say "  export CUSTOMER_CONSOLE_DATABASE_URL=$CC_DSN"
say "  export TENANT_LADDER_DATABASE_URL=$TENANT_DSN"
say ""
say "  eval \"\$(bash scripts/dev_db.sh --export)\"    # both, in one line"
say ""
say "⚠️ Without these, 843 R8 tests SKIP and the run still reads green."
say ""
# 📌 Measured 2026-09-21, when the tenant ladder was added. Two suites want
# DATABASE_URL rather than TENANT_LADDER_DATABASE_URL, and this script does
# NOT set it — deliberately.
#
# Setting it does make `test_tenant_coverage.py` run, and it then FAILS: every
# table reports "missing tenant scoping in the live catalog". That is WS-29's
# unfinished RLS retrofit, not a broken setup — but it reads exactly like one,
# and DATABASE_URL is the app's main DSN, so exporting it here would point
# anything else in the shell at this scratch database too.
say "📌 Two suites want DATABASE_URL instead, and this script does not set it."
say "   Setting it surfaces WS-29's unfinished tenant RLS, which reads as a"
say "   broken setup and is not one. Set it by hand if that is what you want:"
say "     export DATABASE_URL=postgresql+asyncpg://acb:acb@127.0.0.1:${TENANT_PORT}/acb_tenant"
