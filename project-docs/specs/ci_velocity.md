# CI velocity: the relevant tests per PR, the full suite once per batch (WS-CIV)

**Status:** ACTIVE (owner directive, 2026-11-04). Verified against code on
2026-11-04 (branch base `fb26ada52`, after a spec audit). CIV-1 and CIV-2 are
done. CIV-3 and CIV-7 are built on branch `civ3-pr-tier` (2026-11-05), and
they are done at merge. CIV-4 to CIV-6, CIV-8 and CIV-9 are AGENT-SAFE. CIV-4 is HIGH tier, because it changes the
deploy path.

**Shell manifest:** none. This is CI, not an app, so R9 does not apply.

**Owning spec** for how much CI runs, and where. `engineering_practice.md` §7a
keeps the two-speed rules. `staging_and_daily_release.md` keeps the 21:30 UTC
release. This spec links to them and adds nothing to them.

---

## 1. The rule

**Each PR proves the code it changes. The common checks run once per batch.**

- A PR runs lint, the secret scan, the type checks, the SAFETY fences, and the
  unit tests that its diff can affect. Target: about 10 minutes.
- After the merge, `main` runs the full suite in batches. While one run goes,
  new merges wait, and the next run tests them together.
- The 21:30 UTC release ships only a `main` whose full suite is green.
- The owner decision of 2026-10-16 stays: **no merge queue**. A red batch on
  `main` is fixed forward or reverted at once.

Decision: D176 (2026-11-04). Re-check the number at merge.

**The risk we accept.** Some tests read files by a glob and name no path. For
a change to a file that is not Python, the selector can miss such a test. The
batch run on `main` catches it within one run, and the session fixes forward.

## 2. Why (measured 2026-11-04)

| Fact | Number |
|---|---|
| pr-check, opened to last job, median | 40.5 min |
| The 6 unit shards, median each | 26.1 min |
| PRs merged a day (7-day mean) | about 30 |
| Docs-only PRs that still ran all 6 shards | 14% |
| Workbench-only PRs that still ran all 6 shards | 8.5% |
| Full suite again on each push to `main` | about 35 min, about 1,000 runner-min a day |
| Red pr-check runs (13 of 21) | 62% |
| Serial unit time (`.test_durations`) | 5,062 s. R8 files are 75%. |
| The slowest file (`test_crm_field_studio_r8.py`) | 598 s, the floor of any split |

GitHub gives the org 20 hosted jobs at a time. Two PR runs fill it, and a job
waited up to 22 minutes for a runner.

## 3. Scope and non-goals

**In scope:** pr-check, deploy.yml's `test` job, `scripts/affected_tests.py`,
`scripts/ci_local.sh`, a red-batch bisect, the red rate, the suite's speed.

**Non-goals:**
- No merge queue (D-2026-10-16).
- No GitHub self-hosted runner while the repo is public. A fork PR could run
  code on it. The home box takes work only from our own sessions, over
  Tailscale (task 1534).
- No change to when production moves. It moves at 21:30 UTC only (D145).
- No change to the owner's preview step for a UI slice (D145 step 1).

## 4. The tiers

| Tier | Where | What runs | Gate? |
|---|---|---|---|
| 0. Local | Home box first, this PC second (`ci_local.sh`) | The affected tests, lint, the touched app's type check | Advice before a push |
| 1. PR | GitHub (pr-check) | Lint, secret scan, type checks, SAFETY fences, the affected tests. The full set when the diff is FULL. | The 7 required checks |
| 2. Batch | GitHub (deploy.yml on push to `main`) | The full unit suite. One run at a time. Pending merges fold into the next run. | Signal. Red means fix forward now. |
| 3. Release | GitHub (deploy.yml at 21:30 UTC) | The full suite on the tip of `main` | Gate for the release |

## 5. Slices

### CIV-1 · Outside contributors need approval before Actions runs (DONE)

- **Done 2026-11-04.** The repo's fork PR policy moved from
  `first_time_contributors` to `all_external_contributors`, with
  `PUT repos/Hathi-Labs/Metorite/actions/permissions/fork-pr-contributor-approval`.
- **Verify:** `gh api repos/Hathi-Labs/Metorite/actions/permissions/fork-pr-contributor-approval --jq .approval_policy`
  prints `all_external_contributors`.
- **Fence:** advisory. It is a GitHub setting, and no repo test can read it.
- AGENT-SAFE. Task: see §7.

### CIV-2 · `affected_tests.py --gate`: the PR tier's selection, fail open

**Today (verified 2026-11-04).** `FULL_SUITE` (line 42) holds 4 names:
`pyproject.toml`, `uv.lock`, `tests/conftest.py`, `tests/unit/conftest.py`.
`HUB_LIMIT = 40` (line 45) stops only the one-level upstream expansion
(line 208). The selector always picks the direct importers of a hub. It skips
frontend files at line 192, so it matches no test to them by name. A change
under `infra/postgres/` adds only `test_migration_prefixes.py`. The CLI has
`--base` and `--run`, and no `--gate`.

**Done when:**
- `python3 scripts/affected_tests.py --gate --base <sha>` prints one JSON
  object: `{"full": bool, "files": [...], "reasons": [...]}`. It reads
  `git diff <base>...HEAD`. It uses the standard library only, because the CI
  `changes` job has plain `python3` and no `uv`.
- It returns `full: true` for a change to any of these:
  - a file named `conftest.py` anywhere (a basename match), and any file
    under `tests/` whose name starts with `_`.
  - `pyproject.toml`, `uv.lock`, `.test_durations`.
  - `.github/`, `scripts/affected_tests.py`, `scripts/fences.py`,
    `scripts/ci_unit_guard.py`, `scripts/ci_local.sh`, `scripts/ci_pc/`.
  - `packages/acb_common/` (the DB seam and the shared settings).
  - a changed module that more than 40 test files import directly.
- A change under `infra/postgres/` selects every file under `tests/unit/`
  that contains `_DB_GATE`, plus `test_migration_prefixes.py`.
- A changed file that is not Python (frontend, docs, config) selects the
  tests whose text names its path. No import tracing for these files.
- The Python SAFETY fences (`python.safety` in `tests/fences.json`, read
  through `scripts/fences.py`) are always in `files`.
- Any error inside the selector gives `full: true`, with the error in
  `reasons`.
- The current list output and `--run` work as today.

**Files:** `scripts/affected_tests.py`, `tests/unit/test_affected_tests.py`.
**Fence:** `tests/unit/test_affected_tests.py`. Add one case for each FULL
trigger, the R8 rule, the name match and the error rule. Prove each case red
first.
**Verify:** `uv run pytest tests/unit/test_affected_tests.py -q`.
AGENT-SAFE. STANDARD tier.

### CIV-3 · pr-check runs the selection (BUILT 2026-11-05, done at merge)

Starts after CIV-2 merges. **Built choices:** the file list travels as one
space-separated line in a job output (a typical list is 2-20 KB against a 1 MB
limit, and no shard downloads anything). `ci_unit_guard.py gate-output` sends a
list over 200 KB, or a path outside `tests/*.py`, to a full run. The shard
step `Pick this shard's tests` sets `mode` to `full`, `selection` or `empty`.

**Done when:**
- The `changes` job runs CIV-2. It exports `full` and the file list through
  `needs.changes.outputs`.
- The `test` job needs `changes`, with the idiom that `test_fence_lists.py`
  pins for the other jobs:
  `!cancelled() && (needs.changes.result != 'success' || ...)`. When `changes`
  fails, all 6 shards run the full suite (fail open).
- When `full` is true, the 6 shards run as today.
- When `full` is false and the list is not empty, the shards split only the
  selected files. pytest-split splits an explicit file list.
- **An empty list never reaches pytest.** pytest with no paths collects the
  whole tree, and an empty group exits with code 5. So a shard with an empty
  list skips the pytest step, writes an empty JUnit report, and passes.
  Test both cases.
- `scripts/ci_unit_guard.py` gets `--only-selected <file list>`. With it,
  `--must-run` checks only the listed R8 files that are in the selection.
  The literal `--must-run` list stays in pr-check.yml, because 42 suites
  assert their own name in that text. A new case in
  `tests/unit/test_ci_unit_guard.py` covers the filter.
- The guard still finds a JUnit report for each shard. A skipped pytest step
  must not leave it with none.
- The step that writes the measured timings runs only on a FULL run. A
  selection run must not publish part of the timings.
- The aggregate job `Unit tests` keeps `if: always()`. It is never skipped,
  because GitHub counts a skipped job as a pass. The 7 required check names
  do not change. No filter at the trigger (`test_pr_check_triggers`).
- The shard step keeps `fences.py pytest-ignore`, `--reruns 1`, and no `-x`
  (`test_the_shards_skip_the_process_fences_and_report_every_failure`).
- The `frontend` job and its Visual review step stay unfiltered.

**Advisory target (not a test):** a docs-only PR finishes in under 12
minutes. The evidence is one draft PR per class (docs-only, workbench-only,
Python, migration), with each run's job list and wall time, in the PR body.

**Files:** `.github/workflows/pr-check.yml`, `scripts/ci_unit_guard.py`,
`tests/unit/test_ci_unit_guard.py`, `tests/unit/test_fence_lists.py`.
**Verify:** `uv run pytest tests/unit/test_ci_unit_guard.py tests/unit/test_fence_lists.py tests/unit/test_affected_tests.py tests/unit/test_pr_check_triggers.py -q`,
then the four draft PRs above.
AGENT-SAFE. STANDARD tier, with an adversarial review (it changes the merge gate).

### CIV-4 · `main` runs the full suite in batches (HIGH: deploy path)

**Today (verified 2026-11-04).** The `test` job (deploy.yml line 131) has
`needs: lint` and `if: !inputs.skip_tests`. The triggers are push, the 21:30
schedule and dispatch. `publish-release` (line 271) needs `[lint, test,
release-window]` and demands `needs.test.result == 'success'`.

**The trap.** GitHub cancels an older PENDING run in a concurrency group when
a newer one arrives. If the release's `test` job shared the push group, the
next merge would cancel it. `publish-release` would then skip, and that day's
release would be lost. Lines 320 to 330 of deploy.yml record this failure mode
for `deploy`.

**Done when:**
- The `test` job gets a concurrency group for pushes only:
  `group: ${{ github.event_name == 'push' && 'main-full-suite' || format('release-{0}', github.run_id) }}`
  with `cancel-in-progress: false`. A release or dispatch `test` job never
  queues behind a push and is never cancelled.
- A push run that GitHub folds into a later one shows as a cancelled `test`
  job. That is the design. The job comment says so.
- On a push only (behind `if:`), a step writes the tested range to
  `$GITHUB_STEP_SUMMARY`: the SHA of the last green push run, found with the
  Actions API (or "unknown"), to this SHA, with the PR numbers from
  `git log --merges` or the API. A failure of this step never fails `test`.
- `tests/unit/test_deploy_pipeline.py` lines 93 to 104 ban `concurrency` on
  the jobs `lint`, `test` and `publish-release`. Change that test in the same
  PR: allow exactly the expression above on `test`, and keep the ban on the
  workflow level, `lint` and `publish-release`.
- Do not touch the `Run unit tests` step (`test_fence_lists.py` pins
  `fences.py pytest-ignore` there). `test_daily_release.py` still passes.

**Files:** `.github/workflows/deploy.yml`, `tests/unit/test_deploy_pipeline.py`.
**Verify:** `uv run pytest tests/unit/test_deploy_pipeline.py tests/unit/test_daily_release.py tests/unit/test_fence_lists.py -q`.
After the merge, the live evidence is two merges that land during one run:
one green `test` run with both PRs in its summary, and one cancelled run.
AGENT-SAFE. **HIGH tier.** Opus builds it, with an adversarial review.

### CIV-5 · A red batch names the PR that broke it

**Done when:**
- `scripts/bisect_main.sh <good_sha> <bad_sha> <test ids...>` does a binary
  search over the merge commits in the range. It runs only the failed tests,
  on the home box (the `ci_local.sh` lanes). It prints the first bad merge and
  its PR.
- A user timer on the box polls the batch runs every 5 minutes. On a red run,
  it reads the failed test ids from the JUnit artifact, runs the bisect, and
  posts the culprit on a Metorite task.

**Files:** `scripts/bisect_main.sh`, `scripts/home_server/`, `tests/unit/test_bisect_main.py`.
**Verify:** `uv run pytest tests/unit/test_bisect_main.py -q`, with a fixture
repo in which one known commit breaks one test.
AGENT-SAFE. STANDARD tier.

### CIV-6 · Local tier: the affected tests by default, several runs at once on the box

**Done when:**
- `bash scripts/ci_local.sh` runs the CIV-2 selection. `--full` runs the whole suite.
- Each run on the box has its own checkout, so two runs never share a working
  tree.
- The box takes a second run when free memory allows it. Shards come from
  measured peak memory, not from a 2.5 GB guess.

**Files:** `scripts/ci_local.sh`, `scripts/ci_pc/`, `tests/unit/test_ci_pc.py`.
**Verify:** `uv run pytest tests/unit/test_ci_pc.py -q`, then two runs at
once on the box, both green.
AGENT-SAFE. STANDARD tier.

### CIV-7 · The harness says it (BUILT 2026-11-05 with CIV-3, done at merge)

**Done when:**
- CLAUDE.md §6 no longer says "pr-check runs every test on each PR". It names
  the tiers in §4.
- `engineering_practice.md` §1.3 and §7a, and `.claude/commands/next-ticket.md`,
  describe the tiers. Each one points here.
- Every open session is told.

**Fence:** the PROCESS fences that read those files. Re-run them.
AGENT-SAFE. LOW tier.

### CIV-8 · Cut the red rate

**Done when:** the last 50 red pr-check runs are sorted into real failures,
flakes and infrastructure. Each flake has a task, or a fix, or a quarantine
marker with a named owner. The red rate over the next 50 runs is under 20%.

**Verify:** the sorted table, in the task.
AGENT-SAFE. STANDARD tier.

### CIV-9 · A faster full suite

**Done when:**
- The R8 ladder files clone one migrated template database, instead of
  applying the ladder per file.
- `test_crm_field_studio_r8.py` seeds once per module and splits, so no file
  takes more than 180 s.
- `.test_durations` is refreshed.
- The full suite's wall time is under 15 minutes.

**Files:** `tests/unit/_tenant_ladder.py`, `tests/unit/_customer_console_ladder.py`,
`tests/unit/test_crm_field_studio_r8.py`, `.test_durations`.
**Verify:** the same R8 files pass on a real database (R8), before and after,
with the wall times.
AGENT-SAFE. STANDARD tier.

## 6. Order

CIV-2 and CIV-4 start now, beside each other. CIV-3 starts after CIV-2
merges. CIV-7 lands with CIV-3. CIV-5, CIV-6, CIV-8 and CIV-9 follow, in any
order.

## 7. Tasks

The Metorite epic is task 1628, under the Developer Workflow project. Each
slice has one subtask:

| Slice | Task | Slice | Task |
|---|---|---|---|
| CIV-1 | 1629 | CIV-6 | 1634 |
| CIV-2 | 1630 | CIV-7 | 1635 |
| CIV-3 | 1631 | CIV-8 | 1636 |
| CIV-4 | 1632 | CIV-9 | 1637 |
| CIV-5 | 1633 | | |
