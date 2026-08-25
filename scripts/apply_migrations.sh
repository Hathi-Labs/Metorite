#!/usr/bin/env bash
# Apply incremental Postgres migrations against the running stack.
#
# Why this exists
# ---------------
# infra/docker-compose.yml only mounts 00_create_databases.sql and
# 01_schema.sql into the container's /docker-entrypoint-initdb.d — and those
# *only* run on first DB init (empty data volume). Every numbered migration
# after 01 (02_*, …, 18_*) must therefore be applied explicitly on each
# deploy, or new columns/tables silently never reach the live database
# (symptom: gateway 500s on SELECTs referencing the new columns).
#
# All migrations 02+ are written to be idempotent (ADD COLUMN IF NOT EXISTS,
# CREATE TABLE/INDEX IF NOT EXISTS, INSERT … ON CONFLICT DO NOTHING), so this
# runner is safe to execute on every deploy.
#
# Usage:  scripts/apply_migrations.sh
# Env:    APP_DIR (default /opt/acb/app), PG_CONTAINER (default acb-postgres)
#         MIGRATION_LOCK_TIMEOUT (default 5s), MIGRATION_LOCK_RETRIES (default 5)
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/acb/app}"
PG_CONTAINER="${PG_CONTAINER:-acb-postgres}"
# Overridable so the ladder can be replayed against a scratch database without
# pretending a temp directory is the whole app checkout.
MIGRATIONS_DIR="${MIGRATIONS_DIR:-$APP_DIR/infra/postgres}"

# --- Never WAIT for a lock. This is the whole outage, in one setting. ---------
#
# 2026-08-06: a gateway session sat `idle in transaction` on
# email_assistant_settings for 14h44m (a hung LLM call, with a SQLAlchemy session
# open across it). This runner then asked for ACCESS EXCLUSIVE on that table to
# replay `39_email_learned_writing_style.sql` — and waited.
#
# Waiting is what made it an outage rather than a slow deploy. Postgres's lock
# queue is FIFO: once an ACCESS EXCLUSIVE request is QUEUED, every later reader
# queues behind it, even though the reader would not have conflicted with the
# stale transaction it is ultimately waiting on. So one idle session plus one
# patient ALTER froze the table for the entire application. Sending mail reads
# email_assistant_settings for the signature; it stopped. Blocked readers each
# pinned a pooled connection until the pool drained, and endpoints with nothing
# to do with email started answering 500.
#
# With a lock_timeout the ALTER gives up in seconds and never enters the queue,
# so a stale reader can delay a migration but can no longer freeze a table.
# Retries absorb the ordinary case (a long-running query holding the table for a
# moment); exhausting them fails the deploy LOUDLY, which is the correct outcome
# and the one that used to be indistinguishable from a hang.
MIGRATION_LOCK_TIMEOUT="${MIGRATION_LOCK_TIMEOUT:-5s}"
MIGRATION_LOCK_RETRIES="${MIGRATION_LOCK_RETRIES:-5}"

# Pull DB credentials from .env when present, else fall back to compose defaults.
ENV_FILE="$APP_DIR/.env"
env_user="acb"
env_db="acb"
if [ -f "$ENV_FILE" ]; then
  env_user="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  env_db="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
fi
# An explicitly exported value wins over .env — that is how a replay targets a
# scratch database. On the VPS neither is exported, so .env stays authoritative.
PG_USER="${PG_USER:-${env_user:-acb}}"
PG_DB="${PG_DB:-${env_db:-acb}}"

say() { printf "\n==> %s\n" "$*"; }

# ── How we reach Postgres ────────────────────────────────────────────────────
# `docker exec` on the VPS; `PG_MODE=local` for a cluster reached over libpq.
# The same seam backup_db.sh and restore_db.sh gained, for the same reason: a
# script that runs on exactly one machine is a script nobody can test, and this
# one replays 150+ files against the production database.
PG_MODE="${PG_MODE:-docker}"
pgi() { if [ "$PG_MODE" = "local" ]; then "$@"; else docker exec -i "$PG_CONTAINER" "$@"; fi; }

if [ "$PG_MODE" = "local" ]; then
  command -v psql >/dev/null || { echo "ERROR: PG_MODE=local but psql is not on PATH." >&2; exit 1; }
elif ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
  echo "ERROR: Postgres container '$PG_CONTAINER' is not running." >&2
  exit 1
fi

# --- Take a backup BEFORE replaying the ladder (BO-23 done-when 4) ----------
# The migrations below are idempotent but forward-only: there are no down
# migrations, and they run under ON_ERROR_STOP=1. A migration that corrupts
# data rather than failing outright leaves nothing to roll back to. Taking the
# dump here — rather than in the deploy YAML — means it also covers anyone who
# runs this script by hand, which is exactly when it is most likely to matter.
#
# Fail CLOSED: if the backup cannot be taken, the migrations do not run. The
# escape hatch is explicit and has to be typed on purpose.
BACKUP_SCRIPT="$APP_DIR/scripts/backup_db.sh"
if [ "${SKIP_PRE_MIGRATION_BACKUP:-0}" = "1" ]; then
  say "Pre-migration backup SKIPPED (SKIP_PRE_MIGRATION_BACKUP=1)"
elif [ -x "$BACKUP_SCRIPT" ] || [ -f "$BACKUP_SCRIPT" ]; then
  say "Pre-migration backup"
  # No --verify-restore here: the deploy path is latency-sensitive and the
  # nightly timer already carries the deep check. The cheap pg_restore --list
  # integrity check inside backup_db.sh still runs on every dump.
  if ! bash "$BACKUP_SCRIPT"; then
    echo "ERROR: pre-migration backup FAILED — refusing to apply migrations." >&2
    echo "       Fix the backup, or re-run with SKIP_PRE_MIGRATION_BACKUP=1 if" >&2
    echo "       you accept replaying 140+ forward-only migrations with no" >&2
    echo "       restore point." >&2
    exit 1
  fi
else
  # Not fatal only because this script predates backup_db.sh and may be run
  # from an older checkout; say so loudly rather than passing silently.
  echo "  !! $BACKUP_SCRIPT not found — applying migrations WITHOUT a backup." >&2
fi

# --- Prove the prelude PARSES before betting the whole ladder on it ----------
#
# Learned the hard way on the very first deploy that carried it: the prelude
# shipped as `SET lock_timeout = 5s;` — unquoted — and Postgres answers
# "trailing junk after numeric literal". Under ON_ERROR_STOP=1 that failed the
# FIRST migration, so a guard whose entire purpose was to stop a stalled session
# blocking deploys blocked every deploy itself. The tests missed it because they
# asserted the string was PRESENT, not that it was valid SQL, and the harness's
# fake psql drained stdin without parsing it.
#
# Two lessons, both encoded here:
#   1. Quote the value. '5s' and '5000' are both accepted; a bare 5s is not.
#   2. A safety feature must not be able to brick the thing it protects. If the
#      prelude does not parse — a typo'd MIGRATION_LOCK_TIMEOUT, a server that
#      spells it differently — say so LOUDLY and fall back to running without
#      it. Degrading to the previous behaviour is survivable; making the box
#      undeployable is not.
# Per-run error capture. NOT /tmp/migrate_err: `fs.protected_regular=2`
# (Ubuntu default) forbids opening an existing file in a sticky world-writable
# dir owned by another user — root included — so one manual run as `acb` left
# a file that made every later root-run apply die with "Permission denied" at
# the redirect (live, 2026-08-25, while deploying migration 187). Same trap,
# same fix as backup_db.sh's verify_restore.log.
MIGRATE_ERR="$(mktemp)"
trap 'rm -f "$MIGRATE_ERR"' EXIT

LOCK_PRELUDE="SET lock_timeout = '$MIGRATION_LOCK_TIMEOUT';"
if ! printf '%s\n' "$LOCK_PRELUDE" \
     | pgi psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -q \
         >/dev/null 2>"$MIGRATE_ERR"; then
  echo "  !! lock_timeout prelude REJECTED by this server:" >&2
  sed 's/^/     /' "$MIGRATE_ERR" >&2
  echo "  !! MIGRATION_LOCK_TIMEOUT='$MIGRATION_LOCK_TIMEOUT' is not a value it" >&2
  echo "     accepts. Applying migrations WITHOUT a lock timeout — a stale" >&2
  echo "     reader can once again freeze a table for every later query." >&2
  echo "     Fix the value; do not leave this in place." >&2
  LOCK_PRELUDE=""
fi

# ── The ledger (BO-6) ────────────────────────────────────────────────────────
#
# Until this, every deploy replayed EVERY numbered migration — 150+ files. That
# worked only because each is hand-written to be idempotent, which is a property
# of 152 authors' care rather than of the system.
#
# The cost was not only time. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` still
# takes ACCESS EXCLUSIVE when the column is already there, and on 2026-08-06 one
# such queued ALTER, behind a session left `idle in transaction`, froze every
# later reader of that table and took the app down. Asking for 150 locks we do
# not need is the exposure; this removes it.
#
# Created here rather than relying on 153_schema_migrations.sql having run,
# because the ledger must exist BEFORE the loop that would apply that file.
# `IF NOT EXISTS` makes the two agree; the migration file is what documents the
# table and what a fresh install gets.
REPLAY_ALL="${MIGRATION_REPLAY_ALL:-0}"

if [ "$REPLAY_ALL" = "1" ]; then
  say "MIGRATION_REPLAY_ALL=1 — replaying the whole ladder, ignoring the ledger"
  APPLIED_SET=""
else
  pgi psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -q >/dev/null <<'LEDGER'
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename    text PRIMARY KEY,
  checksum    text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now(),
  duration_ms integer
);
LEDGER
  # One query, not one per file: 150 round trips through `docker exec` is
  # several seconds of deploy for something that fits in a single result.
  # `< /dev/null`: `pgi` is `docker exec -i`, which DRAINS stdin. A caller that
  # feeds this script on stdin (the deploy does — `ssh 'bash -s' < …`) would
  # lose every unread line to this one query. Belt as well as braces: the deploy
  # also isolates the whole script, but a helper that steals stdin should not
  # rely on every caller remembering.
  APPLIED_SET="$(pgi psql -tA -U "$PG_USER" -d "$PG_DB" \
    -c "SELECT filename || ' ' || checksum FROM schema_migrations" \
    2>/dev/null < /dev/null || true)"
fi

# "new" | "same" | "changed", from the set read above.
ledger_state() {
  row="$(printf '%s\n' "$APPLIED_SET" | grep -F "$1 " || true)"
  if [ -z "$row" ]; then echo new
  elif [ "${row#* }" = "$2" ]; then echo same
  else echo changed
  fi
}

say "Applying migrations to db '$PG_DB' as '$PG_USER' (container: $PG_CONTAINER)"

# Apply 02+ in numeric order. Only NUMBERED migration files (NN_*.sql) are
# applied — non-numbered .sql files in this dir are reference artifacts, NOT
# migrations. In particular schema.generated.sql is a full pg_dump snapshot (the
# consolidated schema, for humans/tools) whose raw CREATE TYPE/TABLE statements
# are NOT idempotent and MUST NOT be replayed onto an already-migrated DB (it
# errors with `type "..." already exists`). 00/01 are init-only (handled by
# initdb on first boot) and contain statements that aren't re-runnable.
shopt -s nullglob
applied=0
skipped=0
changed_files=""
# Match 2-OR-MORE-digit numeric prefixes (NN_ … NNN_) and apply in NUMERIC
# order. `sort -V` (version sort) is essential now that 3-digit numbers exist:
# plain lexical `sort` puts "100_" BEFORE "99_", which would run a later
# migration before an earlier one. The 2-digit scheme filled up at 99, so
# migrations continue at 100+.
migration_files="$(ls "$MIGRATIONS_DIR"/[0-9][0-9]*_*.sql 2>/dev/null | sort -V)"
if [ -z "$migration_files" ]; then
  # NOT a silent success. `nullglob` deletes a pattern that matches nothing, so
  # without this guard the `ls` below runs bare, lists the CURRENT directory,
  # and hands psql whatever it finds — the first symptom being a syntax error
  # in AGENTS.md. Found while replaying the ladder against a scratch DB with a
  # temp APP_DIR, which is exactly how a mis-set APP_DIR would fail in prod.
  echo "ERROR: no numbered migrations in '$MIGRATIONS_DIR'." >&2
  echo "       APP_DIR='$APP_DIR' — is it the app checkout?" >&2
  exit 1
fi
for f in $migration_files; do
  base="$(basename "$f")"
  case "$base" in
    00_*|01_*) continue ;;  # init-only, skip
  esac

  # Already applied, unchanged? Skip it. This is the win: a steady-state deploy
  # runs ~0 files instead of 152, and takes ~0 locks instead of hundreds.
  sum="$(sha256sum "$f" | cut -d" " -f1)"
  case "$(ledger_state "$base" "$sum")" in
    same) skipped=$((skipped + 1)); continue ;;
    changed)
      # Re-apply rather than refuse. Every file here is idempotent by
      # construction and the repo's whole workflow assumes an edit re-runs on
      # the next deploy; turning that into a hard failure the day the ledger
      # arrives would break deploys for a legitimate fix. But say it LOUDLY —
      # editing an applied migration is a real bug class, because on a box that
      # already ran the original the change only lands if something re-runs it,
      # and until now nothing recorded that it had.
      printf "    ! %s CHANGED since it was applied — re-applying\n" "$base"
      changed_files="$changed_files $base"
      ;;
  esac

  printf "    - %s ... " "$base"
  started_ms="$(date +%s%3N)"
  attempt=1
  while :; do
    # The prelude is prepended to the stream rather than passed as a psql flag so
    # it lands in the SAME session as the migration, ahead of any BEGIN the file
    # opens. Session-level, so one SET covers every statement in it. Empty when
    # the probe above rejected it, in which case the migration runs exactly as it
    # did before this guard existed.
    if { [ -n "$LOCK_PRELUDE" ] && printf '%s\n' "$LOCK_PRELUDE"; cat "$f"; } \
         | pgi psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -q \
             >/dev/null 2>"$MIGRATE_ERR"; then
      echo "ok"
      applied=$((applied + 1))
      # Recorded AFTER success, never before: a row for a migration that failed
      # would make the next deploy skip it and the schema silently diverge.
      if [ "$REPLAY_ALL" != "1" ]; then
        pgi psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -q >/dev/null <<SQL || \
          echo "      !! could not record $base (it WILL re-run next deploy)" >&2
INSERT INTO schema_migrations (filename, checksum, duration_ms)
VALUES ('$base', '$sum', $(( $(date +%s%3N) - started_ms )))
ON CONFLICT (filename) DO UPDATE
  SET checksum = EXCLUDED.checksum, applied_at = now(),
      duration_ms = EXCLUDED.duration_ms;
SQL
      fi
      break
    fi
    # Only a LOCK timeout is retryable. Any other psql error is a real migration
    # failure and must not be papered over by trying it four more times.
    if grep -qi 'lock timeout' "$MIGRATE_ERR" \
       && [ "$attempt" -lt "$MIGRATION_LOCK_RETRIES" ]; then
      printf "lock busy, retry %d/%d ... " "$attempt" "$MIGRATION_LOCK_RETRIES"
      sleep $((attempt * 5))
      attempt=$((attempt + 1))
      continue
    fi
    echo "FAILED"
    if grep -qi 'lock timeout' "$MIGRATE_ERR"; then
      echo "      Could not acquire a lock on this table after $MIGRATION_LOCK_RETRIES tries." >&2
      echo "      Something is holding it. Find the holder before re-running:" >&2
      echo "        SELECT pid, state, now()-state_change AS dur, query" >&2
      echo "          FROM pg_stat_activity" >&2
      echo "         WHERE datname = '$PG_DB' AND state <> 'idle'" >&2
      echo "         ORDER BY state_change;" >&2
      echo "      A session 'idle in transaction' for minutes is a wedged app" >&2
      echo "      handler; pg_terminate_backend(<pid>) releases it." >&2
    fi
    echo "      ----- psql error -----" >&2
    sed 's/^/      /' "$MIGRATE_ERR" >&2
    exit 1
  done
done

if [ -n "$changed_files" ]; then
  echo
  echo "  !! These migrations CHANGED after they had been applied:" >&2
  for c in $changed_files; do echo "     - $c" >&2; done
  echo "     Re-applied here, but any box that has not deployed since the edit" >&2
  echo "     is running the ORIGINAL. Prefer a new numbered file." >&2
fi

say "Migrations complete ($applied applied, $skipped already recorded)"
