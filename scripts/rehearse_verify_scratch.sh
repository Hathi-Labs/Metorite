#!/usr/bin/env bash
# The fence for the scratch databases of `backup_db.sh --verify-restore`.
#
# Why this exists
# ---------------
# On 2026-10-06 the production cluster went READ-ONLY for about 2 hours. The
# nightly deep verify restores into `acb_verify_<epoch>`, and its drop had
# failed silently every night since 2026-09-19 (`>/dev/null 2>&1 || true`).
# 22 scratch copies (923 MB) filled the disk, and the provider stopped every write.
#
# On 2026-10-07 the verify moved OFF the cluster. It had restored into the
# production cluster every night, and that write burst used up the disk I/O
# budget of the managed instance. It now restores into a throwaway container
# on the box, so no run makes a scratch database any more. The sweep stays,
# for the copies that older runs left.
#
# This runs the REAL `backup_db.sh` against a REAL Postgres (R8), in six
# scenes. Each scene names the mutation it catches (R7):
#
#   A. Sweep. Stale scratch databases go, even with a connection open on one.
#      Decoys that only LOOK like scratch databases stay, and are backed up.
#        mutation: drop `WITH (FORCE)`   -> the open connection blocks the drop
#        mutation: widen `$scratch_re`   -> a decoy is dropped
#   B. Read-only server. The sweep still drops a stale scratch database while
#      `default_transaction_read_only = on` comes from the configuration file.
#      And the verify PASSES, because it writes nothing to the cluster.
#        mutation: drop the `SET default_transaction_read_only = off`
#        mutation: restore into the cluster again -> CREATE DATABASE fails
#   C. A failed drop. The backup exits non-zero, AFTER the dump and the
#      manifest exist, and the reason is printed with no password in it.
#        mutation: put back `|| true`    -> the exit code goes green
#        mutation: drop `redact`         -> the shim's password reaches the log
#   D. The advisory guard prints a WARNING with the count (no exit code).
#   E. No copy on the cluster. With a drop that keeps failing, two more nights
#      add no scratch database, and each one still verifies.
#        mutation: restore into the cluster again -> the count grows
#   F. Docker that will not start a container. The verify fails LOUDLY, the
#      dump is kept, and nothing is restored into the cluster instead.
#        mutation: fall back to the cluster -> exit 0, or a new scratch copy
#
# ⚠️ **Run it against a THROWAWAY server only.** It drops every database it
# creates, it toggles a server setting with ALTER SYSTEM, and the sweep in
# `backup_db.sh` drops every stale `acb_verify_<digits>` database on the
# cluster. CI gives it a service container of its own.
#
# Usage:
#   scripts/rehearse_verify_scratch.sh     # against $PGHOST or localhost:5432
#
# Env: PGHOST/PGPORT/PGUSER/PGPASSWORD (libpq). PGUSER must be a superuser,
# because scene B uses ALTER SYSTEM. Docker must answer too, because the
# verify runs in a container (it pulls pgvector/pgvector:pg<server major>).
set -euo pipefail

export PG_MODE=local
export PGUSER="${PGUSER:-postgres}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
LIVE_DB="acb"
OLD1="acb_verify_1000000000"          # 2001: stale
OLD2="acb_verify_1000000001"          # 2001: stale, and held open in scene A
FRESH="acb_verify_$(( $(date -u +%s) - 60 ))"   # a concurrent run: must stay
# Decoys. Each one dies under one specific widening of the pattern.
DECOYS=(
  "acb_verifyx"                       # the name in the incident write-up
  "acb_verifyx1000000000"             # LIKE 'acb_verify_%' ("_" is a wildcard)
  "zz_acb_verify_1000000000"          # a pattern that lost its "^"
  "acb_verify_1000000000_1000000000"  # a pattern that lost its "$"
)

say()  { printf "\n==> %s\n" "$*"; }
pass() { printf "  ok   %s\n" "$*"; }
die()  { printf "  FAIL %s\n" "$*" >&2; exit 1; }

q()      { psql -X -d postgres -tAc "$1"; }
exists() { [ "$(q "select count(*) from pg_database where datname = '$1'")" = "1" ]; }
mk()     { psql -X -q -d postgres -c "CREATE DATABASE \"$1\""; }
rmdb()   { psql -X -q -d postgres -c "DROP DATABASE IF EXISTS \"$1\" WITH (FORCE)" >/dev/null 2>&1 || true; }

command -v psql >/dev/null || die "psql not on PATH"
command -v docker >/dev/null || die "docker not on PATH (the verify runs in a container)"
q 'select 1' >/dev/null 2>&1 || die "no Postgres answers as '$PGUSER' (set PGHOST/PGPORT/PGPASSWORD)"
[ "$(q 'select rolsuper from pg_roles where rolname = current_user')" = "t" ] \
  || die "PGUSER must be a superuser (scene B uses ALTER SYSTEM)"

set_read_only() {
  psql -X -q -d postgres -c 'SET default_transaction_read_only = off' \
    -c "ALTER SYSTEM SET default_transaction_read_only = $1" \
    -c 'SELECT pg_reload_conf()' >/dev/null
}

HOLDER_PID=""
cleanup() {
  [ -n "$HOLDER_PID" ] && kill "$HOLDER_PID" 2>/dev/null || true
  set_read_only off 2>/dev/null || true
  for db in "$LIVE_DB" "$OLD1" "$OLD2" "$FRESH" "${DECOYS[@]}"; do rmdb "$db"; done
  q "select datname from pg_database where datname ~ '^acb_verify_[0-9]+\$'" 2>/dev/null \
    | while read -r db; do [ -n "$db" ] && rmdb "$db"; done
  docker ps -aq --filter label=acb.backup-verify=1 2>/dev/null \
    | while read -r c; do [ -n "$c" ] && docker rm -f -v "$c" >/dev/null 2>&1; done || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# The scratch databases on the cluster, counted.
scratch_count() { q "select count(*) from pg_database where datname ~ '^acb_verify_[0-9]+\$'"; }
# The verify containers on this Docker, counted. Every run must remove its own.
verify_ctrs() { docker ps -aq --filter label=acb.backup-verify=1 | wc -l | tr -d ' '; }

# The scripts read POSTGRES_USER from $APP_DIR/.env. A temp dir means a
# rehearsal can never pick up a real deployment's settings by accident.
export APP_DIR="$WORK/app"
mkdir -p "$APP_DIR"
printf 'POSTGRES_USER=%s\n' "$PGUSER" > "$APP_DIR/.env"
unset CUSTOMER_CONSOLE_DATABASE_URL BACKUP_REMOTE || true

run_backup() {  # run_backup <scene> [path-prefix] -> sets RC, LOG, DEST
  export BACKUP_DIR="$WORK/backups-$1"
  LOG="$WORK/$1.log"
  RC=0
  # A subshell, so a PATH prefix (scene C's shim) cannot outlive the run.
  (cd "$REPO" && PATH="${2:+$2:}$PATH" bash scripts/backup_db.sh --verify-restore) \
    >"$LOG" 2>&1 || RC=$?
  DEST="$BACKUP_DIR/$(ls -1 "$BACKUP_DIR" 2>/dev/null | sort | tail -1)"
}
show_log() { sed 's/^/      | /' "$LOG" | tail -40 >&2; }

say "Seeding a live database"
rmdb "$LIVE_DB"
mk "$LIVE_DB"
psql -X -q -d "$LIVE_DB" -c "CREATE TABLE sentinel (id int PRIMARY KEY); INSERT INTO sentinel VALUES (1), (2), (3);"
pass "seeded '$LIVE_DB'"

# ── A. The sweep, FORCE and the pattern ─────────────────────────────────────
say "A. Sweep: two stale, one fresh, ${#DECOYS[@]} decoys, a connection held open"
for db in "$OLD1" "$OLD2" "$FRESH" "${DECOYS[@]}"; do rmdb "$db"; mk "$db"; done
# A real open session on a stale scratch database: what the pooler leaves.
psql -X -d "$OLD2" -c 'select pg_sleep(600)' >/dev/null 2>&1 &
HOLDER_PID=$!
for _ in $(seq 1 50); do
  [ "$(q "select count(*) from pg_stat_activity where datname = '$OLD2'")" -ge 1 ] && break
  sleep 0.2
done
[ "$(q "select count(*) from pg_stat_activity where datname = '$OLD2'")" -ge 1 ] \
  || die "could not hold a connection open on $OLD2"
pass "a session is open on $OLD2"

run_backup A
# The decoys first: a dropped decoy is the worst outcome, so it names itself.
for db in "${DECOYS[@]}"; do
  exists "$db" || { show_log; die "A: the sweep DROPPED the decoy '$db' — the pattern is too wide"; }
done
pass "all ${#DECOYS[@]} decoys survived the sweep"
[ "$RC" = "0" ] || { show_log; die "A: backup_db.sh exited $RC (the sweep must drop $OLD2 although a session is open on it)"; }
for db in "$OLD1" "$OLD2"; do
  exists "$db" && { show_log; die "A: stale $db survived the sweep"; }
  grep -q "swept stale scratch database $db" "$LOG" || { show_log; die "A: no log line for $db"; }
done
pass "both stale scratch databases swept and logged, the open session included"
exists "$FRESH" || die "A: the sweep dropped $FRESH, which is younger than 6 hours"
pass "the fresh scratch database of a concurrent run survived"
for db in "${DECOYS[@]}"; do
  [ -s "$DEST/$db.dump" ] || { show_log; die "A: decoy '$db' was left out of the backup"; }
done
pass "each decoy is in the backup"
ls "$DEST" | grep -qE '^acb_verify_[0-9]+\.dump$' && die "A: a scratch database was dumped"
[ "$(scratch_count)" = "1" ] \
  || { show_log; die "A: the run made a scratch database on the cluster"; }
pass "the run made no scratch database of its own (only $FRESH is left)"
grep -q "restore verified" "$LOG" || { show_log; die "A: the verify did not pass"; }
grep -qE "public tables: live=1 restored=1" "$LOG" || { show_log; die "A: the table count is wrong"; }
[ "$(verify_ctrs)" = "0" ] || { show_log; die "A: the verify container was left behind"; }
pass "the verify passed in a container, and the container is gone"
kill "$HOLDER_PID" 2>/dev/null || true
HOLDER_PID=""
# The fresh copy has done its job. Scene E counts copies, so it must not stay.
rmdb "$FRESH"

# ── D. The advisory guard ───────────────────────────────────────────────────
# Scene A started with three scratch databases (two stale, one fresh).
say "D. The advisory guard"
grep -q "WARNING (advisory): 3 scratch databases" "$LOG" \
  || { show_log; die "D: no advisory WARNING with the count 3"; }
pass "the guard printed the count, and the exit code stayed 0"

# ── B. A read-only server ───────────────────────────────────────────────────
say "B. Sweep on a server made read-only by its configuration file"
mk "$OLD1"
set_read_only on
[ "$(psql -X -d postgres -tAc 'show default_transaction_read_only')" = "on" ] \
  || die "B: could not make the server read-only"
pass "default_transaction_read_only = on for every new session"
run_backup B
set_read_only off
exists "$OLD1" && { show_log; die "B: the sweep could not drop $OLD1 on a read-only server"; }
grep -q "swept stale scratch database $OLD1" "$LOG" || { show_log; die "B: no sweep log line"; }
pass "the sweep dropped $OLD1 on a read-only server"
# The proof that the verify writes NOTHING to the cluster. Until
# 2026-10-07 this run had to fail, because CREATE DATABASE refuses on a
# read-only server. Now it must pass: the restore happens in the container.
[ "$RC" = "0" ] || { show_log; die "B: the verify failed on a read-only cluster (exit $RC). Does it write there again?"; }
grep -q "restore verified" "$LOG" || { show_log; die "B: no 'restore verified' on a read-only cluster"; }
[ -s "$DEST/$LIVE_DB.dump" ] && [ -s "$DEST/MANIFEST.txt" ] || { show_log; die "B: no dump"; }
pass "the verify passed on a read-only cluster, so it wrote nothing there"

# ── C. A drop that fails ────────────────────────────────────────────────────
# A psql shim refuses every DROP DATABASE and echoes a connection string with
# a password, the way libpq does. Everything else reaches the real server.
say "C. A scratch drop that fails"
# A stale copy, as an older run left it. The sweep must try to drop it.
mk "$OLD1"
REAL_PSQL="$(command -v psql)"
SHIM="$WORK/shim"
mkdir -p "$SHIM"
cat > "$SHIM/psql" <<SH
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    *"DROP DATABASE"*)
      echo 'psql: error: connection to server at "db.example" failed: postgresql://postgres:s3cr3t-shim-pw@db.example:5432/postgres (password=s3cr3t-shim-pw) refused' >&2
      exit 2 ;;
  esac
done
exec "$REAL_PSQL" "\$@"
SH
chmod +x "$SHIM/psql"
run_backup C "$SHIM"
[ "$RC" != "0" ] || { show_log; die "C: the scratch drop failed and the backup exited 0 (silent)"; }
[ -s "$DEST/$LIVE_DB.dump" ] || { show_log; die "C: no dump on disk after the failed drop"; }
[ -s "$DEST/MANIFEST.txt" ] || { show_log; die "C: no manifest on disk after the failed drop"; }
pg_restore --list "$DEST/$LIVE_DB.dump" >/dev/null || die "C: the kept dump is not readable"
pass "exit $RC, and the dump and the manifest are complete"
grep -qE "ERROR: could not drop scratch database 'acb_verify_[0-9]+'" "$LOG" \
  || { show_log; die "C: no ERROR line that names the scratch database"; }
grep -q 'connection to server at "db.example" failed' "$LOG" \
  || { show_log; die "C: the reason was swallowed"; }
grep -q 's3cr3t-shim-pw' "$LOG" && die "C: a password reached the log"
grep -q 'scratch database(s) could not be dropped' "$LOG" || die "C: no closing ERROR"
pass "the ERROR names the database, gives the reason, and shows no password"
grep -q "restore verified" "$LOG" || die "C: the verify itself did not pass first"
pass "the verify passed, so the red exit is the drop alone"

# ── E. No copy on the cluster, night after night ────────────────────────────
# Scene C left $OLD1, and its drop still fails. Two more nights: the count of
# scratch databases must not move, and each night must still verify.
say "E. No copy on the cluster: two more nights with a drop that keeps failing"
before="$(scratch_count)"
run_backup E1 "$SHIM"
run_backup E2 "$SHIM"
after="$(scratch_count)"
[ "$after" = "$before" ] || die "E: $before scratch copies became $after — a run made a copy on the cluster"
for night in E1 E2; do
  grep -q "restore verified" "$WORK/$night.log" || { show_log; die "E: night $night did not verify"; }
done
[ "$RC" != "0" ] || { show_log; die "E: the drop of $OLD1 failed, yet the run exited 0"; }
[ -s "$DEST/$LIVE_DB.dump" ] || die "E: no dump"
pass "$after scratch copy after two more failed nights, and both nights verified"
rmdb "$OLD1"

# ── F. Docker that will not start a container ───────────────────────────────
# A docker shim that fails every call, the way a stopped daemon does. The
# verify must fail loudly, keep the dump, and make no copy on the cluster.
say "F. Docker that will not start a container"
DSHIM="$WORK/dshim"
mkdir -p "$DSHIM"
cat > "$DSHIM/docker" <<'SH'
#!/usr/bin/env bash
echo 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?' >&2
exit 1
SH
chmod +x "$DSHIM/docker"
before="$(scratch_count)"
run_backup F "$DSHIM"
[ "$RC" != "0" ] || { show_log; die "F: Docker failed, yet the verify exited 0"; }
grep -q "the verify container did not start" "$LOG" || { show_log; die "F: no ERROR that names the container"; }
grep -q "restore verified" "$LOG" && { show_log; die "F: a verify passed without a container. Where did it restore?"; }
[ "$(scratch_count)" = "$before" ] || { show_log; die "F: the run fell back to a copy on the cluster"; }
[ -s "$DEST/$LIVE_DB.dump" ] && [ -s "$DEST/MANIFEST.txt" ] || { show_log; die "F: no dump"; }
pass "exit $RC, the dump is kept, and nothing was restored into the cluster"

printf "\n==> SCRATCH-DATABASE REHEARSAL PASSED\n"
