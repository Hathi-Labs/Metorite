# Merge hotspots: why the BACK lines happen, and the fix

Owner: task 3284 (CIV-33). Evidence: the merge log of 2026-11-14.

## The problem

Most BACK lines on 2026-11-14 were not code conflicts. Each one was a
textual collision in a file that every PR edits at the same place:

| File | How a PR edits it | Why it collides |
|---|---|---|
| `project-docs/specs/ai_studio.md` §14 | It changes one row, or adds a row at the end of a group. | One row is one long line. A neighbour row or an added row touches the same hunk. |
| `docs/flag_reload.md` | It adds one paragraph for its flag, at the end. | Two PRs append at the same place. |
| `tests/unit/test_flag_reload.py` | It adds one test for its flag, at the end. | Two PRs append at the same place. |
| `packages/acb_common/acb_common/settings.py` | It adds a flag block after the last flag. | Two PRs append at the same place. |
| `tests/fences.json` | It adds one name to a sorted list. | Two names that sort next to each other share a hunk. |

## The rule for a fix

A fix must make a second PR safe, and must not hide a real conflict. A real
conflict is two PRs that change the same row or the same flag. A merge
driver that keeps both sides turns that case into a silent duplicate. So a
driver fits only a file where each entry stands alone.

## Slice 1 (built): union merge for the two pure append files

`docs/flag_reload.md` and `tests/unit/test_flag_reload.py` take
`merge=union` in `.gitattributes`. Each entry in them stands alone: a
paragraph or a test function. Git keeps both sides.

The driver runs wherever git merges on a lane: `premerge.sh`, and the merge
of main into a branch. GitHub's own merge does not read this attribute, and
`premerge.sh` already runs first, so the PR lands the tree it tested.

The fence is `tests/unit/test_merge_hotspots.py`. It checks the attribute,
proves that two appends merge clean in a throwaway repo, and refuses a
duplicate test name in `test_flag_reload.py`. The duplicate check catches the
one failure of a union: both sides edit the same line.

## Slice 2 (proposed): one file for each §14 row

Move each §14 row body to `project-docs/specs/ai_studio_rows/<id>.md`. Keep
one short index line in §14 with the id, the tier and a link. Then a status
change edits one small file that only its slice owns. Two PRs conflict only
when they edit the same slice. Fences to move first: `test_index_completeness.py`,
`test_doc_names_resolve.py` and each test that reads `ai_studio.md` §14 by
row id. This slice needs a scan of those readers, so it gets its own task.

## Slice 3 (proposed): `settings.py` flags in one registry

A flag block goes into a per-area list that is sorted by name, one entry for
each line. Two sorted insertions land in different hunks unless the names are
neighbours. A union driver is wrong here, because a duplicated field is a
Python error. `tests/fences.json` stays a sorted JSON list. Its commas make a
union driver unsafe, so its fix is the sort order that its fence already
holds.

## Measure

Count BACK lines of the cause "spec-row or flag-list conflict" in
`~/lanes/merge-queue.log` for the week before 2026-11-15 and the week after.
Report it on task 3066 (D218).
