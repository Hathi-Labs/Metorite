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
driver that keeps both sides turns that case into a silent duplicate, or
revives a block that a revert deleted. So we do not use one.

## Slice 1 (tried, rejected): union merge for the two append files

`merge=union` on `docs/flag_reload.md` and `tests/unit/test_flag_reload.py`
merges two appends with no conflict. The review found one failure that no
test sees. Main reverts a flag and so deletes its paragraph and its test. A PR
adds its own entry at the same anchor. The union keeps the PR's lines and the
deleted block, so the reverted flag comes back, with a passing test and a
paragraph that documents a flag that no longer exists. Four of the last five
PRs to that file added at that same anchor. So no union driver goes on a file
whose entries a revert can delete. We built none.

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
neighbours. A union driver is wrong here, for the same reason as in slice 1. `tests/fences.json` stays a sorted JSON list. Its commas make a
union driver unsafe, so its fix is the sort order that its fence already
holds.

## Measure

Count BACK lines of the cause "spec-row or flag-list conflict" in
`~/lanes/merge-queue.log` for the week before 2026-11-15 and the week after.
Report it on task 3066 (D218).
