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
#   2. merges origin/main into a detached trial commit (no ref moves);
#   3. runs the collision fences on that result;
#   4. runs the tests that BOTH sides can affect (the branch's diff and main's
#      diff since the merge base), on the merge result (CIV-MT, task 3277).
#      The two sides need not share a file: PR A makes an argument required,
#      PR B calls the function without it, and no file overlaps.
#      A test that fails here and passes on the branch alone blocks. The run
#      sits under PREMERGE_TEST_TIMEOUT seconds (default 420; merge_bot allows 900 in all). A timeout is
#      not a red: the script says UNPROVEN and still exits 0. It prints the
#      seconds each stage took (the lines `stage <name>: N s`);
#   5. aborts the merge, so the worktree is as it was.
# `gh pr merge --squash` lands the same tree that step 2 built. So a pass
# tests what lands, with no second CI run.
#
# DEFAULT = the fences plus the tests that both sides can affect (CIV-MT). It runs only when
# the branch is behind main. A branch that contains main exits at once, as
# pr-check tested exactly what lands. Off: PREMERGE_MERGE_TEST=0 or --no-tests.
#
# EXIT 0: merge now. EXIT 1: merge origin/main into the branch, fix, push, and
# wait for pr-check. Fence: tests/unit/test_premerge.py.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

# CIV-MT (task 3277): the merge test is ON by default. PREMERGE_MERGE_TEST=0 or
# --no-tests turns it off. --with-tests stays as a no-op alias, for old callers.
merge_test=1
case "${PREMERGE_MERGE_TEST:-1}" in 0|false|no|off|FALSE|NO|OFF) merge_test=0 ;; esac
for arg in "$@"; do
  case "$arg" in
    --with-tests) merge_test=1 ;;
    --no-tests) merge_test=0 ;;
    *) printf 'premerge: FAIL. unknown argument %s. The flags are --no-tests and --with-tests.
' "$arg" >&2; exit 1 ;;
  esac
done
started=$SECONDS
# One budget for the whole run (CIV-MT-3, 2026-11-15: on the merger PC the default ran past
# 1000 s, above the 900 s of merge_bot). Each stage gets what is left, never more than its own
# cap, and says how long it took. A stage that has no time left says UNPROVEN and is skipped.
BUDGET="${PREMERGE_TOTAL_BUDGET:-760}"
remaining() { echo $(( BUDGET - (SECONDS - started) )); }
stage_t=$SECONDS
# `timeout -k` ends with 137 when the child needed the KILL: that is a timeout too.
timed_out() { [ "$1" = 124 ] || [ "$1" = 137 ]; }
# What must stay for the stages after the type check: the picker (at least 60 s), the tests
# (at least 60 s) and a little more for the checkouts and the fetch.
RESERVE="${PREMERGE_RESERVE:-200}"
stage() { say "stage $1: $(( SECONDS - stage_t )) s (total $(( SECONDS - started )) s of $BUDGET)"; stage_t=$SECONDS; }

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
LEDGER_CHECK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/migration_ledger_check.py"  # before any cd
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

# ── 0b. Migration numbers against main and the box ledger (SW-PRE-2, R1) ───
# This runs on EVERY merge, before the early exit. A branch that contains main
# can still hold a number that the ledger booked for another pull request. The
# ledger is ~/lanes/migration-numbers.tsv, written only by reserve_migration.sh.
# WARNING ONLY, by design. The verifier of PR 2816 proved that a hard fail is wrong:
# a closed predecessor PR of the same task still owns the row, a row with no PR
# ("-") never matches, and `gh` may give no PR at all. A false fail blocks a good
# merge, so this step names the next free number and lets step 1 stay the gate.
# The number at or below main's top is refused by step 1, with its own message.
# Fence: tests/unit/test_migration_reservation.py.
ledger="${PREMERGE_LEDGER:-${HOME:-/nonexistent}/lanes/migration-numbers.tsv}"
pr_no="${PREMERGE_PR:-}"
if [ -z "$pr_no" ] && command -v gh >/dev/null 2>&1; then
  pr_no="$(timeout 20 gh pr view --json number --jq .number 2>/dev/null || true)"
fi
added_nums="$(git diff --name-only --diff-filter=A "$base" HEAD -- infra/postgres/ \
  | sed -nE 's#^.*/0*([0-9]+)_[^/]*\.sql$#\1#p' | sort -n | tr '\n' ' ')"
if [ -n "$added_nums" ]; then
  top_now="$(git ls-tree --name-only "$BASE" infra/postgres/ \
    | sed -nE 's#^.*/0*([0-9]+)_[^/]*\.sql$#\1#p' | sort -n | tail -1)"
  say "R1: migration number(s) ${added_nums}against main's highest ${top_now:-0} and the ledger"
  # shellcheck disable=SC2086
  python3 "$LEDGER_CHECK" --main-max "${top_now:-0}" --ledger "$ledger" --ledger-only \
    ${pr_no:+--pr "$pr_no"} $added_nums >&2 \
    || say "WARNING: the ledger lists a number of this branch for another pull request. It does NOT block the merge: a closed predecessor PR of the same task shows up here too. Check the lines above."
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
# The union is read BEFORE the merge, from the two sides (CIV-MT).
git diff --no-renames --name-only "$base" HEAD > "$logs/pr.txt"
git diff --no-renames --name-only "$base" "$BASE" > "$logs/main.txt"
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
fence_rc=0
if [ "${#present[@]}" -gt 0 ]; then
  timeout -k 10 "${PREMERGE_FENCE_TIMEOUT:-240}" env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider "${present[@]}" > "$logs/fences.log" 2>&1 || fence_rc=$?
fi
if timed_out "$fence_rc"; then
  fail "the collision fences did not finish in ${PREMERGE_FENCE_TIMEOUT:-240}s. This machine is slow or a fence hangs: run them by hand, or raise PREMERGE_FENCE_TIMEOUT."
elif [ "$fence_rc" != 0 ]; then
  # CIV-15b (task 2187): an INTERNALERROR or "no tests ran" has no FAILED line, so name them too.
  { grep -E "^(FAILED|ERROR|E |INTERNALERROR)|no tests ran" "$logs/fences.log" || true; } | head -15 >&2
  fail "a collision fence fails once $BASE is merged in. Fix it on the branch, push, and wait for pr-check."
fi
stage "collision fences"

# ── 3b. The type check, on the merge result (CIV-MT-2, task 3369) ──────────
# 2026-11-15: PR 2354 made `layout` and `regions` required on PageModel, PR 2295 added a
# test that builds a PageModel without them. Each passed `tsc` alone. After both merged,
# main's `tsc --noEmit` failed (TS2739), and premerge ran no `tsc`. So when the two
# sides touch the front end, `tsc` runs on the merge result. An error that `tsc` also
# reports on $BASE alone is not the merge's (main red must not block every PR).
# Off with PREMERGE_TSC=0 (or the merge test off). A missing node_modules gets one `npm ci`.
TSC_TIMEOUT="${PREMERGE_TSC_TIMEOUT:-240}"
TSC_CMD="${PREMERGE_TSC_CMD:-npx tsc --noEmit -p tsconfig.json}"
tsc_errors() { { grep -E 'error TS' "$1" || true; } | tr -d '\r' | sed -E 's/\([0-9]+,[0-9]+\)//' | sort -u; }
run_tsc() {
  local cap=$(( $(remaining) - RESERVE ))
  [ "$cap" -gt "$TSC_TIMEOUT" ] && cap=$TSC_TIMEOUT
  if [ "$cap" -lt 20 ]; then echo "no time left for the type check" > "$1"; return 124; fi
  ( cd workbench/control_plane && NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=4096}" \
      timeout -k 10 "$cap" bash -c "$TSC_CMD" ) > "$1" 2>&1
}
if [ "$merge_test" = 1 ] && [ "${PREMERGE_TSC:-1}" != 0 ] \
   && grep -qE '^workbench/control_plane/.*\.(ts|tsx|mts|cts|js|mjs|json)$' "$logs/union.txt"; then
  tsc_started=$SECONDS
  # The type check needs time for the baseline run too: keep 120 s for the tests, at least.
  tsc_cap=$(( $(remaining) - RESERVE ))
  if [ "$tsc_cap" -lt 60 ]; then
    say "UNPROVEN. The type check did not run: only $(( $(remaining) > 0 ? $(remaining) : 0 )) s of the budget are left. pr-check is the evidence."
    tsc_cap=0
  fi
  # A lockfile change on either side makes an old node_modules wrong (a new dependency would
  # fail with TS2307 here and pass in pr-check), so it gets the same npm ci.
  npm_cap="${PREMERGE_NPM_TIMEOUT:-240}"
  [ "$npm_cap" -gt $(( tsc_cap / 2 )) ] && npm_cap=$(( tsc_cap / 2 ))
  if [ "$tsc_cap" != 0 ] && { [ ! -d workbench/control_plane/node_modules ] || grep -q '^workbench/control_plane/package-lock\.json$' "$logs/union.txt"; }; then
    say "type check: no node_modules here, or a lockfile changed: running npm ci (at most ${npm_cap}s)"
    ( cd workbench/control_plane && timeout -k 10 "$npm_cap" npm ci --no-audit --no-fund ) > "$logs/npm.log" 2>&1 || true
  fi
  if [ "$tsc_cap" = 0 ]; then
    :  # said above: no time left
  elif [ ! -d workbench/control_plane/node_modules ]; then
    say "UNPROVEN. The type check did not run: no node_modules here, and npm ci did not finish. pr-check is the evidence."
  else
    say "type check on the merge result (a front-end file changed), at most ${TSC_TIMEOUT}s"
    tsc_rc=0; run_tsc "$logs/tsc.log" || tsc_rc=$?
    if [ "$tsc_rc" = 0 ]; then
      say "type check passed"
    elif timed_out "$tsc_rc"; then
      say "UNPROVEN. The type check hit its time cap (at most ${TSC_TIMEOUT}s) with no verdict. pr-check is the evidence."
    else
      merged_errors="$(tsc_errors "$logs/tsc.log")"
      if [ -z "$merged_errors" ]; then
        tail -8 "$logs/tsc.log" >&2
        say "UNPROVEN. The type check failed with no type error (log above): this machine, not the merge."
      else
        merge_head="$(git rev-parse HEAD)"
        git checkout -q --detach "$BASE"
        base_rc=0; run_tsc "$logs/tsc_main.log" || base_rc=$?
        git checkout -q --detach "$merge_head"
        base_errors="$(tsc_errors "$logs/tsc_main.log")"
        caused="$(comm -23 <(printf '%s\n' "$merged_errors") <(printf '%s\n' "$base_errors"))"
        if timed_out "$base_rc" || { [ "$base_rc" != 0 ] && [ -z "$base_errors" ]; }; then
          # The baseline run did not give a verdict (timeout, crash). It cannot clear or blame.
          say "UNPROVEN. The type check of $BASE alone gave no verdict (exit $base_rc), so the errors of the merge cannot be compared. pr-check is the evidence."
        elif [ -n "$caused" ]; then
          say "the type check took $(( SECONDS - tsc_started )) s"
          printf '%s\n' "$caused" | head -15 >&2
          fail "type errors that exist on the merge of the branch and $BASE and not on $BASE alone. Merge $BASE into the branch, fix, push, and wait for pr-check."
        else
          say "the type errors also exist on $BASE alone, so the merge did not cause them:"
          printf '%s\n' "$merged_errors" | head -5 | sed 's/^/  /'
        fi
      fi
    fi
  fi
  stage "type check"
fi

# ── 4. Both sides changed the same files ───────────────────────────────────
# A test that fails on the merge result is a merge failure only when it PASSES
# on the branch alone (green alone, red together). A test that fails both ways
# is this machine or an older red: six tests fail on the Windows PC and pass
# in CI (LD-3b, 2026-10-30). So a failure is re-run on the branch before it
# can block. Both commands can be replaced, for tests and for other stacks.
# Budget: the callers allow 900 s in all (merge_bot.sh, lane-merge.md). PREMERGE_TOTAL_BUDGET
# (760 s) covers every stage. Each stage gets what is left, minus what the later stages need
# (RESERVE for the type check, 70 s for the tests when the picker runs, two re-runs for the
# tests), and never more than its own cap. The rest (fetch, merge, checkouts) is about 40 s.
TEST_TIMEOUT="${PREMERGE_TEST_TIMEOUT:-420}"
RERUN_TIMEOUT="${PREMERGE_RERUN_TIMEOUT:-120}"
# The test cap is set after the picker ran: it is what is left of PREMERGE_TOTAL_BUDGET,
# minus one re-run, and never more than PREMERGE_TEST_TIMEOUT.
both_full=0
# premerge_select.py picks the tests that BOTH sides can affect, plus each test
# file that either side changed, those first. The whole union took 974 test files
# and ran past 10 minutes on a 62-file union (measured 2026-11-14).
sel() {
  # shellcheck disable=SC2086
  timeout -k 10 "$picker_cap" ${PREMERGE_PY:-env -u VIRTUAL_ENV uv run python} scripts/premerge_select.py --pr "$logs/pr.txt" --main "$logs/main.txt" \
    --first-out "$logs/first.txt" --note-out "$logs/note.txt" > "$logs/sel.txt" || return $?
  # Git Bash keeps a CR that a Windows Python writes, and a CR on a path stops pytest
  # with "file not found" (review of 2026-11-15). Strip it from every list we read.
  for f in sel first note; do tr -d '\r' < "$logs/$f.txt" > "$logs/$f.lf" && mv "$logs/$f.lf" "$logs/$f.txt"; done
}
: > "$logs/sel.txt"; : > "$logs/note.txt"; echo 0 > "$logs/first.txt"
no_picker=0; picker_timeout=0; no_time=0
PYT="${PREMERGE_PYTEST_CMD:-env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider --continue-on-collection-errors -rfE}"
if [ -z "${PREMERGE_AFFECTED_CMD:-}" ]; then
  if [ "$merge_test" = 1 ]; then
    if [ -f scripts/premerge_select.py ]; then
      # The picker gets what is left minus 70 s for the tests, and at most its own cap.
      picker_cap=$(( $(remaining) - 70 ))
      [ "$picker_cap" -gt "${PREMERGE_PICKER_TIMEOUT:-150}" ] && picker_cap="${PREMERGE_PICKER_TIMEOUT:-150}"
      sel_rc=0
      if [ "$picker_cap" -lt 20 ]; then sel_rc=124; else sel || sel_rc=$?; fi
      if timed_out "$sel_rc"; then
        say "UNPROVEN. The picker had no verdict within ${picker_cap}s, so no test was picked and none ran. This machine is slow: raise PREMERGE_PICKER_TIMEOUT or PREMERGE_TOTAL_BUDGET, or read pr-check."
        picker_timeout=1; merge_test=0
        : > "$logs/sel.txt"; echo 0 > "$logs/first.txt"
      elif [ "$sel_rc" != 0 ]; then
        fail "premerge_select.py failed on the merge result."
      fi
      if [ "$(head -1 "$logs/sel.txt")" = FULL ]; then
        # Both sides are FULL: pr-check and main own the full suite. Run only the changed tests.
        both_full=1; sed -i 1d "$logs/sel.txt"
        grep -c . "$logs/sel.txt" > "$logs/first.txt" || echo 0 > "$logs/first.txt"
      fi
    else
      no_picker=1; merge_test=0
    fi
  fi
  # Phase 1: the test files that either side changed. Phase 2: the rest. Both
  # phases always run, so a red of phase 1 that is only this machine hides nothing.
  # An empty list runs NOTHING: pytest with no path runs the whole tree.
  AFFECTED_CMD="rc=0; n=\$(cat $logs/first.txt 2>/dev/null || echo 0); total=\$(grep -c . $logs/sel.txt || true); if [ \"\$total\" -eq 0 ]; then echo 'nothing to run'; exit 0; fi; if [ \"\$n\" -gt 0 ]; then $PYT \$(head -\$n $logs/sel.txt) || rc=1; fi; if [ \"\$total\" -gt \"\$n\" ]; then $PYT \$(tail -n +\$((n+1)) $logs/sel.txt) || rc=1; fi; exit \$rc"
else
  AFFECTED_CMD="$PREMERGE_AFFECTED_CMD"
fi
stage "picker"
if [ "$merge_test" = 1 ]; then
  cap=$(( $(remaining) - 2 * RERUN_TIMEOUT ))
  if [ "$cap" -lt 60 ]; then
    no_time=1; merge_test=0
  elif [ "$cap" -lt "$TEST_TIMEOUT" ]; then
    TEST_TIMEOUT=$cap
  fi
fi
RERUN_CMD="${PREMERGE_RERUN_CMD:-$PYT}"
# Ids at the function level: a parameter such as [Earlier reply] holds a space,
# and a cut id selects nothing on the re-run. The re-run takes whole functions.
failed_ids() { { grep -E '^(FAILED|ERROR) ' "$1" || true; } | tr -d '\r' | awk '{print $2}' | sed 's/\[.*//' | sort -u; }
# rerun_fail <log>: the ids that fail in THIS checkout. An id whose file is not
# here gives no evidence (the file is new on the other side), so it is neither run
# nor counted: pytest stops on a missing path and would hide the other ids.
rerun_fail() {
  local log="$1" id file name here=""
  for id in $merged_fail; do
    file="${id%%::*}"; name="${id##*::}"
    # No file, or no such function, on this side: no evidence. pytest stops on a
    # missing id (exit 4) and runs nothing, so such an id is neither run nor counted.
    # An id with no `::` is a collection error (the whole file): the file alone decides.
    if [ "$file" = "$id" ]; then [ -f "$file" ] && here="$here $id"
    else [ -f "$file" ] && grep -qE "def[[:space:]]+${name}\b" "$file" && here="$here $id"; fi
  done
  [ -n "$here" ] || { : > "$log"; return 0; }
  local rc=0 rcap=$RERUN_TIMEOUT
  [ "$rcap" -gt $(( $(remaining) - 20 )) ] && rcap=$(( $(remaining) - 20 ))
  if [ "$rcap" -lt 15 ]; then
    say "UNPROVEN in part. No time is left for a re-run on $BASE, so these tests count as passing there and the merge is blamed."
    : > "$log"; return 0
  fi
  # shellcheck disable=SC2086
  timeout -k 10 "$rcap" bash -c "$RERUN_CMD \"\$@\"" _ $here > "$log" 2>&1 || rc=$?
  timed_out "$rc" && say "UNPROVEN in part. A re-run hit its cap (${rcap}s), so the tests it did not reach count as passing there and the merge is blamed."
  failed_ids "$log"
}

if [ -n "$overlap" ]; then
  say "both sides changed: $(printf '%s' "$overlap" | tr '\n' ' ')"
fi
if [ "$no_time" = 1 ]; then
  say "UNPROVEN. No test ran: only $(( $(remaining) > 0 ? $(remaining) : 0 )) s of the ${BUDGET} s budget are left after the fences, the type check and the picker. Raise PREMERGE_TOTAL_BUDGET if the caller allows more, or read pr-check."
elif [ "$picker_timeout" = 1 ]; then
  :  # said above
elif [ "$no_picker" = 1 ]; then
  say "UNPROVEN. scripts/premerge_select.py is not in the merge result, so no test was picked. Merge $BASE once the script has landed on it."
elif [ "$merge_test" = 0 ]; then
  say "the affected tests did not run (PREMERGE_MERGE_TEST=0 or --no-tests). pr-check and main run them."
else
  say "running the tests both sides can affect on the merge result: $(wc -l < "$logs/union.txt" | tr -d ' ') files in the union, $( (wc -l < "$logs/sel.txt" 2>/dev/null || echo 0) | tr -d ' ') tests selected, timeout ${TEST_TIMEOUT}s"
  picked_n="$(grep -c . "$logs/sel.txt" || true)"
  if [ "$picked_n" = 0 ] && [ -z "${PREMERGE_AFFECTED_CMD:-}" ]; then
    say "UNPROVEN. The selector picked no test, so NO test ran on the merge result. Nothing is proven here. pr-check is the evidence."
  elif [ "$both_full" = 1 ]; then
    say "UNPROVEN in part. Both sides changed a file that makes every test affected. Only the test files that a side changed ran."
  fi
  [ -s "$logs/note.txt" ] && [ "$both_full" = 0 ] && grep -q . "$logs/note.txt" \
    && say "UNPROVEN in part. The selector says every test is affected by: $(cat "$logs/note.txt"). The other side's tests ran alone."
  rc=0
  timeout -k 10 "$TEST_TIMEOUT" bash -c "$AFFECTED_CMD" > "$logs/affected.log" 2>&1 || rc=$?
  merged_fail="$(failed_ids "$logs/affected.log")"
  if timed_out "$rc" && [ -z "$merged_fail" ]; then
    say "UNPROVEN. The affected tests passed no verdict in ${TEST_TIMEOUT}s. The merge is not shown to be red, and not shown to be green. Raise PREMERGE_TEST_TIMEOUT, or read pr-check."
  elif [ "$rc" != 0 ]; then
    if [ -z "$merged_fail" ]; then
      tail -20 "$logs/affected.log" >&2
      fail "the affected tests did not run cleanly on the merge result, and no test is named (log above)."
    fi
    # A red on the merge result is the merge's fault only when the test passes on
    # the branch alone AND on main alone. Red on main is not this PR's doing.
    back
    alone_fail="$(rerun_fail "$logs/rerun.log")"
    git checkout -q --detach "$BASE"
    main_fail="$(rerun_fail "$logs/rerun_main.log")"
    back
    caused="$(comm -23 <(printf '%s\n' "$merged_fail") <(printf '%s\n%s\n' "$alone_fail" "$main_fail" | sort -u))"
    if [ -n "$caused" ]; then
      printf '  %s\n' $caused >&2
      stage "tests (a red)"
      fail "these tests pass on the branch alone (or are not there) and pass on $BASE alone, and fail once the two are merged. Merge $BASE into the branch, fix, push, and wait for pr-check."
    fi
    say "these tests fail on the branch alone or on $BASE alone, so the merge did not cause them (this machine, or already red):"
    printf '  %s\n' $merged_fail
  fi
  timed_out "$rc" && [ -n "$merged_fail" ] && say "UNPROVEN in part. The run hit ${TEST_TIMEOUT}s after the failures above, so the tests behind them did not finish."
  if grep -q '^workbench/' "$logs/union.txt"; then
    say "a frontend file changed. pr-check runs vitest. To run it here:"
    # shellcheck disable=SC2046
    { timeout -k 5 30 env -u VIRTUAL_ENV uv run python scripts/affected_tests.py $(cat "$logs/union.txt") 2>/dev/null | grep -E "^frontend:" || true; } | head -3 | sed 's/^/  /'
  fi
  stage "tests"
fi

say "PASS. The merge with $BASE is clean and the fences hold. Merge now: gh pr merge --squash"
