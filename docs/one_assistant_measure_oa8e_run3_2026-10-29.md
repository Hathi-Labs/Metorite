# The CRM, Projects and sub-agent measure, run 3 (WS-67 OA-8e, 2026-10-29)

**Verdict: OA-8e is MET for CRM, NOT met for Projects, and NOT met for sub-agents. OA-10 must not start.** Run 3 is much better than run 2 on CRM and on layer (b). Three stop results still occur: result 1, result 9 and result 11.

**Answer, separately:**
- **CRM: met.** The pack arm passes 34 of 36 turns. Delegation passes 21 of 36. No stop result names a CRM pack turn. The two failures are `crm_pipeline` 2 r1 (a wrong answer from the read) and `crm_mirror` 2 r3 (a 200 s timeout, no write).
- **Projects: not met.** The pack arm passes 45 of 57 and delegation passes 49 of 57, so the packs do not beat delegation on this set. Stop 1 occurs (`pm_archive` 2 r3) and stop 11 occurs (`pm_miss` r2 handed off by model choice). The `pm_miss` chat passes 0 of 3 on the pack arm.
- **Sub-agents: not met.** The model now spawns on `fan_companies` (3 of 3) and never on the three chats that must not spawn (stop 10 is ok). But `items_contract` passes 1 of 3, the child answers lose companies (`names_each_company` 1 of 3), and `fan_invoices` is never spawned. Stop 9 occurs: the research median is 32.9 s with spawn and 10.3 s without. Stop 1 occurs on `fan_tasks`.

**Packs still beat delegation on speed and cost.** Across the 105 app turns: pass rate 83.8 percent against 71.4. Turn median 7.2 s against 17.2 s. Calls per turn 3.0 against 5.3. Cost per turn USD 0.0056 against 0.0083.

## 1. What ran

- Code: `c6f4b8025` on `ws67-oa8e-run3-mt-4155ddfc`. It is origin/main (`ec9a837bb`) plus two unmerged branches: OA-8e fix round 3 (PR #1469) and OA-8g. No product code or harness changed in this run.
- Settings: the same as run 2, including `ORCHESTRATOR_FLOOR_TRIM=true` on both arms, in the eval process only. No flag was set on any box.
- Chats: the same 30 as run 2, 3 repeats, 240 turns. The 25 work chats ran on `delegation` and `pack` (105 turns each). The 5 fan-out chats ran on `pack` and `packs+spawn` (15 turns each).
- Model: the production Router through an SSH tunnel on local port 18090. The orchestrator asked `tier-balanced`.
- Chunks: 12 chunks of 8 to 34 turns, run one after another with `--timeout 200` and `--spent-before-from`. Each chunk ended in under 10 minutes. No chat dropped, and none was re-run.
- Cost: USD 1.75 in all (cap USD 6). Run 2 cost USD 2.06.
- Tunnel: opened as process 42956, closed by that PID, and port 18090 is free.
- Raw results: `evals/one_assistant_measure/results_oa8e_run3_2026-10-29.json` (tracked, committed).

## 2. What changed in the code since run 2

Run 2 ran at `09c0bf526`. Run 3 runs at `c6f4b8025`. The changes that matter:
- **OA-8g** (`c5d09504b`, `efd52d679`, `c6f4b8025`): pack rules, pack load words (a bare "capacity" no longer loads the plan pack) and the spawn cue. It follows from the run 2 rows.
- **OA-8e fix round 3** (`a8a0fc0fe`, PR #1469): invented fixture hosts in place of `.example`, refusal texts, writes proven at the gateway, wording checks, and the run 2 re-score.
- The other main commits in between (CRM-U15, Projects board, viewer) do not touch the measure path.

## 3. Run 2 (re-scored) against run 3

Run 2 pass counts use the fix round 3 re-score. Run 2 medians come from the run 2 report.

| Set | Arm | Pass run 2 | Pass run 3 | Turn median s, run 2 / run 3 | Calls per turn, run 2 / run 3 | USD per turn, run 2 / run 3 |
|---|---|---|---|---|---|---|
| CRM | delegation | 23/36 | 21/36 | 14.7 / 11.7 | 4.81 / 4.53 | 0.0078 / 0.0035 |
| CRM | pack | 30/36 | 34/36 | 6.9 / 7.7 | 3.19 / 3.25 | 0.0083 / 0.0038 |
| Projects | delegation | 42/57 | 49/57 | 21.0 / 19.6 | 5.05 / 5.23 | 0.0086 / 0.0085 |
| Projects | pack | 46/57 | 45/57 | 7.6 / 6.2 | 3.25 / 2.83 | 0.0076 / 0.0061 |
| Mixed | delegation | 6/12 | 5/12 | 31.4 / 38.2 | 9.67 / 8.25 | 0.0212 / 0.0216 |
| Mixed | pack | 5/12 | 9/12 | 9.6 / 9.8 | 4.67 / 3.92 | 0.0115 / 0.0073 |
| All app turns | delegation | 71/105 | 75/105 | 18.7 / 17.2 | 5.5 / 5.3 | 0.0097 / 0.0083 |
| All app turns | pack | 81/105 | 88/105 | 7.2 / 7.2 | 3.2 / 3.0 | 0.0077 / 0.0056 |

| Set (pack arm) | Layer (b), run 2 | Layer (b), run 3 | Hand-offs by model choice, run 2 | Run 3 |
|---|---|---|---|---|
| CRM | 2 loads | 2 turns of 36 | 1 | 0 |
| Projects | 14 loads | 4 turns of 57 | 5 | 1 (`pm_miss` r2) |
| Mixed | 0 | 0 | 1 | 1 (`mix_task_from_mail` 2 r2) |
| All app turns | 21.2% of turns that needed a pack | 6.1% (4 of 66) | 7 | 3 (incl. `fan_tasks` r2) |

Stop results, run 3: 1 STOP, 2 ok (83.8 against 71.4 percent), 5 ok (6.1 percent, limit 15), 6 ok (14.2 percent), 7 ok, 8 ok (102 tools, cap 110), 9 STOP, 10 ok (0 of 9), 11 STOP. Results 3 and 4 are n/a (they need the general set).

Stop 1 names `fan_tasks` 1, `mix_call_then_task` 2 (delegation), `pm_archive` 2 (delegation and pack r3) and the spawn-arm `fan_tasks` turns. Stop 11 names three turns, all Projects assistant and all `model_choice`: `pm_miss` r2, `mix_task_from_mail` 2 r2 and `fan_tasks` r2 (pack).

## 4. Sub-agents

| Measure | pack | packs+spawn |
|---|---|---|
| Turns that pass | 12/15 | 6/15 |
| `spawned` (`fan_companies`, `fan_invoices`) | n/a | 3/6 (companies 3/3, invoices 0/3) |
| `not_spawned` (tasks, one read, three searches) | n/a | 9/9 |
| `items_contract` | n/a | 1/3 |
| `names_each_company` | 3/3 | 1/3 |
| `fan_companies` wall time, s (r1 / r2 / r3) | 16.8 / 21.1 / 11.0 | 30.7 / 33.9 / 32.9 |
| Research median, s (stop 9) | 10.3 | 32.9 |
| Cost per turn USD | 0.0065 | 0.0138 |

- On `fan_companies` the model spawned in every repeat, which run 2 never did. The spawn cue works. The spawn costs about 20 s more than reading five pages, and two of three answers lose companies.
- `items_contract` failed twice with "no job block" (r1, r2). The spawn call carried no job block.
- `fan_invoices` is never spawned. The pack arm passes 1 of 3: in r1 and r2 the model said it cannot reach the billing site and made no fetch. In r3 it fetched 10 pages and totalled them. The new fixture host removed the `.example` refusal only in part.
- `fan_tasks` on the spawn arm passes 0 of 3: five `create_personal_task` calls in a row, 138 s, 200 s (a timeout) and 160 s.

## 5. The failing pack-arm turns (20 of 120)

Cause marks: **P** is a product behaviour. **H** is a harness defect or a doubtful check. The cause comes from the stored answer text and tool names only.

| Chat, turn, repeat | Failed checks | Cause |
|---|---|---|
| `crm_pipeline` 2 r1 | `names_my_deals` | The answer said none of the deals is the member's, after one `get_pipeline` call. P (wrong answer from the read). |
| `crm_mirror` 2 r3 | `ran`, `names_zoho_or_offers_a_note` | Timeout at 200 s after `search_crm`, `crm_list_pipelines` and `get_record`. No write. P (slow run). |
| `fan_invoices` 1 r1, r2 | `totals_the_invoices` | The model said it cannot reach an outside billing site and made no fetch. P (a refusal). The fixture host may still read as unreal, so a partial H. |
| `fan_tasks` 1 r2 | `tasks_made`, `no_unasked_write`, `no_projects_delegation` | Five `create_personal_task` calls and 5 cards, plus a hand-off to the Projects assistant. P. The `create_tasks` batch tool was not used. |
| `pm_project_view` 1 r1, r2 | `read_the_project`, r1 also `gives_the_count` | The model opened the board with `render_board` and did not call `project_summary`. P. The r2 answer was right on content, so the check is strict. |
| `pm_project_view` 2 r2 | `listed_overdue`, `names_the_late_tasks` | The model searched for "mobile app" three times and found nothing. P. |
| `pm_archive` 2 r1 | `archived`, `right_pack`, `says_how_many` | The model asked "archive or delete?" before the card. P (asked once too often). |
| `pm_archive` 2 r3 | `archived`, `no_unasked_write`, `one_card_per_write` | Six `archive_task` calls: three, then `find_tasks`, then three more. The answer lists all three as archived. H probable: the checks count calls and not writes that landed. |
| `pm_private_task` 1 r1, r3 | `task_captured`, r3 also `right_pack` | The model asked which project and made no private task. P. |
| `pm_miss` 1 r1, r3 | `read_the_project`, `right_pack` | The model loaded no pack and said "Mobile App" does not exist. P (the layer (b) gap on a vague cue). |
| `pm_miss` 1 r2 | `no_projects_delegation` | The model loaded `projects`, read four tools and handed off. P (model choice). |
| `pm_no_surface` 1 r2 | `listed_overdue`, `names_the_late_tasks` | `my_work` answered "none overdue". P. |
| `pm_no_surface` 2 r2 | `answers_from_the_last_read` | The model searched again and answered from new reads. P. |
| `mix_task_from_mail` 2 r1, r3 | `task_made_from_the_mail` | The model asked which project, or called `create_task` in a team project. P. This check passes 0 of 6 on both arms, so it may be too strict. H possible. |
| `mix_task_from_mail` 2 r2 | `ran`, `task_made_from_the_mail`, `right_pack`, `no_projects_delegation` | A hand-off to the Projects assistant and a 200 s timeout. P. |

No cause is a certain harness defect. Two checks need a look: `pm_archive` 2 (it counts calls) and `task_made_from_the_mail`.

## 6. Time, tokens and cost

All 120 pack turns, against the 105 delegation turns: first text median 2.05 s against 1.63 s, mean 5.3 s against 10.8 s. Turn median 7.2 s against 17.2 s, mean 12.7 s against 32.0 s. Input tokens per turn 72k against 150k. Cache share 0.93 against 0.99. The largest request carried 102 tools, cap 110. Layer (a) time: mean 45 ms, p90 125 ms. The spawn arm costs USD 0.0138 a turn, twice the pack arm, mostly from the `fan_tasks` runs of 138 to 200 s.

## 7. Limits

1. Three repeats of 35 turns give small counts. One turn moves a set by about 3 points.
2. The `general` and `miss` sets, the run with no packs (stop results 3 and 4) and the email chats did not run.
3. The checks `pm_archive` 2 and `task_made_from_the_mail` need a review (section 5).
4. Run 2 is re-scored with the new checks. Run 3 used them from the start, so the two are close but not identical in method.

## 8. What comes next

- Projects: `pm_miss` (0 of 3) and `pm_private_task` (1 of 3) need a rule or load-word edit. A task from a mail or call should default to a private task.
- Sub-agents: the spawn call needs a job block (`items_contract`), and the child answer must keep all companies. `fan_invoices` needs a cue. OA-9e must decide whether a 20 s cost for five pages is the intended trade.
- `fan_tasks`: the model makes five single tasks and not one `create_tasks` call. A rule edit may fix it and cut 150 s.
- Then run 4.

## Appendix: pass by scenario (all repeats)

| Scenario | delegation | pack | packs+spawn |
|---|---|---|---|
| crm_pipeline | 0/6 | 5/6 | - |
| crm_deal_story | 1/3 | 3/3 | - |
| crm_move_deal | 3/6 | 6/6 | - |
| crm_log_call | 3/3 | 3/3 | - |
| crm_new_lead | 3/3 | 3/3 | - |
| crm_convert | 2/3 | 3/3 | - |
| crm_mirror | 6/6 | 5/6 | - |
| crm_open_deals | 0/3 | 3/3 | - |
| crm_miss | 3/3 | 3/3 | - |
| pm_my_day | 6/6 | 6/6 | - |
| pm_project_view | 4/6 | 3/6 | - |
| pm_find_task | 3/3 | 3/3 | - |
| pm_capacity | 3/3 | 3/3 | - |
| pm_capacity_open | 3/3 | 3/3 | - |
| pm_stuck | 6/6 | 6/6 | - |
| pm_status | 3/3 | 3/3 | - |
| pm_tags | 3/3 | 3/3 | - |
| pm_archive | 4/6 | 4/6 | - |
| pm_new_tasks | 3/3 | 3/3 | - |
| pm_calendar | 3/3 | 3/3 | - |
| pm_private_task | 3/3 | 1/3 | - |
| pm_no_surface | 2/6 | 4/6 | - |
| pm_miss | 3/3 | 0/3 | - |
| mix_task_from_mail | 3/6 | 3/6 | - |
| mix_call_then_task | 2/6 | 6/6 | - |
| fan_companies | - | 3/3 | 0/3 |
| fan_invoices | - | 1/3 | 0/3 |
| fan_tasks | - | 2/3 | 0/3 |
| fan_one_read | - | 3/3 | 3/3 |
| fan_three_searches | - | 3/3 | 3/3 |
