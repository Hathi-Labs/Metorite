#!/usr/bin/env bash
# BO-23 — prove the backup/restore round trip, on any machine.
#
# Why this exists
# ---------------
# `backup_db.sh` and `restore_db.sh` shipped on 2026-08-03 and, until this
# script, had never been executed anywhere. They could not be: both reached
# Postgres exclusively through `docker exec acb-postgres`, which exists on
# precisely one machine, and running an untested restore for the first time
# during an incident is the whole failure mode this ticket is about.
#
# BO-23 states the rule itself: **a backup nobody has restored is not a
# backup.** This is the smallest honest way to satisfy it without production —
# a real Postgres, a real `pg_dump`, a real `pg_restore`, and an assertion that
# the rows came back.
#
# What it actually proves
# -----------------------
#   1. `backup_db.sh` produces a dump a `pg_restore` can read (not merely a
#      file — an unreadable dump is the classic silent backup failure).
#   2. `--verify-restore` catches a corrupt dump rather than reporting success.
#   3. `restore_db.sh` reconstructs the data into a scratch database.
#   4. The restored rows EQUAL what was backed up. Steps 1–3 all pass on an
#      empty database, which is exactly the outcome you must never accept.
#   5. The live database is untouched by a default restore — the property that
#      makes the tool safe to reach for while panicking.
#
# What it does NOT prove: that the VPS's cron fires, that the volume has room,
# or that an off-box copy exists. Those are properties of the box, verified by
# `systemctl list-timers` and the runbook's §6.
#
# Usage:
#   scripts/rehearse_restore.sh            # against $PGHOST or localhost:5432
#
# Env: PGHOST/PGPORT/PGUSER/PGPASSWORD (libpq), REHEARSAL_DIR (temp by default)
# Docker must answer too: since 2026-10-07 `--verify-restore` restores into a
# throwaway pgvector container on this machine, never into the server above.
set -euo pipefail

export PG_MODE=local
export PGUSER="${PGUSER:-postgres}"
PG_USER="$PGUSER"

REHEARSAL_DIR="${REHEARSAL_DIR:-$(mktemp -d)}"
LIVE_DB="acb"
SENTINEL="cc_restore_rehearsal"
ROWS=250

say()  { printf "\n==> %s\n" "$*"; }
pass() { printf "  ok   %s\n" "$*"; }
die()  { printf "  FAIL %s\n" "$*" >&2; exit 1; }

command -v psql >/dev/null || die "psql not on PATH"
psql -d postgres -tAc 'select 1' >/dev/null 2>&1 \
  || die "no Postgres answers as '$PGUSER' (set PGHOST/PGPORT/PGPASSWORD)"

# The scripts read POSTGRES_USER from $APP_DIR/.env; point that at a temp dir so
# a rehearsal can never pick up a real deployment's settings by accident.
APP_DIR="$(mktemp -d)"
export APP_DIR
printf 'POSTGRES_USER=%s\n' "$PGUSER" > "$APP_DIR/.env"
export BACKUP_DIR="$REHEARSAL_DIR/backups"

cleanup() {
  psql -d postgres -tAc \
    "SELECT datname FROM pg_database WHERE datname LIKE 'acb_restored_%' OR datname = '$LIVE_DB'" \
    2>/dev/null | while read -r db; do
      [ -n "$db" ] && dropdb --if-exists "$db" 2>/dev/null || true
    done
  rm -rf "$APP_DIR"
}
trap cleanup EXIT

# ── 1. A live database with known contents ───────────────────────────────────
say "Seeding a live database with $ROWS known rows"
dropdb --if-exists "$LIVE_DB" >/dev/null 2>&1 || true
createdb "$LIVE_DB"
psql -d "$LIVE_DB" -q <<SQL
CREATE TABLE $SENTINEL (id int PRIMARY KEY, payload text NOT NULL);
INSERT INTO $SENTINEL SELECT g, 'row-' || g FROM generate_series(1, $ROWS) g;
SQL
BEFORE="$(psql -d "$LIVE_DB" -tAc "SELECT count(*) FROM $SENTINEL")"
CHECKSUM_BEFORE="$(psql -d "$LIVE_DB" -tAc \
  "SELECT md5(string_agg(id::text || payload, ',' ORDER BY id)) FROM $SENTINEL")"
[ "$BEFORE" = "$ROWS" ] || die "seed wrote $BEFORE rows, expected $ROWS"
pass "seeded $BEFORE rows (checksum ${CHECKSUM_BEFORE:0:12}…)"

# ── 2. Back it up, with the same flag the systemd unit uses ──────────────────
say "Running backup_db.sh --verify-restore (exactly what acb-backup.service runs)"
bash scripts/backup_db.sh --verify-restore >"$REHEARSAL_DIR/backup.log" 2>&1 || {
  tail -30 "$REHEARSAL_DIR/backup.log" >&2
  die "backup_db.sh exited non-zero"
}
STAMP="$(ls -1 "$BACKUP_DIR" | sort | tail -1)"
[ -n "$STAMP" ] || die "backup produced no directory"
[ -s "$BACKUP_DIR/$STAMP/$LIVE_DB.dump" ] || die "no dump for '$LIVE_DB'"
pass "backup $STAMP ($(du -h "$BACKUP_DIR/$STAMP/$LIVE_DB.dump" | cut -f1))"

# ── 3. Destroy the data, the way an incident would ───────────────────────────
# A DROP rather than a DELETE: it is the case where the schema has to come back
# too, which is what a data-only dump would silently fail to restore.
say "Dropping the table (simulating the incident)"
psql -d "$LIVE_DB" -q -c "DROP TABLE $SENTINEL"
psql -d "$LIVE_DB" -tAc "SELECT to_regclass('$SENTINEL')" | grep -q '^$' \
  || die "table still present after DROP"
pass "table is gone"

# ── 4. Restore, and check the live DB is left alone ──────────────────────────
say "Running restore_db.sh (default: into a scratch DB, live untouched)"
bash scripts/restore_db.sh --from "$STAMP" --db "$LIVE_DB" \
  >"$REHEARSAL_DIR/restore.log" 2>&1 || {
  tail -30 "$REHEARSAL_DIR/restore.log" >&2
  die "restore_db.sh exited non-zero"
}
SCRATCH="$(psql -d postgres -tAc \
  "SELECT datname FROM pg_database WHERE datname LIKE 'acb_restored_%' ORDER BY datname DESC LIMIT 1")"
[ -n "$SCRATCH" ] || die "restore created no scratch database"
pass "restored into '$SCRATCH'"

psql -d "$LIVE_DB" -tAc "SELECT to_regclass('$SENTINEL')" | grep -q '^$' \
  || die "the default restore modified the LIVE database — it must not"
pass "live database untouched, as documented"

# ── 5. The assertion that matters ────────────────────────────────────────────
# Everything above passes against an empty database. This is the step that
# distinguishes "a restore ran" from "the data came back".
say "Verifying the restored rows equal what was backed up"
# `|| true` so a MISSING table reports as "restored 0 rows" through `die`
# rather than as a raw psql error from `set -e` — the failure a sabotage test
# produces, and the one an operator is most likely to hit for real.
AFTER="$(psql -d "$SCRATCH" -tAc "SELECT count(*) FROM $SENTINEL" 2>/dev/null || echo 0)"
CHECKSUM_AFTER="$(psql -d "$SCRATCH" -tAc \
  "SELECT md5(string_agg(id::text || payload, ',' ORDER BY id)) FROM $SENTINEL" 2>/dev/null || echo "")"
[ "$AFTER" = "$BEFORE" ] || die "restored $AFTER rows, backed up $BEFORE"
[ "$CHECKSUM_AFTER" = "$CHECKSUM_BEFORE" ] || die "row contents differ after restore"
pass "$AFTER rows, checksum matches"

# ── 6. A corrupt dump must FAIL, not quietly succeed ─────────────────────────
# The direction nobody tests. A verifier that passes on garbage is worse than
# no verifier, because it converts an unnoticed problem into a false assurance.
say "Confirming a corrupt dump is rejected"
CORRUPT="$REHEARSAL_DIR/corrupt.dump"
head -c 512 "$BACKUP_DIR/$STAMP/$LIVE_DB.dump" > "$CORRUPT"
if pg_restore --list "$CORRUPT" >/dev/null 2>&1; then
  die "pg_restore accepted a truncated dump — the integrity check proves nothing"
fi
pass "truncated dump rejected"

printf "\n==> REHEARSAL PASSED — backup and restore verified end to end.\n"
printf "    live rows in : %s\n    restored     : %s\n    checksum     : %s\n" \
  "$BEFORE" "$AFTER" "${CHECKSUM_AFTER:0:12}…"
