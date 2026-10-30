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
trap 'git rev-parse -q --verify MERGE_HEAD >/dev/null && git merge --abort; rm -rf "$logs"; true' EXIT
if ! git merge --no-commit --no-ff -q "$BASE" >/dev/null 2>&1; then
  conflicts="$(git diff --name-only --diff-filter=U | tr '\n' ' ')"
  fail "merging $BASE conflicts in: ${conflicts:-unknown files}. Merge it, resolve, push, and wait for pr-check."
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
if [ -n "$overlap" ]; then
  say "both sides changed: $(printf '%s' "$overlap" | tr '\n' ' ')"
  say "running the affected tests on the merge result"
  if ! env -u VIRTUAL_ENV uv run python scripts/affected_tests.py --base "$BASE" --run > "$logs/affected.log" 2>&1; then
    tail -20 "$logs/affected.log" >&2
    fail "the affected tests fail on the merge result. Merge $BASE into the branch, fix, push, and wait for pr-check."
  fi
  if printf '%s\n' "$overlap" | grep -q '^workbench/'; then
    say "a frontend file changed on both sides. Run the vitest command that affected_tests printed:"
    grep -E "vitest" "$logs/affected.log" | head -3 || true
  fi
fi

say "PASS. The merge with $BASE is clean and the fences hold. Merge now: gh pr merge --squash"
