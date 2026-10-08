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
#   scripts/backup_db.sh --verify-restore # ALSO restore into a throwaway
#                                         # LOCAL container (needs Docker)
#   scripts/backup_db.sh --offbox         # ALSO send the night to Supabase
#                                         # Storage (H-123). ONLY
#                                         # acb-backup.service passes it.
#
# Env:
#   BACKUP_DIR      (default /opt/acb/backups)
#   PG_CONTAINER    (default acb-postgres)
#   APP_DIR         (default /opt/acb/app)
#   KEEP_DAILY      (default 14)
#   BACKUP_VERIFY_IMAGE   image of the verify container. Default
#                   pgvector/pgvector:pg<live major>. Set it to pin a digest.
#   BACKUP_VERIFY_MEMORY  memory cap of the verify container (default 1g)
#   BACKUP_REMOTE   optional rsync destination for an off-box copy, e.g.
#                   user@host:/srv/cc-backups . UNSET BY DEFAULT, and the
#                   script says so loudly — see "Off-box" below.
#
#   The off-box copy in Supabase Storage (H-123, owner decision 2026-10-08).
#   It runs ONLY with --offbox. Without the flag nothing is uploaded, whatever
#   the env holds. Every key below lives in /etc/acb/backup-offbox.env
#   (root:root 0600), which only acb-backup.service loads. NEVER in
#   /opt/acb/app/.env: the gateway loads that file (see backup_offbox.sh).
#   With --offbox and any of these set, ALL of these must be set:
#   BACKUP_S3_ENDPOINT    the S3 endpoint, https://<ref>.storage.supabase.co/storage/v1/s3
#   BACKUP_S3_REGION      the region of the project, for example ap-south-1
#   BACKUP_S3_BUCKET      a PRIVATE bucket, for example metorite-backups
#   BACKUP_S3_ACCESS_KEY_ID, BACKUP_S3_SECRET_ACCESS_KEY   an S3 access key
#   BACKUP_GPG_RECIPIENT  the full FINGERPRINT of the owner's public key
#   BACKUP_GPG_PUBLIC_KEY_FILE   a path on this box to that PUBLIC key
#   Optional:
#   BACKUP_S3_PREFIX      the folder in the bucket (default nightly)
#   BACKUP_S3_KEEP        COMPLETE nights to keep in the bucket (default 14)
#   BACKUP_S3_TIMEOUT_SECS  the deadline of the whole off-box step (default 1200),
#                         cut down to the time left in the unit (see below)
#   BACKUP_OFFBOX_ENV_FILE  the root-owned key file (default /etc/acb/backup-offbox.env).
#                         Refused when /opt/acb/app/.env sets it (acb writes that file).
#   BACKUP_FILE_DIRS      the file-data directories, split by spaces
#   BACKUP_MEETING_BOT_VOLUME   the Docker volume of the meeting bot
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/acb/backups}"
PG_CONTAINER="${PG_CONTAINER:-acb-postgres}"
APP_DIR="${APP_DIR:-/opt/acb/app}"
KEEP_DAILY="${KEEP_DAILY:-14}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
VERIFY_RESTORE=0
# --offbox is OPT-IN on purpose (H-123). The pre-migration backup of a deploy
# runs this script too, and it must never upload. The fence over every caller
# is `test_only_the_nightly_unit_passes_offbox`.
offbox_requested=0
for arg in "$@"; do
  case "$arg" in
    --verify-restore) VERIFY_RESTORE=1 ;;
    --offbox) offbox_requested=1 ;;
    *) echo "ERROR: unknown argument '$arg'. Known: --verify-restore, --offbox." >&2; exit 2 ;;
  esac
done
# Absolute, because retention below runs `cd "$BACKUP_DIR"`.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The off-box deadline is cut from the time left in the unit, so it is taken
# here, at the very start. A wall-clock stamp, not the SECONDS counter of
# bash: bash imports SECONDS from the environment, so an env file could move it.
backup_started_at="$(date +%s)"
# == TimeoutStartSec in deploy/hostinger/acb-backup.service. One named value,
# and `test_the_unit_budget_is_one_value` fails when the two drift.
unit_timeout_secs=1800

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
# 🔴 **Incident, 2026-10-07. The verify NO LONGER restores into the cluster.**
# The deep verify did `createdb` and a full `pg_restore` on the production
# Supabase cluster every night. On a small compute size that write burst used
# up the disk I/O budget: checkpoints went from 270 s to over 900 s, and
# statements timed out across the instance for hours. The verify now restores
# into a throwaway container on THIS box (see "Optional deep verify" below),
# and it only READS the cluster: a few catalog queries and one table count.
#
# So no run makes a scratch database any more. The sweep below stays, to clear
# copies that older runs left behind. The rules for it. The fence for all of
# them is `scripts/rehearse_verify_scratch.sh`, which CI runs against a real
# server.
#
#   1. The drop uses FORCE. An open connection to the scratch database (the
#      pooler keeps one) no longer blocks it.
#   2. The drop turns off `default_transaction_read_only` in its own session,
#      as a separate statement. A provider that went read-only because its
#      disk filled can still be given the space back.
#   3. A failed drop prints an ERROR with the reason, and the backup exits 1
#      AFTER the dump and the manifest are complete. A good dump stays on disk.
#   4. A verify run sweeps stale scratch databases.
#   (5. was the cap: with two copies left, make no new one. It went on
#       2026-10-07, because the verify makes no copy on the cluster at all.)
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
# Set to 1 when the verify container could not be removed, whether the verify
# passed or not. The verify subshell reports it through a marker file, never
# through its exit code. Checked LAST, beside the scratch drops.
verify_ctr_failures=0
# Set to 1 when the deep verify failed for ANY reason: no Docker, a pull, a
# start, a readiness wait, the restore or the count. Checked LAST too, so a
# failed verify never costs the Console dump, the off-box copy or retention.
verify_failed=0
# Set to 1 when the off-box copy to Supabase Storage failed for ANY reason: a
# setting, the key, the encryption, the upload, the deadline or the bucket
# retention. Checked LAST too.
offbox_failed=0
# Set to 1 when the Customer Console dump failed or was corrupt. Checked LAST,
# so a Console failure never costs the off-box copy of the app dump.
console_failed=0

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
  warn "cluster. No run makes one since 2026-10-07, so older drops failed. Each is a full"
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
#
# 🔴 **WHERE it restores (incident, 2026-10-07).** Into a throwaway Postgres
# container on THIS box, and never into the cluster the dump came from. The old
# `createdb` and full `pg_restore` on the managed cluster used up its disk I/O
# budget every night (see "Scratch databases" above). The cluster gets READS
# only in this section: a few catalog queries and one table count.
#
# 🔴 **No Docker, no verify, and the exit code says so.** There is NO fallback
# to a restore into the cluster. A missing Docker, an image that will not pull,
# a container that will not start, a failed restore or a wrong count sets
# `verify_failed`. The run then goes on: the Console dump, the off-box copy and
# retention still happen, and the run exits 1 at the END. H-98 is why: a
# Console database with no backup has already lost data once. A verify that
# fails must not take the Console's backup down with it.
# `tests/unit/test_backup_deploy_wiring.py` executes the failure paths, and
# `scripts/rehearse_verify_scratch.sh` scene B proves that a read-only cluster
# still verifies (R8).
if [ "$VERIFY_RESTORE" = "1" ]; then
  say "Deep verify — restoring $APP_DB.dump into a throwaway local container"

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

  # ── The verify runs in a SUBSHELL (fix round 1, 2026-10-07) ────────────────
  # Every `exit` below ends the SUBSHELL, not the backup. So a failure in the
  # verify cannot skip the Console dump, the off-box copy or retention below.
  # The trap that removes the container is the subshell's own, so it fires
  # when the verify ends, not when the backup ends.
  # ⚠️ `set +e` around it, and NOT `if ! ( ... )` or `( ... ) || rc=$?`. Bash
  # turns errexit off inside any command that is the test of an `if` or part
  # of a `||` list, and that reaches into the subshell. The verify would then
  # go on past a failed command. Run plainly, the subshell keeps `set -e`.
  # ⚠️ **The exit code carries ONE thing: did the verify pass.** Any non-zero
  # code is a failed verify. A container that would not go away is reported
  # through a marker FILE, never through a code. Fix round 2 found why: this
  # used exit 2 for it, and psql exits 2 when it loses its connection, so a
  # lost count query read as "dump complete and good" and no verify ERROR.
  verify_ctr_marker="$DEST/.verify_container_not_removed"
  rm -f "$verify_ctr_marker"
  set +e
  (
    set -e
    if ! command -v docker >/dev/null 2>&1; then
      echo "ERROR: the deep verify needs Docker, and 'docker' is not on PATH." >&2
      echo "       The dump at $DEST is complete. The verify did NOT run, and it" >&2
      echo "       never falls back to a restore into the live cluster." >&2
      exit 1
    fi

    # ── The image ──────────────────────────────────────────────────────────────
    # ⚠️ The SAME major version as the live server, read from the server and not
    # written here, so an upgrade of the cluster moves the image with it. Then
    # the pg_restore and the server in the container are of the dump's release.
    # ⚠️ pgvector, not stock postgres: public tables carry `vector` columns, and a
    # stock image fails each of them every night. infra/docker-compose.yml and
    # scripts/dev_db.sh use the same image family.
    # TODO(digest): the default is pinned by TAG only, and a tag moves when the
    # upstream rebuilds it. To pin by digest, set BACKUP_VERIFY_IMAGE in the env
    # file to pgvector/pgvector:pg<N>@sha256:<digest>.
    live_major="$(pg psql -U "$PG_USER" -d postgres -tAc \
      "select current_setting('server_version_num')::int / 10000" | tr -d '[:space:]')"
    if ! [[ "$live_major" =~ ^[0-9]+$ ]]; then
      echo "ERROR: could not read the major version of the live server." >&2
      exit 1
    fi
    verify_image="${BACKUP_VERIFY_IMAGE:-pgvector/pgvector:pg$live_major}"
    # ⚠️ 1g, on purpose. The box has 7 GB, and the gateway, the workbench and
    # Redis share it. A restore of a ~200 MB database needs far less. The same
    # value for --memory-swap stops the container from swapping instead.
    verify_memory="${BACKUP_VERIFY_MEMORY:-1g}"
    verify_ctr="acb-verify-$(date -u +%s)-$$"
    verify_log="$DEST/verify_restore.log"
    prep_log="$DEST/verify_prepare.log"

    # A run killed by SIGKILL fires no trap, so its container stays. Remove each
    # verify container older than the stale age. The age comes from the epoch in
    # the NAME, as for the scratch databases, so a concurrent run keeps its own.
    for old_ctr in $(docker ps -a --filter label=acb.backup-verify=1 \
                       --format '{{.Names}}' 2>/dev/null || true); do
      old_epoch="${old_ctr#acb-verify-}"
      old_epoch="${old_epoch%%-*}"
      if [[ "$old_epoch" =~ ^[0-9]+$ ]] \
         && [ "$old_epoch" -lt $(( $(date -u +%s) - scratch_stale_seconds )) ]; then
        if docker rm -f -v "$old_ctr" >/dev/null 2>&1; then
          echo "    removed stale verify container $old_ctr"
        else
          warn "could not remove the stale verify container $old_ctr"
        fi
      fi
    done

    # Removed on EVERY exit while the trap is armed. `-v` removes the anonymous
    # data volume too. INT, TERM and HUP become an exit, so a stop from systemd
    # runs it. SIGKILL runs nothing, and the sweep above is for that case. Every
    # exit while it is armed is already a failure, so a failed removal here
    # keeps the exit code non-zero.
    remove_verify_ctr() {
      local err
      docker container inspect "$verify_ctr" >/dev/null 2>&1 || return 0
      if err="$(docker rm -f -v "$verify_ctr" 2>&1 >/dev/null)"; then
        return 0
      fi
      echo "ERROR: could not remove the verify container $verify_ctr. Reason:" >&2
      printf '%s\n' "${err:-<docker gave no reason>}" | redact | sed 's/^/    /' >&2
      # The parent cannot see this function's variables, so it reads a FILE.
      : > "$verify_ctr_marker"
      return 1
    }
    on_exit_remove_verify() {
      local rc=$?
      if ! remove_verify_ctr; then
        [ "$rc" = "0" ] && rc=1
      fi
      exit "$rc"
    }
    trap on_exit_remove_verify EXIT
    trap 'exit 1' INT TERM HUP

    # ── Start it ───────────────────────────────────────────────────────────────
    # The password is random and thrown away. initdb needs one. It reaches
    # `docker run` through the environment (`-e NAME` with no value), so no argv
    # and no log holds it. Every connection below is local, inside the container.
    #   --network none  no port and no route. Nothing reaches this server, and it
    #                   reaches nothing. psql and pg_restore run INSIDE it.
    #   no -v           the image declares an anonymous volume for the data, and
    #                   `rm -v` removes it. A tmpfs would charge the data to the
    #                   memory cap.
    #   fsync=off ...   the data is thrown away, so durability buys nothing and
    #                   costs this box disk I/O.
    verify_pw="$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')"
    if ! err="$(POSTGRES_PASSWORD="$verify_pw" docker run -d --rm \
          --name "$verify_ctr" --label acb.backup-verify=1 \
          --network none --memory "$verify_memory" --memory-swap "$verify_memory" \
          --cpus 1 -e POSTGRES_PASSWORD -e POSTGRES_DB=verify \
          "$verify_image" \
          -c fsync=off -c synchronous_commit=off -c full_page_writes=off \
          -c max_parallel_maintenance_workers=0 -c max_parallel_workers_per_gather=0 \
          2>&1 >/dev/null)"; then
      echo "ERROR: the verify container did not start (image $verify_image). Reason:" >&2
      printf '%s\n' "${err:-<docker gave no reason>}" | redact | sed 's/^/    /' >&2
      echo "       The dump at $DEST is complete. The verify did NOT run." >&2
      exit 1
    fi
    unset verify_pw

    # Ready means the FINAL server. The image's entrypoint first runs a server
    # for initdb that listens on the socket only, so a probe of the socket can
    # pass during init and then lose its server. This probe uses TCP on the
    # loopback inside the container, which only the final server opens.
    ready=0
    for _ in $(seq 1 120); do
      if docker exec "$verify_ctr" pg_isready -q -h 127.0.0.1 -U postgres >/dev/null 2>&1; then
        ready=1
        break
      fi
      [ "$(docker container inspect -f '{{.State.Running}}' "$verify_ctr" 2>/dev/null || true)" = "true" ] \
        || break
      sleep 1
    done
    if [ "$ready" != "1" ]; then
      echo "ERROR: the verify container $verify_ctr never accepted connections. Its log:" >&2
      docker logs --tail 20 "$verify_ctr" 2>&1 | redact | sed 's/^/    /' >&2 || true
      exit 1
    fi

    # psql in the container, as its superuser. `vsql_in` takes stdin (-i), and
    # each call to it is fed by a pipe or a file, never by the caller's stdin.
    vsql()    { docker exec "$verify_ctr" psql -X -q -U postgres -d verify "$@"; }
    vsql_in() { docker exec -i "$verify_ctr" psql -X -q -U postgres -d verify "$@"; }

    # ── Make the container look like the cluster, OUTSIDE public ──────────────
    # The restore below takes `public` only, the schema we own. The provider's
    # schemas (auth, storage, vault, realtime, graphql ...) and most of its
    # extensions do not exist in a stock image, and they are not ours to judge.
    # Three things outside public can still be NAMED by a public object, so they
    # are made here first, from the live catalog:
    #   roles         a policy `TO authenticated`
    #   extensions    a `vector` column, a `gin_trgm_ops` index, a default that
    #                 calls extensions.uuid_generate_v4()
    #   publications  `ALTER PUBLICATION supabase_realtime ADD TABLE public.x`
    # 🔴 A failure HERE is not judged, and that is safe. If a public object needs
    # the thing that failed, the restore of public fails below, and THAT fails the
    # run. So a provider extension that the image lacks costs nothing, and one
    # that our tables need cannot hide.
    : > "$prep_log"
    pg psql -U "$PG_USER" -d postgres -tAc \
      "select format('CREATE ROLE %I NOLOGIN;', rolname) from pg_roles
        where rolname !~ '^pg_' and rolname <> 'postgres' order by 1" \
      | vsql_in -v ON_ERROR_STOP=0 >>"$prep_log" 2>&1 || true
    available="$(vsql -tAc "select name from pg_available_extensions" < /dev/null || true)"
    live_ext="$(pg psql -U "$PG_USER" -d "$APP_DB" -tAc \
      "select e.extname, format('CREATE SCHEMA IF NOT EXISTS %I; CREATE EXTENSION IF NOT EXISTS %I WITH SCHEMA %I CASCADE;', n.nspname, e.extname, n.nspname)
         from pg_extension e join pg_namespace n on n.oid = e.extnamespace
        where e.extname <> 'plpgsql' order by e.extname" || true)"
    not_in_image=""
    while IFS='|' read -r ext stmt; do
      [ -n "$ext" ] || continue
      if ! printf '%s\n' "$available" | grep -qxF "$ext"; then
        not_in_image="$not_in_image $ext"
        continue
      fi
      printf '%s\n' "$stmt" | vsql_in -v ON_ERROR_STOP=1 >>"$prep_log" 2>&1 \
        || warn "could not create extension $ext in the verify container (see $prep_log)"
    done <<< "$live_ext"
    if [ -n "$not_in_image" ]; then
      echo "    extensions not in the verify image, so not restored:$not_in_image"
    fi
    pg psql -U "$PG_USER" -d "$APP_DB" -tAc \
      "select format('CREATE PUBLICATION %I;', pubname) from pg_publication order by 1" \
      | vsql_in -v ON_ERROR_STOP=0 >>"$prep_log" 2>&1 || true

    # ── The restore ────────────────────────────────────────────────────────────
    #   --schema=public         ours, and nothing else (see above)
    #   --no-owner --no-acl     owners and grants name roles. They decide who may
    #                           read a table, not whether the table restores.
    # The log goes in $DEST, NOT /tmp. Two reasons, one of which already bit us:
    # `fs.protected_regular=2` (Ubuntu default) forbids opening an existing file
    # in a sticky world-writable dir owned by another user — and that applies to
    # ROOT TOO. So once a manual run as `acb` had created /tmp/verify_restore.log,
    # the root-run systemd unit could no longer write it and every nightly backup
    # failed at the verify step. Keeping it beside the dump also means the
    # evidence for a backup travels with that backup instead of being overwritten
    # by the next run.
    #
    # 🔴 **Every error fails the run now.** Until 2026-10-07 a `permission denied`
    # was tolerated. The restore ran ON SUPABASE then, as a role that may not set
    # `log_min_messages` or read `vault.secrets` (measured 2026-09-19), so a
    # denial was the provider's own internals. Here the restore runs as the
    # container's superuser, into public only. A denial cannot be a provider
    # object any more, and no error of any kind is somebody else's.
    restore_rc=0
    docker exec -i "$verify_ctr" pg_restore -U postgres -d verify \
       --no-owner --no-acl --schema=public \
       < "$DEST/$APP_DB.dump" > "$verify_log" 2>&1 || restore_rc=$?
    restore_errors="$(grep -E '^pg_restore: error' "$verify_log" || true)"
    if [ "$restore_rc" != "0" ] || [ -n "$restore_errors" ]; then
      warn "pg_restore FAILED in the verify container (exit $restore_rc) — see $verify_log"
      printf '%s\n' "${restore_errors:-<no error line, read the log>}" | head -20 >&2
      exit 1
    fi

    # ── The assertion ──────────────────────────────────────────────────────────
    # The restored copy has the same public tables as the live database. Counted
    # from pg_class, not information_schema, for two reasons. information_schema
    # hides a table the live role has no grant on, and the container's superuser
    # sees all of them, so the two sides would count different things. And a
    # table that an EXTENSION owns (deptype 'e') is not ours: a provider
    # extension the image lacks would otherwise read as a lost table.
    public_tables_sql="select count(*) from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm', 'f')
         and not exists (select 1 from pg_depend d
                          where d.classid = 'pg_class'::regclass
                            and d.objid = c.oid and d.deptype = 'e')"
    live="$(pg psql -U "$PG_USER" -d "$APP_DB" -tAc "$public_tables_sql" | tr -d '[:space:]')"
    rest="$(vsql -tAc "$public_tables_sql" < /dev/null | tr -d '[:space:]')"
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
    # Disarm the trap FIRST, so a failed removal is counted once, here.
    trap - EXIT INT TERM HUP
    # A failed removal writes the marker. The verify itself passed.
    if remove_verify_ctr; then
      echo "    verify container $verify_ctr removed"
    fi
    exit 0
  )
  verify_rc=$?
  set -e
  if [ "$verify_rc" != "0" ]; then
    verify_failed=1
    warn "the deep verify FAILED (exit $verify_rc). The backup goes on, and exits 1 at the end."
  fi
  if [ -e "$verify_ctr_marker" ]; then
    verify_ctr_failures=1
    rm -f "$verify_ctr_marker"
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
      warn "pg_restore could not read customer_console.dump — the Console backup FAILED."
      warn "The run goes on, and exits 1 at the end."
      mv -f "$DEST/customer_console.dump" "$DEST/customer_console.dump.corrupt"
      console_failed=1
    fi
  else
    echo "FAILED"
    warn "Could not dump the Customer Console database. The app database above"
    warn "IS backed up; the Console's is NOT. See $DEST/customer_console.err"
    warn "— that file holds the DSN, so do not paste it anywhere."
    warn "The run goes on, and exits 1 at the end."
    if [ -e "$DEST/customer_console.dump" ]; then
      mv -f "$DEST/customer_console.dump" "$DEST/customer_console.dump.failed"
    fi
    console_failed=1
  fi
else
  warn "CUSTOMER_CONSOLE_DATABASE_URL is unset — the Console database is NOT in"
  warn "this backup. If this box serves the Console, that is H-98: the unit is"
  warn "missing its second EnvironmentFile."
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

# --- Off-box copy, AFTER local retention ------------------------------------
# A backup on the same disk as the database survives `DROP TABLE`. It does not
# survive the disk, the box, or the provider account. Two destinations exist.
# Each is optional, and the script warns loudly when neither is set.
# It runs AFTER local retention, so a slow or hung upload never costs it.
#
# 1. BACKUP_REMOTE, an rsync destination. Unchanged since BO-23.
if [ -n "$BACKUP_REMOTE" ]; then
  say "Copying off-box -> $BACKUP_REMOTE"
  rsync -a --delete-after "$DEST" "$BACKUP_REMOTE/" && echo "    off-box copy ok"
fi

# 2. Supabase Storage, through its S3 endpoint (H-123, owner decision
#    2026-10-08). scripts/backup_offbox.sh does the work, and
#    scripts/offbox_lib.sh holds the layout and the guards.
#
# 🔴 **ONLY with --offbox.** Only acb-backup.service passes it. The
# pre-migration backup of a deploy never does, so a deploy never uploads, an
# outage of the bucket cannot block a migration, and a deploy cannot push a
# real night out of the retention. The deploy's environment does not carry
# the key either: it lives in /etc/acb/backup-offbox.env, root:root 0600.
#
# 🔴 **Bounded by the time LEFT in the unit.** The deadline is
#   min(BACKUP_S3_TIMEOUT_SECS (default 1200), unit_timeout_secs - elapsed - 60)
# and `timeout` adds 30 s before a KILL, inside that 60 s margin. A fixed
# 1200 s was wrong: a slow dump (about 11 min recorded for the pre-migration
# one) left too little of the unit's 1800 s, and systemd would have killed the
# unit with no ERROR line. With under 120 s left, the upload is skipped and
# recorded as a failure. A timeout is a failed upload too.
offbox_min_secs=120
offbox_margin_secs=60
#
# 🔴 **A failure costs nothing local.** It sets `offbox_failed`, and the run
# exits 1 at the END. The dump, the Console dump and local retention are done.
offbox_configured=0
if [ -n "${BACKUP_S3_ENDPOINT:-}${BACKUP_S3_REGION:-}${BACKUP_S3_BUCKET:-}${BACKUP_S3_ACCESS_KEY_ID:-}${BACKUP_S3_SECRET_ACCESS_KEY:-}${BACKUP_GPG_RECIPIENT:-}${BACKUP_GPG_PUBLIC_KEY_FILE:-}" ]; then
  offbox_configured=1
fi
if [ "$offbox_requested" = "1" ] && [ "$offbox_configured" = "1" ]; then
  say "Copying off-box -> Supabase Storage (S3), encrypted"
  offbox_timeout="${BACKUP_S3_TIMEOUT_SECS-1200}"
  if ! [[ "$offbox_timeout" =~ ^[0-9]{1,9}$ ]] || [ "$((10#$offbox_timeout))" -lt 1 ]; then
    echo "ERROR: BACKUP_S3_TIMEOUT_SECS '$offbox_timeout' must be a whole number of seconds, 1 or more." >&2
    offbox_failed=1
  else
    offbox_timeout="$((10#$offbox_timeout))"
    elapsed="$(( $(date +%s) - backup_started_at ))"
    left="$(( unit_timeout_secs - elapsed - offbox_margin_secs ))"
    if [ "$left" -lt "$offbox_timeout" ]; then
      offbox_timeout="$left"
    fi
    echo "    deadline ${offbox_timeout}s (run at ${elapsed}s of the unit's ${unit_timeout_secs}s)"
    # The floor is for the time LEFT in the unit. A short BACKUP_S3_TIMEOUT_SECS
    # is the operator's own choice, and it stands.
    if [ "$left" -lt "$offbox_min_secs" ]; then
      echo "ERROR: only ${left}s are left in the unit's ${unit_timeout_secs}s, under the" >&2
      echo "       ${offbox_min_secs}s floor. The off-box copy did NOT run tonight." >&2
      offbox_failed=1
    else
      offbox_rc=0
      timeout --kill-after=30 "$offbox_timeout" \
        bash "$script_dir/backup_offbox.sh" "$DEST" "$STAMP" "$APP_DIR" < /dev/null \
        || offbox_rc=$?
      if [ "$offbox_rc" = "124" ] || [ "$offbox_rc" = "137" ]; then
        echo "ERROR: the off-box copy did not finish in its ${offbox_timeout}s deadline," >&2
        echo "       so timeout stopped it. The night is NOT complete in the bucket." >&2
      fi
      if [ "$offbox_rc" != "0" ]; then
        offbox_failed=1
        warn "the off-box copy FAILED (exit $offbox_rc). The run exits 1 at the end."
      fi
    fi
  fi
  # A KILL runs no trap, so the staging directory can stay. Remove it here.
  rm -rf "$DEST/offbox.work" 2>/dev/null || true
elif [ "$offbox_configured" = "1" ]; then
  echo "    off-box copy: not in this run. Only acb-backup.service passes --offbox."
elif [ -z "$BACKUP_REMOTE" ] && [ "$offbox_requested" = "1" ]; then
  warn "No off-box copy is set up — this backup exists ONLY on this box."
  warn "It protects against bad migrations and dropped tables, NOT against"
  warn "losing the VPS. Set the BACKUP_S3_* keys in /etc/acb/backup-offbox.env"
  warn "to close that gap (H-123)."
elif [ -z "$BACKUP_REMOTE" ]; then
  echo "    off-box copy: not in this run. Only acb-backup.service passes --offbox."
fi

# --- A scratch database that would not drop (rule 3) -------------------------
# Checked LAST on purpose. The dump, the manifest, the Console dump and the
# off-box copy are all complete, so the exit code can be red without one good
# dump being lost. Red is right: a scratch database that stays is a full copy
# of the app database on a disk that a provider can make read-only.
# Each check below prints its ERROR and sets final_rc. The run exits once,
# after all of them, so one failure cannot hide another.
final_rc=0
if [ "$scratch_drop_failures" -gt 0 ]; then
  echo "ERROR: $scratch_drop_failures scratch database(s) could not be dropped. See the" >&2
  echo "       ERROR lines above for each name and reason. The dump at $DEST IS" >&2
  echo "       complete and good. Drop the scratch databases before the disk fills." >&2
  final_rc=1
fi

# --- A deep verify that failed -----------------------------------------------
# Checked LAST, for the reason above. Everything after the verify ran.
if [ "$verify_failed" -gt 0 ]; then
  echo "ERROR: the deep verify FAILED. See the ERROR lines above. The dump at $DEST" >&2
  echo "       is complete. The Console dump, the off-box copy and retention ran." >&2
  echo "       This backup is NOT proven restorable." >&2
  final_rc=1
fi

# --- A verify container that would not go ----------------------------------
# Checked LAST, for the reason above. It holds memory and disk on THIS box,
# not on the cluster, so the dump is still good. Red is still right.
if [ "$verify_ctr_failures" -gt 0 ]; then
  echo "ERROR: the verify container could not be removed. See the ERROR line above." >&2
  echo "       The dump at $DEST IS complete. Remove the container by hand:" >&2
  echo "       docker rm -f -v \$(docker ps -aq --filter label=acb.backup-verify=1)" >&2
  final_rc=1
fi

# --- A Customer Console dump that failed (H-98) ------------------------------
# Checked LAST, for the reason above. The app dump and its off-box copy ran.
if [ "$console_failed" -gt 0 ]; then
  echo "ERROR: the Customer Console dump FAILED. See the warning above. The app" >&2
  echo "       dump at $DEST is complete, and its off-box copy ran." >&2
  final_rc=1
fi

# --- An off-box copy that failed (H-123) -------------------------------------
# Checked LAST, for the reason above. The local dump and retention are done.
if [ "$offbox_failed" -gt 0 ]; then
  echo "ERROR: the off-box copy to Supabase Storage FAILED. See the ERROR lines above." >&2
  echo "       The dump at $DEST is complete, and local retention ran first." >&2
  echo "       This night has NO good copy off the box." >&2
  final_rc=1
fi
if [ "$final_rc" != "0" ]; then
  exit "$final_rc"
fi

say "Backup complete: $DEST"
