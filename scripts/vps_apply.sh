#!/usr/bin/env bash
# WS-25 D1 — the deploy steps, as a versioned file.
#
# This file was lifted BYTE-IDENTICALLY out of `.github/workflows/deploy.yml`'s
# `env.DEPLOY_SCRIPT` (437 lines, sha256 a779724d089319f6…). It is the single
# copy both delivery paths run:
#
#   push path  `.github/workflows/deploy.yml` — `ssh 'bash -s' < this file`
#   pull path  `scripts/vps_pull.sh`          — `git show <sha>:this file | bash`
#
# One file, so the two paths cannot drift. Drift between them would only ever
# surface during an incident, which is the worst moment to discover it.
#
# ── The shebang is new, and it is the only thing that is ─────────────────────
# The extraction left line 1 as `set -e`, because inside a YAML `env:` value fed
# to `bash -s` there was nothing to declare a shell TO. That cost D1 half of its
# stated payoff: with no shebang and no `shell` directive, shellcheck refuses to
# analyse the file at all (SC2148, error) and exits 1 having checked nothing.
# The line is inert on both delivery paths — both invoke `bash <file>` or pipe
# into `bash -s`, where a `#!` is just a comment — so this buys the analysis for
# no behaviour change whatsoever.
#
# The file is deliberately left NON-executable (0644), matching vps_pull.sh.
# Nothing execs it by path; both callers name the interpreter. Marking it +x
# would advertise a fourth way to start a deploy that neither path uses.
#
# ── Running it by hand during an incident ────────────────────────────────────
#   cd /opt/acb/app && APP_DIR=/opt/acb/app bash scripts/vps_apply.sh
#
# ⚠️ but NOT from a checkout you are about to have rewritten. Step 0 below is
# `git reset --hard origin/main` — it replaces THIS FILE while bash is still
# reading it, and bash reads a script incrementally by byte offset. Measured,
# all three outcomes, none of which raise an alarm you would notice:
#   • git replaces by RENAME, so the open fd keeps the old inode: every step
#     runs, but they are the OLD file's steps against the NEW tree. Exit 0.
#   • an in-place rewrite to a SHORTER file: bash resumes past EOF and the
#     remaining steps silently do not happen at all. Exit 0.
#   • an in-place rewrite that merely SHIFTS bytes: bash resumes mid-token
#     (`--quiet` → `iet: command not found`). Exit 127.
# Copy it out first — `git show origin/main:scripts/vps_apply.sh > /tmp/a.sh`
# — and run THAT. This is what vps_pull.sh does, and why.
#
# ⚠️ A copy taken out first is still the WRONG copy when a merge lands during
# the apply. So every step AFTER the pull runs from the pulled commit's own
# copy of this file ("Run the pulled commit's own copy" below). The pull block
# itself (ownership repair, agents.json backup, fetch, reset) runs from the
# copy that started. A change to the pull block takes effect on the next apply.
set -e

# The sha256 of the copy that bash runs now, read before anything can rewrite
# it. It stays empty when bash reads the script from stdin, as the push path
# does (`ssh … bash -s`), because no file then holds it.
VPS_APPLY_SELF_SUM=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then
  VPS_APPLY_SELF_SUM="$(sha256sum < "${BASH_SOURCE[0]}" | cut -d' ' -f1)" || VPS_APPLY_SELF_SUM=""
fi
# A re-executed copy runs from a temp file that the first copy wrote. Remove
# it now, after the hash and before any step can exit, so no exit path leaves
# it behind. bash keeps its fd open, so it reads on. The name must match, so
# this never removes a file that a person ran by hand. Limit: a revert to a
# target older than this code has no such line, and its temp copy stays.
if [ "${VPS_APPLY_REEXECED:-0}" = "1" ]   && [ "${VPS_APPLY_REEXEC_FILE:-}" = "${BASH_SOURCE[0]:-}" ]; then
  case "$VPS_APPLY_REEXEC_FILE" in
    "${TMPDIR:-/tmp}"/acb-vps-apply-reexec.*) rm -f "$VPS_APPLY_REEXEC_FILE" ;;
  esac
fi
APP_DIR="${APP_DIR:-/opt/acb/app}"
cd "$APP_DIR"

# ── ONE DEPLOY AT A TIME ON THIS BOX (H-89, H-164) ──────────────────────────
#
# 🔴 **TWO DELIVERY PATHS SHARE ONE CHECKOUT, AND NOTHING MADE THEM TAKE
# TURNS.** The workflow runs this file over ssh as the app user. The box's own
# `acb-pull.timer` runs it every five minutes. Measured 2026-09-21 to
# 2026-09-26, three failure shapes, all from the overlap:
#
#   • EACCES on `.next.staging/trace`: root's pull left paths the app-user
#     build could not write. Run 36177499439 failed all three rounds.
#   • `Cannot find module 'next/server.js'` and `next: not found`: one path's
#     `npm ci` removed `node_modules/next` while the other built or started.
#   • an orphan `next build` ran for 41 minutes after its CI round ended.
#
# So every apply takes ONE exclusive lock, for the whole apply: fetch,
# install, build, swap, restart. The CI path WAITS for it, with a bound. The
# pull path does NOT wait: `vps_pull.sh` takes the same lock itself, exits 0
# when it is busy, and sets DEPLOY_LOCK_HELD=1 so this file does not take it
# twice.
#
# ⚠️ The lock lives beside the checkout, NOT in /tmp or /run/lock. Those are
# sticky world-writable directories, and `fs.protected_regular=2` on the box
# refuses an O_CREAT open of a file another user owns there — root included.
# The file is opened READ-ONLY, because flock(2) ignores the open mode, so a
# lock file root created stays usable by the app user and the other way round.
#
# `tests/unit/test_deploy_serialize.py` sources the block between the two
# marker lines below and runs it against a real lock file. Keep it free of
# side effects: definitions and defaults only.
# >>> deploy-serialize helpers
DEPLOY_LOCK="${DEPLOY_LOCK:-$(dirname "$APP_DIR")/acb-deploy.lock}"
DEPLOY_MARKER="${DEPLOY_MARKER:-$(dirname "$APP_DIR")/acb-deploy.applied}"
DEPLOY_LOCK_WAIT="${DEPLOY_LOCK_WAIT:-900}"
NEXT_BUILD_TIMEOUT_S="${NEXT_BUILD_TIMEOUT_S:-1200}"
DEPLOY_BUILD_PIDFILE="${DEPLOY_BUILD_PIDFILE:-$DEPLOY_LOCK.build}"

# Give a file this script created to the checkout's owner. A root-run apply
# must not leave a lock or a marker the app user cannot replace.
deploy_give_to_owner() {
  [ "$(id -u)" = "0" ] || return 0
  chown "$(stat -c '%U:%G' "$APP_DIR")" "$@" 2>/dev/null || true
}

# "since <time>, pid <pid>" for whoever holds the lock now.
deploy_lock_holder() {
  hpid="" hsince="" hwho=""
  read -r hpid hsince hwho < "$DEPLOY_LOCK.holder" 2>/dev/null || true
  # /proc, not `kill -0`: the app user cannot signal a root process, and
  # EPERM would read as "dead".
  if [ -n "$hpid" ] && [ -d "/proc/$hpid" ]; then
    echo "since $hsince, pid $hpid ($hwho)"
  else
    echo "since an unknown time, pid unknown (the holder left no record)"
  fi
}

# Take the lock on fd 8. $1 = wait | nowait.
#   0  the lock is held, until this process and its children exit
#   75 nowait, and another deploy holds it
#   1  wait, and it stayed busy for DEPLOY_LOCK_WAIT seconds
deploy_lock_acquire() {
  if [ ! -e "$DEPLOY_LOCK" ]; then
    ( umask 022; : >> "$DEPLOY_LOCK" ) 2>/dev/null || true
    deploy_give_to_owner "$DEPLOY_LOCK"
  fi
  if ! exec 8<"$DEPLOY_LOCK"; then
    echo "    !! cannot open the deploy lock $DEPLOY_LOCK"
    return 1
  fi
  if ! flock -n 8; then
    [ "$1" = "nowait" ] && return 75
    echo "    waiting up to ${DEPLOY_LOCK_WAIT}s: another deploy holds the lock $(deploy_lock_holder)"
    flock -w "$DEPLOY_LOCK_WAIT" 8 || return 1
  fi
  printf '%s %s %s\n' "$$" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$(id -un)" \
    > "$DEPLOY_LOCK.holder.$$" 2>/dev/null \
    && mv -f "$DEPLOY_LOCK.holder.$$" "$DEPLOY_LOCK.holder" 2>/dev/null \
    && deploy_give_to_owner "$DEPLOY_LOCK.holder"
  return 0
}

# True when a COMPLETE apply of $1 already finished and nothing has moved
# since: HEAD is there, the marker names it, and both builds are in place.
deploy_already_applied() {
  [ -n "$1" ] || return 1
  [ "$(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null)" = "$1" ] || return 1
  msha="" mwhen=""
  read -r msha mwhen < "$DEPLOY_MARKER" 2>/dev/null || return 1
  [ "$msha" = "$1" ] || return 1
  [ -f "$APP_DIR/workbench/control_plane/.next/BUILD_ID" ] || return 1
  [ -x "$APP_DIR/workbench/control_plane/node_modules/.bin/next" ] || return 1
  DEPLOY_APPLIED_AT="$mwhen"
  return 0
}

# Written ONLY on the success path, just before the final line. It records
# "a complete apply of this sha finished", which is the claim the skip needs.
#
# $2, when given, is the sha256 of the script that ran the steps. The marker
# is then written only when $1's own copy of this file has that sha256. A
# marker for a sha whose steps did not run is how the BH-7 drop-ins were lost
# on 2026-10-08, with every deploy green. The call at the end of this file
# always passes $2. Fence: `tests/unit/test_deploy_reexec.py`.
record_applied_sha() {
  if [ "$#" -ge 2 ]; then
    ras_want="$(git -C "$APP_DIR" show "$1:scripts/vps_apply.sh" 2>/dev/null | sha256sum | cut -d' ' -f1)"
    if [ -z "$2" ] || [ "$2" != "$ras_want" ]; then
      echo "    !! NOT recording ${1:0:12} as applied: the steps that ran are not that commit's"
      echo "       copy of scripts/vps_apply.sh. The next deploy applies it again."
      return 0
    fi
  fi
  printf '%s %s\n' "$1" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    > "$DEPLOY_MARKER.tmp.$$" 2>/dev/null \
    && mv -f "$DEPLOY_MARKER.tmp.$$" "$DEPLOY_MARKER" 2>/dev/null \
    && deploy_give_to_owner "$DEPLOY_MARKER" \
    && return 0
  echo "    ~ could not write $DEPLOY_MARKER — the next apply rebuilds this sha instead of skipping it"
  return 0
}

# 🔴 H-164: `next start` without `next` is a unit that dies on restart. Check
# the binary BEFORE a restart, and refuse the restart when it is missing, so
# the process that is running now keeps serving.
require_next_bin() {
  [ -x "$1/node_modules/.bin/next" ] && return 0
  echo "    !! $2: $1/node_modules/.bin/next is MISSING — refusing to restart $2 (H-164)."
  echo "       A restart now runs \`next start\` with no \`next\` and the unit dies"
  echo "       with 'next: not found'. The running process keeps serving."
  return 1
}

# The nearest sshd ancestor: the session this apply lives in, if any.
session_anchor() {
  if [ -n "${DEPLOY_TETHER_ANCHOR:-}" ]; then echo "$DEPLOY_TETHER_ANCHOR"; return 0; fi
  p="$$"
  while [ -n "$p" ] && [ "$p" -gt 1 ]; do
    case "$(ps -o comm= -p "$p" 2>/dev/null)" in
      sshd*) echo "$p"; return 0 ;;
    esac
    p="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')"
  done
  return 0
}

# True when this apply had a session and that session is gone.
deploy_session_ended() {
  [ -n "${DEPLOY_SESSION_ANCHOR:-}" ] && [ ! -d "/proc/$DEPLOY_SESSION_ANCHOR" ]
}

# 🔴 **AN ENDED SSH SESSION DOES NOT END THE APPLY.** `ssh 'bash -s'` has no
# tty, so nothing sends SIGHUP when the runner's `timeout` kills ssh or a round
# is cancelled. Worse, the rest of this script is already in the pipe buffer,
# so bash finishes the build and then SWAPS AND RESTARTS with nobody watching.
# Measured 2026-09-25: `next build` ran for 41 minutes after its round ended.
#
# 🔴 **THE WATCHER KILLS THE BUILD, AND ONLY THE BUILD.** The first version
# killed the whole apply, and a review showed that was worse than the orphan.
# 197 of the 219 migration files run in autocommit. A kill in the middle of one
# leaves half a file committed and no ledger row, and each replay then fails.
# A kill between the two `mv` calls of the swap leaves no `.next` at all. So
# migrations, the backup, the unit sync, the swap and the restarts always run
# to their end.
#
# The shape: `run_tethered_build` runs the build under `timeout`, which puts
# the build in its OWN process group, and writes that group id to
# DEPLOY_BUILD_PIDFILE. When the session goes, the watcher sends TERM and then
# KILL to that SAME group, so a child that lost its parent still dies.
# `run_tethered_build` also refuses to START a build after the session has
# gone. The apply then stops at the build with a non-zero exit, and the running
# `.next` keeps serving, because a failed build never swaps.
#
# The pull path runs under systemd with no sshd ancestor, and systemd already
# kills its whole cgroup on a timeout, so it gets no watcher.
tether_to_session() {
  [ "${DEPLOY_TETHER:-1}" = "1" ] || return 0
  anchor="${DEPLOY_SESSION_ANCHOR:-}"
  [ -n "$anchor" ] || return 0
  apply_pid="$$"
  (
    exec 8<&-   # never hold the deploy lock after the apply has gone
    while [ -d "/proc/$anchor" ]; do
      [ -d "/proc/$apply_pid" ] || exit 0
      sleep "${DEPLOY_TETHER_POLL:-5}"
    done
    logger -t acb-deploy "deploy session $anchor ended: apply $apply_pid finishes its current step, and its build is stopped" 2>/dev/null || true
    while [ -d "/proc/$apply_pid" ]; do
      bpg=""
      read -r bpg < "$DEPLOY_BUILD_PIDFILE" 2>/dev/null || true
      # Signal the group only while its leader is still OUR `timeout`. A pid
      # the kernel reused must never lose its group to a stale file.
      if [ -n "$bpg" ] && [ -d "/proc/$bpg" ] \
        && [ "$(cat "/proc/$bpg/comm" 2>/dev/null)" = "timeout" ]; then
        kill -TERM -- "-$bpg" 2>/dev/null || true
        sleep "${DEPLOY_TETHER_GRACE:-20}"
        kill -KILL -- "-$bpg" 2>/dev/null || true
      fi
      sleep "${DEPLOY_TETHER_POLL:-5}"
    done
  ) </dev/null >/dev/null 2>&1 &
  echo "    tethered to deploy session pid $anchor: if it ends, the build stops and nothing else does"
}

# Run "$@" as the one step the watcher may kill. GNU `timeout` makes its own
# process group, so the group id is the pid of `timeout`. `wait` returns the
# build's status, and a killed build is a failed build.
run_tethered_build() {
  if deploy_session_ended; then
    echo "    !! the deploy session $DEPLOY_SESSION_ANCHOR ended — not starting a build that nobody watches"
    return 1
  fi
  timeout -k 60 "$NEXT_BUILD_TIMEOUT_S" "$@" &
  tb_pid=$!
  echo "$tb_pid" > "$DEPLOY_BUILD_PIDFILE" 2>/dev/null || true
  tb_rc=0
  wait "$tb_pid" || tb_rc=$?
  rm -f "$DEPLOY_BUILD_PIDFILE" 2>/dev/null || true
  return "$tb_rc"
}

# 🟢 **H-60: A RESTARTED SERVICE IS READY WHEN IT ANSWERS, NOT WHEN SYSTEMD
# SAYS `active`.** `active` means that a process exists. The gateway then needs
# 15 to 23 s before uvicorn listens (measured 2026-09-27). The old `sleep 3`
# let the apply start the next step while the gateway was still cold.
#
# $1 = a name for the log, $2 = a URL, $3 = seconds to wait (default 120).
# Any HTTP answer from 200 to 499 counts as ready. A refused connection (000)
# and a 5xx do not. Returns 1 when the bound runs out.
wait_ready() {
  wr_name="$1" wr_url="$2" wr_max="${3:-120}"
  wr_start="$(date +%s)"
  while :; do
    wr_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$wr_url" 2>/dev/null || true)"
    case "$wr_code" in
      2??|3??|4??)
        echo "    $wr_name answers HTTP $wr_code after $(( $(date +%s) - wr_start ))s"
        return 0 ;;
    esac
    if [ $(( $(date +%s) - wr_start )) -ge "$wr_max" ]; then
      echo "    !! $wr_name did not answer $wr_url within ${wr_max}s (last answer: ${wr_code:-none})"
      return 1
    fi
    sleep "${WAIT_READY_POLL:-1}"
  done
}

# 🔴 **H-60: `npm ci` DELETES `node_modules` UNDER THE RUNNING SERVER.** The
# live `next start` loads modules on demand, so for the whole install it
# answers 500. Measured 2026-09-27, run 36320367460: `npm ci` took 38 s, and
# the workbench logged `Cannot find module 'next/dist/compiled/cookie'` in
# that window. `package-lock.json` changed in 1 of 324 merges in 30 days, so
# almost every one of those installs rebuilt the tree it deleted, byte for byte.
#
# So the install records what it installed, INSIDE `node_modules`. The next
# apply keeps the tree when the lock file and the node version are the same.
# The stamp is removed BEFORE an install starts and written only after one
# succeeds, so an interrupted install never leaves a stamp beside a damaged
# tree. These run in the app's directory.
#
# ⚠️ DEPLOY_REINSTALL=1 forces the install, NOT DEPLOY_FORCE. A rerun of the
# deploy sends DEPLOY_FORCE=1 to re-apply a sha, usually after an .env edit.
# If that also ran `npm ci`, every rerun would bring the 38 s of 500 back.
deps_stamp() {
  printf '%s %s\n' "$(sha256sum package-lock.json 2>/dev/null | cut -d' ' -f1)" \
    "$(node --version 2>/dev/null)"
}
deps_unchanged() {
  [ "${DEPLOY_REINSTALL:-0}" = "1" ] && return 1
  [ -f package-lock.json ] || return 1
  [ -x node_modules/.bin/next ] || return 1
  [ -f node_modules/.acb-deps-stamp ] || return 1
  [ "$(cat node_modules/.acb-deps-stamp 2>/dev/null)" = "$(deps_stamp)" ]
}
# Only after an install that succeeded.
record_deps_stamp() {
  deps_stamp > node_modules/.acb-deps-stamp 2>/dev/null || true
}

# 🔴 **H-60: A CHANGED CADDY CONFIG IS PROVED ON EVERY HOST, NOT ONLY BY
# `is-active`.** A config can start cleanly and still route a host to nothing,
# or drop a host. So after a restart the apply asks each site host through the
# local Caddy, with the real SNI and Host.
#
# The site hosts: top-level lines `host[, host] {` that name a dotted host.
caddy_sites() {
  awk '/^[^[:space:]#({][^{]*\{[[:space:]]*$/ {
         for (i = 1; i < NF; i++) { h = $i; gsub(",", "", h); if (h ~ /\./) print h }
       }' "$1"
}
# $1 = the Caddyfile. Returns 1 when any host fails.
#
# ⚠️ **THE QUESTION IS "DOES CADDY ROUTE THIS HOST", NOT "IS THE APP
# HEALTHY".** Any three-digit HTTP code answers it, a 500 included. Upstream
# health is `wait_ready`'s job, AFTER the rebuild. A probe that failed on a
# 500 rolled Caddy back BEFORE the rebuild that would fix the 500, so every
# deploy looped (H-60 re-review, P1).
#
# ⚠️ **ONE ATTEMPT IS NOT ENOUGH.** A new hostname has no certificate until
# certmagic fetches it after the start, so the first answers are 000. Each
# host therefore gets attempts until CADDY_PROBE_DEADLINE (90 s), and fails
# only on 000 at the deadline. Each attempt waits CADDY_PROBE_MAX (40 s),
# longer than lb_try_duration (30 s), so a held request ends as a 502, not 000.
caddy_probe_sites() {
  cps_bad=0
  cps_port="${CADDY_PROBE_PORT:-443}"
  if [ -z "$(caddy_sites "$1")" ]; then
    echo "    !! caddy: $1 names no site host — nothing to prove it on"
    return 1
  fi
  for cps_host in $(caddy_sites "$1"); do
    cps_end=$(( $(date +%s) + ${CADDY_PROBE_DEADLINE:-90} ))
    cps_tries=0
    while :; do
      cps_tries=$((cps_tries + 1))
      cps_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "${CADDY_PROBE_MAX:-40}" \
        --resolve "$cps_host:$cps_port:127.0.0.1" \
        "${CADDY_PROBE_SCHEME:-https}://$cps_host:$cps_port/" 2>/dev/null || true)"
      case "$cps_code" in
        [1-5][0-9][0-9])
          echo "    caddy: $cps_host answers HTTP $cps_code (attempt $cps_tries)"
          break ;;
      esac
      if [ "$(date +%s)" -ge "$cps_end" ]; then
        echo "    !! caddy: $cps_host FAILED — no HTTP answer in ${CADDY_PROBE_DEADLINE:-90}s ($cps_tries attempts, last: ${cps_code:-none})"
        cps_bad=1
        break
      fi
      sleep "${CADDY_PROBE_INTERVAL:-2}"
    done
  done
  return "$cps_bad"
}
# <<< deploy-serialize helpers

# Find the session BEFORE the lock wait. A round cancelled during the wait
# leaves this shell under init, and then no sshd is left to find.
DEPLOY_SESSION_ANCHOR=""
if [ "${DEPLOY_TETHER:-1}" = "1" ]; then
  DEPLOY_SESSION_ANCHOR="$(session_anchor)"
fi

echo "==> Taking the deploy lock ($DEPLOY_LOCK)"
if [ "${DEPLOY_LOCK_HELD:-0}" = "1" ]; then
  # vps_pull.sh holds it on fd 8, or the first copy of this script held it on
  # fd 8 before its re-exec. The fd stays open through `exec`, so the lock
  # does too. A second acquire here would wait on that same lock.
  echo "    held by the caller (vps_pull.sh, or this script before its re-exec)"
else
  lock_rc=0
  deploy_lock_acquire "${DEPLOY_LOCK_MODE:-wait}" || lock_rc=$?
  if [ "$lock_rc" = "75" ]; then
    echo "    another deploy holds the lock $(deploy_lock_holder) — nothing to do"
    exit 0
  elif [ "$lock_rc" != "0" ]; then
    echo "    !! another deploy holds the lock $(deploy_lock_holder)."
    echo "       Waited ${DEPLOY_LOCK_WAIT}s and it is still busy. This round did NOTHING:"
    echo "       no fetch, no build, no restart. Retry when the holder finishes."
    exit 1
  fi
  echo "    lock taken"
fi
# A pidfile left by an apply that died inside its build (a reboot, a stop, a
# unit timeout) names a group that no longer exists. Clear it under the lock,
# before a tether can read it.
rm -f "$DEPLOY_BUILD_PIDFILE" 2>/dev/null || true
if deploy_session_ended; then
  if [ "${VPS_APPLY_REEXECED:-0}" = "1" ]; then
    # The first copy already did the fetch and the reset, so "did NOTHING"
    # would be false here.
    echo "    !! the deploy session $DEPLOY_SESSION_ANCHOR ended before the re-executed copy began its steps."
    echo "       The checkout is already reset to ${DEPLOY_TARGET_SHA:0:12}, but no service was restarted."
    echo "       No marker was written, so the next deploy applies ${DEPLOY_TARGET_SHA:0:12} again."
  else
    echo "    !! the deploy session $DEPLOY_SESSION_ANCHOR ended while this apply waited."
    echo "       This round did NOTHING: no fetch, no migration, no build, no restart."
  fi
  exit 1
fi
# After a re-exec, the watcher of the first copy still watches this pid,
# because `exec` keeps the pid. A second watcher would do the same work twice.
if [ "${VPS_APPLY_REEXECED:-0}" != "1" ]; then
  tether_to_session
fi

echo "==> Pulling latest from origin/main"
if [ "${VPS_APPLY_REEXECED:-0}" = "1" ]; then
  # The first copy did the fetch, the skip check and the reset, and then ran
  # this copy. Do not fetch again: a newer origin/main is the next deploy's job.
  echo "    the first copy pulled ${DEPLOY_TARGET_SHA:0:12}. This is that commit's own copy of the script"
else
  # 🔴 **REPAIR THE CHECKOUT'S OWNERSHIP FIRST (H-89).** Same bug as the `.venv`
  # one below, one directory over — and this one is worse, because it blocks the
  # `git reset` that would have delivered its own fix.
  #
  # `git reset --hard` UNLINKS a tracked file to rewrite it, and unlinking needs
  # write permission on the CONTAINING DIRECTORY. A directory owned `root:root`
  # with `drwxr-xr-x` therefore stops the app user dead:
  #
  #   error: unable to unlink old
  #   'workbench/operator_console/src/app/models/ModelDetails.tsx':
  #   Permission denied
  #
  # Measured 2026-08-31: that killed the deploys of PR #190 and PR #198, three
  # rounds each, both with green CI. The box sat on 3ad494bd for a day while
  # `main` moved two merges ahead — and stayed UP the whole time, serving old
  # code, so nothing alarmed. 113 root-owned paths in the tracked tree, created
  # 2026-08-30 16:49 by something in that deploy running as root.
  #
  # `.venv` gets this treatment at line ~270 and the source tree never did, which
  # is why the earlier fix could not save this case: `uv sync` is far downstream
  # of the checkout that now fails.
  #
  # ⚠️ Scoped to what git must rewrite. `.next` is ~66k root-owned build files and
  # is gitignored, so `git reset` never touches it — chowning it here would turn
  # a fast repair into a minutes-long one for no benefit. `node_modules` likewise.
  #
  # `find -exec … +` rather than `chown $(find …)`: the command substitution
  # splits on whitespace, so it breaks on any path with a space in it, and a tree
  # this size can overflow the argument list. `find` under `sudo` also keeps the
  # traversal quiet on directories the app user cannot read.
  CHECKOUT_OWNER="$(stat -c '%U:%G' "$APP_DIR")"
  CHECKOUT_USER="${CHECKOUT_OWNER%%:*}"
  if sudo find "$APP_DIR" \( -name .next -o -name node_modules \) -prune -o \
       ! -user "$CHECKOUT_USER" -exec chown "$CHECKOUT_OWNER" {} + 2>/dev/null; then
    echo "    checkout ownership normalised to $CHECKOUT_OWNER before reset"
  else
    echo "    WARNING: could not repair checkout ownership — git reset may fail"
    echo "             with 'unable to unlink old' (see H-89)."
  fi

  # Preserve runtime-managed state that lives in tracked files but is
  # mutated on the VPS (agents.json = Control-Plane agent registry).
  # git reset --hard would otherwise wipe agents registered via the UI.
  cp apps/services/gateway/agents.json /tmp/acb-agents.json.bak 2>/dev/null || true
  git fetch origin main
  DEPLOY_TARGET_SHA="$(git rev-parse origin/main)"

  # 🟢 **THE SECOND PATH NO LONGER REBUILDS WHAT THE FIRST JUST SHIPPED.**
  # Measured 2026-09-26: a CI build at 06:34, and root's pull rebuilt the SAME
  # commit at 06:47. The pull gate reads its own marker only, so a CI deploy
  # never counted. Now both paths write one marker, and both check it here,
  # INSIDE the lock. A skip prints the final line on purpose: a complete apply
  # of this exact sha has finished, which is what deploy.yml's gate asks.
  # DEPLOY_FORCE=1 (`vps_pull.sh --force`) always re-applies, for an .env edit.
  if [ "${DEPLOY_FORCE:-0}" != "1" ] && deploy_already_applied "$DEPLOY_TARGET_SHA"; then
    echo "    already at ${DEPLOY_TARGET_SHA:0:12}, skipping — a complete apply of it finished at ${DEPLOY_APPLIED_AT:-?}"
    echo "==> Deployment complete"
    exit 0
  fi
  git reset --hard origin/main
  if [ -s /tmp/acb-agents.json.bak ]; then
    cp /tmp/acb-agents.json.bak apps/services/gateway/agents.json
    echo "    restored runtime agents.json ($(wc -l < apps/services/gateway/agents.json) lines)"
  fi
  # ── Run the pulled commit's own copy of this script ───────────────────────
  # 🔴 **THE STEPS THAT RUN MUST BE THE STEPS OF THE SHA THAT IS RECORDED.**
  # Measured 2026-10-08: a deploy of 90fc39e3 ran its own copy of this file.
  # Its reset moved the checkout to 469f5081, which merged during the apply
  # and added the BH-7 step. The old copy ran its old steps and recorded
  # 469f5081 as applied. Each later deploy of 469f5081 then skipped. So the
  # BH-7 drop-ins never went in, and every deploy job was green.
  #
  # So read the target's own copy out of the object database, as vps_pull.sh
  # does, and compare it with the copy that runs now. When they differ, or
  # when this copy came from stdin and has no hash, `exec` the target's copy
  # ONE time. Every step after this point then runs from the target's copy.
  # This pull block does not: it ran from the copy that started, and a change
  # to it takes effect on the next apply. `exec` keeps the pid, so the deploy lock on fd 8 and the watcher
  # of the deploy session stay. VPS_APPLY_REEXECED=1 makes the new copy skip
  # the lock, the watcher and this pull step, so it cannot exec again. stdin
  # becomes /dev/null, because on the push path it holds the rest of the OLD
  # script. The new copy removes its temp file as its first act.
  #
  # Fence: `tests/unit/test_deploy_reexec.py`. It runs an old copy against a
  # merge that adds a step, and it fails unless that step runs exactly once.
  VPS_APPLY_NEXT="$(mktemp "${TMPDIR:-/tmp}/acb-vps-apply-reexec.XXXXXX")"
  if ! git show "$DEPLOY_TARGET_SHA:scripts/vps_apply.sh" > "$VPS_APPLY_NEXT"; then
    rm -f "$VPS_APPLY_NEXT"
    echo "    !! cannot read scripts/vps_apply.sh at ${DEPLOY_TARGET_SHA:0:12}. Refusing to run the steps of another commit"
    exit 1
  fi
  if [ -n "$VPS_APPLY_SELF_SUM" ] && [ "$(sha256sum < "$VPS_APPLY_NEXT" | cut -d' ' -f1)" = "$VPS_APPLY_SELF_SUM" ]; then
    rm -f "$VPS_APPLY_NEXT"
  else
    if [ -z "$VPS_APPLY_SELF_SUM" ]; then
      echo "    this copy came from stdin and cannot be compared. Running the copy of ${DEPLOY_TARGET_SHA:0:12} now (re-exec)"
    else
      echo "    this copy differs from the copy of ${DEPLOY_TARGET_SHA:0:12}. Running that copy now (re-exec)"
    fi
    exec env VPS_APPLY_REEXECED=1 VPS_APPLY_REEXEC_FILE="$VPS_APPLY_NEXT" \
      DEPLOY_TARGET_SHA="$DEPLOY_TARGET_SHA" DEPLOY_LOCK_HELD=1 \
      DEPLOY_TETHER_ANCHOR="$DEPLOY_SESSION_ANCHOR" \
      APP_DIR="$APP_DIR" DEPLOY_LOCK="$DEPLOY_LOCK" DEPLOY_MARKER="$DEPLOY_MARKER" \
      bash "$VPS_APPLY_NEXT" "$@" < /dev/null
  fi
fi

echo "==> Skipping deprecated LiteLLM proxy cleanup (already removed)"

echo "==> Ensuring memory-layer env vars (Neo4j disabled for low-memory VPS)"
# ⚠️ Hardcoded, while APP_DIR above is overridable — so is WB_ENV below. Noticed
# during WS-25 D1 and DELIBERATELY LEFT AS IS: on both delivery paths APP_DIR is
# /opt/acb/app, so "$APP_DIR/.env" would be the identical string today and
# changing it is a behaviour change, not a refactor. Named because D1's whole
# point is that this file can now be hand-run: `APP_DIR=/some/other/checkout`
# would git-reset one tree and then rewrite a DIFFERENT tree's .env, generating
# secrets into the live box while you thought you were in a sandbox. Until this
# is unified (owner's call), hand-run it only with APP_DIR=/opt/acb/app.
ENV_FILE="/opt/acb/app/.env"

# ── WS-49 BH-2: a writer of .env must keep its inode ─────────────────────────
# 🔴 The gateway sandbox (50-hardening.conf) lists .env on ReadWritePaths.
# systemd bind-mounts the INODE that .env has when the gateway starts. A
# writer that replaces the file by rename (`sed -i`, `tmp && mv`) leaves the
# running gateway on the old, unlinked copy. The saves of the gateway then go
# to that dead copy, and the next restart drops them. So every edit of .env
# here builds the new content in a temp file and writes it back INTO the same
# inode. The mode and the owner stay, because the inode stays. An append
# (`>>`) keeps the inode already.
# This apply holds the deploy lock, so no other deploy writes .env at once.
# Fence: tests/unit/test_env_inode.py.
# >>> env helpers
# env_edit_in_place <file> <cmd> [args...] — run "<cmd> [args...] <file>" into
# a temp file, then write the result INTO <file>. A failed command changes
# nothing. `grep -v` exits 1 when it prints no line, and that is a result.
env_edit_in_place() {
  local f="$1" tmp rc=0
  shift
  tmp="$(mktemp "$(dirname "$f")/.env-edit.XXXXXX")"
  "$@" "$f" > "$tmp" || rc=$?
  if [ "$rc" -gt 1 ] || { [ "$rc" = 1 ] && [ "$1" != grep ]; }; then
    rm -f "$tmp"
    echo "    !! could not edit $f (exit $rc). It is unchanged."
    return 1
  fi
  cat "$tmp" > "$f"
  rm -f "$tmp"
}
# <<< env helpers

for _var in MEM0_ENABLED GRAPHITI_ENABLED; do
  if ! grep -qE "^${_var}=" "$ENV_FILE" 2>/dev/null; then
    case "$_var" in
      MEM0_ENABLED)       echo "MEM0_ENABLED=true" >> "$ENV_FILE" ;;
      GRAPHITI_ENABLED)   echo "GRAPHITI_ENABLED=false" >> "$ENV_FILE" ;;
    esac
    echo "    + added $_var to .env"
  fi
done
# Ensure GRAPHITI stays off for this VPS size (idempotent)
if grep -qE '^GRAPHITI_ENABLED=true' "$ENV_FILE" 2>/dev/null; then
  sed -i 's/^GRAPHITI_ENABLED=true/GRAPHITI_ENABLED=false/' "$ENV_FILE"
  echo "    Disabled GRAPHITI (Neo4j) — saves ~500MB RAM"
fi

echo "==> Ensuring public-URL env vars"
# This block used to also seed MICROSOFT_TENANT_ID / AUTH_MICROSOFT_ENTRA_ID_TENANT
# with one company's directory GUID — wrong on every deployment except that one
# company's. Directory pinning is a per-deployment decision: set those keys by
# hand in .env for a single-tenant silo; leave them unset for the multi-directory
# default (`organizations`, see workbench auth.ts and email transport oauth.py).
for _var in GATEWAY_PUBLIC_URL WORKBENCH_PUBLIC_URL; do
  if ! grep -qE "^${_var}=" "$ENV_FILE" 2>/dev/null; then
    case "$_var" in
      GATEWAY_PUBLIC_URL)              echo "GATEWAY_PUBLIC_URL=https://api.metorite.com" >> "$ENV_FILE" ;;
      WORKBENCH_PUBLIC_URL)            echo "WORKBENCH_PUBLIC_URL=https://app.metorite.com" >> "$ENV_FILE" ;;
    esac
    echo "    + added $_var to .env"
  fi
done

# The workbench (Next.js, systemd unit acb-workbench) is a SEPARATE
# service that reads its OWN env file (control_plane/.env.local) via
# process.env — NOT the shared .env above. Its server routes forward
# GATEWAY_INTERNAL_TOKEN to the gateway's require_internal_auth. If the
# two files' tokens drift, every workbench->gateway internal call
# (/memory, /v1/chat/completions for orchestrator chat) 401s while the
# backend agents (which share .env) keep working — a confusing
# partial outage. So make .env the single source of truth: reconcile
# GATEWAY_INTERNAL_TOKEN in .env.local to EXACTLY match .env here,
# in-place, idempotently, preserving every other key in .env.local.
echo "==> Reconciling workbench internal token (.env.local <- .env)"
WB_ENV="/opt/acb/app/workbench/control_plane/.env.local"
SHARED_TOKEN="$(grep -E '^GATEWAY_INTERNAL_TOKEN=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
if [ -z "$SHARED_TOKEN" ]; then
  echo "    ! GATEWAY_INTERNAL_TOKEN not set in $ENV_FILE — skipping (backend also relies on it; fix the secret)"
elif [ ! -f "$WB_ENV" ]; then
  echo "    ! $WB_ENV missing — creating with the token only (other workbench keys must be provisioned separately)"
  printf 'GATEWAY_INTERNAL_TOKEN=%s\n' "$SHARED_TOKEN" > "$WB_ENV"
else
  CURRENT="$(grep -E '^GATEWAY_INTERNAL_TOKEN=' "$WB_ENV" 2>/dev/null | head -1 | cut -d= -f2-)"
  if [ "$CURRENT" = "$SHARED_TOKEN" ]; then
    echo "    already in sync (sha8=$(printf %s "$SHARED_TOKEN" | sha256sum | cut -c1-8))"
  else
    cp -a "$WB_ENV" "$WB_ENV.bak.$(date +%s)"
    # Rewrite the line without a sed s/// (token may contain / & etc.):
    # drop any existing line, then append the authoritative value.
    grep -vE '^GATEWAY_INTERNAL_TOKEN=' "$WB_ENV" > "$WB_ENV.tmp" || true
    printf 'GATEWAY_INTERNAL_TOKEN=%s\n' "$SHARED_TOKEN" >> "$WB_ENV.tmp"
    mv "$WB_ENV.tmp" "$WB_ENV"
    echo "    updated .env.local token to match .env (sha8=$(printf %s "$SHARED_TOKEN" | sha256sum | cut -c1-8)); backup written"
  fi
fi

echo "==> Bootstrapping Docker Compose stack (core only)"
docker compose -f infra/docker-compose.yml --profile core up -d --remove-orphans

echo "==> Waiting for healthchecks (up to 90s)"
deadline=$(( $(date +%s) + 90 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  unhealthy=$(docker ps --filter "label=com.docker.compose.project=acb" --format '{{.Names}}\t{{.Status}}' \
    | awk '$0 ~ /unhealthy|starting/ {print $1}')
  if [ -z "$unhealthy" ]; then break; fi
  sleep 3
done
docker ps --filter "label=com.docker.compose.project=acb" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo "==> Applying database migrations (02+ — init only mounts 00/01)"
# `< /dev/null` is LOAD-BEARING, not tidiness.
#
# This whole file is delivered as `ssh 'bash -s' < vps_apply.sh`, so the script
# IS stdin. `apply_migrations.sh` calls `docker exec -i`, which attaches and
# DRAINS stdin — swallowing every line of this script that has not been read
# yet. Bash then reaches EOF and exits 0, so the deploy reports success having
# silently skipped the gateway restart and the workbench rebuild below.
#
# It bit on 2026-08-07 and it bit invisibly: six consecutive deploys went green
# while the box kept serving an old bundle, because `verify()` health-checks the
# STILL-RUNNING previous deployment and cannot tell it apart from a new one.
# The trigger was a new ledger query (`-c "SELECT filename ..."`) — before it,
# every psql call piped its own input and stdin was never touched.
#
# External-database seam: a box whose Postgres is managed elsewhere (e.g.
# Supabase) sets PG_MODE=local — plus PGHOST/PGPORT/PGUSER/PGPASSWORD/
# PGSSLMODE for libpq, and deliberately SKIP_PRE_MIGRATION_BACKUP=1, because
# the local dump path needs superuser and the provider's PITR replaces it
# (docs/EXTERNAL_POSTGRES.md). Those keys live in .env, not this shell's
# environment, so lift them across before the runner starts.
# PGUSER is deliberately NOT lifted: the runner passes -U "$PG_USER" computed
# from POSTGRES_USER in .env, and an explicit -U outranks PGUSER anyway — set
# POSTGRES_USER/POSTGRES_DB to the managed values instead.
for _k in PG_MODE PGHOST PGPORT PGPASSWORD PGSSLMODE SKIP_PRE_MIGRATION_BACKUP; do
  _v="$(grep -E "^${_k}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2-)"
  if [ -n "$_v" ]; then export "${_k}=${_v}"; fi
done
APP_DIR="$APP_DIR" bash scripts/apply_migrations.sh < /dev/null

echo "==> Applying the Customer Console ladder (D47 · H-24)"
# ⚠️ This ran NOWHERE until 2026-08-27, and that is the board's own
# "platform_api is on the box but inert". `infra/customer_console/` is the
# Console's ladder against the Console's OWN Supabase project (D34); the applier
# above is bolted to the local docker Postgres and cannot reach it. So the
# Console got a dedicated DSN-driven applier and nothing ever invoked it: the
# deploy shipped Console CODE expecting a schema its database did not have, and
# reported success. CP-12 made it visible — migration 009 creates the `operator`
# tables and, unapplied, `GET /operators` answers 500 rather than 404 (H-64).
#
# R6 puts this BEFORE the Console restart further down, so old code never meets
# new schema. `< /dev/null` is the same load-bearing redirect as the tenant call
# above, for the identical reason: this whole file is delivered ON STDIN.
#
# ⚠️ FAIL-CLOSED, and the exact condition is deliberate. H-24 says "fail the
# deploy when its DSN is unset rather than skipping". Read literally that would
# brick every TENANT deploy on a box that runs no Console at all, so the test is
# provisioned-and-misconfigured rather than merely unset:
#   • a DSN is present         -> apply the ladder.
#   • no DSN, unit ENABLED     -> the Console is live and misconfigured. FAIL.
#   • no DSN, unit not enabled -> not provisioned here. Say so LOUDLY, continue.
# The thing H-24 closes is the SILENT skip. A loud, reasoned skip is not one.
#
# ⚠️ A FOURTH CASE, added 2026-09-03 (H-100). The three above ask only whether
# the DSN is PRESENT. A DSN that is present and DEAD fell into the first one,
# ran the ladder, and `psql` failed under `set -e` — which took the rest of this
# file with it. The workbench rebuild is ~360 lines BELOW here, so the box kept
# serving old code from a stack where all four units read `active`.
#
# Measured 2026-09-02: the owner deleted the Console Supabase project during the
# Mumbai migration and the console `.env` still named it. Deploys failed for
# hours. `vps-health.yml` probed the public URLs and the app answered on OLD
# code, so every signal stayed green. Only `acb-pull.service` knew, and nothing
# read it.
#
#   • DSN present but UNREACHABLE, unit ENABLED     -> FAIL, and NAME the cause.
#   • DSN present but UNREACHABLE, unit not enabled -> skip LOUDLY, continue.
#
# The second half is the part that matters most. A dead Console DSN on a box
# that does not run the Console must not take the tenant deploy down with it.
# That is the whole failure this case exists to stop.
CC_ENV="$APP_DIR/apps/services/customer_console/.env"
CC_DSN="$(grep -E '^CUSTOMER_CONSOLE_DATABASE_URL=' "$CC_ENV" 2>/dev/null | tail -1 | cut -d= -f2-)"
# systemd's EnvironmentFile accepts quoted values; psql would take the quotes
# literally and try to resolve them as a hostname.
CC_DSN="${CC_DSN%\"}"; CC_DSN="${CC_DSN#\"}"
CC_DSN="${CC_DSN%\'}"; CC_DSN="${CC_DSN#\'}"
# Reachability probe. `psql` wants a libpq URL and the service DSN carries a
# SQLAlchemy driver suffix, so strip it the same way
# `apply_customer_console_migrations.sh` does.
#
# ⚠️ NEVER let this command's stderr reach a log. `psql` quotes the WHOLE
# connection string, password included, when it cannot connect — that is how a
# tenant credential reached a transcript on 2026-09-02 (H-97). Exit code only.
cc_reachable() {
  local dsn="${CC_DSN/+psycopg2/}"
  dsn="${dsn/+psycopg/}"
  command -v psql >/dev/null 2>&1 || return 0   # cannot probe -> do not block
  PGCONNECT_TIMEOUT=10 psql "$dsn" -tAc 'select 1' >/dev/null 2>&1
}

if [ -n "$CC_DSN" ] && ! cc_reachable; then
  if systemctl is-enabled --quiet acb-customer-console 2>/dev/null; then
    echo "    !! The Console DSN in $CC_ENV is PRESENT but UNREACHABLE, and"
    echo "       acb-customer-console is ENABLED. The database it names is gone,"
    echo "       renamed, or refusing this credential."
    echo "       Refusing to continue: the ladder cannot run, so the service"
    echo "       would serve new code against an unmigrated schema."
    echo "       Fix CUSTOMER_CONSOLE_DATABASE_URL on the box, then redeploy."
    exit 1
  fi
  echo "    !! The Console DSN in $CC_ENV is PRESENT but UNREACHABLE."
  echo "       acb-customer-console is NOT enabled here, so this box does not"
  echo "       serve the Console -> ladder SKIPPED, deploy CONTINUES."
  echo "       ⚠️ Still a defect: the .env names a database that does not answer."
elif [ -n "$CC_DSN" ]; then
  CUSTOMER_CONSOLE_DATABASE_URL="$CC_DSN" \
    bash scripts/apply_customer_console_migrations.sh < /dev/null
elif systemctl is-enabled --quiet acb-customer-console 2>/dev/null; then
  echo "    !! acb-customer-console is ENABLED, but $CC_ENV carries no"
  echo "       CUSTOMER_CONSOLE_DATABASE_URL. The service would serve new code"
  echo "       against an unmigrated schema. Refusing to continue."
  exit 1
else
  echo "    no Console DSN and acb-customer-console is not enabled here"
  echo "    -> Console ladder SKIPPED (this box does not run the Console)"
fi

echo "==> Syncing Python deps"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 🔴 **TWO DELIVERY PATHS, TWO UIDs, ONE VENV.** This aborted every push-path
# deploy from 2026-08-26 to 2026-08-29, and it aborted them RIGHT HERE — before
# the workbench build and before the Operator Console block. So the box took
# each new checkout and none of the new builds, which is why `git log` on the
# box read current while every compiled surface stayed days behind.
#
# The header of this file says "one file, so the two paths cannot drift". The
# FILE does not drift. The UID does:
#
#   pull path   acb-pull.service runs `User=root`  → writes root-owned files
#   push path   deploy.yml SSHes as the app user   → cannot remove them
#
# Measured 2026-08-29: 36 files of 9883 under `.venv` were `root:root` in an
# otherwise `acb:acb` tree, and `uv sync` stopped on the first one it met:
#
#   error: failed to remove file `…/sherpa_onnx-1.13.6.dist-info/INSTALLER`:
#          Permission denied (os error 13)
#
# `set -e` (line 42) then took the remaining 480 lines with it. The job polled
# 24 times x 3 rounds for a SHA no process was working toward any more, and
# reported the box unreachable — so for three days this read as a network or
# health fault, which is where the diagnosis kept going.
#
# ⚠️ **Normalise AFTER the sync, and only as root.** Root can always remove
# root's files, so the root path never fails — it only leaves the mess that
# makes the NEXT app-user deploy fail. Deriving the owner from $APP_DIR rather
# than naming `acb` keeps this correct on a box that installs somewhere else.
#
# 🔴 **REPAIR BEFORE, NORMALISE AFTER — and the BEFORE half is the one that
# matters.** The first version of this fix (PR #155) only chowned as root,
# after the sync, and it never once ran. The reason is a race nobody had to
# lose deliberately:
#
#   1. a merge moves `release`;
#   2. the PUSH path (app user) reaches the box first and checks out the SHA;
#   3. it dies here at `uv sync`, because the root files are still there;
#   4. `acb-pull` wakes, compares SHAs, says "already current" — and never
#      applies. So ROOT NEVER GETS A TURN, and the repair never executes.
#
# Measured 2026-08-29: the box sat at 16f5dccd with the chown present at line
# 254 of its own checkout, 39 root-owned files under `.venv`, and `/providers`
# still 404. The fix was on the box and could not reach itself.
#
# So repair up front, with `sudo`, whoever is running. The app user already
# holds passwordless sudo — this script uses `sudo systemctl` throughout — and
# without it the app-user path has no way out of a hole only root can dig it
# out of.
VENV_OWNER="$(stat -c '%U:%G' "$APP_DIR")"
if [ -d "$APP_DIR/.venv" ]; then
  if sudo chown -R "$VENV_OWNER" "$APP_DIR/.venv" 2>/dev/null; then
    echo "    venv ownership repaired to $VENV_OWNER before sync"
  else
    echo "    WARNING: could not chown $APP_DIR/.venv — uv sync may fail on"
    echo "             files this user cannot remove (see PR #155/#156)."
  fi
fi
uv sync
if [ "$(id -u)" = "0" ] && [ -d "$APP_DIR/.venv" ]; then
  chown -R "$VENV_OWNER" "$APP_DIR/.venv"
  echo "    venv normalised to $VENV_OWNER — root ran this apply"
fi

# ── The Copilot CLI, fetched BEFORE any service starts (H-181) ────────────
# 🔴 github-copilot-sdk 1.x does NOT ship the CLI in the wheel. Without this
# step the FIRST CopilotClient() downloads ~150 MB with a blocking urlopen
# inside the gateway's event loop, so /health and every request stall, and an
# unreachable GitHub fails every Copilot agent. It is cached per version under
# the service user's ~/.cache, so a second deploy is a no-op.
# ⚠️ As the SERVICE user, because the cache lives in that user's home. And it
# FAILS the deploy, because a box with no CLI is broken, not degraded.
echo "==> Fetching the Copilot CLI for the installed SDK"
SVC_USER="${VENV_OWNER%%:*}"
if [ "$(id -un)" = "$SVC_USER" ]; then
  uv run --no-sync python -m copilot download-runtime
else
  sudo -u "$SVC_USER" -H bash -c "cd '$APP_DIR' && '$(command -v uv)' run --no-sync python -m copilot download-runtime"
fi

# ── [TRIAL] Free local diarization (sherpa-onnx) ──────────────────
# Adds free CPU-only speaker separation for Whisper transcripts.
# Fully reversible: set LOCAL_DIAR=0 below (or NOTES_LOCAL_DIARIZATION=0
# in .env) + redeploy to fall back to the Deepgram/Whisper path. The
# app is fail-safe — if any of this is missing it silently no-ops.
LOCAL_DIAR="1"
MODELS_DIR="$APP_DIR/models/sherpa"
SEG_MODEL="$MODELS_DIR/segmentation.onnx"
EMB_MODEL="$MODELS_DIR/embedding.onnx"
upsert_env() {  # key value — set-or-replace in $ENV_FILE (paths ok)
  if grep -qE "^$1=" "$ENV_FILE" 2>/dev/null; then
    env_edit_in_place "$ENV_FILE" sed "s|^$1=.*|$1=$2|"
  else
    echo "$1=$2" >> "$ENV_FILE"
  fi
}
echo "==> [trial] Local diarization (sherpa-onnx), LOCAL_DIAR=$LOCAL_DIAR"
if [ "$LOCAL_DIAR" = "1" ]; then
  mkdir -p "$MODELS_DIR"
  # ffmpeg decodes the meeting audio → 16kHz PCM for sherpa. The VPS
  # never needed it before (cloud STT decodes server-side), so install
  # it on demand; without it the diarization pass silently no-ops.
  if ! command -v ffmpeg >/dev/null; then
    echo "    installing ffmpeg (needed to decode audio for diarization)…"
    (sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg) >/dev/null 2>&1 \
      && echo "    + ffmpeg installed" || echo "    ! ffmpeg install failed — decode will no-op"
  fi
  uv pip install -q 'sherpa-onnx>=1.10' 'numpy>=1.24' \
    || echo "    ! sherpa-onnx install failed — local diar will no-op"
  if [ ! -f "$SEG_MODEL" ]; then
    curl -fsSL -o /tmp/seg.tar.bz2 "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2" \
      && tar xjf /tmp/seg.tar.bz2 -C /tmp \
      && cp /tmp/sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx "$SEG_MODEL" \
      && echo "    + segmentation model (int8, ~6MB)" || echo "    ! seg download failed"
  fi
  if [ ! -f "$EMB_MODEL" ]; then
    curl -fsSL -o "$EMB_MODEL" "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx" \
      && echo "    + speaker-embedding model (CAM++, ~27MB)" || echo "    ! emb download failed"
  fi
  if [ -f "$SEG_MODEL" ] && [ -f "$EMB_MODEL" ]; then
    upsert_env NOTES_LOCAL_DIARIZATION 1
    upsert_env SHERPA_SEG_MODEL "$SEG_MODEL"
    upsert_env SHERPA_EMB_MODEL "$EMB_MODEL"
    grep -qE "^SHERPA_DIAR_THRESHOLD=" "$ENV_FILE" || echo "SHERPA_DIAR_THRESHOLD=0.7" >> "$ENV_FILE"
    echo "    local diarization ENABLED (Whisper transcripts get free speakers)"
  else
    upsert_env NOTES_LOCAL_DIARIZATION 0
    echo "    models missing — left OFF; Deepgram/Whisper path unaffected"
  fi
else
  upsert_env NOTES_LOCAL_DIARIZATION 0
  echo "    LOCAL_DIAR=0 — local diarization OFF"
fi

# ── WS-49 BH-7: the .env strip and the systemd drop-ins ─────────────────────
# Spec: project-docs/specs/box_hardening.md §5 BH-7 (B7-2, B7-3, Q3c, Q3e).
#
# 🔴 **THIS RUNS BEFORE THE FIRST SERVICE RESTART OF THE APPLY.** That is the
# WhatsApp bridge restart below, and then the gateway restart. A drop-in that
# goes in after a restart applies only at the NEXT restart, so the BH-2 strict
# check would read stale properties. The BO-23 unit loop near the end stays
# where it is, after the restarts, on purpose.
#
# 1. strip_t2_vendor_env_line removes a CUSTOM_APPS_T2_VENDOR_DIR line from
#    .env, and warns. It never refuses, because the gateway writes .env, and a
#    refusal would let the gateway stop every deploy. An EnvironmentFile line
#    wins over the Environment line of 40-agent-site.conf, so the line must go.
# 2. install_dropins installs each deploy/hostinger/*.service.d/*.conf, then
#    runs daemon-reload. It writes only the names that the repo holds, and it
#    deletes nothing. It never writes a 90-* name: that is the BH-2 rollback
#    (scripts/bh2_rollback.sh), and a deploy must keep a rollback in place.
# 3. restart_stale_dropin_units (near the end) restarts ONCE each active unit
#    that started before its newest installed drop-in. A unit that a step of
#    this apply restarted after the install is current, and gets no second
#    restart. The test reads the box, not this apply, so an apply that died
#    after the install is repaired by the next one.
#
# tests/unit/test_agent_deps_target.py and tests/unit/test_unit_hardening.py
# source the block between the two marker lines below. Keep it free of side
# effects: definitions and defaults only.
# >>> bh7 helpers
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"

strip_t2_vendor_env_line() {  # <env file>
  local f="$1" pat='^[[:space:]]*(export[[:space:]]+)?CUSTOM_APPS_T2_VENDOR_DIR[[:space:]]*='
  [ -f "$f" ] || return 0
  grep -qE "$pat" "$f" || return 0
  # A write INTO the file keeps its inode, mode and owner (env helpers above,
  # WS-49 BH-2). `sed -i` would replace the inode. No value is printed.
  env_edit_in_place "$f" sed -E "/$pat/d"
  echo "    WARN BH-7: removed a CUSTOM_APPS_T2_VENDOR_DIR line from $f."
  echo "    WARN BH-7: the T2 vendor dir is /opt/acb/t2-vendor, from 40-agent-site.conf."
}

install_dropins() {  # <dir that holds the *.service.d dirs>
  local src="$1" d unit_d conf name dest
  for d in "$src"/*.service.d; do
    [ -d "$d" ] || continue
    unit_d="$(basename "$d")"
    for conf in "$d"/*.conf; do
      [ -f "$conf" ] || continue
      name="$(basename "$conf")"
      case "$name" in
        90-*)
          echo "    !! skipped $unit_d/$name: a 90-* name is a rollback on the box, never a repo file"
          continue ;;
      esac
      dest="$SYSTEMD_UNIT_DIR/$unit_d/$name"
      if sudo cmp -s "$conf" "$dest"; then
        continue
      fi
      sudo install -d -m 0755 "$SYSTEMD_UNIT_DIR/$unit_d"
      sudo install -m 0644 "$conf" "$dest"
      echo "    installed $unit_d/$name"
    done
  done
  sudo systemctl daemon-reload
}

restart_stale_dropin_units() {  # <dir that holds the *.service.d dirs>
  local src="$1" d unit_d unit f m newest since started
  for d in "$src"/*.service.d; do
    [ -d "$d" ] || continue
    unit_d="$(basename "$d")"
    unit="${unit_d%.d}"
    newest=0
    for f in "$SYSTEMD_UNIT_DIR/$unit_d"/*.conf; do
      [ -f "$f" ] || continue
      m="$(stat -c %Y "$f" 2>/dev/null || echo 0)"
      if [ "$m" -gt "$newest" ]; then newest="$m"; fi
    done
    [ "$newest" -gt 0 ] || continue
    if ! systemctl is-active --quiet "$unit"; then
      echo "    $unit is not active: its drop-ins apply at its next start"
      continue
    fi
    # --timestamp=unix gives "@<epoch>". A zone name (AEST) is not something
    # `date -d` can parse, and a failed parse would restart the unit on every
    # deploy (fix round 1, B). A value that is not an epoch counts as 0.
    since="$(systemctl show "$unit" -p ActiveEnterTimestamp --timestamp=unix --value 2>/dev/null || true)"
    started="${since#@}"
    case "$started" in ''|*[!0-9]*) started=0 ;; esac
    if [ "$started" -ge "$newest" ]; then
      echo "    $unit started after its newest drop-in: no restart"
      continue
    fi
    echo "    restarting $unit once, so that its new drop-in applies"
    sudo systemctl restart "$unit" || echo "    !! could not restart $unit — check: systemctl status $unit"
  done
}
# <<< bh7 helpers

echo "==> WS-49 BH-7: the .env strip and the systemd drop-ins"
strip_t2_vendor_env_line "$ENV_FILE"
install_dropins "$APP_DIR/deploy/hostinger"
echo "    drop-ins installed and systemd reloaded"

# ── WS-49 BH-2: the gateway sandbox, its write list and its strict check ────
# Spec: project-docs/specs/box_hardening.md §5 BH-2 items 2, 5 and 6.
#
# deploy/hostinger/acb-gateway.service.d/50-hardening.conf went in with the
# drop-ins above, so the gateway restart below runs the sandbox.
#
# 1. compile_bytecode and ensure_gateway_rw_paths run before that restart. A
#    path on ReadWritePaths with no "-" must exist, or systemd cannot set up
#    the sandbox and the gateway does not start. The step makes each such
#    path but .env, and the home dirs of the "-" entries. A missing .env
#    stops the deploy HERE, before the restart, so the gateway that runs now
#    keeps serving.
# 2. bh2_strict_check runs near the end, after the last restart and before
#    the marker. It passes ONLY when acb-gateway is active, with
#    NoNewPrivileges=yes and ProtectSystem=strict. A valid rollback
#    (scripts/bh2_rollback.sh status = 0) passes too, and prints the WARN.
#    Anything else fails the deploy, and no marker is written.
#
# tests/unit/test_bh2_strict_check.py sources the block between the two
# marker lines below. Keep it free of side effects: definitions and defaults
# only.
# >>> bh2 helpers
BH2_UNIT="${BH2_UNIT:-acb-gateway}"
BH2_ROLLBACK_SCRIPT="${BH2_ROLLBACK_SCRIPT:-$APP_DIR/scripts/bh2_rollback.sh}"
# The home of the service user, as 50-hardening.conf names it.
BH2_HOME="${BH2_HOME:-/home/acb}"

# Each path on the ReadWritePaths of 50-hardening.conf, before the restart.
# - .env is the one path the deploy cannot make: it holds the secrets. A
#   missing .env stops the deploy, and the gateway that runs keeps serving.
# - data/ and the two JSON files are made when absent. Git tracks the JSON
#   files today, but a later commit that drops one must not stop every deploy.
#   agents.json holds a list, and the models cache holds an object.
# - The "-" home dirs are made as the service user, so they bind on a fresh
#   box. ~/.cache/github-copilot-sdk comes from the Copilot CLI fetch above.
ensure_gateway_rw_paths() {  # <app dir> [<home of the service user>]
  local app="$1" home="${2:-$BH2_HOME}" owner user f body d
  owner="$(stat -c '%U:%G' "$app")"
  user="${owner%%:*}"
  if [ ! -f "$app/.env" ]; then
    echo "    !! $app/.env is missing. It is on the ReadWritePaths of 50-hardening.conf,"
    echo "       so the sandboxed gateway cannot start without it."
    return 1
  fi
  if [ ! -d "$app/data" ]; then
    mkdir -p "$app/data"
    if [ "$(id -u)" = "0" ]; then chown "$owner" "$app/data"; fi
    echo "    made $app/data, a ReadWritePaths dir of the gateway"
  fi
  for f in "$app/infra/provider_models_cache.json" "$app/apps/services/gateway/agents.json"; do
    [ -f "$f" ] && continue
    case "$f" in */agents.json) body='[]' ;; *) body='{}' ;; esac
    mkdir -p "$(dirname "$f")"
    printf '%s\n' "$body" > "$f"
    if [ "$(id -u)" = "0" ]; then chown "$owner" "$f"; fi
    echo "    made $f as $body: it is on the ReadWritePaths of the gateway"
  done
  for d in "$home/.acb/agents" "$home/.copilot" "$home/.cache/copilot"; do
    [ -d "$d" ] && continue
    if [ "$(id -un)" = "$user" ]; then
      mkdir -p "$d"
    else
      sudo -u "$user" mkdir -p "$d"
    fi
    echo "    made $d as $user, so its ReadWritePaths entry binds"
  done
}

# The gateway cannot write __pycache__ under .venv, apps/ or packages/ in
# the sandbox. Python then skips the write with no error, and compiles each
# changed module again at every start. So the deploy compiles them, as the
# owner of the checkout, before the restart. Best-effort: a file that does
# not compile is logged, and the deploy goes on. The timestamp mode is right
# here, because `git reset` and `uv sync` give a changed file a new mtime.
compile_bytecode() {  # <app dir>
  local app="$1" owner py rc=0
  owner="$(stat -c '%U' "$app")"
  py="${BH2_PYTHON:-$app/.venv/bin/python}"
  if [ ! -x "$py" ]; then
    echo "    ! no $py, so no bytecode was compiled (non-fatal)"
    return 0
  fi
  local -a cmd=("$py" -m compileall -q -j 0 --invalidation-mode timestamp
                -x '[/\\](node_modules|\.next|\.git)[/\\]' "$app/.venv/lib" "$app/apps" "$app/packages")
  if [ "$(id -un)" = "$owner" ]; then
    "${cmd[@]}" >/dev/null 2>&1 || rc=$?
  else
    sudo -u "$owner" -H "${cmd[@]}" >/dev/null 2>&1 || rc=$?
  fi
  if [ "$rc" = "0" ]; then
    echo "    compiled the bytecode of .venv, apps/ and packages/"
  else
    echo "    ! compileall exited $rc: a file did not compile (non-fatal). Python compiles it at start."
  fi
  return 0
}

# On a failure it sets BH2_FAIL_WHY to the reason, for the deploy's last line.
bh2_strict_check() {
  local active nnp psys rb_out rb_rc=0
  BH2_FAIL_WHY=""
  active="$(systemctl show "$BH2_UNIT" -p ActiveState --value 2>/dev/null || true)"
  nnp="$(systemctl show "$BH2_UNIT" -p NoNewPrivileges --value 2>/dev/null || true)"
  psys="$(systemctl show "$BH2_UNIT" -p ProtectSystem --value 2>/dev/null || true)"
  # status reads the root-only ack with sudo, and takes no deploy lock.
  rb_out="$(bash "$BH2_ROLLBACK_SCRIPT" status 2>&1)" || rb_rc=$?
  if [ "$rb_rc" = "0" ]; then
    printf '%s\n' "$rb_out" | sed 's/^/    /'
  fi
  if [ "$active" != "active" ]; then
    echo "    !! BH-2 strict check: $BH2_UNIT is '${active:-unknown}', not active."
    BH2_FAIL_WHY="$BH2_UNIT is not active ('${active:-unknown}'). A rollback does not pass an inactive gateway"
    return 1
  fi
  if [ "$nnp" = "yes" ] && [ "$psys" = "strict" ]; then
    echo "    BH-2 strict check: $BH2_UNIT is active, NoNewPrivileges=yes, ProtectSystem=strict"
    return 0
  fi
  if [ "$rb_rc" = "0" ]; then
    echo "    BH-2 strict check: the sandbox is off (NoNewPrivileges=${nnp:-?}, ProtectSystem=${psys:-?}),"
    echo "    and a valid rollback holds it off. Fix the cause, then run bh2_rollback.sh off."
    return 0
  fi
  echo "    !! BH-2 strict check: NoNewPrivileges=${nnp:-?}, ProtectSystem=${psys:-?},"
  echo "       and no valid rollback (bh2_rollback.sh status gave $rb_rc: $(printf '%s\n' "$rb_out" | head -n 1))."
  BH2_FAIL_WHY="$BH2_UNIT is not sandboxed, and no valid rollback is on"
  return 1
}
# <<< bh2 helpers

# ── WhatsApp bridge (whatsmeow, personal-number QR) ───────────────
# A localhost-only Go service that links a PERSONAL number by QR and
# streams messages to the gateway's /whatsapp/bridge/ingest (same
# triage brain as a Cloud API number). Env is added BEFORE the gateway
# restart below so the gateway picks up WHATSAPP_BRIDGE_URL/SECRET.
# Opt out any time with WHATSAPP_BRIDGE_ENABLED=0 in .env. Fail-safe:
# a build/start hiccup only skips the bridge, never the whole deploy.
# NOTE: unofficial multi-device is outside WhatsApp's ToS — the number
# can be banned; idle is harmless, risk begins when a number is paired.
echo "==> WhatsApp bridge (whatsmeow, personal-number QR)"
grep -qE '^WHATSAPP_BRIDGE_ENABLED=' "$ENV_FILE" || echo "WHATSAPP_BRIDGE_ENABLED=1" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_URL='         "$ENV_FILE" || echo "WHATSAPP_BRIDGE_URL=http://localhost:8790" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_GATEWAY_URL=' "$ENV_FILE" || echo "WHATSAPP_BRIDGE_GATEWAY_URL=http://localhost:8080" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_ADDR='        "$ENV_FILE" || echo "WHATSAPP_BRIDGE_ADDR=127.0.0.1:8790" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_STORE='       "$ENV_FILE" || echo "WHATSAPP_BRIDGE_STORE=/opt/acb/data/whatsapp_bridge/bridge-store.db" >> "$ENV_FILE"
# Voice calls: recorded audio lands beside the session store and is
# swept after RETENTION_DAYS (recording runs ~115 MB per call-hour,
# so unbounded would fill the VPS). Blank RECORD_DIR = no recording.
grep -qE '^WHATSAPP_BRIDGE_CALL_RECORD_DIR='    "$ENV_FILE" || echo "WHATSAPP_BRIDGE_CALL_RECORD_DIR=/opt/acb/data/whatsapp_bridge/call-recordings" >> "$ENV_FILE"
grep -qE '^WHATSAPP_BRIDGE_CALL_RETENTION_DAYS=' "$ENV_FILE" || echo "WHATSAPP_BRIDGE_CALL_RETENTION_DAYS=7" >> "$ENV_FILE"
# Generate a strong shared secret once (used by BOTH gateway + bridge).
if ! grep -qE '^WHATSAPP_BRIDGE_SECRET=.+' "$ENV_FILE"; then
  _wbsecret="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  env_edit_in_place "$ENV_FILE" grep -vE '^WHATSAPP_BRIDGE_SECRET='
  echo "WHATSAPP_BRIDGE_SECRET=$_wbsecret" >> "$ENV_FILE"
  echo "    + generated WHATSAPP_BRIDGE_SECRET"
fi

WB_ENABLED="$(grep -E '^WHATSAPP_BRIDGE_ENABLED=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
if [ "$WB_ENABLED" = "1" ]; then
  mkdir -p /opt/acb/data/whatsapp_bridge
  # Ensure a Go >=1.24 toolchain (pure-Go / CGO-free build). Install to
  # /usr/local/go on demand; cached across deploys.
  GO=""
  if command -v go >/dev/null 2>&1 && go version 2>/dev/null | grep -qE 'go1\.(2[4-9]|[3-9][0-9])'; then
    GO="$(command -v go)"
  elif [ -x /usr/local/go/bin/go ] && /usr/local/go/bin/go version | grep -qE 'go1\.(2[4-9]|[3-9][0-9])'; then
    GO="/usr/local/go/bin/go"
  else
    echo "    installing Go 1.24.7…"
    if curl -fsSL -o /tmp/go.tgz https://go.dev/dl/go1.24.7.linux-amd64.tar.gz; then
      sudo rm -rf /usr/local/go && sudo tar -C /usr/local -xzf /tmp/go.tgz && GO="/usr/local/go/bin/go" && echo "    + Go installed"
    fi
  fi
  if [ -n "$GO" ] && ( cd "$APP_DIR/apps/services/whatsapp_bridge" && GOFLAGS=-mod=mod CGO_ENABLED=0 "$GO" build -o whatsapp_bridge . ); then
    sudo cp "$APP_DIR/deploy/hostinger/acb-whatsapp-bridge.service" /etc/systemd/system/acb-whatsapp-bridge.service || true
    sudo systemctl daemon-reload || true
    sudo systemctl enable acb-whatsapp-bridge >/dev/null 2>&1 || true
    sudo systemctl restart acb-whatsapp-bridge || true
    sleep 2
    if systemctl is-active --quiet acb-whatsapp-bridge; then
      echo "    WhatsApp bridge is active (localhost:8790)"
    else
      echo "    ! WhatsApp bridge not active (non-fatal) — journalctl -u acb-whatsapp-bridge"
    fi
  else
    echo "    ! bridge build/toolchain unavailable (non-fatal) — personal-number linking stays offline"
  fi
else
  echo "    WHATSAPP_BRIDGE_ENABLED != 1 — ensuring bridge is stopped"
  sudo systemctl disable --now acb-whatsapp-bridge >/dev/null 2>&1 || true
fi

# ── Self-hosted meeting bot (Note Taker §3.13) ────────────────────
# A headless-Chrome participant that joins a Meet link, records the
# call, and feeds the normal transcribe → diarize → notes pipeline.
# Runs as a Docker service (profile "meetingbot") because each in-call
# bot is a real Chrome (~1-3 GB RAM + up to 2 CPU) — it needs a box
# with headroom, which is why this is opt-out-able rather than core.
# Env is written BEFORE the gateway restart so the gateway picks up
# MEETING_BOT_URL/TOKEN. Fail-safe: a build or start hiccup skips the
# bot only — the rest of the deploy (and the whole app) is unaffected.
echo "==> Meeting bot (self-hosted, headless Chrome)"
grep -qE '^MEETING_BOT_ENABLED=' "$ENV_FILE" || echo "MEETING_BOT_ENABLED=1" >> "$ENV_FILE"
# The gateway talks to the worker over the host-published port; the
# worker calls back to the gateway from inside its container.
upsert_env MEETING_BOT_URL "http://127.0.0.1:8095"
upsert_env NOTES_BOT_PROVIDER "selfhosted"
upsert_env NOTES_LIVE_CALLBACK_BASE "http://host.docker.internal:8080"
# Where the worker fetches streaming-ASR credentials. Without this
# the bot records fine but produces no live captions — the failure
# mode is silent, so it is wired by default rather than left opt-in.
upsert_env NOTES_LIVE_TOKEN_URL \
  "http://host.docker.internal:8080/notes/stt/bot-live-token"
# Persistent Chrome profile for the bot. Meet AUTO-DECLINES
# anonymous participants (they never get to knock) whenever the
# host isn't in the call yet or link-guests can't ask in — a
# signed-in profile is the only unattended fix. The dir is inside
# the container (its own volume); sign in once via
# POST /google-login. Defaults preserved on re-deploy.
grep -qE '^MEET_PROFILE_DIR=' "$ENV_FILE" || \
  echo "MEET_PROFILE_DIR=/profile" >> "$ENV_FILE"
# Live VNC view of the bot's browser (loopback only). Default off.
grep -qE '^MEET_VNC=' "$ENV_FILE" || echo "MEET_VNC=0" >> "$ENV_FILE"
# One shared secret, both directions (gateway -> worker, and the
# worker's live-segment callback). Generated once, then reused.
if ! grep -qE '^MEETING_BOT_TOKEN=.+' "$ENV_FILE"; then
  _mbtoken="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  env_edit_in_place "$ENV_FILE" grep -vE '^MEETING_BOT_TOKEN='
  echo "MEETING_BOT_TOKEN=$_mbtoken" >> "$ENV_FILE"
  echo "    + generated MEETING_BOT_TOKEN"
fi

MB_ENABLED="$(grep -E '^MEETING_BOT_ENABLED=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
# Never recreate the worker while a bot is in a live call — each
# bot is a Chrome inside the container, so `up --build` mid-meeting
# kills the notetaker someone is relying on (this happened). The
# skipped rebuild simply lands on the next deploy.
MB_ACTIVE="$(curl -fsS --max-time 5 http://127.0.0.1:8095/health 2>/dev/null \
  | grep -o '"active":[0-9]*' | cut -d: -f2 || true)"
if [ "$MB_ENABLED" = "1" ] && [ -n "$MB_ACTIVE" ] && [ "$MB_ACTIVE" -gt 0 ] 2>/dev/null; then
  echo "    ~ $MB_ACTIVE bot(s) in a call right now — keeping the running worker; rebuild deferred to the next deploy"
elif [ "$MB_ENABLED" = "1" ]; then
  # --build is cheap after the first run (layer cache); the first
  # deploy pulls the Playwright base image, which is large.
  # --env-file is explicit on purpose: compose resolves a bare .env
  # against the project directory (infra/), not the app root.
  #
  # `timeout` and </dev/null are both scar tissue: an apt postinst
  # that prompts (tzdata asking "Geographic area:") hung this build
  # for 15 minutes until the deploy's SSH session died, and the
  # deploy then reported success from gateway health while the OLD
  # bot image stayed live. Bounded + no tty means a prompt fails
  # fast instead of eating the deploy.
  MB_IMAGE_BEFORE="$(docker images -q acb-meeting-bot:latest 2>/dev/null || true)"
  if timeout 900 docker compose --env-file "$ENV_FILE" \
       -f infra/docker-compose.yml --profile meetingbot \
       up -d --build meeting-bot </dev/null 2>&1 | tail -5; then
    sleep 5
    if curl -fsS --max-time 10 http://127.0.0.1:8095/health >/dev/null 2>&1; then
      MB_IMAGE_AFTER="$(docker images -q acb-meeting-bot:latest 2>/dev/null || true)"
      if [ -n "$MB_IMAGE_BEFORE" ] && \
         [ "$MB_IMAGE_BEFORE" = "$MB_IMAGE_AFTER" ]; then
        echo "    meeting bot UP on 127.0.0.1:8095 (image unchanged — nothing to rebuild)"
      else
        echo "    meeting bot UP on 127.0.0.1:8095 (image rebuilt)"
      fi
    else
      echo "    !! meeting bot started but /health not answering — join-by-link is DOWN"
      docker logs --tail 20 acb-meeting-bot 2>&1 | sed 's/^/      /' || true
    fi
  else
    # Loud and specific: the old container is probably still serving,
    # which is exactly the state that reads as "deployed" but isn't.
    echo "    !! meeting bot build/start FAILED or timed out — join-by-link is running the PREVIOUS image"
    tail -20 /tmp/mb-build.log 2>/dev/null | sed 's/^/      /' || true
  fi
else
  echo "    MEETING_BOT_ENABLED=0 — meeting bot OFF (join-by-link unavailable)"
  docker compose --env-file "$ENV_FILE" -f infra/docker-compose.yml \
    --profile meetingbot rm -sf meeting-bot >/dev/null 2>&1 || true
fi

echo "==> Installing the Caddy config (H-60)"
# Caddy fronts every public hostname. If it is down, the whole app is
# unreachable, however healthy the services behind it are.
#
# 🟢 **THE REPO FILE IS THE CONFIG, AND IT GOES IN BEFORE THE RESTARTS.** The
# repo copy carries `lb_try_duration`, so a request that meets a restarting
# upstream waits for it instead of getting a 502. Installing it HERE, before
# the gateway restart, means this deploy's own restarts are covered too.
#
# 🔴 **A BAD FILE MUST NEVER REACH THE BOX.** The repo file is validated first.
# A file that does not validate stops the apply HERE, before any restart, and
# the live config is untouched. A file that validates but does not start is
# rolled back to the copy it replaced. Both end the deploy red.
#
# ⚠️ **RESTART ONLY WHEN THE FILE CHANGED.** The config has `admin off`, so
# `systemctl reload caddy` cannot work and a change needs a restart. Until
# 2026-09-28 this step ran `reload || restart` on EVERY deploy, and the reload
# always failed, so every deploy restarted Caddy and cut every open connection,
# chat streams included. Measured 2026-09-27 at 10:46:57, 10:55:43, 12:58:19.
#
# ⚠️ Until 2026-09-28 the repo file was STALE. It had no apex, no www and no
# operator host. The old step installed it only when the live file was invalid,
# and that would have taken three hostnames down. It now matches the box.
CADDY_LIVE=/etc/caddy/Caddyfile
CADDY_REPO="$APP_DIR/deploy/hostinger/caddy/Caddyfile"
CADDY_BAK=""
CADDY_CHANGED=0
echo "    caddy state: $(systemctl is-active caddy 2>&1 || true)"
if ! sudo caddy validate --config "$CADDY_REPO" --adapter caddyfile >/dev/null 2>&1; then
  echo "CADDY CONFIG INVALID: $CADDY_REPO does not validate."
  sudo caddy validate --config "$CADDY_REPO" --adapter caddyfile 2>&1 | tail -5 || true
  echo "    The live config is UNTOUCHED and the site keeps serving. No service"
  echo "    was restarted. Fix the repo file and deploy again."
  exit 1
fi
CADDY_RESTARTED=0
if sudo cmp -s "$CADDY_REPO" "$CADDY_LIVE"; then
  echo "    $CADDY_LIVE matches the repo — Caddy is NOT restarted"
else
  if sudo test -e "$CADDY_LIVE"; then
    # 🔴 No backup, no install. A new config with nothing to roll back to is
    # a bet the site cannot afford (H-60 review).
    CADDY_BAK="$CADDY_LIVE.bak.$(date +%s)"
    if ! sudo cp -a "$CADDY_LIVE" "$CADDY_BAK"; then
      echo "CADDY CONFIG NOT INSTALLED: could not back up $CADDY_LIVE to $CADDY_BAK."
      echo "    The live config is UNTOUCHED and no service was restarted."
      exit 1
    fi
    # Keep the last 5 backups this step made, newest first.
    # shellcheck disable=SC2012
    ls -1t "$CADDY_LIVE".bak.[0-9]* 2>/dev/null | tail -n +6 | while read -r old; do
      sudo rm -f -- "$old" || true
    done
  fi
  sudo install -m 0644 "$CADDY_REPO" "$CADDY_LIVE"
  CADDY_CHANGED=1
  echo "    installed the repo Caddyfile (the previous one is ${CADDY_BAK:-absent: there was none})"
fi
sudo systemctl enable caddy >/dev/null 2>&1 || true
if [ "$CADDY_CHANGED" = "1" ] || ! systemctl is-active --quiet caddy; then
  echo "    restarting Caddy (the config changed, or Caddy was down)"
  sudo systemctl restart caddy || true
  CADDY_RESTARTED=1
  sleep 2
fi
# `active` is not enough. After a restart, every site host must answer through
# Caddy. A host that does not answer rolls the config back (see
# caddy_probe_sites for what counts as an answer).
CADDY_OK=1
if ! systemctl is-active --quiet caddy; then
  echo "    !! Caddy is not active"
  CADDY_OK=0
elif [ "$CADDY_RESTARTED" = "1" ] && ! caddy_probe_sites "$CADDY_LIVE"; then
  CADDY_OK=0
fi
if [ "$CADDY_OK" != "1" ]; then
  sudo journalctl -u caddy --no-pager -n 40 || true
  if [ -n "$CADDY_BAK" ]; then
    echo "    !! the new Caddy config does not serve every host — restoring $CADDY_BAK"
    sudo install -m 0644 "$CADDY_BAK" "$CADDY_LIVE"
    sudo systemctl restart caddy || true
    sleep 2
    echo "    caddy state after the rollback: $(systemctl is-active caddy 2>&1 || true)"
  fi
  echo "CADDY FAILED TO START"
  exit 1
fi
echo "Caddy is active"

echo "==> Restarting gateway (systemd)"
# `restart` alone does NOT survive a reboot — without the enable
# symlink the box comes back with the gateway down until the next
# deploy. Install + enable the unit every time so boot is covered.
#
# 🟢 **THE WORKBENCH UNIT GOES IN HERE TOO, BEFORE THE GATEWAY RESTART (H-60,
# H-164).** It carried `Requires=acb-gateway.service`, so this restart ALSO
# restarted the workbench, BEFORE its install and build. Every Caddy 502 on
# :3001 from 2026-09-26 to 2026-09-27 is in that second. The unit now says
# `Wants=`, which keeps the start order and drops the restart. It must be on
# the box before this restart, or this deploy still takes the workbench down.
#
# The drop-ins (acb-gateway.service.d/40-agent-site.conf and the rest) went in
# at the BH-7 step above, before any restart, so this restart runs with them.
# That includes the BH-2 sandbox (50-hardening.conf). Its ReadWritePaths must
# exist first, or the gateway does not start (ensure_gateway_rw_paths).
echo "==> WS-49 BH-2: compile the Python bytecode (best-effort)"
compile_bytecode "$APP_DIR"
ensure_gateway_rw_paths "$APP_DIR" || {
  echo "GATEWAY NOT RESTARTED: .env is missing, and the sandboxed gateway cannot start without it."
  echo "    The gateway that runs now keeps serving. Restore .env, then deploy again."
  exit 1
}
sudo cp "$APP_DIR/deploy/hostinger/acb-gateway.service" /etc/systemd/system/acb-gateway.service
sudo cp "$APP_DIR/deploy/hostinger/acb-workbench.service" /etc/systemd/system/acb-workbench.service
sudo systemctl daemon-reload
sudo systemctl enable acb-gateway >/dev/null 2>&1 || true
sudo systemctl restart acb-gateway
sleep 3
systemctl is-active --quiet acb-gateway || { echo "GATEWAY FAILED TO START"; exit 1; }
# Wait until it ANSWERS. The build below does not need it, but the verify at
# the end of the deploy does, and so does every workbench request.
wait_ready "gateway" "http://127.0.0.1:8080/health" 120 \
  || { echo "GATEWAY FAILED TO START: active, but /health never answered"; exit 1; }
echo "Gateway is active"

echo "==> Restarting the Customer Console (systemd)"
# The unit FILE arrives via the BO-23 sync loop below, which deliberately does
# not restart services. That is correct for the loop and wrong for the Console:
# `git reset --hard` above moves files, it does not restart a running Python
# process, so without this step the Console serves whatever code it started with
# until somebody notices. Its ladder is already applied, so R6 holds.
#
# Conditional on the unit being enabled — the same "does this box run a Console"
# test as the ladder step. Failure is LOUD: a dead service behind a green deploy
# is the WS-25 failure mode this file carries the most scar tissue about.
if systemctl is-enabled --quiet acb-customer-console 2>/dev/null; then
  sudo systemctl restart acb-customer-console
  sleep 3
  if systemctl is-active --quiet acb-customer-console \
    && wait_ready "Customer Console" "http://127.0.0.1:8090/health" 60; then
    echo "    Customer Console is active (127.0.0.1:8090)"
  else
    echo "CUSTOMER CONSOLE FAILED TO START"
    sudo journalctl -u acb-customer-console --no-pager -n 40 || true
    exit 1
  fi
else
  echo "    acb-customer-console is not enabled here — skipping restart"
fi

# ── App Workshop T2 (React) build vendor cache ────────────────────
# Shared, pinned react/react-dom/esbuild/lucide-react the
# app-builder agent's build script
# (apps/agents/agent-app-builder/build/build_t2.mjs) resolves
# against via esbuild's nodePaths — installed ONCE here, never
# per-app.
# lucide-react: zero runtime deps of its own (only a react peer
# dep, already here) — pinned to the exact version
# workbench/control_plane itself uses, so every T2 app gets real
# icon components for free instead of hand-rolled SVGs.
#
# 🔴 WS-49 BH-7 (P5 variant 2). This step runs `npm install` as a user with
# sudo, so nothing the gateway can write may steer it:
#   • The dir is the constant /opt/acb/t2-vendor. This step reads NO path from
#     .env, because the gateway writes .env. The gateway unit gets the same
#     value from 40-agent-site.conf, and BH-2 leaves the dir read-only to it.
#   • `rm -f /opt/acb/t2-vendor/.npmrc` and `--userconfig /dev/null`: no
#     planted npm config applies.
#   • `--ignore-scripts`: no package script runs. esbuild still works without
#     its postinstall, because npm installs its platform binary
#     (@esbuild/linux-x64) as an optional dependency, not by a script.
# Fence: tests/unit/test_agent_deps_target.py (BH-F6).
echo "==> Provisioning T2 (React) vendor cache for the App Workshop builder"
T2_VENDOR_DIR="/opt/acb/t2-vendor"
if [ ! -d "$T2_VENDOR_DIR" ]; then
  sudo install -d -m 0755 -o "${VENV_OWNER%%:*}" -g "${VENV_OWNER##*:}" "$T2_VENDOR_DIR"
fi
# A planted .npmrc DIRECTORY makes `rm -f` fail. Under `set -e` that would
# let the gateway stop every deploy, so the second form removes it.
rm -f /opt/acb/t2-vendor/.npmrc 2>/dev/null || rm -rf /opt/acb/t2-vendor/.npmrc
printf '%s\n' \
  '{ "name": "cc-app-workshop-t2-vendor", "private": true,' \
  '  "dependencies": { "react": "18.3.1", "react-dom": "18.3.1", "esbuild": "0.24.0", "lucide-react": "1.17.0" } }' \
  > "$T2_VENDOR_DIR/package.json"
if [ ! -d "$T2_VENDOR_DIR/node_modules/react" ] || [ ! -d "$T2_VENDOR_DIR/node_modules/esbuild" ] || [ ! -d "$T2_VENDOR_DIR/node_modules/lucide-react" ]; then
  (cd "$T2_VENDOR_DIR" && npm install --no-audit --no-fund --omit=dev --ignore-scripts --userconfig /dev/null) \
    && echo "    + T2 vendor cache installed ($T2_VENDOR_DIR)" \
    || echo "    ! T2 vendor install failed — T2 (React) apps will fail to build; T1 apps unaffected"
else
  echo "    vendor cache already present, skipping install ($T2_VENDOR_DIR)"
fi

# ── Build a Next.js app WITHOUT taking it down ────────────────────
#
# 🔴 **`rm -rf .next` BEFORE a build is an outage, and after a failed build it
# is a permanent one.** This function replaced that pattern on 2026-09-01.
#
# Measured that morning: `app.metorite.com` answered **HTTP 500 on every route**
# — including `/` — while `acb-workbench` restart-looped every 5 seconds with
# "Could not find a production build in the '.next' directory". The build had
# not failed. It was simply still running, and the directory the live server
# serves from had already been deleted to make room for it. Two Next builds run
# per deploy, so that window is minutes.
#
# ⚠️ **Nothing alarmed.** `systemctl is-active` was true the whole time (the
# unit restarts, so it is always "starting"), the gateway answered 200, and
# `vps-health.yml` counted an HTTP 500 as proof of life. This is the same
# green-signal-about-the-machinery failure the Operator Console block below
# describes, wearing its fourth hat.
#
# The fix: build into a staging directory, and rename it onto `.next` only
# after the build produces a BUILD_ID. Downtime becomes one restart instead of
# one build, and a FAILED build changes nothing at all — the app keeps serving
# the previous build while the deploy exits non-zero and says so.
#
# ⚠️ The swap is a RENAME, never a copy. A rename is atomic within a
# filesystem; a copy is not, and a server that reloads mid-copy reads half a
# build.
#
# ⚠️ The clean-build requirement has NOT gone away — a kept `.next` produces
# stale client-reference-manifest errors under Turbopack. The staging directory
# satisfies it for free: it is removed before every build, so it is always
# empty, and `.next` is never written in place.
#
# Requires `distDir: process.env.NEXT_DIST_DIR || ".next"` in the app's
# next.config — both apps carry it, and `test_deploy_next_build_swap.py`
# fails if either loses it.
# The V8 heap cap for `next build`. It was 1024 MB until 2026-09-27, when the
# control-plane build outgrew it: all three rounds of the #493 deploy died with
# "Ineffective mark-compacts near heap limit" and SIGABRT, while the box had
# ~6 GB free and the kernel killed nothing. The box has 8 GB and no swap. The
# running servers use under 1.5 GB, so 3072 MB leaves room for them. The two
# builds never run at once, because the deploy lock serialises them.
# Fence: test_deploy_next_build_swap.py::test_the_build_heap_cap_fits_the_app.
NEXT_BUILD_HEAP_MB="${NEXT_BUILD_HEAP_MB:-3072}"
# Remove a build-tree directory without ever aborting the deploy.
#
# 🔴 **A HOUSEKEEPING `rm` KILLED TWO PRODUCTION DEPLOYS ON 2026-09-20.**
#
# `.next` on the box carries root-owned files (H-89 — something here writes
# into the checkout as root). The apply runs as the deploy user, so
# `rm -rf .next.previous` returns "Permission denied" and, under `set -e`,
# takes the whole script with it. The post-swap call is the dangerous one:
# `.next` has ALREADY been replaced, and the `systemctl restart` eleven lines
# below never runs. The box is then serving a build it did not load, the
# gateway answers its own `/version` correctly, and the deploy reports
# SUCCESS. The owner sees the old UI and is told the fix shipped.
#
# Deleting last build's leftovers is never worth a failed release. Try as
# ourselves, then with sudo, then give up and say so.
drop_dir() {
  [ -e "$1" ] || return 0
  rm -rf "$1" 2>/dev/null && return 0
  sudo rm -rf "$1" 2>/dev/null && return 0
  echo "    ~ could not remove $1 (left on disk; harmless, and the next apply retries)"
  return 0
}

# Install this directory's dependencies. Never end the deploy.
#
# 🔴 **BOTH Next apps died here, on three separate deploys on 2026-09-20.**
#
# `node_modules` on the box carries root-owned files (H-89). The apply runs
# as the deploy user, so `npm ci` exits with EACCES. Under `set -e` that ends
# the script — and the workbench install sits ABOVE the Operator Console,
# Caddy, the watchdog, the unit sync (BO-23) and the health probe. One
# permission error skipped all of them, and the deploy still reported
# success.
#
# So: try, then reclaim our own build tree and try once more, then warn and
# carry on. The BUILD is the gate. `build_next_staged` either produces a
# BUILD_ID or keeps the running build and fails loudly.
#
# ⚠️ The chown is a self-heal, NOT a fix for H-89. Something here still writes
# into the checkout as root, and it will do it again.
# ⚠️ **This function RETURNS 0 even when every install failed**, on purpose:
# a stale-but-working `node_modules` still builds, and refusing here would
# stop a deploy that would otherwise have shipped.
#
# 🔴 **It used to swallow the REASON as well, and that cost a day.** Every
# attempt sent stderr to /dev/null and the chown carried `|| true`, so a tree
# npm could not repair produced four silent lines and then a Next build that
# failed for no stated cause. Measured 2026-09-20 (H-137): the real error was
# `EACCES` on a root-owned path INSIDE `node_modules`, which the ownership
# repair at the top of this script deliberately prunes. H-89 owns that cause.
# The first attempt stays quiet because it is expected to fail here. Every
# attempt after it speaks.
npm_install_here() {
  name="$1"
  # Measure first. With both paths as the app user this count is 0 (H-89).
  reclaim_build_tree "$name"
  # H-60: keep the tree the running server loads from, when nothing changed.
  if deps_unchanged; then
    echo "    $name: package-lock.json and node are unchanged since the last"
    echo "      install — node_modules is KEPT, so the running server keeps"
    echo "      serving (H-60). DEPLOY_REINSTALL=1 installs again."
    return 0
  fi
  # BEFORE any install: an install that is killed half way must leave no
  # stamp beside the tree it damaged (H-60 review).
  rm -f node_modules/.acb-deps-stamp 2>/dev/null || true
  npm ci --prefer-offline 2>/dev/null && { record_deps_stamp; return 0; }
  echo "    ~ $name: npm ci failed — reclaiming the build tree and retrying"
  if ! sudo chown -R "$(id -un):$(id -gn)" node_modules 2>&1; then
    echo "    !! $name: could NOT reclaim node_modules — an install that fails"
    echo "       with EACCES after this is H-89, not an npm problem."
  fi
  npm ci --prefer-offline && { record_deps_stamp; return 0; }
  echo "    ~ $name: npm ci failed again — falling back to npm install"
  npm install && { record_deps_stamp; return 0; }
  echo "    ! $name: npm install failed — building against the node_modules"
  echo "      already here. ⚠️ If the build below fails, THIS is why."
  return 0
}

# 🔴 **RECLAIM THE BUILD TREE BEFORE BUILDING INTO IT (H-89, H-137 part two).**
#
# Measured 2026-09-21 on run 35588464803, the first deploy H-137's gate turned
# red instead of green: `.next` was `root:root` with 3803 paths under it, the
# Next build could not write its own trace file, and all three rounds died at
# `EACCES ... .next.staging/trace`. 759 more sat under the operator console.
#
# ⚠️ **The sweep at the top of this script CANNOT catch this**, and that is
# deliberate. It prunes `.next` because the directory is gitignored and huge,
# so `git reset` never touches it — chowning 60k files on every apply would
# turn a fast repair into a slow one. The pruning is right. The gap it leaves
# is that nothing else owned the build tree either.
#
# So the repair happens HERE: at the one moment it matters, scoped to the one
# app about to be built, and only when something is actually mis-owned.
#
# 📏 **The printed total is H-89's measurement.** `node_modules` is in the
# list since the deploy lock landed, because that is where 50220 root-owned
# paths sat on 2026-09-26. With the pull path running as the app user, every
# line this prints should read `reclaimed 0`. A non-zero total names a root
# writer that is still there.
reclaim_build_tree() {
  name="$1"
  owner="$(stat -c '%U:%G' .)"
  user="${owner%%:*}"
  total=0
  for t in node_modules .next .next.staging .next.previous; do
    [ -e "$t" ] || continue
    # `find ! -user` first, so the common case costs a traversal and no write.
    n="$(sudo find "$t" ! -user "$user" -print 2>/dev/null | wc -l)"
    [ "$n" -gt 0 ] || continue
    total=$((total + n))
    if sudo chown -R "$owner" "$t" 2>&1; then
      echo "    ~ $name: reclaimed $n path(s) under $t (H-89)"
    else
      echo "    !! $name: could NOT reclaim $t — the build below will fail"
      echo "       with EACCES, and that is H-89 rather than a code fault."
    fi
  done
  echo "    $name: build tree ownership — reclaimed $total path(s) not owned by $user (H-89)"
}

# ⚠️ No `reclaim_build_tree` here. `npm_install_here` runs it once, before the
# install, for this same app, and both call sites install first. The scan
# costs about 3 s per app, so a second scan per build bought nothing.
build_next_staged() {
  name="$1"
  drop_dir .next.staging
  drop_dir .next.previous
  # 🔴 **THE PREVIOUS BUILD'S GENERATED TYPES CAN DEADLOCK THE NEXT ONE.**
  #
  # `tsconfig.json` includes BOTH `.next/types/**/*.ts` and
  # `.next.staging/types/**/*.ts`, because either can be the live dist dir.
  # So a staged build isolates its OUTPUT and still type-checks the LAST
  # build's generated `validator.ts`.
  #
  # That file names every route by path. Rename a route directory and the old
  # validator points at a module that no longer exists:
  #
  #   Type error: Cannot find module
  #     '../../src/app/api/people/[...path]/route.js'
  #
  # Now nothing can recover on its own. The build cannot pass until `.next`
  # is replaced, and the swap below only replaces `.next` AFTER a build
  # passes. Measured on production 2026-09-20: PR #306 renamed that route to
  # `[[...path]]`, and every apply for the next eleven hours failed here.
  #
  # ⚠️ Removing these is safe and is NOT a violation of "a failed build
  # changes nothing". `types/` holds TypeScript declarations for the
  # typecheck. `next start` never reads them — it needs BUILD_ID, the server
  # chunks and the manifests, all untouched. So the running app keeps serving
  # while the build fails, which is the property this function exists for.
  drop_dir .next/types
  drop_dir .next/dev/types
  # A failed build returns 1 with `.next` untouched, and `set -e` ends the
  # apply there. `timeout` bounds it: a build that hangs is killed, and the
  # running build keeps serving. `-k 60` escalates to SIGKILL, because a
  # bound that can be ignored is not a bound. The orphan of 2026-09-25 ran
  # for 41 minutes with nothing to stop it.
  #
  # `run_tethered_build` adds the `timeout`, and it is the ONLY step that an
  # ended CI session may kill. See `tether_to_session`.
  build_rc=0
  run_tethered_build env NEXT_DIST_DIR=".next.staging" \
    NODE_OPTIONS="--max-old-space-size=$NEXT_BUILD_HEAP_MB" \
    npm run build || build_rc=$?
  if [ "$build_rc" != "0" ]; then
    if [ "$build_rc" = "124" ] || [ "$build_rc" = "137" ]; then
      echo "    !! $name: build TIMED OUT after ${NEXT_BUILD_TIMEOUT_S}s and was killed"
    fi
    echo "    ! $name: build failed (exit $build_rc) — keeping the running build"
    return 1
  fi
  # BUILD_ID is the file `next start` looks for and fails on. Checking it
  # rather than only the exit code is the difference between "the build
  # command returned 0" and "there is a build here" — this repo has been
  # burned by that distinction three times.
  if [ ! -f .next.staging/BUILD_ID ]; then
    echo "    ! $name: no BUILD_ID in .next.staging — keeping the running build"
    return 1
  fi
  if [ -d .next ]; then mv .next .next.previous; fi
  mv .next.staging .next
  # ⚠️ AFTER the swap, so it must not be able to fail. See drop_dir above.
  drop_dir .next.previous
  echo "    $name: new build swapped in"
}

echo "==> Rebuilding + restarting workbench (Next.js)"
cd "$APP_DIR/workbench/control_plane"
if [ -f package-lock.json ] || [ -f package.json ]; then
  npm_install_here "workbench"
  build_next_staged "workbench"
fi
# The unit file went in at the gateway step, before any restart (H-60).
# See the gateway note above — enable so the workbench survives reboots.
sudo systemctl enable acb-workbench >/dev/null 2>&1 || true
# Install, build, swap — and only THEN restart, and only with `next` present.
# This is the workbench's ONLY restart in the apply: the gateway restart above
# no longer takes it down (H-60). Caddy holds the requests for this second.
require_next_bin "$APP_DIR/workbench/control_plane" "acb-workbench" || exit 1
sudo systemctl restart acb-workbench
sleep 3
systemctl is-active --quiet acb-workbench || { echo "WORKBENCH FAILED TO START"; exit 1; }
wait_ready "workbench" "http://127.0.0.1:3001/" 60 \
  || { echo "WORKBENCH FAILED TO START: active, but / never answered"; exit 1; }
echo "Workbench is active"

# ── Operator Console (Next.js, staff-only) ────────────────────────
#
# 🔴 **THIS BLOCK EXISTS BECAUSE THE CONSOLE DRIFTED FOR TWO DAYS.** Measured
# 2026-08-28: `operator.metorite.com` served, and both `/models` (merged
# 2026-08-27) and `/providers` (merged 2026-08-28) answered **404**. The site
# was up, Caddy routed it, and nothing in this script rebuilt it — so every
# operator feature merged to `main` stayed on `main`.
#
# The unit file is `deploy/hostinger/acb-operator-console.service` (WS-49
# BH-2, 2026-10-09). It is the unit that ran on the box, byte for byte, and
# the BO-23 unit loop below installs it. Its drop-in
# (`acb-operator-console.service.d/50-hardening.conf`) went in at the BH-7
# step, before this restart, so this restart runs with it.
#
# ⚠️ **Deliberately AFTER the workbench.** Customer surfaces come up first, so
# a failure here fails the job loudly without having delayed a single customer
# request. That ordering is the whole reason this is safe to fail hard on.
#
# Conditional on the unit being enabled — the same test the Console block uses,
# so a box that does not run the operator console skips this silently rather
# than failing. Override the name if it runs under a different one.
OC_UNIT="${OPERATOR_CONSOLE_UNIT:-acb-operator-console}"
OC_DIR="$APP_DIR/workbench/operator_console"

if systemctl is-enabled --quiet "$OC_UNIT" 2>/dev/null; then
  echo "==> Rebuilding + restarting Operator Console ($OC_UNIT)"
  cd "$OC_DIR"
  if [ -f package-lock.json ] || [ -f package.json ]; then
    npm_install_here "operator console"
    # Same staged build as the workbench, for the same reason and with the same
    # guarantee: the console keeps serving its previous build until a new one
    # exists. See `build_next_staged` above.
    build_next_staged "operator console"
  fi
  require_next_bin "$OC_DIR" "$OC_UNIT" || exit 1
  sudo systemctl restart "$OC_UNIT"
  sleep 3
  if ! systemctl is-active --quiet "$OC_UNIT"; then
    echo "OPERATOR CONSOLE FAILED TO START"
    sudo journalctl -u "$OC_UNIT" --no-pager -n 40 || true
    exit 1
  fi
  # A cold server answers 000, not 404, so the route probe below would pass
  # on a server that is not listening yet. Wait for it first (H-60).
  if ! wait_ready "$OC_UNIT" "http://127.0.0.1:${OPERATOR_CONSOLE_PORT:-3002}/" 60; then
    echo "OPERATOR CONSOLE FAILED TO START: active, but / never answered"
    sudo journalctl -u "$OC_UNIT" --no-pager -n 40 || true
    exit 1
  fi
  echo "    Operator Console is active"

  # 🔴 **ACTIVE IS NOT SERVED, AND THE DIFFERENCE COST TWO DAYS.**
  #
  # Measured 2026-08-28 and again 2026-08-29: the unit was `active (running)`
  # for the whole period `/providers` and `/models` answered 404. `is-active`
  # reports that a process holds the port. It reports NOTHING about which build
  # that process loaded, and a Next.js server started against a stale `.next`
  # holds the port perfectly while serving last week's routes.
  #
  # That is this repo's most expensive recurring failure wearing its third hat:
  # a green signal that describes the machinery instead of the delivery. So
  # probe the routes, and fail the deploy when one is missing.
  #
  # ⚠️ The route list is DERIVED FROM THE SOURCE TREE, never written down here.
  # The whole defect is a thing that was correct when written and silently
  # stopped matching the tree. A hardcoded list would rot the same way.
  #
  # ⚠️ Only 404 is a failure. `/providers` with no session answers a redirect to
  # `/login`, and that IS a pass — it proves the route compiled. Treating a
  # redirect as failure would make this gate refuse a correct deploy.
  OC_PORT="${OPERATOR_CONSOLE_PORT:-3002}"
  oc_stale=""
  for oc_page in "$OC_DIR"/src/app/*/page.tsx; do
    [ -f "$oc_page" ] || continue
    oc_route="$(basename "$(dirname "$oc_page")")"
    oc_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
      "http://127.0.0.1:$OC_PORT/$oc_route" 2>/dev/null || echo 000)"
    [ "$oc_code" = "404" ] && oc_stale="$oc_stale /$oc_route"
  done
  if [ -n "$oc_stale" ]; then
    echo "OPERATOR CONSOLE IS SERVING A STALE BUILD — 404 on:$oc_stale"
    echo "    The unit is active and these routes exist in src/app/."
    echo "    The build did not take. Do NOT record this deploy as successful."
    exit 1
  fi
  echo "    Operator Console serves every route in src/app/"
else
  echo "    $OC_UNIT is not enabled here — skipping the Operator Console."
  echo "    If it runs under another name, set OPERATOR_CONSOLE_UNIT in .env"
  echo "    on the box. If it runs on this host at all, it is NOT being"
  echo "    rebuilt by this script and it WILL drift."
fi

echo "==> Checking Caddy is still serving"
# The config went in before the restarts (see "Installing the Caddy config").
# This is a check only. It never reloads or restarts a running Caddy, because
# a restart cuts every open connection (H-60).
if ! systemctl is-active --quiet caddy; then
  echo "    caddy is DOWN — restarting"
  sudo systemctl restart caddy || true
  sleep 2
  systemctl is-active --quiet caddy || {
    echo "CADDY FAILED TO START"
    sudo journalctl -u caddy --no-pager -n 40 || true
    exit 1
  }
fi
echo "Caddy is active"

echo "==> Installing health watchdog (systemd timer)"
# Self-heals services between deploys and captures network forensics
# every 10 min. Deliberately installed here so it can never drift
# from the repo. See deploy/hostinger/health-watchdog.sh.
# The script is already in place via git reset; just make sure it is
# executable. (Do NOT `install` it onto itself — src == dest is an
# error, which is how it ended up non-executable the first time.)
sudo chmod 0755 "$APP_DIR/deploy/hostinger/health-watchdog.sh" || true
sudo cp "$APP_DIR/deploy/hostinger/acb-health-watchdog.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/hostinger/acb-health-watchdog.timer"   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now acb-health-watchdog.timer || true
if systemctl is-active --quiet acb-health-watchdog.timer; then
  echo "    watchdog timer active — next: $(systemctl show acb-health-watchdog.timer -p NextElapseUSecRealtime --value 2>/dev/null || echo '?')"
else
  # Non-fatal: a missing watchdog must never block shipping the app.
  echo "    ! watchdog timer NOT active (non-fatal)"
  sudo systemctl status acb-health-watchdog.timer --no-pager 2>&1 | head -15 || true
fi

echo "==> Ensuring the off-box backup tools (H-123)"
# backup_offbox.sh uploads the nightly copy with rclone, after zstd and gpg. The
# box has gpg and zstd. rclone comes from apt HERE, so no hand install drifts
# from the repo, and a rebuilt box gets it on the first deploy. Idempotent: a
# tool that is present costs one `command -v`.
# Non-fatal on purpose. A missing tool must never block shipping the app, and
# the nightly unit then fails LOUDLY on its own (`needs 'rclone'`).
# ⚠️ `< /dev/null`: this file IS the shell's stdin on the push path, and a
# command that reads stdin eats the rest of the deploy.
_offbox_missing=""
command -v rclone >/dev/null 2>&1 || _offbox_missing="$_offbox_missing rclone"
command -v gpg    >/dev/null 2>&1 || _offbox_missing="$_offbox_missing gnupg"
command -v zstd   >/dev/null 2>&1 || _offbox_missing="$_offbox_missing zstd"
# ⚠️ `DPkg::Lock::Timeout=120`: unattended-upgrades holds the dpkg lock for
# minutes at a time, and without a wait the install fails at once. A failed
# `apt-get update` does NOT stop the install: the package lists already on the
# box can still hold the package. Each outcome is logged.
if [ -n "$_offbox_missing" ]; then
  echo "    installing:$_offbox_missing"
  if sudo apt-get -o DPkg::Lock::Timeout=120 update -qq >/dev/null 2>&1 < /dev/null; then
    echo "    apt-get update ok"
  else
    echo "    !! apt-get update FAILED — trying the install from the package lists on the box"
  fi
  # shellcheck disable=SC2086 # one word per package, on purpose
  if sudo DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y -qq $_offbox_missing >/dev/null 2>&1 < /dev/null; then
    echo "    + installed:$_offbox_missing"
  else
    echo "    !! could not install:$_offbox_missing — the nightly off-box copy FAILS until it is"
  fi
else
  echo "    rclone, gpg and zstd are present"
fi

echo "==> Syncing systemd units (BO-23)"
# The repo is the source of truth for the box's units; without this step a
# unit added there only reaches the machine if somebody remembers to copy it
# by hand — which is how acb-backup.timer sat unscheduled while the tooling
# existed. PR #380 added this loop to deploy/hostinger/deploy.sh, but that is
# the MANUAL runbook script: both automated delivery paths (the workflow and
# the box's poller) run THIS file, so the loop must live here to run at all.
#
# Files only, plus `enable --now` for TIMERS. Services are deliberately left
# alone: restarting the gateway is this script's own earlier step, and a
# surprise restart here would be harder to explain than a stale unit file.
UNITS_CHANGED=0
for unit in "$APP_DIR"/deploy/hostinger/*.service "$APP_DIR"/deploy/hostinger/*.timer; do
  [ -e "$unit" ] || continue
  name="$(basename "$unit")"
  if ! sudo cmp -s "$unit" "/etc/systemd/system/$name"; then
    sudo install -m 0644 "$unit" "/etc/systemd/system/$name"
    echo "    installed $name"
    UNITS_CHANGED=1
  fi
done
if [ "$UNITS_CHANGED" = "1" ]; then
  sudo systemctl daemon-reload
fi
# ⚠️ **acb-backup.timer carried a carve-out here until 2026-09-20, and it is
# gone on purpose (H-132).** On a `PG_MODE=local` box this loop actively ran
# `systemctl disable --now acb-backup.timer` on every apply. The reason was
# real when it was written on 2026-08-17. acb-backup.service loaded NO
# EnvironmentFile, so it defaulted to `PG_MODE=docker`, dumped the EMPTY local
# container, passed `--verify-restore`, and forged a green restore point.
#
# `EnvironmentFile=/opt/acb/app/.env` landed in that unit on 2026-09-19. That
# is the exact line whose absence the carve-out described. The hole is filled
# and the guard outlived it. It silently reverted two hand-enables, and it told
# neither session. A box with backups disabled for that reason gets them back
# on the next apply.
#
# What replaces it: the unit loads the env file, `PG_MODE=local` follows from
# it, and `backup_db.sh` EXITS 1 in local mode when no Postgres answers. The
# false green needs `PG_MODE=docker`, which now needs the EnvironmentFile to be
# missing. `test_the_backup_service_must_load_its_credentials` is that fence.
for timer in "$APP_DIR"/deploy/hostinger/*.timer; do
  [ -e "$timer" ] || continue
  sudo systemctl enable --now "$(basename "$timer")" >/dev/null 2>&1 \
    || echo "    !! could not enable $(basename "$timer") — check: systemctl status $(basename "$timer")"
done
systemctl list-timers --no-pager 'acb-*' 2>/dev/null | head -5 || true

echo "==> WS-49 BH-7: one restart for a unit whose drop-in changed"
# The BH-7 step installed the drop-ins before every restart of this apply. A
# unit that this apply restarted after that already runs with them. A unit
# that is active and started BEFORE its newest drop-in (the WhatsApp bridge
# when its build is off, for one) gets ONE restart here. A unit that is not
# active (the oneshot acb-smoke-chat) gets none: its next start applies them.
restart_stale_dropin_units "$APP_DIR/deploy/hostinger"

echo "==> WS-49 BH-2: the strict check of the gateway sandbox"
# 🔴 **THIS GOES AFTER THE LAST RESTART AND BEFORE THE MARKER.** The last
# restart is restart_stale_dropin_units just above. A restart after this check
# could start a unit that the check never read. The marker is
# record_applied_sha at the end. A failure here exits 1 BEFORE it, so the
# next deploy applies this sha again.
#
# ⚠️ That retry has a cost. vps_pull.sh tries one target sha at most 3 times
# (MAX_FAILS), and each try restarts the gateway before it reaches this check.
# So a strict check that is broken costs 3 gateway restarts, and then the
# timer stops and says so. The way out is the rollback, which passes this
# check for 72 hours:
#   sudo bash /opt/acb/app/scripts/bh2_rollback.sh on
# Fence: tests/unit/test_bh2_strict_check.py.
if ! bh2_strict_check; then
  echo "BH-2 STRICT CHECK FAILED: ${BH2_FAIL_WHY:-the check failed}."
  echo "    No marker was written, so the next deploy applies this sha again."
  echo "    Read: systemctl show acb-gateway -p ActiveState -p NoNewPrivileges -p ProtectSystem"
  echo "    Rollback for 72 h: sudo bash $APP_DIR/scripts/bh2_rollback.sh on"
  exit 1
fi

echo "==> Running infra health probe"
cd "$APP_DIR"
uv run python scripts/check_infra.py || {
  echo "INFRA PROBE FAILED — check logs: docker compose -f infra/docker-compose.yml logs --tail=100"
  exit 1
}

# 🔴 **`deploy.yml` GREPS FOR THIS EXACT LINE.** It is how the workflow tells
# "the apply finished" from "the apply died half way and the old build is
# still serving" — two states that looked identical until H-137, because a
# healthy app on the right SHA answers yes to every other check.
# ⚠️ Do not reword it, and do not move it. It must stay the LAST echo here.
# `test_deploy_pipeline.py::TestTheApplyMustReachItsEnd` fences both sides.
# The marker goes first, so it can only record an apply that got this far.
# It records HEAD only when HEAD's own copy of this file ran the steps.
record_applied_sha "$(git -C "$APP_DIR" rev-parse HEAD)" "$VPS_APPLY_SELF_SUM"
echo "==> Deployment complete"
