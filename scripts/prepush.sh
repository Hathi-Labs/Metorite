#!/usr/bin/env bash
# Run before `git push`: the checks that fail CI most often, in a few minutes.
#
#   bash scripts/prepush.sh            # fences + the tests this change can affect
#   bash scripts/prepush.sh --harden   # the hardening pass: process fences block too
#
# Why (measured 15-19 Oct): 29 of 70 red CI runs were a static fence that
# runs locally in seconds, and every red run cost about 30 minutes.
# Two-speed CI contract: project-docs/specs/engineering_practice.md §7a.
#
# Not the merge gate. pr-check runs the whole suite on every PR.
set -u
cd "$(dirname "$0")/.."
harden=false; [ "${1:-}" = "--harden" ] && harden=true
rc=0

echo "== safety fences (always block) =="
uv run python scripts/fences.py run safety || rc=1

echo "== process fences ($($harden && echo blocking || echo advisory)) =="
if ! uv run python scripts/fences.py run process; then
  if $harden; then rc=1; else echo "advisory: fix these in the hardening pass"; fi
fi

echo "== tests this change can affect =="
uv run python scripts/affected_tests.py --base origin/main --run || rc=1

if $harden; then
  # NEW files only: a whole-file check of a modified file bills this branch
  # for the 19,094 grandfathered errors.
  echo "== STE on the markdown files this branch added =="
  git diff --name-only --diff-filter=A origin/main...HEAD -- '*.md' |
    xargs -r node .claude/hooks/ste-lint.mjs || rc=1
fi

[ $rc -eq 0 ] && echo "prepush: OK" || echo "prepush: FAILED"
exit $rc
