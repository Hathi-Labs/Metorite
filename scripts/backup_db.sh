#!/usr/bin/env bash
# BO-23 — application-level Postgres backup.
#
# Why this exists
# ---------------
# Until this script, the ONLY recovery path was Hostinger's VM image backup.
# Measured on 2026-08-03, that path is:
#   - weekly, with exactly TWO images retained (then 2026-07-22 and 2026-07-29)
#   - therefore an RPO of up to 7 days
#   - ~58 minutes to restore, and it reverts the WHOLE MACHINE — code, .env,
#     Docker volumes, everything — so "recover one dropped table" is not a
#     thing it can do
# Meanwhile 140+ migrations replay forward-only on every deploy. A migration
# that corrupts data is therefore unrecoverable in any granular way.
#
# This gives us a per-database, point-in-day logical backup that can be
# restored into a SCRATCH database and inspected before anything live is
# touched. See deploy/hostinger/BACKUP-RESTORE.md for the runbook.
#
# Where backups are written, and why NOT under the app dir
# --------------------------------------------------------
# BACKUP_DIR defaults to /opt/acb/backups — deliberately OUTSIDE
# /opt/acb/app. The deploy pipeline runs `git reset --hard`, which has already
# destroyed tracked runtime state in this repo's history. Anything written
# under the app dir is one deploy away from being gone.
#
# Usage:
#   scripts/backup_db.sh                  # dump + cheap integrity check
#   scripts/backup_db.sh --verify-restore # ALSO restore into a scratch DB
#
# Env:
#   BACKUP_DIR      (default /opt/acb/backups)
#   PG_CONTAINER    (default acb-postgres)
#   APP_DIR         (default /opt/acb/app)
#   KEEP_DAILY      (default 14)
#   BACKUP_REMOTE   optional rsync destination for an off-box copy, e.g.
#                   user@host:/srv/cc-backups . UNSET BY DEFAULT, and the
#                   script says so loudly — see "Off-box" below.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/acb/backups}"
PG_CONTAINER="${PG_CONTAINER:-acb-postgres}"
APP_DIR="${APP_DIR:-/opt/acb/app}"
KEEP_DAILY="${KEEP_DAILY:-14}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
VERIFY_RESTORE=0
[ "${1:-}" = "--verify-restore" ] && VERIFY_RESTORE=1

say()  { printf "\n==> %s\n" "$*"; }
warn() { printf "  !! %s\n" "$*" >&2; }

# Credentials come from the same place apply_migrations.sh reads them, so the
# two can never disagree about which cluster is "the" database.
ENV_FILE="$APP_DIR/.env"
PG_USER="acb"
if [ -f "$ENV_FILE" ]; then
  PG_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  PG_USER="${PG_USER:-acb}"
fi

# The APPLICATION database, from the same seam — the path component of
# DATABASE_URL. ⚠️ This exists because "the app database is named acb" stopped
# being true: a box provisioned Supabase-style names it `postgres`
# (POSTGRES_DB=postgres), and on 2026-08-25 the enumeration below — which
# excludes `postgres` as "the maintenance database" — therefore dumped NOTHING
# and the pre-migration gate fail-closed on a live deploy. The app database is
# a fact of the environment, never of this script.
APP_DB="acb"
if [ -f "$ENV_FILE" ]; then
  _dburl="$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  if [ -n "$_dburl" ]; then
    _dbname="${_dburl##*/}"
    _dbname="${_dbname%%\?*}"
    APP_DB="${_dbname:-acb}"
  fi
fi

# ── How we reach Postgres ────────────────────────────────────────────────────
#
# On the VPS the cluster lives in a container, so every command goes through
# `docker exec`. That coupling is also why this script had never been run
# anywhere else — and therefore why, per this ticket's own rule, it was not yet
# a backup. `PG_MODE=local` runs the identical logic against a Postgres reached
# through libpq (PGHOST/PGPORT/PGUSER), which is what makes the restore
# rehearsal in scripts/rehearse_restore.sh possible on any machine.
#
# The seam is two functions rather than a variable prefix on purpose: `docker
# exec -i` and a bare local command differ in more than a prefix, and a string
# that is sometimes empty splits badly under `set -u`.
PG_MODE="${PG_MODE:-docker}"
# ⚠️ The docker branch must invoke `docker exec` — the first version of this
# seam called the function's own name there, which is infinite recursion, which
# is a bash segfault at the pre-migration gate on every deploy (2026-08-07).
# CI never saw it because the rehearsal runs PG_MODE=local; the executing guard
# in tests/unit/test_backup_deploy_wiring.py now runs this exact branch.
pg()  { if [ "$PG_MODE" = "local" ]; then "$@"; else docker exec "$PG_CONTAINER" "$@"; fi; }
pgi() { if [ "$PG_MODE" = "local" ]; then "$@"; else docker exec -i "$PG_CONTAINER" "$@"; fi; }

require_postgres() {
  if [ "$PG_MODE" = "local" ]; then
    command -v psql >/dev/null || { echo "ERROR: PG_MODE=local but psql is not on PATH." >&2; exit 1; }
    psql -U "$PG_USER" -d postgres -tAc 'select 1' >/dev/null 2>&1 || {
      echo "ERROR: PG_MODE=local but no Postgres answers as '$PG_USER' (check PGHOST/PGPORT/PGPASSWORD)." >&2
      exit 1
    }
  elif ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    echo "ERROR: Postgres container '$PG_CONTAINER' is not running." >&2
    exit 1
  fi
}

require_postgres

STAMP="$(date -u '+%Y-%m-%dT%H%M%SZ')"
DEST="$BACKUP_DIR/$STAMP"
mkdir -p "$DEST"

say "Backing up cluster '$PG_CONTAINER' as '$PG_USER' -> $DEST"

# --- Globals first -----------------------------------------------------------
# Roles and their passwords live in the CLUSTER, not in any one database. A
# per-database dump restored into a fresh cluster comes up with no roles, and
# every GRANT in it fails. Dump globals separately so a bare-metal rebuild is
# actually possible rather than only theoretically possible.
say "Globals (roles, grants)"
pg pg_dumpall -U "$PG_USER" --globals-only > "$DEST/globals.sql"

# --- Every non-template database --------------------------------------------
# Enumerated rather than hardcoded: `litellm_proxy` holds API keys and spend
# records and would have been silently missed by an acb-only backup, and any
# database added later is picked up without editing this script. The
# maintenance database `postgres` is excluded — UNLESS it IS the app database
# (see APP_DB above; the exclusion once dumped nothing on a Supabase-named box
# and the migration gate refused a live deploy, 2026-08-25).
DBS="$(pg psql -U "$PG_USER" -d postgres -tAc \
  "select datname from pg_database where datistemplate = false and (datname <> 'postgres' or datname = '$APP_DB') order by datname")"

for db in $DBS; do
  printf "    - %-16s ... " "$db"
  # -Fc (custom format) is compressed AND selectively restorable: pg_restore
  # can pull a single table out of it. A plain .sql dump can only be replayed
  # whole, which is useless for the "recover one table" case that motivates
  # this script.
  pg pg_dump -U "$PG_USER" -d "$db" -Fc > "$DEST/$db.dump"
  # Cheap integrity check on EVERY run: pg_restore --list parses the archive's
  # table of contents, so a truncated or half-written dump fails here rather
  # than at 3am during an incident. This is the difference between having a
  # backup and believing you have one.
  if ! pgi pg_restore --list > /dev/null < "$DEST/$db.dump" 2>/dev/null; then
    echo "CORRUPT"
    warn "pg_restore could not read $db.dump — treating this backup as FAILED"
    exit 1
  fi
  echo "ok ($(du -h "$DEST/$db.dump" | cut -f1))"
done

# --- Manifest ----------------------------------------------------------------
# Records enough to answer "what was true when this was taken" without
# restoring it: checksums, the migration high-water mark, and row counts for
# the tables whose loss would be noticed first.
say "Manifest"
{
  echo "taken_utc:        $STAMP"
  echo "host:             $(hostname)"
  echo "pg_container:     $PG_CONTAINER"
  echo "pg_version:       $(pg psql -U "$PG_USER" -d postgres -tAc 'show server_version' | tr -d ' ')"
  echo "app_commit:       $(git -C "$APP_DIR" -c safe.directory="$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "migration_files:  $(ls "$APP_DIR"/infra/postgres/[0-9][0-9]*_*.sql 2>/dev/null | wc -l)"
  echo "app_db:           $APP_DB"
  echo "databases:        $(echo "$DBS" | tr '\n' ' ')"
  echo ""
  echo "# anchor row counts (a restore that does not reproduce these is wrong)"
  # The names must be REAL tables. Two of the original five were not
  # (`email_message`, `gtd_task`; the tables are `email_messages` and
  # `gtd_items`), so they printed "n/a" on every backup — and "n/a" reads as
  # benign. A restore could have lost the entire email mirror, the largest
  # dataset here, without contradicting a single anchor. A wrong anchor is
  # worse than no anchor: it occupies the slot where the check should be.
  # So an unresolvable name is now reported as MISSING, loudly.
  for t in app_user email_messages gtd_items meeting agent_run; do
    if ! pg psql -U "$PG_USER" -d "$APP_DB" -tAc \
         "select to_regclass('public.$t')" 2>/dev/null | grep -q .; then
      printf "%-20s %s\n" "$t:" "MISSING — anchor names a table that does not exist"
      continue
    fi
    n="$(pg psql -U "$PG_USER" -d "$APP_DB" -tAc \
         "select count(*) from $t" 2>/dev/null || echo "QUERY FAILED")"
    printf "%-20s %s\n" "$t:" "$n"
  done
  echo ""
  echo "# sha256"
  (cd "$DEST" && sha256sum ./*.dump ./globals.sql)
} > "$DEST/MANIFEST.txt"
cat "$DEST/MANIFEST.txt" | sed 's/^/    /'

# --- Optional deep verify ----------------------------------------------------
# The cheap check proves the file is READABLE. This proves it is RESTORABLE,
# which is a different claim — and the one everybody assumes without testing.
if [ "$VERIFY_RESTORE" = "1" ]; then
  say "Deep verify — restoring $APP_DB.dump into a scratch database"
  SCRATCH="acb_verify_$(date -u +%s)"
  pg createdb -U "$PG_USER" "$SCRATCH"
  # Trap so a failure part-way through cannot leave a stray multi-hundred-MB
  # database behind on a box with finite disk.
  trap 'pg dropdb -U "$PG_USER" --if-exists "$SCRATCH" >/dev/null 2>&1 || true' EXIT
  # The log goes in $DEST, NOT /tmp. Two reasons, one of which already bit us:
  # `fs.protected_regular=2` (Ubuntu default) forbids opening an existing file
  # in a sticky world-writable dir owned by another user — and that applies to
  # ROOT TOO. So once a manual run as `acb` had created /tmp/verify_restore.log,
  # the root-run systemd unit could no longer write it and every nightly backup
  # failed at the verify step. Keeping it beside the dump also means the
  # evidence for a backup travels with that backup instead of being overwritten
  # by the next run.
  if pgi pg_restore -U "$PG_USER" -d "$SCRATCH" --no-owner --no-acl \
       < "$DEST/$APP_DB.dump" > "$DEST/verify_restore.log" 2>&1; then
    live="$(pg psql -U "$PG_USER" -d "$APP_DB" -tAc \
            "select count(*) from information_schema.tables where table_schema='public'")"
    rest="$(pg psql -U "$PG_USER" -d "$SCRATCH" -tAc \
            "select count(*) from information_schema.tables where table_schema='public'")"
    echo "    public tables: live=$live restored=$rest"
    if [ "$live" != "$rest" ]; then
      warn "table count MISMATCH — backup is not a faithful copy"
      exit 1
    fi
    echo "    restore verified"
  else
    warn "pg_restore FAILED — see /tmp/verify_restore.log"
    tail -20 /tmp/verify_restore.log >&2
    exit 1
  fi
  pg dropdb -U "$PG_USER" --if-exists "$SCRATCH" >/dev/null 2>&1 || true
  trap - EXIT
fi

# --- Off-box copy ------------------------------------------------------------
# A backup on the same disk as the database survives `DROP TABLE`. It does not
# survive the disk, the box, or the provider account. Until BACKUP_REMOTE is
# set this is a same-box backup and the script refuses to pretend otherwise.
if [ -n "$BACKUP_REMOTE" ]; then
  say "Copying off-box -> $BACKUP_REMOTE"
  rsync -a --delete-after "$DEST" "$BACKUP_REMOTE/" && echo "    off-box copy ok"
else
  warn "BACKUP_REMOTE is unset — this backup exists ONLY on this box."
  warn "It protects against bad migrations and dropped tables, NOT against"
  warn "losing the VPS. Set BACKUP_REMOTE to close that gap."
fi

# --- Retention ---------------------------------------------------------------
say "Retention (keeping $KEEP_DAILY most recent)"
cd "$BACKUP_DIR"
# `ls -1d` over timestamped dirs sorts correctly because the stamp is
# ISO-8601 UTC with a fixed width — lexical order IS chronological order.
total="$(ls -1d [0-9]*Z 2>/dev/null | wc -l)"
if [ "$total" -gt "$KEEP_DAILY" ]; then
  # Pruning failure must NOT fail the backup. Under `set -e` a bare `rm -rf`
  # hitting one unremovable dir (2026-08-06: a root-owned dir left by the
  # pull unit's User=root era) aborted the script AFTER the dump had already
  # succeeded — and because apply_migrations.sh fail-closes on this script's
  # exit code, one stale directory blocked every deploy on the box. The dump
  # is the product; retention is housekeeping. Warn loudly, keep the exit 0.
  ls -1d [0-9]*Z | head -n "-$KEEP_DAILY" | while read -r old; do
    echo "    pruning $old"
    if ! rm -rf "$old" 2>/dev/null; then
      warn "could not prune $old (permissions?) — backup itself SUCCEEDED;"
      warn "fix ownership (chown -R acb:acb $BACKUP_DIR) to resume pruning."
    fi
  done
fi
echo "    $(ls -1d [0-9]*Z 2>/dev/null | wc -l) backup(s) retained, $(du -sh "$BACKUP_DIR" | cut -f1) total"

say "Backup complete: $DEST"
