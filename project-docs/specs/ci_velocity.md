# CI velocity: the relevant tests per PR, the full suite once per batch (WS-CIV)

**Status:** ACTIVE (owner directive, 2026-11-04). Verified against code on
2026-11-04 (branch base `fb26ada52`, after a spec audit). CIV-1 and CIV-2 are
done. CIV-3 and CIV-7 are built on branch `civ3-pr-tier` (2026-11-05), and
they are done at merge. CIV-4, CIV-8 and CIV-9 are AGENT-SAFE.
CIV-4, CIV-4b, CIV-4c and CIV-13 are HIGH tier, because they change the deploy path.
CIV-6 is built (task 1634, PR open). CIV-4b merged on 2026-11-05 (task 1711). CIV-13 (D178) is BUILT on
2026-11-05: the home box starts the 21:30 release. The owner installs its timer.

CIV-4c is BUILT on 2026-11-07 (task 1736, branch `civ4c-r8-cover`).

CIV-21 (D194) is BUILT on 2026-11-07 (task 2210, branch
`civ21-three-releases-mt-06e30527`). Production releases three times a day,
at 05:30, 13:30 and 21:30 UTC. The owner installs the new timer.

**Shell manifest:** none. This is CI, not an app, so R9 does not apply.

**Owning spec** for how much CI runs, and where. `engineering_practice.md` §7a
keeps the two-speed rules. `staging_and_daily_release.md` keeps the release
slots at 05:30, 13:30 and 21:30 UTC (D194). This spec links to them and adds nothing to them.

---

## 1. The rule

**Each PR proves the code it changes. The common checks run once per batch.**

- A PR runs lint, the secret scan, the type checks, the SAFETY fences, and the
  unit tests that its diff can affect. Target: about 10 minutes.
- After the merge, `main` runs the full suite in batches. While one run goes,
  new merges wait, and the next run tests them together.
- Each release slot (05:30, 13:30 and 21:30 UTC, D194) ships only a `main`
  whose full suite is green.
- **R8 runs at the release.** The batch has no database, so its R8 suites
  skip. Each release runs every R8 file against two real Postgres servers,
  and a red R8 gate holds the release (CIV-4b). CIV-9 makes the R8 files
  fast, and then R8 moves into the batch too.
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
- No GitHub self-hosted runner, except the box runners of CIV-23 (D198). They
  carry only the label `metorite-box`, run only the unit shard job, and never
  take a fork PR. They run as user `ghrunner` with no sudo and a rootless
  Docker. The home box also takes work from our own sessions, over Tailscale
  (task 1534).
- This spec does not change the full-suite rule of a release. CIV-21
  (D194) changes only the clock: production moves three times a day, at
  05:30, 13:30 and 21:30 UTC.
- No change to the owner's preview step for a UI slice (D145 step 1).

## 4. The tiers

| Tier | Where | What runs | Gate? |
|---|---|---|---|
| 0. Local | Home box first, this PC second (`ci_local.sh`) | The affected tests, lint, the touched app's type check | Advice before a push |
| 1. PR | GitHub (pr-check) | Lint, secret scan, type checks, SAFETY fences, the affected tests. The full set when the diff is FULL. | The 7 required checks |
| 2. Batch | GitHub (deploy.yml on push to `main`) | The full unit suite with no database, so R8 skips until CIV-9. One run at a time. Pending merges fold into the next run. | Signal. Red means fix forward now. |
| 3. Release | GitHub (deploy.yml at 05:30, 13:30 and 21:30 UTC, D194) | The full suite on the tip of `main`, and the R8 files in 6 shards with both databases (CIV-4b) | Gate for the release |

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
- A change under `infra/postgres/` selects every R8 file, plus
  `test_migration_prefixes.py`. CIV-4b widened the R8 rule to
  `r8_files()`: `_DB_GATE`, an R8 variable, or a helper that names one.
  `_DB_GATE` alone found 348 of the 454 R8 files.
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

### CIV-3 · pr-check runs the selection (BUILT 2026-11-05, merges only after CIV-4b)

Starts after CIV-2 and CIV-4b merge. Without CIV-4b, an R8 suite that a PR
did not select would run nowhere before production.

Starts after CIV-2 merges. **Built choices:** the file list travels as one
space-separated line in a job output (a typical list is 2-20 KB against a 1 MB
limit, and no shard downloads anything). `ci_unit_guard.py gate-output` sends a
list over 200 KB, or a path outside `tests/*.py`, to a full run. The shard
step `Pick this shard's tests` sets `mode` to `full`, `selection` or `empty`.
One rule in `affected_tests.py` covers a test that reads files it never names.
`scanning_tests` picks every unit test with a scan idiom. The idioms are `glob`,
`rglob`, `iglob`, `iterdir`, `os.walk`, `Path.walk()`, `listdir`, `scandir`,
`git ls-files`, `git ls-tree`, `git grep`, a `grep` or `rg` argument, `fnmatch`,
`iter_modules`, `walk_packages` and `importlib.resources`.
The rule runs when the PR changes any Python file, in `tests/` or `workbench/` too.
A docs-only PR picks only the tests that name the doc. The owner chose this rule
on 2026-11-05 (option A, on task 1631). Round 6 extends the trigger to `tests/`
and `workbench/`, so no selection shrinks against round 4. Four rounds of folder and tree heuristics had missed
scanners, so the heuristics are gone.

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
  The literal `--must-run` list stays in pr-check.yml, because 36 test files
  assert their own name in that text (counted 2026-11-05). A new
  case in `tests/unit/test_ci_unit_guard.py` covers the filter.
  deploy.yml's R8 gate keeps the full list, with no `--only-selected`.
  `test_deploy_pipeline.py` fails when the two lists differ.
- The guard still finds a JUnit report for each shard. A skipped pytest step
  must not leave it with none.
- The step that writes the measured timings runs only on a FULL run. A
  selection run must not publish part of the timings.
- The aggregate job `Unit tests` keeps `if: always()`. It is never skipped,
  because GitHub counts a skipped job as a pass. The 7 required check names
  do not change. No filter at the trigger (`test_pr_check_triggers`).
- The shard step keeps `fences.py pytest-ignore`, `--reruns 1`, and no `-x`
  (`test_the_shards_skip_the_process_fences_and_report_every_failure`).
  ⚠️ `--ignore` does not drop a file that the command line names (pytest
  9.1, measured 2026-11-05). So a selection run must also remove the
  process fences from its file list, as `affected_tests.py --r8` does.
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

**Status: BUILT (2026-11-05).** Branch `civ4-batch`, task 1632. The verifier
gave PASS. The Opus review asked for one change in round 1, then gave APPROVE.
The live evidence comes after the merge. Two review rounds added the three
"Built" points below.

**Built beyond the first text (review rounds, 2026-11-05):**
- **A rerun is not batched.** The group is
  `${{ github.event_name == 'push' && github.run_attempt == 1 && 'main-full-suite' || format('release-{0}', github.run_id) }}`.
  A person's rerun of an old push run gets its own group, so it cannot cancel
  a newer batch. `test` has `timeout-minutes: 90`.
- **A batch tests the tip of `main`.** GitHub does not keep pending jobs in
  order ("ordering is not guaranteed"), so the job that survives can belong to
  an OLDER merge. On a first-attempt push, the step "Test the tip of main"
  fetches `main` and checks out its tip when the job starts. It prints a
  "Tested commit" notice. A release, a dispatch and a rerun still test exactly
  `GITHUB_SHA`, the commit they ship.
- **The range** runs from the commit that the last green batch tested (its
  "Tested commit" notice, through check-run annotations, so the job has
  `checks: read`) to the tested tip. CIV-5's bisect reads that notice, not
  `head_sha`.
- **A timeout also shows as `cancelled`.** A fold is "cancelled and never
  started". A hung batch ran 90 minutes and carries the annotation "The job
  has exceeded the maximum execution time". Treat a hung batch as red.
  next-ticket.md and AGENTS.md say so, and they tell a session to find the
  push run whose `test` job started after its merge and whose summary names
  its PR.

**Before (verified 2026-11-04).** The `test` job (deploy.yml line 131) has
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

### CIV-4b · A release runs every R8 suite against real databases (HIGH: deploy path)

**Status: BUILT (2026-11-05).** Branch `civ4b-r8-main`. Task 1711. CIV-4
merged first.

**Why.** pr-check is the only workflow that sets the two R8 variables and
starts the two Postgres services. The `test` job of deploy.yml has no
database, so every R8 suite skips there. Today each PR runs each R8 suite, and that
hides the gap. After CIV-3, an R8 suite that a PR did not select would run
nowhere before production.

**The measurement (2026-11-05), and the decision.**
- With both databases set, the full suite needs 127.7 and 131.7 min of test
  time. These are the JUnit sums of two green pr-check runs. The R8 files
  take 102.5 to 104.4 min of that.
- The `test` job of deploy.yml stops at 90 minutes. With two services on
  `test`, each batch and each release would end as `cancelled`. Then
  `publish-release` would skip.
- A higher `timeout-minutes` also fails. The schedule run starts 1.9 to 3.6
  hours late, so a gate of 140 minutes would miss the release window on
  most nights.
- The decision (coordinator, 2026-11-05): a release runs the R8 files in 6
  shards, beside `test`. CIV-9 makes the R8 files fast, and then R8 moves
  into the push batch.

**Built:**
- `r8-suite` is a matrix of 6 shards. It runs on the schedule, and on each
  dispatch without `skip_tests`. A `nightly` dispatch (CIV-13) runs it too.
  The condition names the two events, so a new trigger does not start it.
  A push does not run it. It joins no concurrency group.
- It copies pr-check's two services exactly: `postgres:16` on port 5433 and
  `pgvector/pgvector:pg16` on port 5434. It also copies the two variables
  and the two assert steps.
- Each shard runs the output of `affected_tests.py --r8` (480 files on 2026-11-05),
  plus the guard's 4 files that need no database (`GUARD_ALSO`). It uses
  pr-check's split by duration, `--reruns 1`, no `-x`, and a JUnit report.
- A list of fewer than 100 files stops the shard before pytest starts.
- `r8-gate` fails when a shard failed. Then it runs
  `ci_unit_guard.py check` over the 6 reports. It uses pr-check's full
  `--must-run`, `--never-skip` and `--disarmed` lists, and no
  `--only-selected`.
- `publish-release` needs `r8-gate` green. It accepts a skip only on a
  `skip_tests` dispatch, the same rule as for `test`. `release-window` reads
  the clock after `r8-gate`.
- No job in deploy.yml sets `DATABASE_URL`. pr-check.yml gives the reason.
- `r8_files()` in `affected_tests.py` is the one rule for an R8 file. The
  file holds `_DB_GATE`, names an R8 variable, or names a
  `tests/unit/_*.py` helper that names one.
- That rule found all 454 files that skipped for an unset R8 variable in
  deploy run 41951395371. `_DB_GATE` alone found 348.
- `r8_gate_files()` leaves out the process fences. pytest's `--ignore` does
  not drop a file that the command line names.
- The `test` job, its group, its tip step and its timeout do not change. It
  keeps `-x`.

**The 35 files that check for their own name** read pr-check.yml only, so a
second list does not break them. `test_deploy_pipeline.py` fails when the
two lists differ. Add a new R8 suite to both files.

**Fence:** `tests/unit/test_deploy_pipeline.py` covers the triggers, the
needs chain, the `skip_tests` rule, the services, the variables, the assert
steps, the split and the guard lists. `tests/unit/test_affected_tests.py`
covers the R8 rule.
**Files:** `.github/workflows/deploy.yml`, `scripts/affected_tests.py`,
`tests/unit/test_deploy_pipeline.py`, `tests/unit/test_affected_tests.py`.
**Verify:** `uv run pytest tests/unit/test_deploy_pipeline.py tests/unit/test_daily_release.py tests/unit/test_fence_lists.py tests/unit/test_ci_unit_guard.py tests/unit/test_affected_tests.py -q`.

After the merge, the live evidence is the first 21:30 UTC run: six green R8
shards, a green `r8-gate`, and a moved `release` ref.
AGENT-SAFE. **HIGH tier.** Opus builds it, with an adversarial review.

### CIV-4c · Every R8 skip of a release runs in the R8 gate (HIGH: deploy path)

**Status: BUILT (2026-11-07).** Branch `civ4c-r8-cover`. Task 1736.

**Why.** `r8_files()` finds an R8 file by a text match, one level deep. A
new file can get its database gate by import only, and name no mark. Then
`--r8` leaves it out. The `test` job skips it, and the R8 gate never runs
it. On 2026-11-07 the list held each such file, but no fence proved it.

**Built:**
- The `test` job of deploy.yml writes a JUnit report to `junit/test.xml`.
  Its pytest command keeps `-x`, `-v`, `--reruns 1` and the fence ignore.
- A new step after the unit run writes `affected_tests.py --r8` to
  `r8_files.txt`. Then it runs `ci_unit_guard.py r8-covered`.
- The step has no `if:`. It runs only after a green unit run, so a red run
  stays red.
- `r8-covered` fails for each file that skipped for an R8 reason and is not
  in the list. A module that skipped at collection counts.
- An R8 reason starts with `CUSTOMER_CONSOLE_DATABASE_URL` or
  `TENANT_LADDER_DATABASE_URL`. Or it names `R8` and also a variable or
  "real Postgres". A Redis or platform skip is not an R8 reason.
- It fails closed. No report, a report with no test, a missing list and an
  empty list each fail. The step also stops on an empty list before the
  guard starts.
- pr-check.yml gets no copy. Its shards have both databases, and its guard
  already fails on each R8 skip.
- `r8-suite`, `r8-gate`, `publish-release` and the release window do not
  change.

**Fence:** `tests/unit/test_ci_unit_guard.py` covers the rule with JUnit
reports in a temporary folder. `tests/unit/test_deploy_pipeline.py` covers
the step, its place, the old flags and the unchanged jobs.

**Files:** `.github/workflows/deploy.yml`, `scripts/ci_unit_guard.py`,
`tests/unit/test_ci_unit_guard.py`, `tests/unit/test_deploy_pipeline.py`.
**Verify:** `uv run pytest tests/unit/test_ci_unit_guard.py tests/unit/test_deploy_pipeline.py -q`.
AGENT-SAFE. **HIGH tier.**

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

**Status: BUILT 2026-11-07** (task 1633, branch `civ5-bisect`). Not live: the
owner has not installed the timer.

**Built.**
- `scripts/bisect_main.sh <good> <bad> <test id>...` searches the first-parent
  commits in (good, bad]. It does not call `git bisect`. Each probe has a
  detached worktree in a temp dir, and the EXIT trap removes it. It checks
  first that the good commit passes and the bad commit fails, else it exits 3.
  A probe exit code of 0 is good and 1 is bad. Any other code skips the commit.
  Adjacent commits that cannot be judged give exit 4 and the range. The cap
  is 12 probes (exit 5). It prints `FIRST_BAD <sha> <subject> PR=<n or none>`.
- `scripts/home_server/bisect_poll.sh` is one poll pass. It acts only when the
  newest completed batch run is red and new. It finds the last green batch as
  the good sha, reads the failed ids with `scripts/junit_failed_ids.py`, runs
  the bisect and prints `BISECT_RESULT <json>`. State is `handled` in
  `~/.cache/metorite-bisect`. It never reruns or cancels a run.
- `scripts/home_server/systemd/metorite-bisect-poll.{service,timer}` are unit
  files. Nobody installed them.
- Fence: `tests/unit/test_bisect_main.py` (18 tests, a fixture repo and a
  shimmed `gh_cmd`).

**By hand.** `bash scripts/bisect_main.sh <good> <bad> tests/unit/test_x.py::test_y`.
The default probe is `uv run python -m pytest -x -q -p no:cacheprovider "$@"`.
Set `BISECT_TEST_CMD` to reuse a prepared lane or venv. `BISECT_TIMEOUT_S` and
`BISECT_MAX_PROBES` set the limits.

**Left for the owner.**
- Install the timer (steps in the service file).
- A lane session reads the `BISECT_RESULT` line in the journal and posts it on
  a Metorite task. The script has no Metorite credentials.
- Known gap: the push batch job writes no JUnit file. Only the R8 shards
  upload one (`r8-junit-shard-N`). The poller reads an artifact named
  `unit-junit` (`BISECT_ARTIFACT`). A later slice must add
  `--junitxml` and an upload step to `test` in `deploy.yml`. Until then the
  poller records a red run with "no failed test id".

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

**Built (2026-11-04).** Measured on the home box with cgroup `memory.peak`: a
shard is 3.2 to 4.4 GB, a full run of 5 shards peaked at 17.7 GB, and the jobs
around the shards add about 5 GB. The slot defaults are 3 GB per shard and 5 GB
fixed (`scripts/ci_pc/slots.sh`). A 23 GB box takes two selected runs at once,
or one full run. Evidence: two selected runs at once, both green, in 245 s and
252 s. A selected run alone took 184 s. Two jobs run one at a time across runs,
because they read the whole Docker daemon or the CPU clock: `backup-restore`
and `frontend`. Two tests fail in every full run on the box, before and after
this slice: `test_attachment_ocr.py` and `test_web_reader_confine.py`
(`test_each_attack_works_unconfined`). That is a finding for the board. The PC
lane is unchanged in kind, but nobody ran `--pc` from Windows yet.

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
- **R8 moves into the push batch** (the follow-up of CIV-4b). The R8 files
  must fit in the 90 minutes of `test` on one runner, with a margin. Then
  `test` gets pr-check's two services, the two variables and the guard. The
  push batch then finds an R8 break within one run, not at the release.

**Files:** `tests/unit/_tenant_ladder.py`, `tests/unit/_customer_console_ladder.py`,
`tests/unit/test_crm_field_studio_r8.py`, `.test_durations`.
**Verify:** the same R8 files pass on a real database (R8), before and after,
with the wall times.
AGENT-SAFE. STANDARD tier.

### CIV-13 · The home box starts the 21:30 release (HIGH: deploy path)

Owner decision, 2026-11-05: "Box triggers 21:30" (D178). GitHub starts the
`30 21 * * *` cron 3.5 to 5.5 h late. Run 41949347780 started at 01:33 UTC,
so the window of 21:00 to 02:00 UTC often closed first.

**What changes.**
- A user timer of `dev` on the home box runs at 21:30 UTC. It runs
  `scripts/home_server/nightly_release.sh run`. The script dispatches
  `deploy.yml` on `main` with `nightly=true`, and logs the URL of the run.
- A NIGHTLY run is a schedule run or a dispatch with `nightly=true`. A
  nightly dispatch says "nightly" in its run name, because the runs API does
  not return the inputs.
- `RELEASE_HOLD` and the window hold a nightly dispatch, as they hold the
  schedule. A nightly dispatch with `skip_tests` does not publish.
- A night ships once. The step `once` of `release-window` holds a nightly
  run when another nightly run's `publish-release` passed since 21:00 UTC.
  After midnight, it looks back to 21:00 on the day before.
- The cron stays as the backup.

**What does not change.** A plain dispatch behaves as before: no hold, no
window, and `skip_tests` still publishes. A push behaves as before. Its run
name stays its commit message.

**The choices.**
- A rerun (`run_attempt` 2 or more) skips the check, as it skips the clock.
- A plain dispatch never counts. The owner's dispatch of 2026-11-05, run
  41969146897, does not hold a nightly run.
- When the API gives no answer, the run SHIPS and warns. A second ship of
  one night is one more apply. A lost night holds a day of merges back.
- ⚠️ A race is possible and benign. Two nightly runs can both pass `once`
  before either publishes. The push of the same sha is then a no-op, and
  `deploy-production` serialises the two deploys. A newer sha makes one
  more restart. No concurrency group goes on `publish-release`, because
  `test_deploy_pipeline.py` bans it.
- The script refuses outside 21:00 to 01:00 UTC. The tests take about
  40 min, and a late `Persistent=true` start must not ship in working hours.

**Acceptance.**
1. A nightly dispatch is held by `RELEASE_HOLD`, and it obeys the 21:00 to
   02:00 UTC window. It cannot skip the tests.
2. A plain dispatch behaves as today, and the tests pin it.
3. A nightly run does not publish when another nightly run's
   `publish-release` passed since 21:00 UTC. A rerun is exempt.
4. The timer, the service, the script and the install step exist, with the
   clock guard and the log of the run URL.
5. D178 and the doc lines are in place.

**Files:** `.github/workflows/deploy.yml`,
`scripts/home_server/nightly_release.sh`,
`scripts/home_server/metorite-nightly-release.service`,
`scripts/home_server/metorite-nightly-release.timer`.
**Fence:** `tests/unit/test_daily_release.py` and
`tests/unit/test_nightly_release_box.py`.
**Install (owner, on the home box, as `dev`):**
`bash ~/Metorite/scripts/home_server/nightly_release.sh install`. It turns
on linger, copies the script, and enables the timer. In WSL, the Windows
task "Metorite WSL keep-alive" keeps the distro up.
HIGH tier.

D194 (CIV-21) moves this timer to three slots. The text above keeps the
record of 2026-11-05.

### CIV-21 · Production releases three times a day (HIGH: deploy path)

**Status: BUILT 2026-11-07** (task 2210, branch
`civ21-three-releases-mt-06e30527`). Owner decision, 2026-11-07 (D194). It
supersedes D145 point 5 and the time in D178 point 1.

**What changes.**
- Production moves three times a day: at 05:30, 13:30 and 21:30 UTC. That
  is 11:00, 19:00 and 03:00 IST.
- `deploy.yml` has three crons: `30 5 * * *`, `30 13 * * *` and
  `30 21 * * *`.
- The step `clock` of `release-window` opens a window for each slot. Each
  window opens 30 min before its slot: 21:00 to 02:00, 05:00 to 10:00 and
  13:00 to 18:00 UTC. The windows do not overlap. A late nightly run waits
  for the next slot. A person's rerun is not held.
- A slot ships once. The step `once` looks back to the open time of the
  window of the current slot. That is the latest of 05:00, 13:00 and 21:00
  UTC that is not after now.
- The home box timer has three `OnCalendar` lines. The script dispatches
  only from 21:00 to 01:00, 05:00 to 09:00 and 13:00 to 17:00 UTC.

**What does not change.** The input `nightly`, the step id `once`, the
output names and the unit name keep their names. `RELEASE_HOLD`, the R8
gate, the full-suite rule and a plain dispatch behave as before.

**Acceptance.**
1. Each slot has a cron, a window and a timer line. A test fails when one
   is missing.
2. The windows do not overlap, and a test fails when two do.
3. A nightly run in a slot does not publish when another nightly run
   published since the window of that slot opened.
4. D194 and the doc lines are in place.

**Files:** `.github/workflows/deploy.yml`,
`scripts/home_server/nightly_release.sh`,
`scripts/home_server/metorite-nightly-release.service`,
`scripts/home_server/metorite-nightly-release.timer`.
**Fence:** `tests/unit/test_daily_release.py` and
`tests/unit/test_nightly_release_box.py`.
**Install (owner, on the home box, as `dev`):** run
`bash ~/Metorite/scripts/home_server/nightly_release.sh install` again
after the merge. Until then the box fires at 21:30 UTC only.
HIGH tier.

### CIV-20: fewer PRs sent back (task 2207)

**Status: slices 1 and 3 BUILT 2026-11-07. Slice 2 is CIV-8** (PR 1872, merged).
STANDARD tier. Of the PRs that lane-merge handled from 2026-11-06 22:05 UTC,
21 went BACK, about 1 in 3. The causes: 8 merge conflicts, 6 a missing
"Visual review:" line, 6 a red test, 1 a migration number below main's highest.

**Slice 1.** `scripts/home_server/lane_handover.sh <PR> <task> <worktree> <lane>
<note>` is the one way a lane hands a PR over. It appends the line to
`~/lanes/merge-queue.tsv` only after these checks pass: the worktree is clean
and sits at the PR head, the body has the Visual review line when the diff
touches `workbench/control_plane/src`, a trial merge of `origin/main` has no
conflict, and each new migration number is above main's highest and free of
other open PRs. A failed check prints the reason and appends nothing.
**Fence:** `tests/unit/test_lane_handover.py`.

**Slice 3.** `.gitattributes` gives `project-docs/INDEX.md` the `union` merge
driver, so two PRs that each add a row no longer conflict. A fence checks that
no spec row repeats in one section. Other append-only files stay out: a union
there would keep both sides of a real clash in one line.
**Fence:** `tests/unit/test_index_union.py`.

**Open.** The lane rules live outside the repo. Rule 14 must name
`lane_handover.sh`. The done test is a BACK rate under 1 in 10 over 20
hand-overs.

### CIV-19: the lane keeper (task 2206)

**Status: BUILT 2026-11-07** (task 2206). STANDARD tier.
On 2026-11-07 the box lanes sat idle for about 5 hours. An account switch ended
the turn of 8 lanes. The loops that wake lanes ran on the owner's PC, and the
PC went quiet.

A box user timer runs `lane_keeper.py` every 10 minutes. Only the lanes named in
`~/lanes/keeper-lanes.txt` (one name per line) may wake, and with no file nothing
wakes. The keeper reads `claude agents --json` and keeps the newest session of
each listed lane. A lane is ended when the state is done, blocked or idle and the
transcript is more than 10 minutes old. A lane in a `sleep 600` has state
working, so the keeper leaves it alone. The keeper also leaves a lane alone when
it has a busy session. That is a busy session with the lane's name, or a busy
session of any name whose transcript header names the lane.

**The fork rule (2026-11-08).** A resume of a session that still runs starts a
copy. The first keeper woke the old session again at each pass, and lane-merge
reached 3 live copies. Now the keeper runs `claude stop <old id>` while the old
session has a process, and it resumes only when the stop worked. The resume is
`claude --bg --resume <id> -n <lane> --remote-control=<lane> <note>`. One session
of the lane is left, and it has the lane's name, so the next pass finds it. The
busy check reads the header of a busy session's transcript: a fork copies the
header of its parent, so it still starts with the lane's name.

A lane wakes once in 30 minutes. After an account change the keeper wakes every
ended listed lane once, with no cap. Each wake goes to `~/lanes/keeper.log`.
The keeper stops only the ended session that it is about to resume, and it
never starts a new lane. `lane-chat` and `lane-bill` are also on the default skip
list, because they wait on the owner.

The account switch is a hook. `KEEPER_SWITCH_CMD` names the command, and the
keeper gives it `KEEPER_SKIP_ACCOUNTS`. The keeper does not run it while the box
sits on a skip-list account. The repo holds no switch command, so the owner
supplies it.

**Files:** `scripts/home_server/lane_keeper.py`,
`scripts/home_server/metorite-lane-keeper.service`,
`scripts/home_server/metorite-lane-keeper.timer`.
**Fence:** `tests/unit/test_lane_keeper.py`.

### CIV-10: the weekly delivery review (task 1643)

**Status: BUILT 2026-11-07** (task 1643, branch `civ10-review`). STANDARD tier.
Owner directive, 2026-11-04: revisit the workflow and CI each week.

A box user timer runs `/delivery-review` each Monday at 07:00 UTC. The command
measures CI, reads the Metorite items through the MCP, and posts one report
task. It opens one fix task for each item over its threshold, under epic
task 1628. The thresholds live in the command file.

**Files:** `scripts/delivery_metrics.py` (read-only, stdlib, prints one JSON
object), `.claude/commands/delivery-review.md`,
`scripts/home_server/delivery_review.sh`,
`scripts/home_server/metorite-delivery-review.service`,
`scripts/home_server/metorite-delivery-review.timer`.
**Fence:** `tests/unit/test_delivery_metrics.py`.

**The owner does two things.** Install the timer (the unit header says how).
Confirm the headless command in `delivery_review.sh`, because the repo holds
no tested launcher. The first run is manual:
`python3 scripts/delivery_metrics.py --days 7` on the box.

**Known limits.** A selection miss is matched through `commits/<sha>/pulls`,
because a squash merge gives main a new sha. A red batch with no PR run in the
window is not counted. Run time is `updated_at` minus `run_started_at`, so a
rerun adds to it.

## 6. Order

CIV-2 and CIV-4 start now, beside each other. CIV-4b follows CIV-4. It is a
sharded R8 gate. It runs on each release slot and on a dispatch, beside
`test`, and `publish-release` needs it green. CIV-3 merges only after CIV-4b,
because the PR gate then skips R8 suites that CIV-4b still covers. CIV-7
lands with CIV-3. CIV-5, CIV-6, CIV-8 and CIV-9 follow, in any
order.

## 7. Tasks

The Metorite epic is task 1628, under the Developer Workflow project. Each
slice has one subtask:

### CIV-17 · pr-check sizes its unit shards to the selection (task 1988)

The org is on GitHub Free: 20 jobs at once for every repo. One pr-check run
used about 17, and 13 box lanes pushed at once on 2026-11-06. Runs queued for
1 to 3 hours. CIV-3 made a shard run only the selected tests, but all six
shard jobs still started for an empty selection.

**What changes.**
- The `changes` job also puts out `shards`, a JSON list: `[]` for an empty
  selection, `[1]` for 40 test files or fewer, `[1,2,3]` for 120 or fewer, and
  `[1,2,3,4,5,6]` otherwise. `scripts/ci_unit_guard.py` owns the rule
  (`SHARD_TIERS`). The limits are a first guess. Re-pick them from the next 10
  PRs.
- The `test` matrix reads `fromJSON(needs.changes.outputs.shards ||
  '[1,2,3,4,5,6]')`, so a failed `changes` job runs all six. `--splits` comes
  from the pick step, which calls the same function.
- The `test` job does not start for `[]`, because GitHub fails a matrix with
  no values. `Unit tests` still runs (`if: always()`). It passes a skipped
  `test` job ONLY when `changes` succeeded and `shards` is `[]`. Any other skip
  stays red (#1077).
- Drafts are NOT skipped. The first design skipped the heavy jobs on a draft.
  A skipped required check leaves a passing twin on the head SHA (#1077), and
  skipping the browser suite breaks D-PM-21. So every job runs on a draft too.

**Fence:** `tests/unit/test_ci_unit_guard.py`, `tests/unit/test_fence_lists.py`.
**Verify:** a docs-only PR starts no shard and `Unit tests` passes. A PR that
breaks one test turns `Unit tests` red. Compare jobs per PR before and after
on 10 PRs.
AGENT-SAFE. STANDARD tier, with an Opus diff review.


| Slice | Task | Slice | Task |
|---|---|---|---|
| CIV-1 | 1629 | CIV-6 | 1634 |
| CIV-2 | 1630 | CIV-7 | 1635 |
| CIV-3 | 1631 | CIV-8 | 1636 |
| CIV-4 | 1632 | CIV-9 | 1637 |
| CIV-5 | 1633 | CIV-4b | 1711 |
| CIV-17 | 1988 | CIV-13 | 1721 |
| CIV-10 | 1643 | CIV-4c | 1736 |
| CIV-21 | 2210 | | |
| CIV-20 | 2207 | | |
| CIV-19 | 2206 | | |
