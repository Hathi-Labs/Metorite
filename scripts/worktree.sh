#!/usr/bin/env bash
# worktree.sh — start a session in its own worktree, and tear one down safely.
#
# WHY THIS EXISTS. Two sessions in one checkout fight over the same working
# tree: one switches branch under the other, `git status` mixes their edits,
# and a `reset --hard` by either destroys the other's uncommitted work. A
# worktree gives each session its own directory and its own branch, sharing
# one `.git`. That is the whole point — parallel sessions, one history.
#
# Owner directive, 2026-09-03: every new development session starts here.
#
# Usage:
#   bash scripts/worktree.sh new <slug> [base]   # base defaults to origin/main
#   bash scripts/worktree.sh list
#   bash scripts/worktree.sh remove <slug>
#
# ⚠️ `remove` IS THE DANGEROUS ONE, and it is why this file exists rather than
# a line in a README. Some worktrees here carry a `node_modules` JUNCTION
# pointing at the primary checkout's real directory. `git worktree remove`
# follows it and deletes THE REAL ONE. That has happened.
#
# The guard is `cmd //c rmdir`, which is precise in a way `rm -rf` is not:
#   • given a junction, it removes the LINK and never the target
#   • given a real non-empty directory, it REFUSES
# So the safe case succeeds and the dangerous case stops, with no test for
# "is this a junction" that could get the answer wrong.
set -euo pipefail

# Worktrees live BESIDE the repo, never inside it. Inside, every tool that
# walks the tree — ruff, vitest, the STE linter, `git status` — would find a
# second copy of the whole codebase and act on it.
if [ -n "${METORITE_WT_ROOT:-}" ]; then
  WT_ROOT="$METORITE_WT_ROOT"
elif [ -d /c/Users ] || [ -d /mnt/c/Users ]; then
  WT_ROOT="/c/wt"          # the shape already on this machine: C:\wt-<slug>
else
  WT_ROOT="$HOME/wt"
fi

REPO="$(git rev-parse --show-toplevel)"
cmd="${1:-}"

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

wt_path() { printf '%s-%s' "$WT_ROOT" "$1"; }

case "$cmd" in
new)
  slug="${2:-}"; base="${3:-origin/main}"
  [ -n "$slug" ] || usage
  path="$(wt_path "$slug")"
  [ -e "$path" ] && { echo "!! $path already exists"; exit 1; }

  # Fetch first. A worktree cut from a stale `origin/main` starts behind and
  # every PR from it opens with an avoidable merge.
  git -C "$REPO" fetch --quiet origin
  git -C "$REPO" worktree add -b "$slug" "$path" "$base"

  echo
  echo "   worktree: $path"
  echo "   branch:   $slug   (from $base)"
  echo
  echo "   cd \"$path\""
  echo
  echo "   ⚠️ Frontend work needs its own install here. Turbopack resolves"
  echo "      through a junction to the WRONG root, so do not link it:"
  echo "        cd \"$path/workbench/control_plane\" && npm install"
  ;;

list)
  git -C "$REPO" worktree list
  echo
  echo "primary checkout: $REPO"
  n="$(git -C "$REPO" worktree list | wc -l)"
  if [ "$n" -gt 6 ]; then
    echo
    echo "⚠️ $n worktrees. Stale ones hold branches nobody reads (H-63)."
    echo "   Prune the merged ones: bash scripts/worktree.sh remove <slug>"
  fi
  ;;

remove)
  slug="${2:-}"
  [ -n "$slug" ] || usage
  path="$(wt_path "$slug")"
  [ -d "$path" ] || { echo "!! no worktree at $path"; exit 1; }

  # ⚠️ THE JUNCTION FIRST, ALWAYS. See the header. `cmd rmdir` unlinks a
  # junction and refuses a real non-empty directory, so this line is safe to
  # run blind — which is exactly why it runs blind rather than behind a test.
  if command -v cmd >/dev/null 2>&1; then
    win="$(cd "$path" && pwd -W 2>/dev/null || true)"
    if [ -n "$win" ] && [ -d "$path/node_modules" ]; then
      cmd //c rmdir "$(printf '%s' "$win" | tr '/' '\\')\\node_modules" >/dev/null 2>&1 \
        && echo "   unlinked the node_modules junction" \
        || echo "   node_modules is a real directory (left alone)"
    fi
  fi

  git -C "$REPO" worktree remove "$path" --force
  echo "   removed $path"
  echo "   ⚠️ The BRANCH still exists. Delete it when it is merged:"
  echo "      git -C \"$REPO\" branch -d $slug"
  ;;

*)
  usage
  ;;
esac
