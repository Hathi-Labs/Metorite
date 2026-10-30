#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# premerge.sh: test the merge with TODAY's main, right before `gh pr merge` (LD-3).
#
#   bash scripts/premerge.sh      # in the PR's worktree. Commit everything first.
#
# WHY. pr-check tests the PR merged with main AS IT WAS when the run started.
# Branch protection does not ask for an up-to-date branch (strict: false), and
# there is no merge queue (owner decision, 2026-10-16). So a PR can merge green
# onto a main that moved after its run:
#   * two PRs took the same migration number, 3 times in 2 weeks (R1);
#   * two PRs took the same H-id (2026-09-23) and the same D-number (2026-10-25);
#   * two PRs went green alone and red together, twice in one day (HANDOFF H-10).
#
# WHAT. When the branch is behind origin/main, this script:
#   1. refuses a new migration numbered at or below main's highest (R1);
#   2. merges origin/main into the worktree WITHOUT a commit;
#   3. runs the collision fences on that result;
#   4. when both sides changed the same files, runs the affected tests too;
#   5. aborts the merge, so the worktree is as it was.
# `gh pr merge --squash` lands the same tree that step 2 built. So a pass
# tests what lands, with no second CI run.
#
# EXIT 0: merge now. EXIT 1: merge origin/main into the branch, fix, push, and
# wait for pr-check. Fence: tests/unit/test_premerge.py.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

FENCES=(
  tests/unit/test_migration_prefixes.py
  tests/unit/test_handoff_queue.py
  tests/unit/test_decision_ids.py
  tests/unit/test_fence_lists.py
  tests/unit/test_index_completeness.py
  tests/unit/test_doc_names_resolve.py
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

behind="$(git rev-list --count "HEAD..$BASE")"
if [ "$behind" = 0 ]; then
  say "the branch contains $BASE. pr-check tested what will land. Merge now."
  exit 0
fi
base="$(git merge-base HEAD "$BASE")"
say "the branch is $behind commit(s) behind $BASE"

# ── 1. Migration numbers (R1) ───────────────────────────────────────────────
num() { basename "$1" | { grep -oE '^[0-9]+' || true; } | sed 's/^0*//'; }
main_max=0
while IFS= read -r f; do
  n="$(num "$f")"
  if [ -n "$n" ] && [ "$n" -gt "$main_max" ]; then main_max="$n"; fi
done < <(git ls-tree --name-only "$BASE" infra/postgres/ | grep -E '/[0-9]+_[^/]*\.sql$' || true)
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
AFFECTED_CMD="${PREMERGE_AFFECTED_CMD:-env -u VIRTUAL_ENV uv run python scripts/affected_tests.py --base $BASE --run -pno:cacheprovider --continue-on-collection-errors -rfE}"
RERUN_CMD="${PREMERGE_RERUN_CMD:-env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider --continue-on-collection-errors -rfE}"
# Ids at the function level: a parameter such as [Earlier reply] holds a space,
# and a cut id selects nothing on the re-run. The re-run takes whole functions.
failed_ids() { { grep -E '^(FAILED|ERROR) ' "$1" || true; } | awk '{print $2}' | sed 's/\[.*//' | sort -u; }

if [ -n "$overlap" ]; then
  say "both sides changed: $(printf '%s' "$overlap" | tr '\n' ' ')"
  say "running the affected tests on the merge result"
  if ! bash -c "$AFFECTED_CMD" > "$logs/affected.log" 2>&1; then
    merged_fail="$(failed_ids "$logs/affected.log")"
    if [ -z "$merged_fail" ]; then
      tail -20 "$logs/affected.log" >&2
      fail "the affected tests did not run cleanly on the merge result, and no test is named (log above)."
    fi
    # Back to the branch alone, then the same tests again.
    back
    # shellcheck disable=SC2086
    bash -c "$RERUN_CMD \"\$@\"" _ $merged_fail > "$logs/rerun.log" 2>&1 || true
    alone_fail="$(failed_ids "$logs/rerun.log")"
    caused="$(comm -23 <(printf '%s\n' "$merged_fail") <(printf '%s\n' "$alone_fail"))"
    if [ -n "$caused" ]; then
      printf '  %s\n' $caused >&2
      fail "these tests pass on the branch alone and fail once $BASE is merged in. Merge $BASE into the branch, fix, push, and wait for pr-check."
    fi
    say "these tests fail on the branch alone too, so the merge did not cause them (this machine, or already red):"
    printf '  %s\n' $merged_fail
  fi
  if printf '%s\n' "$overlap" | grep -q '^workbench/'; then
    say "a frontend file changed on both sides. Run the vitest command that affected_tests printed:"
    { grep -E "vitest" "$logs/affected.log" || true; } | head -3
  fi
fi

say "PASS. The merge with $BASE is clean and the fences hold. Merge now: gh pr merge --squash"
