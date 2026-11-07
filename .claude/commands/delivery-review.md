---
description: Weekly delivery review (CIV-10). Measure CI and the workflow, post one report task, and open one fix task for each item over its threshold.
argument-hint: "[days, default 7]"
---

# /delivery-review — the weekly check on delivery speed

Owner directive, 2026-11-04: "constantly revisit the complete development
workflow and CI/CD workflow so that we maintain the velocity of development".
A box user timer starts this command each Monday at 07:00 UTC as a headless
session with the Metorite MCP. `project-docs/specs/ci_velocity.md` (CIV-10)
owns it. The CI model is there too: the PR tier, the batch on main, the release.

Argument: `$ARGUMENTS` (default: 7 days).

## Thresholds

An item over its threshold gets one fix task. The owner may change a number.
Change it here and nowhere else.

| Metric key | Threshold | Why this number |
|---|---|---|
| `pr_check.p90_min` | over 30 | A PR must finish inside one coffee break. CIV-3 aims at 15 min. |
| `pr_check.red_rate` | over 0.20 | One red PR run in five wastes a rerun and a context switch. |
| `queue_wait.p90_min` | over 20 | A job that waits for a runner is idle time, not test time. |
| `batch_main.median_min` | over 60 | The full suite must fit inside the 21:30 release window. |
| `selection_misses.batch_red_after_pr_green` | over 0 | A miss means the PR selection skipped a test it needed. |
| `slowest_tests[].seconds` | over 180 for any file | One slow file sets the floor of the batch time. |
| Owner queue, oldest item | over 3 days | The owner wait is the biggest delay that CI cannot fix. |
| Stale hold | any hold stale over 2 hours | A stale hold blocks a task that nobody works. |
| `open_prs_older_than_2d` | any PR older than 2 days | Long branches cause collisions (root `CLAUDE.md` §4). |

## Rules for the headless session

1. Use only these tools: Bash (for `scripts/delivery_metrics.py` and read-only
   `gh` calls) and the Metorite MCP.
2. Never cancel or rerun a GitHub run. Never merge. Never push.
3. Never write to a task that you did not create. Create only the report task
   and the fix tasks of step 4.
4. Write all prose in Simplified Technical English (`docs/style_ste.md`).
   Use at most 20 words in a sentence, no semicolon, and the active voice.
5. A metric that is `null` is not a pass. Report it as "no data" and copy its
   line from `errors`. Do not open a fix task for it.

## Steps

### 1. Measure CI

Find the last report. Call `search_tasks` with "Delivery review" in project
Developer Workflow, take the newest report task, and read its description. The
last JSON sits there in a fenced `json` block. Write it to
`/tmp/delivery_prev.json`. If no report task exists, skip `--compare`.

```bash
python3 scripts/delivery_metrics.py --days 7 --compare /tmp/delivery_prev.json --out /tmp/delivery_now.json
```

The script prints one JSON object. Its keys:

- `pr_check`: `runs`, `median_min`, `p90_min`, `red_rate`, `top_failing_jobs`
  (name and count over the red runs).
- `queue_wait`: `jobs`, `median_min`, `p90_min`, `max_min`.
- `batch_main`: `runs`, `median_min`, `p90_min`, `red_rate`. It counts the
  `deploy.yml` runs with event push on `main`.
- `selection_misses`: `batch_red_after_pr_green`, `red_batches`, `matched`.
- `slowest_tests`: the 10 slowest files, with `seconds`.
- `open_prs_older_than_2d`: `number`, `title`, `age_days`.
- `delta`: this week minus last week, for every number. It exists only with
  `--compare`.
- `errors`: the calls that failed. Their metrics are `null`.

### 2. Measure the Metorite items

Call the MCP. The script cannot do it.

- **Owner queue.** Find the task "Owner queue · the daily pass". Count its open
  items. Find the age of the oldest one in days.
- **Stale holds.** Call `list_tasks` for tasks In progress. Keep those with
  `held_by.stale` true. Note the task number and the hold age.
- **Waits on the owner.** Call `list_agent_runs` with `needs_agent` false and
  status awaiting. Count them, and note the oldest.

### 3. Compare with the thresholds

Build one table. Use one row for each line of the Thresholds table above.
Give the measured value, the delta from last week, and a verdict of ok, over or
no data.

### 4. Post the tasks

1. Create ONE report task in project Developer Workflow (id
   `519d9f0d-8dde-4910-b733-b4fa2ed3c2b9`). Title it `Delivery review YYYY-MM-DD`
   with today's date. Set its parent to epic task 1628 (id
   `ed3027f8-bf27-4f56-9a40-2c89141606d0`).
2. Put the comparison table and the deltas in its description.
3. Add the full JSON of `/tmp/delivery_now.json` in a fenced `json` block at
   the end of the description. The next run reads it as its last week.
4. For each item over its threshold, create ONE fix task. Title it
   `Fix: <metric key> over <threshold>`. Set importance 2 and parent epic 1628.
   Put the measurement and the cause in the description. For a red rate, list
   `top_failing_jobs`.
5. Before each fix task, call `search_tasks` for its title. If an open task
   with the same title exists, skip it and say so in the report task.
6. Add a comment on the report task that lists the fix tasks you opened.

### 5. Stop

Finish the run. The report task is the only output the owner needs to read.
