# The Projects, mixed and fan-out measure, run 5 (WS-67 OA-8e, 2026-10-30)

**Projects verdict: met by the stop rules.** Stop 1, 2, 5 and 11 are all ok on the pack arm. The `mix_task_from_mail` double write is gone. Two cautions follow. Pack ties delegation on pass rate (59 of 69 each). Also `mix_call_then_task` fell from 6 of 6 to 3 of 6 on the pack arm.

**Production note (2026-10-30 20:50 UTC).** The Projects packs went live in production at 09:16 UTC. They reach no member of Fracktal Works, Hathi Labs or Shaurrya Teleservices. Production `MAF_CODING_SCOPE` holds the `projects` target for those three orgs. So `_run_covered` is true there, and the OA-8b rule loads no Projects pack in a covered org. The specialist keeps that work. Each of the 9 production `executor.pack_plan` lines after 09:16 offered only `email`, `email-setup` and `crm-records`. This run and staging had no `MAF_CODING_SCOPE`, so they could not show it. The owner decides the scope (gate `ws43-sandbox-flip`).

**Sub-agents verdict: NOT met, and not fully measured.** Stop 9 and stop 10 both occur on the data that is valid. A provider outage stopped the run part way. It spoiled 9 of 12 `fan_contracts` rows. The last chunk (`fan_tasks`, `fan_one_read`, `fan_three_searches`) did not run.

**Answer, separately:**
- **Stop 1 is ok on the pack arm.** `no_unasked_write` passes 81 of 81 pack turns. The harness lists two delegation turns (`mix_task_from_mail` 2 r2, `mix_call_then_task` 2 r2). No pack turn is named.
- **Stop 2 is ok, as a tie.** Pack passes 59 of 69 on the Projects and mixed sets. Delegation passes 59 of 69. Both are 85.5 percent.
- **Stop 5 is ok.** Layer (b) occurs on 6 of 51 turns that needed a pack (11.8 percent, limit 15). They are `pm_status` x3 and `pm_miss` x3.
- **Stop 11 is ok.** No hand-off ran on a packed app. OA-8i refused 3 attempts, all in `pm_miss` (`handoffs_refused`, not counted). `pm_miss` now passes 3 of 3 on the pack arm. Run 4 had 0 of 3.
- **Stop 9 occurs.** Valid pair: `fan_contracts` r1 took 28.2 s on `pack` and 43.3 s on `packs+spawn`. The harness median (43.3 s against 3.2 s) is spoiled by the outage.
- **Stop 10 occurs.** The model spawned on 2 of 6 `not_spawned` turns (`fan_invoices` r1, `fan_companies` r2). The limit is 1 in 9. Only 6 of the 9 turns ran.
- **`items_contract` is 3 of 3.** Run 3 had 1 of 3. The job block is now there.
- Stop 3 and 4 are n/a. They need other sets.

## 1. What ran

- Code: `4c6bb8257` on `ws67-oa8e-run5-mt-4155ddfc`. It is origin/main plus OA-8i (PR #1509). No product code or harness changed.
- Chats: the 14 `pm_*` chats, `mix_task_from_mail` and `mix_call_then_task` on `delegation` and `pack`. Then `fan_companies`, `fan_invoices` and `fan_contracts` on `pack` and `packs+spawn`. 3 repeats. 186 turns in all: 162 on Projects and mixed, and 24 fan-out.
- Not run: `fan_tasks`, `fan_one_read` and `fan_three_searches`. The Router answered 502 from the start of that chunk. The CRM set did not run, as asked.
- Model: the production Router through an SSH tunnel on local port 18090. The orchestrator asked `tier-balanced`.
- Chunks: 7 chunks, one after another, with `--timeout 240` and `--spent-before-from`. The mixed chunk took about 9 minutes. No other chunk ran longer.
- Cost: USD 1.51 (cap USD 6). Run 4 cost USD 1.36.
- Tunnel: opened as process 44660 and closed by that PID. Port 18090 has no listener.
- Raw results: `results_oa8e_run5_2026-10-30.json` in the scratch folder. They are not committed.

**The outage.** During chunk g (`fan_contracts`) the vendor `deepseek` began to answer HTTP 402 (payment required) through the Router. The Router then answered 502 "upstream provider error" on every call. The box log shows 38 such errors in 3 hours. I probed for about 35 minutes and the 502 did not clear. The vendor balance is a money matter, so I did not act on it. Chunks a to f have no empty answer, so their rows are valid.

## 2. What changed in the code since run 4

Run 4 ran at `07f27e558`. Run 5 runs at `4c6bb8257`.
- **OA-8i** (PR #1509): no double write between a pack and its specialist in one turn. The harness counts a refused hand-off in `handoffs_refused`.
- **OA-9f**: the spawn rule (spawn for long material, not for short reads) and the heavy chat `fan_contracts` with a turn 2.
- **OA-9c** and **OA-8g/8h** were already in run 4 or earlier.

## 3. Run 4 against run 5, Projects and mixed

| Set | Arm | Pass run 4 / run 5 | Turn median s | Calls per turn | USD per turn |
|---|---|---|---|---|---|
| Projects | delegation | 49/57 / 50/57 | 18.2 / 21.6 | 4.91 / 4.91 | 0.0081 / 0.0084 |
| Projects | pack | 53/57 / 52/57 | 6.6 / 6.2 | 2.88 / 3.09 | 0.0067 / 0.0051 |
| Mixed | delegation | 6/12 / 9/12 | 40.0 / 37.3 | 9.83 / 6.42 | 0.0284 / 0.0194 |
| Mixed | pack | 9/12 / 7/12 | 8.7 / 8.2 | 5.75 / 3.25 | 0.0149 / 0.0063 |

| Set (pack arm) | Layer (b), run 4 | Layer (b), run 5 | Hand-offs by model choice, run 4 | Run 5 |
|---|---|---|---|---|
| Projects | 5 turns of 57 | 6 turns of 57 (`pm_status` x3, `pm_miss` x3) | 2 | 0 (3 refused) |
| Mixed | 0 | 0 | 2 | 0 |

Over both sets, the pack turn median is 6.7 s against 22.9 s for delegation.

### Pack arm, pass by scenario

| Scenario | Run 4 | Run 5 | Note |
|---|---|---|---|
| pm_project_view | 5/6 | 4/6 | Two strict checks (section 6). |
| pm_stuck | 6/6 | 5/6 | One `names_the_blocked_task`. |
| pm_tags | 3/3 | 2/3 | The model asked which tasks. |
| pm_private_task | 3/3 | 2/3 | The model asked a question first. |
| pm_miss | 0/3 | 3/3 | **OA-8i works.** The pack answers and the hand-off is refused. |
| mix_task_from_mail | 3/6 | 4/6 | **The double write is gone.** |
| mix_call_then_task | 6/6 | 3/6 | New failures (section 6). |
| All other pm chats | all pass | all pass | |

Delegation, run 5: `pm_no_surface` 3/6, `pm_project_view` 4/6, `pm_stuck` 5/6, `pm_calendar` 2/3, `mix_call_then_task` 4/6 and `mix_task_from_mail` 5/6. All other chats pass in full.

## 4. The double write

**Gone.** `mix_task_from_mail` turn 2 on the pack arm: r2 made one private task (`create_personal_task`, 1 card, 1 gateway write, #901, 6 s). r3 made one team task (1 card, 1 write). r1 made no write. No pack turn has two cards or two writes. No hand-off ran. Run 4 had two cards and a hand-off in r2 and r3.

The task is still not always private. r3 chose the Website Relaunch project, and r1 asked and made none. The check `task_made_from_the_mail` passes 1 of 3 on the pack arm.

## 5. Sub-agents

Run 3 against run 5. Run 3 had 15 turns on each arm. Run 5 has 12 turns on each arm, and 9 `fan_contracts` rows are spoiled.

| Measure | pack, run 3 / run 5 | packs+spawn, run 3 / run 5 |
|---|---|---|
| Turns that pass | 12/15 / 8/12 | 6/15 / 5/12 |
| Valid turns that pass | n/a / 8/8 | n/a / 5/7 |
| `spawned` on the research chat | n/a | 3/3 on `fan_companies` / 1 of 1 valid on `fan_contracts` |
| `not_spawned` | n/a | 9/9 / 4/6 (stop 10 occurs) |
| `items_contract` | n/a | 1/3 / 3/3 |
| `names_each_company` | 3/3 / 3/3 | 1/3 / 3/3 |
| `fan_companies` time, s (r1 / r2 / r3) | 16.8 / 21.1 / 11.0 → 18.3 / 16.5 / 18.9 | 30.7 / 33.9 / 32.9 → 19.5 / 22.6 / 17.4 |
| `fan_invoices` time, s (r1 / r2 / r3) | not in the run 3 report → 11.1 / 11.3 / 10.9 | not in the run 3 report → 18.3 / 10.2 / 11.1 |
| Cost per turn USD | 0.0065 / 0.0154 | 0.0138 / 0.0209 |

- **The spawn rule works in part.** On `fan_companies` the model spawned in 1 of 3 repeats (run 3: 3 of 3). On `fan_invoices` it spawned in 1 of 3 (run 3: 0 of 3). Two spawns on light reads break stop 10. A spawn cost USD 0.028 to 0.030, against 0.004 to 0.006 for the same read in one step.
- **The spawn on `fan_companies` r2 was slow.** It took 22.6 s with 13 worker calls, against 16.5 s on `pack`.
- **The child answers are better.** `names_each_company` is 3 of 3 on both arms. `items_contract` is 3 of 3.

### `fan_contracts`: the one valid pair (r1)

| Arm | Turn | Total time s | Orchestrator input tokens | All input tokens | Cost USD | Result |
|---|---|---|---|---|---|---|
| pack | 1 | 28.2 | 196,512 | 196,512 | 0.1209 | pass |
| pack | 2 | 8.9 | 15,933 | 15,933 | 0.0181 | pass (Kaveri, Rs 15,000) |
| packs+spawn | 1 | 43.3 | 139,927 | 312,348 | 0.1524 | pass, spawned, 19 worker calls, 24 fetches |
| packs+spawn | 2 | 2.9 | 0 | 0 | 0.0 | **spoiled**: empty answer (outage) |

- On turn 1 the spawn arm is 15 s slower and costs USD 0.03 more. It uses 29 percent fewer orchestrator input tokens (140k against 197k). It uses 59 percent more input tokens in all (312k against 197k).
- Turn 2 on the pack arm carries 15.9k input tokens. The spawn arm turn 2 has no valid data, so the question "what does the parent carry forward" stays open.
- The other 8 `fan_contracts` rows (r2 and r3 on both arms) have an empty answer in 2.9 s. They show no product fault. The ninth spoiled row is the r1 spawn turn 2.

## 6. The failing pack and spawn turns

Cause marks: **P** is a product behaviour. **H** is a harness defect or a doubtful check. **O** is the outage. The cause comes from the stored answer, tool names, cards and writes.

| Chat, turn, repeat | Failed checks | Cause |
|---|---|---|
| `pm_project_view` 1 r2 | `read_the_project`, `gives_the_count` | Tools: `projects_tree`, `render_board`. The answer gave 2 open and 3 done, which is right. H (strict, as in runs 3 and 4). |
| `pm_project_view` 1 r3 | `gives_the_count` | Tools include `project_summary`. The answer says 2 open and 3 done. H (strict count wording). |
| `pm_stuck` 1 r3 | `names_the_blocked_task` | Tool: `analytics_stuck`. The answer names #23 as stuck and as blocking other work. H possible. P not excluded. |
| `pm_tags` 1 r1 | `tags_made`, `right_pack` | Tools: `find_tasks` x2, `projects_tree`, `list_tasks`. The model asked which two tasks to tag. No pack loaded. P. |
| `pm_private_task` 1 r2 | `task_captured` | Tool: `projects_tree`. The model chose a private task, then asked for a date. No write. P (asked once too often). |
| `mix_task_from_mail` 2 r1 | `task_made_from_the_mail` | Tools: `create_task`, `projects_tree`, `create_task`. No card and no gateway write. The model asked which project. P. |
| `mix_task_from_mail` 2 r3 | `task_made_from_the_mail` | Tools: `projects_tree`, `create_task`. One card, one write in Website Relaunch (a team project). P (the private rule did not fire). |
| `mix_call_then_task` 1 r1 | `call_logged` | Tools: `search_crm` x2, `log_activity`, 1 card, 1 write. The note went to the contact. The same check fails on delegation r3. H probable. |
| `mix_call_then_task` 2 r1 | `task_captured`, `right_pack` | Tools: `search_crm` x2, `log_activity`. The model logged the task in the CRM and not in Projects. P. |
| `mix_call_then_task` 2 r2 | `task_captured`, `right_pack` | One orchestrator call, no tool. The model asked "which project, or private?". P (asked, no write). |
| `fan_invoices` 1 r1 (spawn) | `not_spawned` | The model spawned and 4 worker calls read the pages. The total is right (Rs 2,33,400). P (stop 10). |
| `fan_companies` 1 r2 (spawn) | `not_spawned` | The model spawned with 13 worker calls. The table is right. P (stop 10). |
| `fan_contracts`, 9 rows | many | Empty answer, 2.9 s, one orchestrator call. Rows: turn 1 and 2 of r2 and r3 on both arms, and turn 2 of r1 on the spawn arm. O. Not a product result. |

## 7. Time, tokens and cost

All 81 pack turns, against 69 delegation turns: turn median 6.7 s against 22.9 s, mean 8.3 s against 29.5 s. Input tokens per turn 75k against 166k. Cache share 0.93 against 0.97. The largest request carried 102 tools, cap 110. Layer (a) time: mean 77 ms, p90 156 ms (run 4: 121 ms and 281 ms). The spawn arm costs USD 0.0209 a turn over 12 turns, but 9 of them are empty rows.

## 8. Limits

1. The outage cut the run. `fan_tasks`, `fan_one_read` and `fan_three_searches` did not run. Stop 10 has 6 of 9 turns, and the `fan_tasks` check on the spawn arm is missing.
2. `fan_contracts` has one valid repeat. The turn 2 comparison needs a re-run.
3. Three repeats give small counts. One turn moves a set by about 4 points.
4. Pack ties delegation on pass rate. The gain is in time and cost.
5. `read_the_project` and `gives_the_count` on `pm_project_view` still look too strict.

## 9. What comes next

- The owner must restore the `deepseek` balance (HTTP 402 at the vendor). Then re-run `fan_contracts` and the chunk `fan_tasks,fan_one_read,fan_three_searches` on `pack` and `packs+spawn`, 3 repeats.
- Spawn: the model still spawns on light reads in 2 of 6 turns. The OA-9f rule needs a firmer cue. A spawn of 4 to 13 worker calls costs 5 to 7 times the single step.
- Projects: a task from a call (`mix_call_then_task` 2) must go to Projects, private by default, with no question.
- OA-10 must not start.

## Appendix: pass by scenario, all repeats

| Scenario | delegation | pack | packs+spawn |
|---|---|---|---|
| pm_my_day | 6/6 | 6/6 | - |
| pm_project_view | 4/6 | 4/6 | - |
| pm_find_task | 3/3 | 3/3 | - |
| pm_capacity | 3/3 | 3/3 | - |
| pm_capacity_open | 3/3 | 3/3 | - |
| pm_stuck | 5/6 | 5/6 | - |
| pm_status | 3/3 | 3/3 | - |
| pm_tags | 3/3 | 2/3 | - |
| pm_archive | 6/6 | 6/6 | - |
| pm_new_tasks | 3/3 | 3/3 | - |
| pm_calendar | 2/3 | 3/3 | - |
| pm_private_task | 3/3 | 2/3 | - |
| pm_no_surface | 3/6 | 6/6 | - |
| pm_miss | 3/3 | 3/3 | - |
| mix_task_from_mail | 5/6 | 4/6 | - |
| mix_call_then_task | 4/6 | 3/6 | - |
| fan_companies | - | 3/3 | 2/3 |
| fan_invoices | - | 3/3 | 2/3 |
| fan_contracts | - | 2/6 (2 of 2 valid pass) | 1/6 (1 of 1 valid passes) |
| fan_tasks, fan_one_read, fan_three_searches | - | not run | not run |
