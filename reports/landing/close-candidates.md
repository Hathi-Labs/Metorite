# Close candidates for the owner (task 4019)

Date: 2026-11-26. Author: lane `lane-land`. Task:
https://app.metorite.com/projects?task=105ff8ac-bed2-4f86-ac23-51e0de982738

No lane closed any of these PRs. The owner approves the closes in one batch.

## Finished on main by another PR

| PR | Task | Replaced by | Evidence |
|---|---|---|---|
| 1846 | 2006 | PR 2068, merged | Commit `9c0872991f` on `main` has the same title. PR 2068 is the "v2" branch of the same slice. |
| 2056 | 336 | PR 2776, merged | Commit `d9f8e8d8a6` on `main`. The body of 2776 says that it is PR 2056, finished on a new branch. |
| 2137 | 1049 | PR 2774, merged | Commit `481fddc097` on `main`. The body of 2774 says that it is PR 2137, finished on a new branch. |

## Old daily refreshes of `.test_durations`

Each of these PRs changes only `.test_durations`. PR 2860, of 2026-11-26, is the
newest refresh, so it replaces each of them.

| PR | Date |
|---|---|
| 2371 | 2026-11-15 |
| 2442 | 2026-11-16 |
| 2580 | 2026-11-18 |
| 2659 | 2026-11-20 |
| 2679 | 2026-11-21 |
| 2717 | 2026-11-22 |
| 2796 | 2026-11-23 |
| 2822 | 2026-11-24 |
| 2828 | 2026-11-25 |

PR 2860 is red itself. `tests/unit/test_scanner_budget.py` fails on shard 2. Two
scanner files cost more than 30 seconds with the new data. One listed file costs
less than 20 seconds. Each daily refresh stays open until a lane repairs this.

## Need an owner

This section lists draft or unverified PRs whose task states no acceptance
criteria. The triage adds them here.
