#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# premerge.sh: test the merge with TODAY's main, right before `gh pr merge` (LD-3).
#
#   bash scripts/premerge.sh               # in the PR's worktree. Commit everything first.
#   bash scripts/premerge.sh --no-tests    # collision fences only (same as PREMERGE_MERGE_TEST=0)
#
# WHY. pr-check tests the PR merged with main AS IT WAS when the run started.
# Branch protection does not ask for an up-to-date branch (strict: false), and
# there is no merge queue (owner decision, 2026-10-16). So a PR can merge green
# onto a main that moved after its run:
#   * two PRs took the same migration number, 3 times in 2 weeks (R1);
#   * two PRs took the same H-id (2026-09-23) and the same D-number (2026-10-25);
#   * two PRs went green alone and red together, twice in one day (HANDOFF H-10).
#
# FIRST, on every branch, it refuses a patch that the automatic revert took
# out of main, when the patch is unchanged (CIV-31, step 0 below).
#
# WHAT. When the branch is behind origin/main, this script:
#   1. refuses a new migration numbered at or below main's highest (R1);
#   2. merges origin/main into the worktree WITHOUT a commit;
#   3. runs the collision fences on that result;
#   4. runs the affected tests on the UNION of the branch's diff and main's
#      diff since the merge base, on the merge result (CIV-32, task 3277).
#      The two sides need not share a file: PR A makes an argument required,
#      PR B calls the function without it, and no file overlaps.
#      A test that fails here and passes on the branch alone blocks. The run
#      sits under PREMERGE_TEST_TIMEOUT seconds (default 600; merge_bot allows 900 in all). A timeout is
#      not a red: the script says UNPROVEN and still exits 0. It prints the
#      minutes it took;
#   5. aborts the merge, so the worktree is as it was.
# `gh pr merge --squash` lands the same tree that step 2 built. So a pass
# tests what lands, with no second CI run.
#
# DEFAULT = the fences plus the union affected run (CIV-32). It runs only when
# the branch is behind main. A branch that contains main exits at once, as
# pr-check tested exactly what lands. Off: PREMERGE_MERGE_TEST=0 or --no-tests.
#
# EXIT 0: merge now. EXIT 1: merge origin/main into the branch, fix, push, and
# wait for pr-check. Fence: tests/unit/test_premerge.py.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

# CIV-32 (task 3277): the merge test is ON by default. PREMERGE_MERGE_TEST=0 or
# --no-tests turns it off. --with-tests stays as a no-op alias, for old callers.
merge_test="${PREMERGE_MERGE_TEST:-1}"
for arg in "$@"; do
  case "$arg" in
    --with-tests) merge_test=1 ;;
    --no-tests) merge_test=0 ;;
    *) printf 'premerge: FAIL. unknown argument %s. The flags are --no-tests and --with-tests.
' "$arg" >&2; exit 1 ;;
  esac
done
started=$SECONDS

FENCES=(
  tests/unit/test_migration_prefixes.py
  tests/unit/test_handoff_queue.py
  tests/unit/test_decision_ids.py
  tests/unit/test_fence_lists.py
  tests/unit/test_index_completeness.py
  tests/unit/test_doc_names_resolve.py
  # CIV-25 2c: exact-list pins that every feature PR edits (green alone, red together).
  tests/unit/test_projects_chat_x_pin.py
  tests/unit/test_index_union.py
  tests/unit/test_chat_parity.py
)
BASE="${PREMERGE_BASE:-origin/main}"

say()  { printf 'premerge: %s\n' "$*"; }
fail() { printf 'premerge: FAIL. %s\n' "$*" >&2; exit 1; }

cd "$(git rev-parse --show-toplevel)"

if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "the worktree has uncommitted changes. Commit them first: the trial merge must not touch them."
fi
git rev-parse -q --verify MERGE_HEAD >/dev/null && fail "a merge is already in progress here."

[ "${PREMERGE_SKIP_FETCH:-0}" = 1 ] || git fetch -q origin "${BASE#origin/}"
base="$(git merge-base HEAD "$BASE")"

# ── 0. A reverted patch does not land again unchanged (CIV-31) ─────────────
# The automatic revert (scripts/home_server/auto_revert.py) puts the trailer
# "Auto-Revert: <sha>" on main. A branch with the same patch as such a
# commit lands the break again. So does a revert of the revert. This check
# runs before the early exit, because a branch that contains main can hold
# such a patch. -U0: the patch has no context lines, so an edit near the
# reverted lines on main does not hide a re-land. Any change to the changed
# lines lets it through. Only a merge through this script runs the check.
# Fence: tests/unit/test_auto_revert.py.
patch_id() { git diff -U0 --no-color --no-ext-diff "$1" "$2" | git patch-id --stable | cut -d' ' -f1; }
mine="$(patch_id "$base" HEAD)"
if [ -n "$mine" ]; then
  while IFS= read -r gone; do
    [ -n "$gone" ] || continue
    git cat-file -e "${gone}^{commit}" 2>/dev/null \
      || fail "$BASE names the reverted commit ${gone:0:12}, and this clone does not have it. Fetch, then run this again."
    if [ "$(patch_id "${gone}^" "$gone")" = "$mine" ]; then
      fail "this branch lands the patch of ${gone:0:12} again, unchanged. The automatic revert took it out of $BASE because it broke tests (CIV-31). Fix the cause, change the patch, and push."
    fi
  done < <(git log "$BASE" --since=90.days --format=%B --grep='Auto-Revert: ' \
    | sed -nE 's/^Auto-Revert: ([0-9a-f]{40})[[:space:]]*$/\1/p' | sort -u)
fi

behind="$(git rev-list --count "HEAD..$BASE")"
if [ "$behind" = 0 ]; then
  say "the branch contains $BASE. pr-check tested what will land. Merge now."
  exit 0
fi
say "the branch is $behind commit(s) behind $BASE"

# ── 1. Migration numbers (R1) ───────────────────────────────────────────────
num() { basename "$1" | { grep -oE '^[0-9]+' || true; } | sed 's/^0*//'; }
say "R1: reading the highest migration number on $BASE"
# One pipeline, not one subshell per file: about 340 files took 45 min on a
# loaded PC (CIV-12b, task 1709). Leading zeros go, so 007 reads as 7.
main_max="$(git ls-tree --name-only "$BASE" infra/postgres/ \
  | sed -nE 's#^.*/0*([0-9]+)_[^/]*\.sql$#\1#p' | sort -n | tail -1)"
main_max="${main_max:-0}"
while IFS= read -r f; do
  [ -n "$f" ] || continue
  n="$(num "$f")"
  [ -n "$n" ] || continue
  if [ "$n" -le "$main_max" ]; then
    fail "migration $(basename "$f") is not above main's highest number ($main_max). Take $((main_max + 1)) (R1)."
  fi
done < <(git diff --name-only --diff-filter=A "$base" HEAD -- infra/postgres/ | grep -E '/[0-9]+_[^/]*\.sql$' || true)

# ── 2. The trial merge ──────────────────────────────────────────────────────
overlap="$(comm -12 <(git diff --name-only "$base" HEAD | sort) <(git diff --name-only "$base" "$BASE" | sort))"
logs="$(mktemp -d)"
# The trial merge is a real commit on a DETACHED head, so the tests see a
# clean, committed checkout. With an uncommitted merge, a test that compares
# a build SHA with `git rev-parse HEAD` failed for no reason (LD-3b). The EXIT
# trap aborts any merge and goes back to the branch. The trial commit is left
# unreferenced. No ref moves, and nothing is rewritten.
branch="$(git symbolic-ref -q --short HEAD)" || fail "HEAD is detached. Check out the PR branch first."
back() {
  git rev-parse -q --verify MERGE_HEAD >/dev/null && git merge --abort
  [ "$(git symbolic-ref -q --short HEAD || true)" = "$branch" ] || git checkout -q "$branch"
}
trap 'back; rm -rf "$logs"; true' EXIT
# The union is read BEFORE the merge, from the two sides (CIV-32).
git diff --name-only "$base" HEAD > "$logs/pr.txt"
git diff --name-only "$base" "$BASE" > "$logs/main.txt"
sort -u "$logs/pr.txt" "$logs/main.txt" > "$logs/union.txt"
git checkout -q --detach
if ! git merge --no-ff --no-edit -q "$BASE" > "$logs/merge.log" 2>&1; then
  conflicts="$(git diff --name-only --diff-filter=U | tr '\n' ' ')"
  if [ -z "$conflicts" ]; then
    # No conflicted path: git refused before it merged, most often because an
    # untracked file here has the name of a file that main adds. Show git's words.
    sed 's/^/  /' "$logs/merge.log" >&2
    fail "git refused the trial merge (above). Move the untracked files it names, then run this again."
  fi
  fail "merging $BASE conflicts in: $conflicts. Merge it, resolve, push, and wait for pr-check."
fi

# ── 3. The collision fences, on the merge result ───────────────────────────
present=()
for t in "${FENCES[@]}"; do [ -f "$t" ] && present+=("$t"); done
say "collision fences on the merge result: ${#present[@]} files"
if [ "${#present[@]}" -gt 0 ] && ! env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider "${present[@]}" > "$logs/fences.log" 2>&1; then
  { grep -E "^(FAILED|ERROR|E )" "$logs/fences.log" || true; } | head -15 >&2
  fail "a collision fence fails once $BASE is merged in. Fix it on the branch, push, and wait for pr-check."
fi

# ── 4. Both sides changed the same files ───────────────────────────────────
# A test that fails on the merge result is a merge failure only when it PASSES
# on the branch alone (green alone, red together). A test that fails both ways
# is this machine or an older red: six tests fail on the Windows PC and pass
# in CI (LD-3b, 2026-10-30). So a failure is re-run on the branch before it
# can block. Both commands can be replaced, for tests and for other stacks.
TEST_TIMEOUT="${PREMERGE_TEST_TIMEOUT:-600}"
# premerge_select.py picks the tests that BOTH sides can affect, plus each test
# file that either side changed. The whole union took 974 test files and ran
# past 10 minutes on a 62-file union (measured 2026-11-14). The list is read
# from the merge tree, so a branch without the script falls back to a note.
sel() { env -u VIRTUAL_ENV uv run python scripts/premerge_select.py --pr "$logs/pr.txt" --main "$logs/main.txt" > "$logs/sel.txt"; }
if [ -z "${PREMERGE_AFFECTED_CMD:-}" ]; then
  if [ "$merge_test" = 1 ]; then
    if [ -f scripts/premerge_select.py ]; then
      sel || fail "premerge_select.py failed on the merge result."
    else
      say "UNPROVEN. scripts/premerge_select.py is not in the merge result, so no test was picked. Merge $BASE once the script has landed on it."
      merge_test=0
    fi
  fi
  AFFECTED_CMD="if [ \"\$(head -1 $logs/sel.txt)\" = FULL ] || [ ! -s $logs/sel.txt ]; then echo 'nothing to run, or both sides are FULL'; else env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider --continue-on-collection-errors -rfE \$(cat $logs/sel.txt); fi"
else
  AFFECTED_CMD="$PREMERGE_AFFECTED_CMD"
fi
RERUN_CMD="${PREMERGE_RERUN_CMD:-env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider --continue-on-collection-errors -rfE}"
# Ids at the function level: a parameter such as [Earlier reply] holds a space,
# and a cut id selects nothing on the re-run. The re-run takes whole functions.
failed_ids() { { grep -E '^(FAILED|ERROR) ' "$1" || true; } | awk '{print $2}' | sed 's/\[.*//' | sort -u; }

if [ -n "$overlap" ]; then
  say "both sides changed: $(printf '%s' "$overlap" | tr '\n' ' ')"
fi
if [ "$merge_test" = 0 ]; then
  say "the affected tests did not run (PREMERGE_MERGE_TEST=0 or --no-tests). pr-check and main run them."
else
  say "running the tests both sides can affect on the merge result: $(wc -l < "$logs/union.txt" | tr -d ' ') files in the union, $(wc -l < "$logs/sel.txt" 2>/dev/null | tr -d ' ' || true) tests selected, timeout ${TEST_TIMEOUT}s"
  rc=0
  touch "$logs/sel.txt"; timeout "$TEST_TIMEOUT" bash -c "$AFFECTED_CMD" > "$logs/affected.log" 2>&1 || rc=$?
  if [ "$rc" = 124 ]; then
    say "UNPROVEN. The affected tests passed no verdict in ${TEST_TIMEOUT}s. The merge is not shown to be red, and not shown to be green. Raise PREMERGE_TEST_TIMEOUT, or read pr-check."
  elif [ "$rc" != 0 ]; then
    merged_fail="$(failed_ids "$logs/affected.log")"
    if [ -z "$merged_fail" ]; then
      tail -20 "$logs/affected.log" >&2
      fail "the affected tests did not run cleanly on the merge result, and no test is named (log above)."
    fi
    # Back to the branch alone, then the same tests again.
    back
    # shellcheck disable=SC2086
    timeout "$TEST_TIMEOUT" bash -c "$RERUN_CMD \"\$@\"" _ $merged_fail > "$logs/rerun.log" 2>&1 || true
    alone_fail="$(failed_ids "$logs/rerun.log")"
    caused="$(comm -23 <(printf '%s\n' "$merged_fail") <(printf '%s\n' "$alone_fail"))"
    if [ -n "$caused" ]; then
      printf '  %s\n' $caused >&2
      say "the merge test took $(( (SECONDS - started + 59) / 60 )) minute(s)"
      fail "these tests pass on the branch alone and fail once $BASE is merged in. Merge $BASE into the branch, fix, push, and wait for pr-check."
    fi
    say "these tests fail on the branch alone too, so the merge did not cause them (this machine, or already red):"
    printf '  %s\n' $merged_fail
  fi
  if grep -q '^workbench/' "$logs/union.txt"; then
    say "a frontend file changed. Run the vitest command that affected_tests printed:"
    { grep -E "vitest" "$logs/affected.log" || true; } | head -3
  fi
  say "the merge test took $(( (SECONDS - started + 59) / 60 )) minute(s)"
fi

say "PASS. The merge with $BASE is clean and the fences hold. Merge now: gh pr merge --squash"
