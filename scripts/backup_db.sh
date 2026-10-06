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

# ── Scratch databases of the deep verify ────────────────────────────────────
#
# 🔴 **Incident, 2026-10-06.** The deep verify below restores into a scratch
# database named `acb_verify_<epoch>`. On the managed cluster its drop failed
# every night from 2026-09-19, and `>/dev/null 2>&1 || true` hid each failure.
# 22 scratch copies (923 MB, measured) piled up beside a 184 MB product database.
# Supabase then put the whole project into read-only mode, so every write
# failed for about 2 hours, and deploys hung in the pre-migration backup.
#
# The rules that replace the silent drop. The fence for all of them is
# `scripts/rehearse_verify_scratch.sh`, which CI runs against a real server.
#
#   1. The drop uses FORCE. An open connection to the scratch database (the
#      pooler keeps one) no longer blocks it.
#   2. The drop turns off `default_transaction_read_only` in its own session,
#      as a separate statement. A provider that went read-only because its
#      disk filled can still be given the space back.
#   3. A failed drop prints an ERROR with the reason, and the backup exits 1
#      AFTER the dump and the manifest are complete. A good dump stays on disk.
#   4. A run sweeps stale scratch databases before it makes a new one.
#   5. If two or more are still there after the sweep, the run makes no new
#      one. So a drop that keeps failing cannot pile copies up again.
#
# ⚠️ **The one pattern.** A scratch database matches `^acb_verify_[0-9]+$` and
# NOTHING else. `LIKE 'acb_verify_%'` is wrong, because `_` is a LIKE
# wildcard: it also matches `acb_verifyx`. This pattern decides what the sweep
# DROPS, so a wide pattern deletes somebody's database.
#
# ⚠️ Lower-case names on purpose. `test_integrations_env_hardening.py` reads
# every UPPER-CASE variable here as an env name that the deployment sets.
scratch_re='^acb_verify_[0-9]+$'
# A scratch database whose name is older than this is left over. Six hours is
# far longer than one verify takes, so a concurrent run keeps its own copy.
scratch_stale_seconds=21600
scratch_drop_failures=0

# psql and libpq can echo a connection string, password included, into an
# error. Apply this to every error text before it reaches a log. It is the
# same sed as apply_customer_console_migrations.sh, plus the key=value form and
# the literal PGPASSWORD.
redact() {
  local text
  text="$(sed -E 's#(//[^:/@[:space:]]+):[^@[:space:]]*@#\1:***@#g; s#(password=)[^[:space:]]*#\1***#Ig')"
  if [ -n "${PGPASSWORD:-}" ]; then
    text="${text//"$PGPASSWORD"/***}"
  fi
  printf '%s\n' "$text"
}

# drop_scratch <db> — drop one scratch database, or say loudly why not.
# Returns non-zero on failure. It never exits, so the caller decides when.
#
# ⚠️ Two `-c` options, never one string. psql sends each `-c` as its own
# statement in ONE session, so the SET applies to the DROP. One string with
# both statements runs as one implicit transaction, and DROP DATABASE refuses
# to run inside a transaction block.
# ⚠️ Through a pooler, this needs SESSION mode (port 5432). In transaction mode
# the two statements can reach two different server connections.
drop_scratch() {
  local db="$1" err
  if ! [[ "$db" =~ $scratch_re ]]; then
    echo "ERROR: refusing to drop '$db' — it is not a scratch database ($scratch_re)." >&2
    return 1
  fi
  if err="$(pg psql -U "$PG_USER" -d postgres -X -q -v ON_ERROR_STOP=1 \
              -c 'SET default_transaction_read_only = off' \
              -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)" 2>&1 >/dev/null)"; then
    return 0
  fi
  echo "ERROR: could not drop scratch database '$db'. Reason:" >&2
  printf '%s\n' "${err:-<psql gave no reason>}" | redact | sed 's/^/    /' >&2
  return 1
}

# The advisory guard. It reports a pile-up and changes no exit code. It runs on
# EVERY backup, the pre-migration one too, because that is the run an operator
# watches during a deploy.
scratch_count="$(pg psql -U "$PG_USER" -d postgres -tAc \
  "select count(*) from pg_database where datname ~ '$scratch_re'" 2>/dev/null || true)"
if ! [[ "$scratch_count" =~ ^[0-9]+$ ]]; then
  warn "WARNING (advisory): could not count the scratch databases."
elif [ "$scratch_count" -gt 2 ]; then
  warn "WARNING (advisory): $scratch_count scratch databases ($scratch_re) exist on this"
  warn "cluster. A run leaves at most one, so earlier drops failed. Each is a full"
  warn "copy of the app database. On a managed provider they fill the disk, and a"
  warn "full disk makes the project read-only (2026-10-06)."
fi

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
# ⚠️ **`acb_verify_%` is EXCLUDED, added 2026-09-19.** The deep verify below
# restores into a scratch database of that name. Two runs overlapping — or one
# that died before its trap fired — leaves it behind, and the next run then
# enumerates it, dumps it, and writes its checksum into the manifest as though
# it were a real database. Observed: `acb_verify_1789810167.dump` in a manifest.
# A backup that backs up its own scratch copy wastes disk and, worse, makes the
# manifest describe something that was never part of the product.
# ⚠️ The exclusion uses `$scratch_re` since 2026-10-06. It used
# `LIKE 'acb_verify_%'`, which ALSO excluded a real database named, for
# example, `acb_verifyx`, so that database was never backed up.
DBS="$(pg psql -U "$PG_USER" -d postgres -tAc \
  "select datname from pg_database where datistemplate = false and datname !~ '$scratch_re' and (datname <> 'postgres' or datname = '$APP_DB') order by datname")"

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
  # The names must be REAL tables. Two of the original five were not (the
  # email one lacked its plural, and the task one named no table at all), so
  # they printed "n/a" on every backup — and "n/a" reads as
  # benign. A restore could have lost the entire email mirror, the largest
  # dataset here, without contradicting a single anchor. A wrong anchor is
  # worse than no anchor: it occupies the slot where the check should be.
  # So an unresolvable name is now reported as MISSING, loudly.
  # The task anchor is `pm_tasks`, the one task store (D53). Migration 217
  # dropped the retired store it used to name (WS-39 S8, 2026-09-23).
  for t in app_user email_messages pm_tasks meeting agent_run; do
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

  # The sweep (rule 4). The age comes from the epoch in the NAME, compared with
  # the server's clock. `substring(... from '[0-9]+$')` is NULL for a name
  # with no trailing digits, and a NULL age never qualifies.
  stale="$(pg psql -U "$PG_USER" -d postgres -tAc \
    "select datname from pg_database
      where datname ~ '$scratch_re'
        and substring(datname from '[0-9]+\$')::numeric
            < extract(epoch from now()) - $scratch_stale_seconds
      order by datname")"
  for old in $stale; do
    if drop_scratch "$old"; then
      echo "    swept stale scratch database $old"
    else
      scratch_drop_failures=$((scratch_drop_failures + 1))
    fi
  done

  # 🔴 **The cap (rule 5).** A drop can fail for a reason that FORCE and the SET
  # do not cure, and the first cause of the incident was never seen. So after
  # the sweep, count again. Two or more left means drops keep failing. Then
  # this run makes NO new copy, and the pile stops at two whatever the cause.
  # The verify is lost for that night, and the exit code says so.
  left="$(pg psql -U "$PG_USER" -d postgres -tAc \
    "select count(*) from pg_database where datname ~ '$scratch_re'")"
  if [ "$left" -ge 2 ]; then
    echo "ERROR: $left scratch databases ($scratch_re) are still on the cluster after" >&2
    echo "       the sweep. The deep verify is SKIPPED, so that no new copy is made." >&2
    scratch_drop_failures=$((scratch_drop_failures + 1))
    VERIFY_RESTORE=0
  fi
fi

if [ "$VERIFY_RESTORE" = "1" ]; then
  SCRATCH="acb_verify_$(date -u +%s)"
  pg createdb -U "$PG_USER" "$SCRATCH"
  # Trap so a failure part-way through cannot leave a stray multi-hundred-MB
  # database behind on a box with finite disk. Every exit while it is armed is
  # already a failure, so a failed drop here keeps the exit code non-zero.
  on_exit_drop_scratch() {
    local rc=$?
    if ! drop_scratch "$SCRATCH"; then
      [ "$rc" = "0" ] && rc=1
    fi
    exit "$rc"
  }
  trap on_exit_drop_scratch EXIT
  # The log goes in $DEST, NOT /tmp. Two reasons, one of which already bit us:
  # `fs.protected_regular=2` (Ubuntu default) forbids opening an existing file
  # in a sticky world-writable dir owned by another user — and that applies to
  # ROOT TOO. So once a manual run as `acb` had created /tmp/verify_restore.log,
  # the root-run systemd unit could no longer write it and every nightly backup
  # failed at the verify step. Keeping it beside the dump also means the
  # evidence for a backup travels with that backup instead of being overwritten
  # by the next run.
  # ⚠️ **A MANAGED cluster dumps schemas we may not restore, and that is not a
  # broken backup.** Measured 2026-09-19 on Supabase: the restore raised
  # exactly two errors — `permission denied to set parameter log_min_messages`
  # and `permission denied for table vault.secrets`. Both are the provider's
  # own internals. Our `public` schema restored completely.
  #
  # 🔴 **So a non-zero exit is NOT the assertion.** The assertion is, and always
  # was, the line below: the restored copy has the same public tables as the
  # live database. Failing the whole backup on the provider's vault would mean
  # nightly red on a backup that is in fact good, and a unit that is red every
  # night is a unit nobody reads.
  #
  # ⚠️ **Matched on PERMISSION DENIED, not on schema names.** A first attempt
  # grepped for `vault` and failed: pg_restore's error line reads
  # `permission denied for table secrets` and names no schema at all — the
  # schema appears only on the following `Command was: COPY vault.secrets` line.
  # A rule that reads one line cannot see it.
  #
  # 🔴 **Why a permission denial is safe to tolerate HERE and nowhere else.**
  # The scratch database was created moments ago by this same role, so every
  # object we own in it is owned by us. A denial can therefore only be a
  # provider object the dump carried along. Any OTHER error — a syntax failure,
  # a missing type, a constraint violation — still fails the backup.
  restore_rc=0
  pgi pg_restore -U "$PG_USER" -d "$SCRATCH" --no-owner --no-acl \
     < "$DEST/$APP_DB.dump" > "$DEST/verify_restore.log" 2>&1 || restore_rc=$?

  if [ "$restore_rc" != "0" ]; then
    OURS_FAILED="$(grep -E '^pg_restore: error' "$DEST/verify_restore.log" \
                   | grep -Ev 'permission denied' || true)"
    if [ -n "$OURS_FAILED" ]; then
      warn "pg_restore FAILED on objects we own — see $DEST/verify_restore.log"
      printf '%s\n' "$OURS_FAILED" | head -20 >&2
      exit 1
    fi
    say "Restore raised only provider-managed errors — continuing to the table check"
    grep -cE '^pg_restore: error' "$DEST/verify_restore.log" \
      | sed 's/^/    provider-managed errors ignored: /'
  fi

  live="$(pg psql -U "$PG_USER" -d "$APP_DB" -tAc \
          "select count(*) from information_schema.tables where table_schema='public'")"
  rest="$(pg psql -U "$PG_USER" -d "$SCRATCH" -tAc \
          "select count(*) from information_schema.tables where table_schema='public'")"
  echo "    public tables: live=$live restored=$rest"
  # 🔴 Zero on BOTH sides is not agreement, it is two failures matching. A
  # restore that produced no tables at all would otherwise pass this check.
  if [ -z "$live" ] || [ "$live" = "0" ]; then
    warn "the LIVE database reports no public tables — the check cannot mean anything"
    exit 1
  fi
  if [ "$live" != "$rest" ]; then
    warn "table count MISMATCH — backup is not a faithful copy"
    exit 1
  fi
  echo "    restore verified"
  # Disarm the trap FIRST, so a failed drop is counted once, here.
  trap - EXIT
  if drop_scratch "$SCRATCH"; then
    echo "    scratch database $SCRATCH dropped"
  else
    scratch_drop_failures=$((scratch_drop_failures + 1))
  fi
fi

# --- The Customer Console's own cluster (H-98) -------------------------------
# 🔴 **The Console database has NEVER been covered here, and the owner lost
# Console data on 2026-09-01 with no backup to restore.** Everything above
# enumerates `pg_database` on ONE connection. The Console is a SEPARATE
# Supabase project reached with its own credentials, so it can never appear in
# that listing however many databases the app's cluster grows.
#
# ⚠️ **Skipped LOUDLY when the DSN is absent.** A box without the Console must
# still back up the app database rather than fail the whole unit — but a silent
# skip is exactly how this gap survived. The warning below is the evidence.
#
# ⚠️ **`pg_dump` takes the URL directly.** Re-deriving PGHOST and PGUSER from
# it would be a second parser for a string libpq already understands, and
# getting the pooler's user format wrong is a failure that shows up at restore.
if [ -n "${CUSTOMER_CONSOLE_DATABASE_URL:-}" ]; then
  say "Customer Console cluster (separate project)"
  # The app's DSN carries a SQLAlchemy driver suffix libpq does not understand.
  CC_DSN="${CUSTOMER_CONSOLE_DATABASE_URL/postgresql+psycopg:/postgresql:}"
  CC_DSN="${CC_DSN/postgresql+asyncpg:/postgresql:}"
  printf "    - %-16s ... " "customer_console"
  # ⚠️ stderr to a FILE, never the console: pg_dump echoes the whole connection
  # string on failure, password included. That has reached a transcript here.
  if pg_dump -d "$CC_DSN" -Fc > "$DEST/customer_console.dump" \
       2> "$DEST/customer_console.err"; then
    if pg_restore --list > /dev/null < "$DEST/customer_console.dump" 2>/dev/null; then
      echo "ok ($(du -h "$DEST/customer_console.dump" | cut -f1))"
      rm -f "$DEST/customer_console.err"
      (cd "$DEST" && sha256sum ./customer_console.dump >> MANIFEST.txt)
    else
      echo "CORRUPT"
      warn "pg_restore could not read customer_console.dump — backup FAILED"
      exit 1
    fi
  else
    echo "FAILED"
    warn "Could not dump the Customer Console database. The app database above"
    warn "IS backed up; the Console's is NOT. See $DEST/customer_console.err"
    warn "— that file holds the DSN, so do not paste it anywhere."
    exit 1
  fi
else
  warn "CUSTOMER_CONSOLE_DATABASE_URL is unset — the Console database is NOT in"
  warn "this backup. If this box serves the Console, that is H-98: the unit is"
  warn "missing its second EnvironmentFile."
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

# --- A scratch database that would not drop (rule 3) -------------------------
# Checked LAST on purpose. The dump, the manifest, the Console dump and the
# off-box copy are all complete, so the exit code can be red without one good
# dump being lost. Red is right: a scratch database that stays is a full copy
# of the app database on a disk that a provider can make read-only.
if [ "$scratch_drop_failures" -gt 0 ]; then
  echo "ERROR: $scratch_drop_failures scratch database(s) could not be dropped. See the" >&2
  echo "       ERROR lines above for each name and reason. The dump at $DEST IS" >&2
  echo "       complete and good. Drop the scratch databases before the disk fills." >&2
  exit 1
fi

say "Backup complete: $DEST"
