#!/usr/bin/env bash
# Run slots for the local CI lanes (CIV-6, task 1634). Sourced, never run.
#
# One lane used to run ONE suite at a time (flock ~/.ci.lock), because each
# shard was guessed at 2.5 GB. Measured on the home box (2026-11-04, cgroup
# memory.peak of each container, polled each 2 s): one shard is 0.7 to 3.2 GB
# with its two Postgres servers, and the other jobs add about 5 GB. So a lane
# takes several runs, as long as their reserved memory fits.
#
# A run RESERVES memory before it starts:
#     need = CI_FIXED_GB + shards * CI_SHARD_GB
# The reservation is a file in $CI_SLOT_DIR that the run holds a flock on, for
# as long as it lives. A crashed run drops its flock, so its reservation
# counts for nothing. No cleanup is needed.
#
# A run starts when BOTH hold:
#   1. the live reservations plus its own need fit in MemTotal - CI_OTHER_GB.
#      This counts memory that a young run has not touched yet.
#   2. MemAvailable now is at least its own need. This counts the sessions
#      and services that are not CI.
# With no live run, check 1 passes for the smallest grant, so a run always
# makes progress.
#
# The legacy ~/.ci.lock stays: a new run holds it SHARED, an older ci_local.sh
# (which still holds it exclusive and uses ~/metorite) waits for the new runs,
# and the new runs wait for it. Drop that one release after CIV-6 lands.
#
# Fence: tests/unit/test_ci_pc.py, tests/unit/test_ci_slots.py.

# Measured, in GB. Re-measure with scripts/ci_pc/memsample.sh after a change
# to the suite. Round up: a reservation too small starves the VM.
: "${CI_SHARD_GB:=2}"       # pytest process + 2 Postgres on tmpfs, per shard
: "${CI_FIXED_GB:=6}"       # lint, frontend, operator-console, migrations, backup, caddy
: "${CI_E2E_GB:=10}"         # the browser suite container (--e2e), capped at 10g
: "${CI_OTHER_GB:=6}"       # kept free for sessions, Docker and the dev stack
: "${CI_SLOT_DIR:=$HOME/.ci-slots}"
: "${CI_MEMINFO:=/proc/meminfo}"
: "${CI_SLOT_WAIT_S:=3600}"
: "${CI_SLOT_POLL_S:=10}"

_ci_meminfo_gb() { # <field> -> whole GB (rounded down)
  awk -v f="$1:" '$1 == f {print int($2 / 1048576)}' "$CI_MEMINFO"
}

# Whole GB reserved by runs that are alive: a run is alive while its .res file
# is flocked. `flock -n` on a free file succeeds, so a dead run counts for 0.
_ci_live_gb() {
  local f sum=0 gb
  for f in "$CI_SLOT_DIR"/*.res; do
    [ -f "$f" ] || continue
    if ! flock -n "$f" true 2>/dev/null; then
      read -r gb < "$f" 2>/dev/null || gb=0
      sum=$(( sum + ${gb:-0} ))
    fi
  done
  echo "$sum"
}

# ci_slot_grant <want> <min> [extra_gb]  ->  prints the shard count this run may start
# with right now, or nothing when even <min> does not fit. Pure: it reserves
# nothing, so a lane probe can call it.
ci_slot_grant() {
  local want="$1" min="$2" extra="${3:-0}" total avail live budget s
  total=$(_ci_meminfo_gb MemTotal); avail=$(_ci_meminfo_gb MemAvailable)
  live=$(_ci_live_gb)
  budget=$(( total - CI_OTHER_GB ))
  for (( s = want; s >= min; s-- )); do
    local need=$(( CI_FIXED_GB + extra + s * CI_SHARD_GB ))
    if [ $(( live + need )) -le "$budget" ] && [ "$avail" -ge "$need" ]; then
      echo "$s"; return 0
    fi
  done
  return 1
}

# ci_slot_acquire <want> <min> [extra_gb]  ->  sets CI_SHARDS_GRANTED, and holds the
# reservation on fd 7 until this shell exits. Waits up to CI_SLOT_WAIT_S.
ci_slot_acquire() {
  local want="$1" min="$2" extra="${3:-0}" waited=0 s id
  mkdir -p "$CI_SLOT_DIR"
  id="$(date +%Y%m%d-%H%M%S)-$$"
  exec 8>"$CI_SLOT_DIR/admit"
  while :; do
    # The admit lock makes "count, then reserve" one step. Two runs that
    # look at the same free memory would both start.
    flock -w 60 8 || { echo "ci slots: the admit lock is stuck" >&2; return 1; }
    if s=$(ci_slot_grant "$want" "$min" "$extra"); then
      echo "$(( CI_FIXED_GB + extra + s * CI_SHARD_GB ))" > "$CI_SLOT_DIR/$id.res"
      exec 7>>"$CI_SLOT_DIR/$id.res"
      flock -n 7 || { echo "ci slots: cannot lock my reservation" >&2; return 1; }
      CI_SHARDS_GRANTED="$s"; CI_SLOT_ID="$id"; export CI_SHARDS_GRANTED CI_SLOT_ID
      # Drop the files of dead runs, but never one that is alive. Still under
      # the admit lock: a sweep outside it could delete a file that another
      # run has written and not yet locked (verifier finding, 2026-11-04).
      local f
      for f in "$CI_SLOT_DIR"/*.res; do
        [ "$f" = "$CI_SLOT_DIR/$id.res" ] && continue
        flock -n "$f" rm -f "$f" 2>/dev/null || true
      done
      flock -u 8
      return 0
    fi
    flock -u 8
    if [ "$waited" -ge "$CI_SLOT_WAIT_S" ]; then
      echo "ci slots: no room for $min shards after ${waited}s" >&2; return 1
    fi
    [ "$waited" = 0 ] && echo "ci slots: waiting for room ($(_ci_live_gb) GB reserved, $(_ci_meminfo_gb MemAvailable) GB free)"
    sleep "$CI_SLOT_POLL_S"; waited=$(( waited + CI_SLOT_POLL_S ))
  done
}
