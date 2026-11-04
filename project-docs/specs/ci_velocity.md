# CI velocity: the relevant tests per PR, the full suite once per batch (WS-CIV)

**Status:** ACTIVE (owner directive, 2026-11-04). Verified against code on
2026-11-04 (origin/main `cb5fd9c2a`). CIV-1 is done. CIV-2 to CIV-9 are
AGENT-SAFE. CIV-4 is HIGH tier, because it changes the deploy path.

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

Decision: D175 (2026-11-04). Re-check the number at merge.

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

**Done when:**
- `uv run python scripts/affected_tests.py --gate --base <sha>` prints one
  JSON object: `{"full": bool, "files": [...], "reasons": [...]}`.
- It returns `full: true` for a change to any of these:
  - any `conftest.py`, and any helper under `tests/` whose name starts with `_`.
  - `pyproject.toml`, `uv.lock`, `.test_durations`.
  - `.github/`, `scripts/affected_tests.py`, `scripts/fences.py`,
    `scripts/ci_unit_guard.py`, `scripts/ci_local.sh`, `scripts/ci_pc/`.
  - a hub module, which more than 40 test files import. Today the selector
    skips the hub, and so it under-selects.
  - `packages/acb_common/` (the DB seam and the shared settings).
- A change under `infra/postgres/` selects every R8 file (the files that use
  `_DB_GATE`), plus the migration fences.
- The SAFETY fences (`scripts/fences.py`) are always in `files`.
- Any error inside the selector gives `full: true`, with the error in `reasons`.
- A docs-only diff selects only the SAFETY fences and the tests that name a
  changed path.

**Files:** `scripts/affected_tests.py`, `tests/unit/test_affected_tests.py`.
**Fence:** `tests/unit/test_affected_tests.py`. Add one case for each FULL
trigger, and prove each case red first.
**Verify:** `uv run pytest tests/unit/test_affected_tests.py -q`.
AGENT-SAFE. STANDARD tier.

### CIV-3 · pr-check runs the selection

**Done when:**
- The `changes` job runs CIV-2 and exports `full` and the file list.
- When `full` is true, the 6 shards run as today.
- When `full` is false, the shards split only the selected files. A shard with
  no files passes in under a minute.
- `scripts/ci_unit_guard.py --must-run` checks only the R8 files in the
  selection, unless the run is FULL. Today the guard expects every listed R8
  file in every run, so a selection would turn `Unit tests` red.
- The 7 required check names do not change.
- A docs-only PR finishes in under 12 minutes. A workbench-only PR runs no
  backend test that does not name a workbench path.
- Fail open stays: when `changes` fails, everything runs.

**Files:** `.github/workflows/pr-check.yml`, `scripts/ci_unit_guard.py`,
`tests/unit/test_ci_unit_guard.py`, `tests/unit/test_fence_lists.py`.
**Verify:** `uv run pytest tests/unit/test_ci_unit_guard.py tests/unit/test_fence_lists.py tests/unit/test_affected_tests.py -q`,
then one draft PR per class (docs-only, workbench-only, Python, migration),
with each run's job list and wall time in the PR.
AGENT-SAFE. STANDARD tier, with an adversarial review (it changes the merge gate).

### CIV-4 · `main` runs the full suite in batches (HIGH: deploy path)

**Done when:**
- deploy.yml's `test` job on a push to `main` has
  `concurrency: { group: main-full-suite, cancel-in-progress: false }`.
  GitHub then keeps one pending run, so merges that land during a run fold
  into the next one.
- The job summary names the tested range: the last green SHA to this SHA, and
  the PRs in it.
- On the 21:30 release and on a dispatch, the job still gates
  `publish-release`. That does not change.

**Files:** `.github/workflows/deploy.yml`, plus its fences under `tests/unit/test_deploy_*`.
**Verify:** `uv run pytest tests/unit -k deploy_workflow -q`, then evidence
from two merges that land during one run: one run, two PRs in its summary.
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

### CIV-7 · The harness says it

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

CIV-2, then CIV-3. CIV-4 runs beside them. CIV-7 lands with CIV-3. CIV-5,
CIV-6, CIV-8 and CIV-9 follow, in any order.

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
