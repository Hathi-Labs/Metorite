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
#   bash scripts/worktree.sh remove <slug>           # refuses a dirty worktree
#   bash scripts/worktree.sh remove --force <slug>   # deletes its unsaved work
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
#
# `remove` also refuses a worktree with uncommitted or untracked work (LD-6).
# Another session may be editing there. Fence: tests/unit/test_worktree_remove.py.
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

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

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
  force=0; slug=""
  for a in "${@:2}"; do
    if [ "$a" = "--force" ]; then force=1; else slug="$a"; fi
  done
  [ -n "$slug" ] || usage
  path="$(wt_path "$slug")"
  [ -d "$path" ] || { echo "!! no worktree at $path"; exit 1; }

  # ⚠️ UNSAVED WORK BEFORE ANYTHING ELSE (LD-6). This script once passed
  # --force every time, and --force deletes exactly the uncommitted and
  # untracked files. Another session may still be editing here. So check
  # first, before the dev stack stops and the junction goes: a refusal must
  # leave the worktree as it was. Ignored files (node_modules, .venv,
  # .dev_up) do not count, so a finished worktree passes.
  dirty="$(git -C "$path" status --porcelain 2>/dev/null || true)"
  if [ -n "$dirty" ] && [ "$force" != 1 ]; then
    echo "!! $path has uncommitted or untracked work:"
    printf '%s\n' "$dirty" | head -20 | sed 's/^/     /'
    echo "   Commit it or move it. To delete it anyway:"
    echo "      bash scripts/worktree.sh remove --force $slug"
    exit 1
  fi
  # The branch survives the removal, so unpushed commits are not lost. Say so
  # anyway: a branch nobody pushed is easy to forget.
  ahead="$(git -C "$path" rev-list --count HEAD --not --remotes 2>/dev/null || echo 0)"
  if [ "$ahead" != 0 ]; then
    echo "   ⚠️ $ahead commit(s) on this branch are on no remote. The branch keeps them."
  fi

  # ⚠️ A RUNNING PROCESS FIRST. Measured 2026-09-03: this script removed a
  # worktree that still had `uvicorn` and `python` serving out of its `.venv`.
  # Nothing was lost — the branch was merged and the tree was clean — but the
  # server was then running from a directory git no longer tracked, and the
  # files would not delete.
  #
  # A file mtime does NOT catch this. The directory had not been written to for
  # a day while the process ran happily inside it, so "untouched recently" read
  # as idle when it was not.
  #
  # A warning and not a refusal: the owner may well want the worktree gone and
  # the server stopped. What must not happen is finding out afterwards.
  #
  # A dev stack (scripts/dev_up.sh) is the common case, so stop it first, with
  # its containers. Its state folder says that one exists.
  if [ -f "$path/.dev_up/state" ] && [ -f "$path/scripts/dev_up.sh" ]; then
    echo "   stopping the dev stack of this worktree"
    bash "$path/scripts/dev_up.sh" --down || echo "   !! dev_up --down failed. Check: docker ps"
  fi
  if command -v powershell >/dev/null 2>&1; then
    win_prefix="$(printf '%s' "$path" | sed 's|^/\([a-zA-Z]\)/|\1:\\\\|; s|/|\\\\|g')"
    procs="$(powershell -NoProfile -Command \
      "(Get-Process | Where-Object { \$_.Path -like '${win_prefix}*' } | \
        ForEach-Object { \$_.ProcessName + '(' + \$_.Id + ')' }) -join ' '" 2>/dev/null | tr -d '\r')"
    if [ -n "$procs" ]; then
      echo "   ⚠️ PROCESSES ARE STILL RUNNING FROM THIS WORKTREE:"
      echo "      $procs"
      echo "      Stop them first, or the files will not delete and the server"
      echo "      keeps serving from a directory git no longer tracks."
      echo "      Continuing in 5s — Ctrl-C to stop."
      sleep 5
    fi
  fi

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

  if [ "$force" = 1 ]; then
    git -C "$REPO" worktree remove --force "$path"
  else
    git -C "$REPO" worktree remove "$path"
  fi
  echo "   removed $path"
  echo "   ⚠️ The BRANCH still exists. Delete it when it is merged:"
  echo "      git -C \"$REPO\" branch -d $slug"
  ;;

*)
  usage
  ;;
esac
