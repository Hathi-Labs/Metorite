# Close candidates, PC landing lane (task 4039)

Date: 2026-11-26. Main at the check: `a77215c969`. Lane: pc-land-core.

**The owner decides once.** Approve the list, or name the rows to keep. The lane
closes no pull request.

| PR | Task | Why | Evidence on main | What stays to do |
|---|---|---|---|---|
| 1091, late Approve of a parked card | 3231 (Done) | Main has the change | PR 2323 (`3a140091e`) added `late_approve.py` and `CHAT_LATE_APPROVE_ORGS` | Nothing. Task 3294 gates the flag flip |
| 1898, KG-6 graph step | 791 | Main has a different design | FIND-3 (PR 2450) built the related step and `Source.open`. `find_engine.md` says KG-6 adds its edges to that step | Build the `kb_links` edges on the FIND-3 step. Keep the R8 cases of PR 1898 |
| 1902, KG-7 `get_entity` | 792 | A decision replaced it | D-FIND-3 in `find_engine.md` section 10: `get_entity` becomes the card mode of `resolve_name` | Give tasks 792 and 793 a new scope. The card mode is not built |
| 2653, skill offer draft | 3829 | Replaced | PR 2884 holds the same code in one clean commit. The Secret scan read two test literals in an old commit of PR 2653 | Close after PR 2884 merges |

## Not on this list, but the owner must answer

| PR | Task | Question | Recommendation |
|---|---|---|---|
| 1692, spec for chat-built tools | 1655 | D187, D224 and D233 replaced parts of this spec. Close it, or write it again? | Close it. Keep D182 as the record |
| 1793, seat tiers | 1280 | The task waits on the base price (D204 point 9) | Give the numbers, or let the per-seat price be the base price |
| 1400, chat wave 2 | 2124 | Does a move to a new parent move the task to the project of that parent? | Cut the PR again in small slices |
| 2094, agent work review | 349 | Keep the new column, or use the policy store of SW-94? | Use the policy store |
| 2515, MM-1c acts | 3615 | Do you accept `proactive_loop.md` section 5.3? | Drop that one hunk and land the rest |
