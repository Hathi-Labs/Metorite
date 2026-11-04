#!/usr/bin/env bash
# Run pr-check on a preset local CI machine. From Windows, or on the home server.
#
#   bash scripts/ci_local.sh              # the commit at HEAD
#   bash scripts/ci_local.sh --e2e        # also the browser suite, after the
#                                         # unit phase (about 20 more minutes)
#   bash scripts/ci_local.sh --ref <ref>  # another commit
#   bash scripts/ci_local.sh --home       # only the home server
#   bash scripts/ci_local.sh --pc         # only this PC
#
# TWO LANES, so two runs can go at once (task 1534):
#   home  the home server: user ci on metorite-home, port 2222, over
#         Tailscale (scripts/home_server/). The default lane.
#   pc    the `metorite-ci` WSL2 distro on this PC.
# With no lane named, the home server runs it. This PC takes the run when
# the home server is busy with another run or does not answer. Each lane
# runs one suite at a time: a suite needs about 2.5 GB per shard.
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
lock_free() { "$1" bash -c 'exec 9>~/.ci.lock; flock -n 9' >/dev/null 2>&1; }
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
      if ! lock_free w_home && pc_ok && lock_free w_pc; then
        lane=pc
        echo "The home server runs another suite, so this PC takes this one."
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
# One run at a time per lane. Say so, instead of a silent wait.
if ! lock_free w; then
  echo "Another local CI run holds the $lane lane. This run waits for it, at most 1 hour."
fi

sha="$(git rev-parse "$ref")"
[ -z "$(git status --porcelain --untracked-files=no)" ] || \
  echo "⚠️  Uncommitted changes are NOT in this run. It tests ${sha:0:9}."

# The distro's clone fetches from GitHub. A commit GitHub does not have yet
# travels as a git bundle on stdin: the distro has no mount of C: on purpose.
git fetch -q origin main
w bash -lc 'cd ~/metorite && git fetch -q origin'
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
  w bash -lc "cd ~/metorite && git fetch -q ~/inbox-$tag.bundle $bref; rc=\$?; rm -f ~/inbox-$tag.bundle; exit \$rc"
fi

# Check out the commit, then run ITS copy of run_suite.sh. One run at a time.
# Only when there ARE options: `printf ' %q'` with no arguments prints `''`,
# and run_suite.sh refuses it, so every bare `ci_local.sh` failed at once
# (reported by a peer session, 2026-10-20).
args=""
[ ${#pass[@]} -gt 0 ] && args="$(printf ' %q' "${pass[@]}")"
rc=0
w bash -lc "set -e; exec 9>~/.ci.lock; flock -w 3600 9; cd ~/metorite
  git -c advice.detachedHead=false checkout -q -f $sha
  git clean -fdxq -e .venv -e node_modules -e .next
  exec bash scripts/ci_pc/run_suite.sh$args" || rc=$?
if [ "$lane" = home ]; then
  echo "The logs are on the home server. Read one with: ssh -p $CI_HOME_PORT $CI_HOME cat <a path above>"
fi
exit "$rc"
