#!/usr/bin/env bash
# The part of a local CI run that happens ON the lane (CIV-6, task 1634).
# Sourced after slots.sh. `scripts/ci_local.sh` sends both files as one
# `bash -lc` string, so this code runs from the invoker's checkout and the
# lane needs no copy of it. Never run by hand.
#
#   ci_remote_run <sha> [--full] [--shards N] [--e2e]
#
# 1. Make a git worktree of <sha> for THIS run: ~/ci-wt/<sha12>-<pid>. Two
#    runs never share a working tree, so a checkout, a `git clean`, `.next`
#    and `junit/` of one run cannot reach another.
# 2. Pick the tests. The default is the CIV-2 selection (`affected_tests.py
#    --gate`, the PR tier). `--full` runs every test, as pr-check does on a
#    merge to main. A selector that fails, or says "full", gives the full run.
# 3. Take a slot (slots.sh), then run run_suite.sh in the worktree.
# 4. Remove the worktree.

CI_BASE="${CI_BASE:-$HOME/metorite}"
CI_WT_DIR="${CI_WT_DIR:-$HOME/ci-wt}"

ci_sweep_worktrees() {
  # A run that was killed leaves its worktree. Its .live file is then not
  # flocked. Remove the ones that are dead and older than an hour.
  local d
  for d in "$CI_WT_DIR"/*/; do
    [ -d "$d" ] || continue
    d="${d%/}"
    if flock -n "$d.live" true 2>/dev/null && [ -z "$(find "$d.live" -mmin -60 2>/dev/null)" ]; then
      git -C "$CI_BASE" worktree remove --force "$d" >/dev/null 2>&1 || rm -rf "$d"
      rm -f "$d.live"
    fi
  done
  git -C "$CI_BASE" worktree prune >/dev/null 2>&1 || true
}

ci_remote_run() {
  local sha="$1"; shift
  local full=no shards="" fwd=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --full) full=yes; shift ;;
      --shards) shards="$2"; shift 2 ;;
      *) fwd+=("$1"); shift ;;
    esac
  done
  local wt="$CI_WT_DIR/${sha:0:12}-$$-$(date +%s)"
  mkdir -p "$CI_WT_DIR"

  # The legacy lane lock, SHARED: an older ci_local.sh holds it exclusive and
  # works in $CI_BASE, so the two kinds must not overlap. New runs share it.
  exec 9>"${CI_LANE_LOCK:-$HOME/.ci.lock}"
  flock -s -w 3600 9 || { echo "ci: the lane lock is held by an older run for over an hour" >&2; return 1; }

  # One git writer at a time in the shared clone.
  (
    flock -w 300 5 || exit 1
    ci_sweep_worktrees
    git -C "$CI_BASE" worktree add -q --detach "$wt" "$sha"
  ) 5>"$HOME/.ci-git.lock" || { echo "ci: cannot make a worktree for ${sha:0:9}" >&2; return 1; }
  exec 6>"$wt.live"; flock -n 6
  # Double quotes: $wt is a LOCAL, gone when the EXIT trap runs. Single quotes
  # removed nothing and left every worktree behind (first two-run test).
  trap "cd \"\$HOME\"; git -C \"$CI_BASE\" worktree remove --force \"$wt\" >/dev/null 2>&1 || rm -rf \"$wt\"; rm -f \"$wt.live\"" EXIT
  cd "$wt" || return 1

  local want min sel_args=() nfiles=0
  if [ "$full" = no ]; then
    local gate
    gate="$(python3 scripts/affected_tests.py --gate --base "${CI_SELECT_BASE:-origin/main}")" || gate=""
    nfiles="$(printf '%s' "$gate" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(-1); raise SystemExit
if d.get("full") or not d.get("files"):
    print(-1); raise SystemExit
open(".ci-selected", "w").write("\n".join(d["files"]) + "\n")
print(len(d["files"]))' 2>/dev/null)" || nfiles=-1
    if [ "${nfiles:--1}" -gt 0 ] 2>/dev/null; then
      sel_args=(--select-file "$wt/.ci-selected")
      want=$(( 1 + nfiles / 150 )); [ "$want" -gt 3 ] && want=3; min=1
      echo "ci: the CIV-2 selection, $nfiles test files (bash scripts/ci_local.sh --full runs every test)"
    else
      echo "ci: the selector says full (or failed), so every test runs"
    fi
  fi
  if [ ${#sel_args[@]} -eq 0 ]; then want=6; min=2; fi
  if [ -n "$shards" ]; then want="$shards"; min="$shards"; fi

  # The browser suite is a 10 GB container: reserve it, or two --e2e runs overfill.
  local extra=0 a
  for a in "${fwd[@]}"; do [ "$a" = --e2e ] && extra="$CI_E2E_GB"; done
  ci_slot_acquire "$want" "$min" "$extra" || return 3
  echo "ci: slot $CI_SLOT_ID, $CI_SHARDS_GRANTED shard(s), worktree $wt"
  # CIV-6b. A dropped client (ssh gone, Ctrl-C, a killed ci_local.sh) sends this
  # shell a HUP, INT or TERM. Run in the foreground, the EXIT trap then removed
  # the worktree under the live job containers, and a run that would have
  # finished died with FileNotFoundError (measured 2026-11-06). So:
  #  * the suite runs in its own process group;
  #  * the trap is installed BEFORE the launch, so no signal meets the default
  #    action. A signal that comes before the pid is known sets `stopping`,
  #    and the code after the launch stops the group;
  #  * a stop sends TERM to the group, then KILL after CI_STOP_GRACE_S (60 s),
  #    so a suite that ignores TERM cannot hold the lane for ever;
  #  * the EXIT trap (worktree removal) runs only after the suite has ended.
  local suite_pid="" rc=0 stopping=0 waited=0 grace="${CI_STOP_GRACE_S:-60}"
  trap 'stopping=1; [ -n "$suite_pid" ] && kill -TERM -- "-$suite_pid" 2>/dev/null' HUP INT TERM
  CI_SLOT_GRANTED=1 CI_DEPS_DIR="${CI_DEPS_DIR:-$HOME/ci-deps}" \
    setsid bash scripts/ci_pc/run_suite.sh --shards "$CI_SHARDS_GRANTED" "${sel_args[@]}" "${fwd[@]}" &
  suite_pid=$!
  [ "$stopping" = 1 ] && kill -TERM -- "-$suite_pid" 2>/dev/null
  while kill -0 "$suite_pid" 2>/dev/null; do
    if [ "$stopping" = 1 ]; then
      waited=$(( waited + 1 ))
      [ "$waited" -gt "$grace" ] && kill -KILL -- "-$suite_pid" 2>/dev/null
    fi
    # A trapped signal ends this `wait` at once, so the loop sees `stopping`.
    sleep 1 & wait $!
  done
  wait "$suite_pid"; rc=$?
  trap - HUP INT TERM
  return "$rc"
}
