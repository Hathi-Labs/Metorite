# Close candidates, PC landing lane (task 4039)

Date: 2026-11-26. Main at the check: `a77215c969`. Row 2534: `78c89e45f7`. Lane: pc-land-core.

**The owner decides once.** Approve the list, or name the rows to keep. The lane
closes no pull request.

| PR | Task | Why | Evidence on main | What stays to do |
|---|---|---|---|---|
| 1091, late Approve of a parked card | 3231 (Done) | Main has the change | PR 2323 (`3a140091e`) added `late_approve.py` and `CHAT_LATE_APPROVE_ORGS` | Nothing. Task 3294 gates the flag flip |
| 1898, KG-6 graph step | 791 | Main has a different design | FIND-3 (PR 2450) built the related step and `Source.open`. `find_engine.md` says KG-6 adds its edges to that step | Build the `kb_links` edges on the FIND-3 step. Keep the R8 cases of PR 1898 |
| 1902, KG-7 `get_entity` | 792 | A decision replaced it | D-FIND-3 in `find_engine.md` section 10: `get_entity` becomes the card mode of `resolve_name` | Give tasks 792 and 793 a new scope. The card mode is not built |
| 2653, skill offer draft | 3829 | Replaced | PR 2884 holds the same code in one clean commit. The Secret scan read two test literals in an old commit of PR 2653 | PR 2884 merged on 2026-11-26 (`dcbc49983`). Close now |
| 2534, mark a session stale on an unread steer | 3640 | Main has the behaviour | The final PR 2521 (`3829199e07`) added `rows_for_digest`. `test_r8_a_late_steer_drops_the_session` passes: the next turn drops the session. Main merged at `165ec0928a`, 89 R8 tests passed | Nothing. The PR only changes the drop reason, and it can drop one more session than needed |

## Approved by the owner

| PR | Task | Owner answer | What stays to do |
|---|---|---|---|
| 1692, spec for chat-built tools | 1655 | 2026-11-26, on the task: "Close PR 1692 and keep decision D182 as the record" | The close act. The PC lane closes no pull request |

## Not on this list, but the owner must answer

| PR | Task | Question | Recommendation |
|---|---|---|---|
| 1793, seat tiers | 1280 | The task waits on the base price (D204 point 9) | Give the numbers, or let the per-seat price be the base price |
| 1400, chat wave 2 | 2124 | Answered 2026-11-26: a task moves with its new parent. Cut the missing parts again in small PRs | The re-cut task. Close PR 1400 after its last part merges |
| 2094, agent work review | 349 | Answered. A box lane has built on it since 2026-11-27 12:44 UTC | That lane lands it. Its run was quiet from 17:19 UTC |

## Result, 2026-11-27 23:35 UTC

The PC lane merged 20 pull requests of its list. Another lane merged PR 2515. Two open pull requests wait on the owner: 2163 (screen approval, task 859) and 1998 (`TOOL_PACKS`, task 1680).
