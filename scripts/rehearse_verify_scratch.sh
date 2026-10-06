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
# This runs the REAL `backup_db.sh` against a REAL Postgres (R8), in four
# scenes. Each scene names the mutation it catches (R7):
#
#   A. Sweep. Stale scratch databases go, even with a connection open on one.
#      Decoys that only LOOK like scratch databases stay, and are backed up.
#        mutation: drop `WITH (FORCE)`   -> the open connection blocks the drop
#        mutation: widen `$scratch_re`   -> a decoy is dropped
#   B. Read-only server. The sweep still drops a stale scratch database while
#      `default_transaction_read_only = on` comes from the configuration file.
#        mutation: drop the `SET default_transaction_read_only = off`
#   C. A failed drop. The backup exits non-zero, AFTER the dump and the
#      manifest exist, and the reason is printed with no password in it.
#        mutation: put back `|| true`    -> the exit code goes green
#        mutation: drop `redact`         -> the shim's password reaches the log
#   D. The advisory guard prints a WARNING with the count (no exit code).
#   E. The cap. With a drop that keeps failing, the third night makes no new
#      copy, so the pile stops at two.
#        mutation: remove the post-sweep count -> a third copy is made
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
# because scene B uses ALTER SYSTEM.
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
  rm -rf "$WORK"
}
trap cleanup EXIT

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
[ "$(q "select count(*) from pg_database where datname ~ '^acb_verify_[0-9]+\$'")" = "1" ] \
  || { show_log; die "A: the run left its own scratch database behind"; }
pass "the run dropped its own scratch database"
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
# CREATE DATABASE must still refuse on a read-only server, and loudly.
[ "$RC" != "0" ] || { show_log; die "B: a read-only server cannot give a verify, yet the run exited 0"; }
[ -s "$DEST/$LIVE_DB.dump" ] && [ -s "$DEST/MANIFEST.txt" ] || { show_log; die "B: no dump"; }
pass "the verify failed loudly (exit $RC), and the dump was kept"

# ── C. A drop that fails ────────────────────────────────────────────────────
# A psql shim refuses every DROP DATABASE and echoes a connection string with
# a password, the way libpq does. Everything else reaches the real server.
say "C. A scratch drop that fails"
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
pass "the failure came after a passed verify, so it is the drop alone"

# ── E. The cap: a drop that keeps failing cannot pile copies up ─────────────
# Scene C left one copy. Two more nights with the same broken drop: the first
# leaves a second copy, and the next one must refuse to make a third.
say "E. The cap: two more nights with a drop that keeps failing"
run_backup E1 "$SHIM"
run_backup E2 "$SHIM"
left="$(q "select count(*) from pg_database where datname ~ '^acb_verify_[0-9]+\$'")"
[ "$left" -le 2 ] || die "E: $left scratch copies after three failed nights — the pile grows"
grep -q "The deep verify is SKIPPED" "$LOG" || { show_log; die "E: the third night did not skip the verify"; }
grep -q "restore verified" "$WORK/E1.log" || { show_log; die "E: the second night should still verify (one copy left)"; }
grep -q "restore verified" "$LOG" && { show_log; die "E: the third night made a new copy"; }
[ "$RC" != "0" ] || { show_log; die "E: a skipped verify exited 0"; }
[ -s "$DEST/$LIVE_DB.dump" ] || die "E: no dump on the night the verify was skipped"
pass "$left copies after three failed nights, and the third night skipped the verify loudly"

printf "\n==> SCRATCH-DATABASE REHEARSAL PASSED\n"
