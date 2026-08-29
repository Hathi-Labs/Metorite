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
