#!/usr/bin/env bash
# Run pr-check on a preset local CI machine. From Windows, or on the home server.
#
#   bash scripts/ci_local.sh              # the commit at HEAD
#   bash scripts/ci_local.sh --e2e        # also the browser suite, after the
#                                         # unit phase (about 20 more minutes)
#   bash scripts/ci_local.sh --full       # every test, as pr-check does on a
#                                         # merge. The default is the CIV-2
#                                         # selection: the tests the diff
#                                         # against origin/main can affect.
#   bash scripts/ci_local.sh --ref <ref>  # another commit
#   bash scripts/ci_local.sh --home       # only the home server
#   bash scripts/ci_local.sh --pc         # only this PC
#
# TWO LANES, so two runs can go at once (task 1534):
#   home  the home server: user ci on metorite-home, port 2222, over
#         Tailscale (scripts/home_server/). The default lane.
#   pc    the `metorite-ci` WSL2 distro on this PC.
# With no lane named, the home server runs it. This PC takes the run when
# the home server has no room for another run or does not answer.
# A lane takes SEVERAL runs at once (CIV-6). Each run has its own git
# worktree, and reserves memory from a slot pool sized from the measured peak
# per shard: scripts/ci_pc/slots.sh.
# On the home server itself, the run goes to its local ci user.
#
# Why: GitHub builds every job from scratch (2-4 minutes of `uv sync`,
# `npm ci`, apt and image pulls each), and runs queue behind the Free plan's
# 20-job cap. Here everything is already installed. Same jobs, same commands:
# they are read from .github/workflows/pr-check.yml (scripts/ci_pc/steps.py).
#
# It tests COMMITS, as CI does. Uncommitted changes are not tested.
# It is not the merge gate. pr-check on GitHub still is.
# One-time setup: bash scripts/ci_pc/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DISTRO=metorite-ci
CI_HOME="${CI_HOME:-ci@metorite-home}"
CI_HOME_PORT="${CI_HOME_PORT:-2222}"
lane="${CI_LANE:-auto}"
ref=HEAD
pass=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) ref="$2"; shift 2 ;;
    --home) lane=home; shift ;;
    --pc) lane=pc; shift ;;
    *) pass+=("$1"); shift ;;
  esac
done
# On the home server (Linux with a ci user), the run stays on this machine.
if [ "$(uname -s)" = Linux ] && id ci >/dev/null 2>&1; then lane=self; fi

# `-e`, not `--`: `--` hands the command to a shell first, and that shell
# expands every `$` meant for the command inside the distro.
w_pc() { MSYS_NO_PATHCONV=1 wsl.exe -d "$DISTRO" -e "$@"; }
# ssh hands the command to the far shell, so each word is quoted for it.
w_home() {
  ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -p "$CI_HOME_PORT" "$CI_HOME" \
    "$(printf '%q ' "$@")"
}
w_self() { sudo -n -u ci -H "$@"; }
pc_ok() {
  timeout 25 wsl.exe -l -q 2>/dev/null | tr -d '\0\r' | grep -qx "$DISTRO" \
    && timeout 60 wsl.exe -d "$DISTRO" -e true >/dev/null 2>&1
}
# ConnectTimeout bounds the wait when the box is off or Tailscale is down.
home_ok() { w_home true >/dev/null 2>&1; }
# Does the lane have room for one more run (the smallest: one shard) now?
has_room() { "$1" bash -c "$(cat scripts/ci_pc/slots.sh); ci_slot_grant 1 1 >/dev/null" >/dev/null 2>&1; }
pc_down() {
  echo "The $DISTRO distro on this PC does not answer, or it is not there." >&2
  echo "Check with: bash scripts/ci_pc/doctor.sh   (first time: bash scripts/ci_pc/setup.sh)" >&2
  exit 3
}

case "$lane" in
  self) ;;
  pc) pc_ok || pc_down ;;
  home)
    home_ok || { echo "The home server ($CI_HOME, port $CI_HOME_PORT) does not answer. Is Tailscale on?" >&2; exit 3; } ;;
  auto)
    if home_ok; then
      lane=home
      if ! has_room w_home && pc_ok && has_room w_pc; then
        lane=pc
        echo "The home server has no room for another suite, so this PC takes this one."
      fi
    elif pc_ok; then
      lane=pc
      echo "The home server does not answer, so this PC runs the suite."
    else
      echo "Neither the home server nor this PC's $DISTRO distro answers." >&2
      exit 3
    fi ;;
  *) echo "CI_LANE must be home, pc or auto, not $lane" >&2; exit 2 ;;
esac
case "$lane" in
  home) w() { w_home "$@"; } ;;
  pc) w() { w_pc "$@"; } ;;
  self) w() { w_self "$@"; } ;;
esac
echo "local CI lane: $lane"

sha="$(git rev-parse "$ref")"
[ -z "$(git status --porcelain --untracked-files=no)" ] || \
  echo "⚠️  Uncommitted changes are NOT in this run. It tests ${sha:0:9}."

# The distro's clone fetches from GitHub. A commit GitHub does not have yet
# travels as a git bundle on stdin: the distro has no mount of C: on purpose.
git fetch -q origin main
# One git writer at a time in the lane's shared clone: runs now overlap.
GITLOCK='exec 5>~/.ci-git.lock; flock -w 300 5; cd ~/metorite'
w bash -lc "$GITLOCK && git fetch -q origin"
if ! git merge-base --is-ancestor "$sha" origin/main; then
  # A bundle carries REFS, not bare commits, so name the commit for a moment.
  # The ref and the inbox file are per run: two sessions that start at once
  # (two lanes invite it) overwrote one shared name, and a run died with
  # "reference is not a tree" (2026-11-03).
  tag="${sha:0:12}-$$"
  bref="refs/ci-local/$tag"
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"; git update-ref -d "$bref" 2>/dev/null' EXIT
  git update-ref "$bref" "$sha"
  git bundle create -q "$tmp" "$bref" --not origin/main
  w bash -c "cat > ~/inbox-$tag.bundle" < "$tmp"
  w bash -lc "$GITLOCK && git fetch -q ~/inbox-$tag.bundle $bref; rc=\$?; rm -f ~/inbox-$tag.bundle; exit \$rc"
fi

# The run happens on the lane (scripts/ci_pc/remote_run.sh): its own git
# worktree of the commit, the CIV-2 selection (or --full), a memory slot, then
# ITS copy of run_suite.sh. remote_run.sh and slots.sh travel in the command,
# so the lane keeps no copy that could go stale.
# Only when there ARE options: `printf ' %q'` with no arguments prints `''`,
# and run_suite.sh refuses it, so every bare `ci_local.sh` failed at once
# (reported by a peer session, 2026-10-20).
args=""
[ ${#pass[@]} -gt 0 ] && args="$(printf ' %q' "${pass[@]}")"
# A few tuning variables reach the lane (a test of the slots, a re-measure).
tune=""
for v in CI_LANE_LOCK CI_SHARD_GB CI_FIXED_GB CI_OTHER_GB; do
  [ -n "${!v:-}" ] && tune+="export $v=$(printf %q "${!v}")"$'\n'
done
rc=0
w bash -lc "$tune$(cat scripts/ci_pc/slots.sh scripts/ci_pc/remote_run.sh)
ci_remote_run $sha$args" || rc=$?
if [ "$lane" = home ]; then
  echo "The logs are on the home server. Read one with: ssh -p $CI_HOME_PORT $CI_HOME cat <a path above>"
fi
exit "$rc"
