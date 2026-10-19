#!/usr/bin/env bash
# Run pr-check on THIS PC, in the preset `metorite-ci` WSL distro. From Windows.
#
#   bash scripts/ci_local.sh              # the commit at HEAD
#   bash scripts/ci_local.sh --e2e        # also the browser suite (auto when
#                                         # the branch touches control_plane)
#   bash scripts/ci_local.sh --ref <ref>  # another commit
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
ref=HEAD
pass=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) ref="$2"; shift 2 ;;
    *) pass+=("$1"); shift ;;
  esac
done

# `-e`, not `--`: `--` hands the command to a shell first, and that shell
# expands every `$` meant for the command inside the distro.
w() { MSYS_NO_PATHCONV=1 wsl.exe -d "$DISTRO" -e "$@"; }
if ! wsl.exe -l -q 2>/dev/null | tr -d '\0\r' | grep -qx "$DISTRO"; then
  echo "No $DISTRO distro. Run once: bash scripts/ci_pc/setup.sh" >&2
  exit 2
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
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"; git update-ref -d refs/ci-local/head 2>/dev/null' EXIT
  git update-ref refs/ci-local/head "$sha"
  git bundle create -q "$tmp" refs/ci-local/head --not origin/main
  w bash -c 'cat > ~/inbox.bundle' < "$tmp"
  w bash -lc 'cd ~/metorite && git fetch -q ~/inbox.bundle refs/ci-local/head'
fi

# Check out the commit, then run ITS copy of run_suite.sh. One run at a time.
args="$(printf ' %q' "${pass[@]+"${pass[@]}"}")"
w bash -lc "set -e; exec 9>~/.ci.lock; flock -w 3600 9; cd ~/metorite
  git -c advice.detachedHead=false checkout -q -f $sha
  git clean -fdxq -e .venv -e node_modules -e .next
  exec bash scripts/ci_pc/run_suite.sh$args"
