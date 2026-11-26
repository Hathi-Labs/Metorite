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
| 1835 | 1688 | PR 2748, merged | Commit `c06e2e0078` put each line that this PR adds on `main`. |
| 1903 | 1690 (Done) | PR 2748, merged | `main` holds the split into WT-6a and WT-6b and the `HELD_CLASS_C_ORGS` text. Only an older WT-6a row and the D4 line differ. |
| 2587 | 3736 | PR 2644, merged | Commit `7624d589ab` built SEM-1b and records D-SEM-3 = option A. Not on `main`: the table of six conflicts, acceptance (a) to (j), D-SEM-12 and D-SEM-13. If you still want those two decisions, they need a small new edit. |

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

PR 2860 was red itself, because of `tests/unit/test_scanner_budget.py`. The lane
repaired it and handed it to the merge bot. Task 4037 owns the slow scan fences.

## Need an owner

| PR | Task | Why |
|---|---|---|
| 1769 | 1400 | You parked it on 2026-11-06. A task comment of 2026-11-11 says that we will switch to Anthropic or Google models, so the OpenRouter design needs a rewrite or a close. The slices OR-1 to OR-10 need the same decision. |
