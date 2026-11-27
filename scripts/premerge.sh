#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# premerge.sh: test the merge with TODAY's main, right before `gh pr merge` (LD-3).
#
#   bash scripts/premerge.sh               # in the PR's worktree. Commit everything first.
#   bash scripts/premerge.sh --no-tests    # collision fences only (same as PREMERGE_MERGE_TEST=0)
#   bash scripts/premerge.sh --auto-tests  # the merge test only for a migration or an R8 suite
#                                          # (same as PREMERGE_MERGE_TEST=auto)
#
# THREE MODES (engineering_practice.md 7b.9, LBB-5a, task 4046). The last flag wins.
#   1     the default. Step 4 always runs on a behind head. The merge bot keeps it.
#   0     --no-tests: the fences and the trial merge only. No type check, no step 4.
#   auto  --auto-tests: the fences, the type check and the picker always run. Step 4
#         runs only when a side adds or changes a migration (infra/postgres/ or
#         infra/customer_console/), or when the picker's list holds an R8 suite
#         (`affected_tests.py --r8`). An R8 suite needs both database variables.
#         A step with no answer FAILs. A cap hit at the step's own cap is UNPROVEN
#         (exit 0), except at the picker and at `--r8`: a cap hit there FAILs.
#   WHO MAY USE auto: a merger that runs --no-tests today. For it, auto is stricter.
#   A switch of the merge bot, or of a merger that runs the default, to auto
#   loosens the gate. That switch, and a change of the default, is the owner's.
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
#      sits under PREMERGE_TEST_TIMEOUT seconds (default 420; merge_bot allows 900 in all). A timeout
#      at the cap that main's version gives is not a red: the script says UNPROVEN and still
#      exits 0. A cap that the budget cut below it, and a picker with no verdict, FAIL. It prints
#      the seconds each stage took (the lines `stage <name>: N s`);
#   5. aborts the merge, so the worktree is as it was.
# `gh pr merge --squash` lands the same tree that step 2 built. So a pass
# tests what lands, with no second CI run.
#
# DEFAULT = the fences plus the tests that both sides can affect (CIV-MT). It runs only when
# the branch is behind main. A branch that contains main exits at once, as
# pr-check tested exactly what lands. Off: PREMERGE_MERGE_TEST=0 or --no-tests.
#
# EXIT 0: merge now. EXIT 1: merge origin/main into the branch, fix, push, and
# wait for pr-check. EXIT 75: the release freeze (step 00). Wait, and run it again
# later. Fence: tests/unit/test_premerge.py.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

# CIV-MT (task 3277): the merge test is ON by default. PREMERGE_MERGE_TEST=0 or
# --no-tests turns it off. --with-tests resets the mode to the default (old callers use it).
# LBB-5a (task 4046): PREMERGE_MERGE_TEST=auto or --auto-tests. In auto, merge_test stays 1,
# so the type check and the picker run as in the default. Only step 4 asks auto first.
merge_test=1
auto=0
case "${PREMERGE_MERGE_TEST:-1}" in
  0|false|no|off|FALSE|NO|OFF) merge_test=0 ;;
  auto|AUTO) auto=1 ;;
esac
for arg in "$@"; do
  case "$arg" in
    --with-tests) merge_test=1; auto=0 ;;
    --no-tests) merge_test=0; auto=0 ;;
    --auto-tests) merge_test=1; auto=1 ;;
    *) printf 'premerge: FAIL. unknown argument %s. The flags are --no-tests, --auto-tests and --with-tests.
' "$arg" >&2; exit 1 ;;
  esac
done
started=$SECONDS
# One budget for the whole run (CIV-MT-3, 2026-11-15: on the merger PC the default ran past
# 1000 s, above the 900 s of merge_bot). Each stage gets what is left, never more than its own
# cap, and says how long it took. The budget must never pass a merge that main's version (no
# budget) blocks (verifier of PR 2407, round 2). So these FAIL with exit 1 and no verdict: a
# stage that has no time left to start, a stage that hits a cap the budget cut below its own,
# a run that the system killed before its cap (exit 137), and a picker with no verdict (cap
# hit, killed, or no time). UNPROVEN with exit 0 stays only where main has it: a tsc run at
# its own full cap, a test run at main's cap, no node_modules, a baseline crash.
# The tests get exactly the cap that main gives them (main_cap, at least 60 s), and the picker
# takes its time from the rest. The callers allow 900 s, so the 140 s above the budget hold it.
BUDGET="${PREMERGE_TOTAL_BUDGET:-760}"
remaining() { echo $(( BUDGET - (SECONDS - started) )); }
stage_t=$SECONDS
# `timeout -k` ends with 137 when the child needed the KILL: that is a timeout too.
timed_out() { [ "$1" = 124 ] || [ "$1" = 137 ]; }
# hit_cap <rc> <start> <cap>: the run stopped at its cap: 124, or 137 once the cap had passed.
# An earlier 137 is a kill by the system (most often for lack of memory), not a cap: main
# reads it as a failure, so it FAILs here too (review of PR 2407 at d874c32dfc, P1).
hit_cap() { [ "$1" = 124 ] || { [ "$1" = 137 ] && [ $(( SECONDS - $2 )) -ge "$3" ]; }; }
killed_early() { [ "$1" = 137 ] && ! hit_cap "$@"; }
# What the merged type check leaves for the stages after it: the picker and the tests (what
# main gives them). The baseline run of tsc on $BASE keeps only 20 s,
# because its FAIL needs no later stage.
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
# out_of_time <what> [knob]: the budget left a stage with no verdict. Main's version had no
# budget and could still block here, so this is a FAIL, never a pass.
out_of_time() { fail "$1. The merge is not proven. Run premerge again when the box is quieter, or raise PREMERGE_TOTAL_BUDGET${2:+ or $2}."; }
left0() { local r; r=$(remaining); echo $(( r > 0 ? r : 0 )); }
# killed <what> <start> <cap>: a run that the system killed before its cap.
killed() { fail "$1 was killed after $(( SECONDS - $2 )) s, before its cap of $3 s (exit 137: the system killed it, most often for lack of memory). There is no verdict, so the merge is not proven. Run premerge again when the box has more free memory."; }

cd "$(git rev-parse --show-toplevel)"

if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "the worktree has uncommitted changes. Commit them first: the trial merge must not touch them."
fi
git rev-parse -q --verify MERGE_HEAD >/dev/null && fail "a merge is already in progress here."

[ "${PREMERGE_SKIP_FETCH:-0}" = 1 ] || git fetch -q origin "${BASE#origin/}"

# ── 00. The release freeze (CIV, task 3415) ────────────────────────────────
# No merge from 04:20, 12:20 and 20:20 UTC until the release run of that slot
# starts, or slot + 30 minutes. scripts/release_freeze.sh is the ONE copy of the
# rule, and merge_bot.sh runs it too. Exit 75 = frozen: wait, it is not a defect
# of the branch. Override: PREMERGE_FREEZE_OK=<task number>.
# Fence: tests/unit/test_release_freeze.py.
freeze_rc=0
bash "$(dirname "$LEDGER_CHECK")/release_freeze.sh" || freeze_rc=$?
case "$freeze_rc" in
  0) ;;
  75) exit 75 ;;
  *) fail "the release freeze check failed (exit $freeze_rc)." ;;
esac
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
    fail "migration $(basename "$f") is not above main's highest number ($main_max). Take $((main_max + 1)) (R1). lane-merge runs \`merge_bot.sh --renumber <pr> <sha>\` to fix it (CIV-RN)."
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
# run_tsc <log> [keep]: keep = the seconds of the budget that stay for later stages (RESERVE).
# It sets tsc_cut=1 when the budget, not PREMERGE_TSC_TIMEOUT, set the cap (tsc_cap_s seconds).
run_tsc() {
  local cap=$(( $(remaining) - ${2:-$RESERVE} ))
  tsc_cut=1
  [ "$cap" -ge "$TSC_TIMEOUT" ] && { cap=$TSC_TIMEOUT; tsc_cut=0; }
  tsc_cap_s=$(( cap > 0 ? cap : 0 ))
  if [ "$cap" -lt 20 ]; then echo "no time left for the type check" > "$1"; return 124; fi
  ( cd workbench/control_plane && NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=4096}" \
      timeout -k 10 "$cap" bash -c "$TSC_CMD" ) > "$1" 2>&1
}
if [ "$merge_test" = 1 ] && [ "${PREMERGE_TSC:-1}" != 0 ] \
   && grep -qE '^workbench/control_plane/.*\.(ts|tsx|mts|cts|js|mjs|json)$' "$logs/union.txt"; then
  tsc_started=$SECONDS
  # The merged run keeps RESERVE for the picker and the tests. It does not start with under 60 s.
  # Main always runs tsc, so a type check that cannot start is a FAIL (verifier of PR 2407, round 2).
  tsc_cap=$(( $(remaining) - RESERVE ))
  if [ "$tsc_cap" -lt 60 ]; then
    stage "type check"
    out_of_time "no time is left for the type check ($(left0) s of the budget, and ${RESERVE} s stay for the picker and the tests)"
  fi
  # A lockfile change on either side makes an old node_modules wrong (a new dependency would
  # fail with TS2307 here and pass in pr-check), so it gets the same npm ci.
  npm_max="${PREMERGE_NPM_TIMEOUT:-240}"; npm_cap=$npm_max
  [ "$npm_cap" -gt $(( tsc_cap / 2 )) ] && npm_cap=$(( tsc_cap / 2 ))
  if [ ! -d workbench/control_plane/node_modules ] || grep -q '^workbench/control_plane/package-lock\.json$' "$logs/union.txt"; then
    say "type check: no node_modules here, or a lockfile changed: running npm ci (at most ${npm_cap}s)"
    npm_rc=0; ( cd workbench/control_plane && timeout -k 10 "$npm_cap" npm ci --no-audit --no-fund ) > "$logs/npm.log" 2>&1 || npm_rc=$?
    if timed_out "$npm_rc" && [ "$npm_cap" -lt "$npm_max" ]; then
      stage "type check"
      out_of_time "npm ci had only ${npm_cap} s of the budget (its own cap is ${npm_max} s) and did not finish"
    fi
  fi
  if [ ! -d workbench/control_plane/node_modules ]; then
    say "UNPROVEN. The type check did not run: no node_modules here, and npm ci did not finish. pr-check is the evidence."
  else
    say "type check on the merge result (a front-end file changed), at most ${TSC_TIMEOUT}s"
    tsc_t=$SECONDS; tsc_rc=0; run_tsc "$logs/tsc.log" || tsc_rc=$?
    if [ "$tsc_rc" = 0 ]; then
      say "type check passed"
    elif killed_early "$tsc_rc" "$tsc_t" "$tsc_cap_s"; then
      stage "type check"
      killed "the type check of the merge result" "$tsc_t" "$tsc_cap_s"
    elif timed_out "$tsc_rc" && [ "$tsc_cut" = 1 ]; then
      stage "type check"
      out_of_time "the type check of the merge result had only ${tsc_cap_s} s of the budget (its own cap is ${TSC_TIMEOUT} s) and did not finish"
    elif hit_cap "$tsc_rc" "$tsc_t" "$tsc_cap_s"; then
      say "UNPROVEN. The type check hit its time cap (at most ${TSC_TIMEOUT}s) with no verdict. pr-check is the evidence."
    else
      merged_errors="$(tsc_errors "$logs/tsc.log")"
      if [ -z "$merged_errors" ]; then
        tail -8 "$logs/tsc.log" >&2
        say "UNPROVEN. The type check failed with no type error (log above): this machine, not the merge."
      else
        merge_head="$(git rev-parse HEAD)"
        git checkout -q --detach "$BASE"
        # The baseline keeps only 20 s: a FAIL needs no later stage (review of PR 2407, P1).
        base_t=$SECONDS; base_rc=0; run_tsc "$logs/tsc_main.log" 20 || base_rc=$?
        git checkout -q --detach "$merge_head"
        base_errors="$(tsc_errors "$logs/tsc_main.log")"
        caused="$(comm -23 <(printf '%s\n' "$merged_errors") <(printf '%s\n' "$base_errors"))"
        if killed_early "$base_rc" "$base_t" "$tsc_cap_s"; then
          stage "type check"
          killed "the type check of $BASE alone" "$base_t" "$tsc_cap_s"
        elif timed_out "$base_rc" && [ "$tsc_cut" = 1 ]; then
          stage "type check"
          out_of_time "the type check of $BASE alone had only ${tsc_cap_s} s of the budget (its own cap is ${TSC_TIMEOUT} s) and did not finish, so the type errors of the merge cannot be compared"
        elif hit_cap "$base_rc" "$base_t" "$tsc_cap_s" || { [ "$base_rc" != 0 ] && [ -z "$base_errors" ]; }; then
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
# (RESERVE for the merged type check, 20 s for its baseline, main_cap and 10 s for the tests
# when the picker runs, two re-runs for the tests), and never more than its own cap. The rest
# (fetch, merge, checkouts) is about 40 s.
TEST_TIMEOUT="${PREMERGE_TEST_TIMEOUT:-420}"
RERUN_TIMEOUT="${PREMERGE_RERUN_TIMEOUT:-120}"
# The test cap is main_cap, set before the picker as main sets it: what is left of
# PREMERGE_TOTAL_BUDGET minus two re-runs, never under 60 s and never more than
# PREMERGE_TEST_TIMEOUT. A timeout at that cap is UNPROVEN, exactly as on main.
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
no_picker=0
PYT="${PREMERGE_PYTEST_CMD:-env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider --continue-on-collection-errors -rfE}"
# main_cap: the test cap of main's version, which counts no picker time (it sets the cap before
# the picker). The tests get exactly this cap (review of PR 2407 at d874c32dfc, P2).
main_cap=$(( $(remaining) - 2 * RERUN_TIMEOUT ))
[ "$main_cap" -lt 60 ] && main_cap=60
[ "$main_cap" -gt "$TEST_TIMEOUT" ] && main_cap=$TEST_TIMEOUT
TEST_TIMEOUT=$main_cap
# auto: the cap of `affected_tests.py --r8`. PREMERGE_R8_TIMEOUT may lower it, never raise it.
R8_TIMEOUT=60
[ -n "${PREMERGE_R8_TIMEOUT:-}" ] && [ "$PREMERGE_R8_TIMEOUT" -lt "$R8_TIMEOUT" ] && R8_TIMEOUT=$PREMERGE_R8_TIMEOUT
if [ -z "${PREMERGE_AFFECTED_CMD:-}" ]; then
  if [ "$merge_test" = 1 ]; then
    if [ -f scripts/premerge_select.py ]; then
      # The picker gets what is left minus the tests' cap and their 10 s kill grace.
      # PREMERGE_PICKER_TIMEOUT, when set, is an upper bound. Main has no picker cap, so it
      # would run past the bot's limit (a BACK): a picker with no time or no verdict FAILs.
      picker_cap=$(( $(remaining) - main_cap - 10 ))
      # auto: the R8 list (`affected_tests.py --r8`) runs after the picker, inside the budget.
      # Its cap and its 5 s kill grace come out of the picker's time. The default is unchanged.
      [ "$auto" = 1 ] && picker_cap=$(( picker_cap - R8_TIMEOUT - 5 ))
      if [ "$picker_cap" -lt 20 ]; then
        stage "picker"
        out_of_time "no time is left for the picker ($(left0) s of the budget, and the tests need ${main_cap} s)"
      fi
      if [ -n "${PREMERGE_PICKER_TIMEOUT:-}" ] && [ "$picker_cap" -gt "$PREMERGE_PICKER_TIMEOUT" ]; then
        picker_cap=$PREMERGE_PICKER_TIMEOUT
      fi
      picker_t=$SECONDS; sel_rc=0; sel || sel_rc=$?
      if [ "$sel_rc" = 124 ]; then
        stage "picker"
        out_of_time "the picker hit its cap of ${picker_cap} s with no verdict" PREMERGE_PICKER_TIMEOUT
      elif killed_early "$sel_rc" "$picker_t" "$picker_cap"; then
        stage "picker"
        killed "the picker" "$picker_t" "$picker_cap"
      elif [ "$sel_rc" = 137 ]; then
        stage "picker"
        out_of_time "the picker did not stop at its cap of ${picker_cap} s, so it was killed (exit 137)" PREMERGE_PICKER_TIMEOUT
      elif [ "$sel_rc" != 0 ]; then
        fail "premerge_select.py failed on the merge result."
      fi
      if [ "$(head -1 "$logs/sel.txt")" = FULL ]; then
        # Both sides are FULL: pr-check and main own the full suite. Run only the changed tests.
        both_full=1; sed -i 1d "$logs/sel.txt"
        grep -c . "$logs/sel.txt" > "$logs/first.txt" || echo 0 > "$logs/first.txt"
      fi
    else
      # auto decides from the picker's list. With no picker it has no answer: FAIL, where the
      # default says UNPROVEN and passes (LBB-5a: auto is stricter than --no-tests, never looser).
      if [ "$auto" = 1 ]; then
        stage "picker"
        fail "auto: scripts/premerge_select.py is not in the merge result, so auto cannot tell which tests the merge reaches. Merge $BASE into the branch, push, and run premerge again."
      fi
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

# ── 4a. auto: does the merge add the most risk? (LBB-5a, task 4046) ────────
# Step 4 runs only when M or R is true. M: a side adds or changes a migration since the
# merge base. R: the picker's list holds an R8 suite. `affected_tests.py --r8` runs for every
# behind merge in auto, so the database rule holds also when M is true. Any step here with
# no answer FAILs: auto must never pass what --no-tests fails. Spec: engineering_practice.md 7b.9.
auto_skip=0
if [ "$auto" = 1 ]; then
  m_file="$( { grep -hE '^infra/(postgres|customer_console)/' "$logs/pr.txt" "$logs/main.txt" || true; } | head -1 || true)"
  r_file=""
  if [ -n "${PREMERGE_AFFECTED_CMD:-}" ]; then
    # A test override replaces the picker. R counts as true, so the database rule applies.
    r_file="PREMERGE_AFFECTED_CMD"
  else
    r8_cap=$(( $(remaining) - main_cap - 10 ))
    [ "$r8_cap" -gt "$R8_TIMEOUT" ] && r8_cap=$R8_TIMEOUT
    if [ "$r8_cap" -lt 5 ]; then
      stage "auto"
      out_of_time "auto: no time is left for \`affected_tests.py --r8\` ($(left0) s of the budget, and the tests need ${main_cap} s)"
    fi
    r8_t=$SECONDS; r8_rc=0
    # shellcheck disable=SC2086
    timeout -k 5 "$r8_cap" ${PREMERGE_PY:-env -u VIRTUAL_ENV uv run python} scripts/affected_tests.py --r8 \
      > "$logs/r8.raw" 2> "$logs/r8.err" || r8_rc=$?
    # A Windows Python writes CRLF and may write a backslash. Either one makes the match empty,
    # and then R reads false and step 4 never runs. Strip both, and drop blank lines.
    { tr -d '\r' < "$logs/r8.raw" | sed 's#\\#/#g' | grep -v '^[[:space:]]*$' > "$logs/r8.txt"; } || true
    if timed_out "$r8_rc"; then
      stage "auto"
      fail "auto: \`affected_tests.py --r8\` hit its cap of ${r8_cap} s after $(( SECONDS - r8_t )) s with no answer, so auto cannot tell whether the merge touches an R8 suite. Run premerge again when the box is quieter, or run it in the default mode."
    elif [ "$r8_rc" != 0 ]; then
      tail -8 "$logs/r8.err" >&2 || true
      stage "auto"
      fail "auto: \`affected_tests.py --r8\` failed (exit $r8_rc, log above), so auto cannot tell whether the merge touches an R8 suite. Run premerge in the default mode."
    elif [ ! -s "$logs/r8.txt" ]; then
      stage "auto"
      fail "auto: \`affected_tests.py --r8\` printed no file, so auto cannot tell whether the merge touches an R8 suite. Run premerge in the default mode."
    fi
    r_file="$( { grep -Fx -f "$logs/r8.txt" "$logs/sel.txt" || true; } | head -1 || true)"
  fi
  if [ -n "$m_file" ]; then
    say "auto: migration $m_file"
  fi
  if [ "$r_file" = PREMERGE_AFFECTED_CMD ]; then
    say "auto: R8 suite (PREMERGE_AFFECTED_CMD is set, so R counts as true)"
  elif [ -n "$r_file" ]; then
    say "auto: R8 suite $r_file"
  fi
  if [ -n "$r_file" ]; then
    # The database rule. An R8 file that needs an unset variable skips and proves nothing.
    unset_vars=""
    for v in CUSTOMER_CONSOLE_DATABASE_URL TENANT_LADDER_DATABASE_URL; do
      [ -n "${!v:-}" ] || unset_vars="$unset_vars $v"
    done
    if [ -n "$unset_vars" ]; then
      say "auto: not exported:$unset_vars"
      stage "auto"
      fail 'auto: the merge touches R8 suites, and the database is not exported. Run `eval "$(bash scripts/dev_db.sh --export)"`, then run premerge again. One R8 run at a time: dev_db uses one shared scratch database.'
    fi
  fi
  if [ -z "$m_file" ] && [ -z "$r_file" ]; then
    auto_skip=1
  fi
  stage "auto"
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
  # A re-run that the budget skips or that does not finish gives no proof. Its tests count
  # as passing here, so the merge is blamed. $logs/rerun_cut names what to raise (P3).
  local rc=0 rcap=$RERUN_TIMEOUT knob=PREMERGE_RERUN_TIMEOUT t
  [ "$rcap" -gt $(( $(remaining) - 20 )) ] && { rcap=$(( $(remaining) - 20 )); knob=PREMERGE_TOTAL_BUDGET; }
  if [ "$rcap" -lt 15 ]; then
    say "UNPROVEN in part. No time is left for a re-run here, so these tests count as passing here and the merge is blamed." >&2
    echo "$knob" >> "$logs/rerun_cut"
    : > "$log"; return 0
  fi
  t=$SECONDS
  # shellcheck disable=SC2086
  timeout -k 10 "$rcap" bash -c "$RERUN_CMD \"\$@\"" _ $here > "$log" 2>&1 || rc=$?
  if timed_out "$rc"; then
    say "UNPROVEN in part. A re-run did not finish (exit $rc after $(( SECONDS - t )) s, cap ${rcap}s), so the tests it did not reach count as passing here and the merge is blamed." >&2
    echo "$knob" >> "$logs/rerun_cut"
  fi
  failed_ids "$log"
}

if [ -n "$overlap" ]; then
  say "both sides changed: $(printf '%s' "$overlap" | tr '\n' ' ')"
fi
if [ "$no_picker" = 1 ]; then
  say "UNPROVEN. scripts/premerge_select.py is not in the merge result, so no test was picked. Merge $BASE once the script has landed on it."
elif [ "$auto_skip" = 1 ]; then
  # Where the default says UNPROVEN, auto says UNPROVEN too, never the skip line (review of
  # PR 2921, P3). The exit stays 0, as in the default. No test ran, so the lines say so.
  picked_n="$(grep -c . "$logs/sel.txt" || true)"
  unproven=0
  if [ "$picked_n" = 0 ]; then
    say "UNPROVEN. The selector picked no test, so NO test ran on the merge result. Nothing is proven here. pr-check is the evidence."
    unproven=1
  fi
  if [ "$both_full" = 1 ]; then
    say "UNPROVEN. Both sides changed a file that makes every test affected, and auto ran no test (no migration and no R8 suite in the merge). pr-check is the evidence."
    unproven=1
  elif [ -s "$logs/note.txt" ] && grep -q . "$logs/note.txt"; then
    say "UNPROVEN. The selector says every test is affected by: $(cat "$logs/note.txt"), and auto ran no test (no migration and no R8 suite in the merge). pr-check is the evidence."
    unproven=1
  fi
  if [ "$unproven" = 0 ]; then
    say "auto: no migration and no R8 suite in the merge. The trial merge, the fences, the type check and the green pr-check decide."
  fi
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
  rc=0; test_t=$SECONDS
  timeout -k 10 "$TEST_TIMEOUT" bash -c "$AFFECTED_CMD" > "$logs/affected.log" 2>&1 || rc=$?
  merged_fail="$(failed_ids "$logs/affected.log")"
  if killed_early "$rc" "$test_t" "$TEST_TIMEOUT"; then
    # shellcheck disable=SC2086
    [ -z "$merged_fail" ] || printf '  failed before the kill: %s\n' $merged_fail >&2
    stage "tests"
    killed "the affected test run" "$test_t" "$TEST_TIMEOUT"
  fi
  if hit_cap "$rc" "$test_t" "$TEST_TIMEOUT" && [ -z "$merged_fail" ]; then
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
      if [ -s "$logs/rerun_cut" ]; then
        fail "these tests fail once the two are merged. A re-run on the branch alone or on $BASE alone did not run or did not finish (above), so the merge is blamed with no proof that they pass there. Raise $(sort -u "$logs/rerun_cut" | paste -sd ' ' - | sed 's/ / or /g'), or merge $BASE into the branch, fix, push, and wait for pr-check."
      fi
      fail "these tests pass on the branch alone (or are not there) and pass on $BASE alone, and fail once the two are merged. Merge $BASE into the branch, fix, push, and wait for pr-check."
    fi
    say "these tests fail on the branch alone or on $BASE alone, so the merge did not cause them (this machine, or already red):"
    printf '  %s\n' $merged_fail
  fi
  hit_cap "$rc" "$test_t" "$TEST_TIMEOUT" && [ -n "$merged_fail" ] && say "UNPROVEN in part. The run hit ${TEST_TIMEOUT}s after the failures above, so the tests behind them did not finish."
  if grep -q '^workbench/' "$logs/union.txt"; then
    say "a frontend file changed. pr-check runs vitest. To run it here:"
    # shellcheck disable=SC2046
    { timeout -k 5 30 env -u VIRTUAL_ENV uv run python scripts/affected_tests.py $(cat "$logs/union.txt") 2>/dev/null | grep -E "^frontend:" || true; } | head -3 | sed 's/^/  /'
  fi
  stage "tests"
fi

say "PASS. The merge with $BASE is clean and the fences hold. Merge now: gh pr merge --squash"
