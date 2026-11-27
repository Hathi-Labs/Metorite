# The Projects and mixed measure, run 4 (WS-67 OA-8e, 2026-10-29)

**Projects verdict: NOT met.** Two of the four Projects tests fail. Stop 2 and stop 5 pass. Stop 1 and stop 11 occur. Two chats cause both: `mix_task_from_mail` and `pm_miss`.

**Answer, separately:**
- **Stop 1 occurs on the pack arm.** `mix_task_from_mail` 2 r2 and r3 fail `no_unasked_write`. Each turn made two cards and two write calls. A guard check on a mixed pack turn fails.
- **Stop 2 is ok.** Pack passes 62 of 69 (89.9 percent). Delegation passes 55 of 69 (79.7 percent). Pack is ahead in the Projects set (53 against 49) and in the mixed set (9 against 6).
- **Stop 5 is ok.** Layer (b) occurs on 5 of 51 turns that needed a pack (9.8 percent, limit 15). All 5 are Projects turns.
- **Stop 11 occurs.** Four pack turns handed off by model choice: `pm_miss` r1 and r2, and `mix_task_from_mail` 2 r2 and r3.
- **Two OA-8h rules work.** `pm_private_task` goes from 1 of 3 to 3 of 3. `pm_archive` goes from 4 of 6 to 6 of 6 on the pack arm. The named-project cue loads the pack in 2 of 3 `pm_miss` repeats, but both then hand off.
- Stop results 3, 4, 9 and 10 are n/a. They need other sets.

## 1. What ran

- Code: `07f27e558` on `ws67-oa8e-run4-mt-4155ddfc`. No product code or harness changed in this run.
- Sets: the 14 `pm_*` chats of the Projects set, and `mix_task_from_mail` and `mix_call_then_task` of the mixed set. No CRM chat and no fan-out chat ran.
- Arms: `delegation` and `pack`. 3 repeats. 138 turns (69 on each arm). The settings are the same as run 3, in the eval process only. No flag was set on any box.
- Model: the production Router through an SSH tunnel on local port 18090. The orchestrator asked `tier-balanced`.
- Chunks: 9 chunks of 8 to 18 turns on each arm, run one after another with `--timeout 200` and `--spent-before-from`. Each chunk ended in under 10 minutes. No chat dropped.
- Cost: USD 1.36 in all (cap USD 4). Run 3 cost USD 1.75 for 240 turns, with the CRM and fan-out chats.
- Tunnel: opened as process 93324 and closed by that PID. Port 18090 is free (no listener).
- Raw results: `results_oa8e_run4_2026-10-29.json` in the scratch folder. They are not committed, as for run 2 and run 3.

## 2. What changed in the code since run 3

Run 3 ran at `c6f4b8025`. Run 4 runs at `07f27e558`.
- **OA-8h** (PR #1481, `adaabd6a8`, `5b22529db`, `545e8e1f5`): new Projects pack rules. "Private by place" covers the My Tasks or Calendar app, and a task made from a mail, call or chat. "Archive in one step" uses `bulk_update`. A `projects` loader cue covers a named project. The page is named "My Tasks". The `fan_tasks` words changed (not run here).
- **OA-8g** and **OA-8e fix round 3** were already in run 3.
- **OA-9c** (#1480, sub-agent children and held packs) does not touch these sets.
- The other main commits (Projects board filter, chat receipts, sidebar) do not touch the measure path.

## 3. Run 3 against run 4, Projects and mixed

| Set | Arm | Pass run 3 / run 4 | Turn median s | Calls per turn | USD per turn |
|---|---|---|---|---|---|
| Projects | delegation | 49/57 / 49/57 | 19.6 / 18.2 | 5.23 / 4.91 | 0.0085 / 0.0081 |
| Projects | pack | 45/57 / 53/57 | 6.2 / 6.6 | 2.83 / 2.88 | 0.0061 / 0.0067 |
| Mixed | delegation | 5/12 / 6/12 | 38.2 / 40.0 | 8.25 / 9.83 | 0.0216 / 0.0284 |
| Mixed | pack | 9/12 / 9/12 | 9.8 / 8.7 | 3.92 / 5.75 | 0.0073 / 0.0149 |

| Set (pack arm) | Layer (b), run 3 | Layer (b), run 4 | Hand-offs by model choice, run 3 | Run 4 |
|---|---|---|---|---|
| Projects | 4 turns of 57 | 5 turns of 57 (`pm_status` x3, `pm_miss` x2) | 1 (`pm_miss` r2) | 2 (`pm_miss` r1, r2) |
| Mixed | 0 | 0 | 1 (`mix_task_from_mail` 2 r2) | 2 (`mix_task_from_mail` 2 r2, r3) |
| Both sets | n/a | 9.8% of turns that needed a pack (5 of 51) | 2 | 4 |

Delegation had one error: `mix_call_then_task` 2 r2 timed out at 200 s. The pack arm had no error. Over both sets, the pack turn median is 6.8 s against 19.3 s. Pack costs USD 0.0081 a turn against 0.0117.

The mixed pack arm costs more than in run 3. Calls per turn rose from 3.9 to 5.8, and USD from 0.0073 to 0.0149. The two long `mix_task_from_mail` turns (136 s and 144 s) cause this.

## 4. Pack arm, pass by scenario

| Scenario | Run 3 | Run 4 | Note |
|---|---|---|---|
| pm_my_day | 6/6 | 6/6 | |
| pm_project_view | 3/6 | 5/6 | The run 4 failure is one strict check (section 5). |
| pm_find_task | 3/3 | 3/3 | |
| pm_capacity | 3/3 | 3/3 | |
| pm_capacity_open | 3/3 | 3/3 | |
| pm_stuck | 6/6 | 6/6 | |
| pm_status | 3/3 | 3/3 | The model loads the pack in all 3 repeats (layer b). |
| pm_tags | 3/3 | 3/3 | |
| pm_archive | 4/6 | 6/6 | **OA-8h "archive in one step" works.** |
| pm_new_tasks | 3/3 | 3/3 | |
| pm_calendar | 3/3 | 3/3 | |
| pm_private_task | 1/3 | 3/3 | **OA-8h "private by place" works in the My Tasks app.** |
| pm_no_surface | 4/6 | 6/6 | |
| pm_miss | 0/3 | 0/3 | The cue loads `projects` in r1 and r2. Both hand off. r3 loads nothing. |
| mix_task_from_mail | 3/6 | 3/6 | The private rule fires in r2 only. The hand-off and double write remain. |
| mix_call_then_task | 6/6 | 6/6 | |

Delegation for reference, run 4: `pm_archive` 3/6, `pm_no_surface` 3/6, `mix_task_from_mail` 3/6, `mix_call_then_task` 3/6, `pm_calendar` 2/3 and `pm_status` 2/3. All other chats pass in full.

## 5. The failing pack-arm turns (7 of 69)

Cause marks: **P** is a product behaviour. **H** is a harness defect or a doubtful check. The cause comes from the stored answer text, tool names and cards.

| Chat, turn, repeat | Failed checks | Cause |
|---|---|---|
| `pm_project_view` 1 r1 | `read_the_project` | Tools: `projects_tree`, `open_in_app`. No `project_summary`. The answer gave the right count (5 tasks, 2 open). H (strict check, as in run 3). |
| `pm_miss` 1 r1 | `no_projects_delegation` | The model loaded `projects`, called 5 read tools, and the run handed off to the Projects assistant. The answer is right. P (model choice after the cue loaded the pack). |
| `pm_miss` 1 r2 | `no_projects_delegation` | The same pattern: pack loaded, 4 read tools, hand-off. The answer is right. P (model choice). |
| `pm_miss` 1 r3 | `read_the_project`, `right_pack` | The model called `crm_list_records` twice and loaded no pack. It said "Mobile App" does not exist. P (the cue did not fire). |
| `mix_task_from_mail` 2 r1 | `task_made_from_the_mail` | The model asked which project and offered a private task. It made no task. P (asked once too often). |
| `mix_task_from_mail` 2 r2 | `right_pack`, `no_unasked_write`, `no_projects_delegation` | The model made a private task (#902), which is right. But it called `create_personal_task` twice (2 cards), handed off, and took 136 s. P (stop 1 and stop 11). |
| `mix_task_from_mail` 2 r3 | `task_made_from_the_mail`, `right_pack`, `no_unasked_write`, `no_projects_delegation` | The model called `create_task` twice in the Website Relaunch team project (2 cards), handed off, and took 144 s. P (the private rule did not fire. Stop 1 and stop 11). |

No cause is a certain harness defect. `read_the_project` on `pm_project_view` is the one doubtful check. It failed 3 of 6 in run 3 and 1 of 6 in run 4.

## 6. Reading the OA-8h rules

- **Private by place: works for the app, partly for a mail.** `pm_private_task` passes 3 of 3. In `mix_task_from_mail` the model chose a private task in r2 (right), asked in r1, and used a team project in r3.
- **Archive in one step: works.** `pm_archive` passes 6 of 6. The old patterns (asking "archive or delete", or six calls) did not return.
- **Named-project cue: half works.** `projects` loaded in 2 of 3 `pm_miss` repeats. The answers were right. But the model then handed off, so `no_projects_delegation` fails. The cue needs a line that says the pack's read tools answer a named project with no hand-off.
- **Mail task: the write runs twice.** In both hand-off turns the model made the task, then repeated it. The second call causes the `no_unasked_write` failure. The hand-off is the likely root cause.

## 7. Time, tokens and cost

All 69 pack turns, against the 69 delegation turns: first text median 3.7 s against 2.6 s, mean 6.0 s against 15.3 s. Turn median 6.8 s against 19.3 s, mean 11.0 s against 32.3 s. Input tokens per turn 95k against 190k. Cache share 0.92 against 0.99. The largest request carried 102 tools, cap 110. Layer (a) time: mean 121 ms, p90 281 ms (run 3: mean 45 ms, p90 125 ms). The cause of the rise was not checked. The model wait is much larger, so the rise does not change the turn.

## 8. Limits

1. Three repeats of 23 turns give small counts. One turn moves a set by about 4 points.
2. Only the Projects and mixed sets ran. The CRM, fan-out, `general` and `miss` sets did not.
3. The `read_the_project` check on `pm_project_view` needs a look.
4. In 13 pack-arm turns a pack loaded and none of its tools ran (stop 6 reads 18.8 percent, ok).

## 9. What comes next

- Fix `pm_miss`: the pack must answer a named project in the same run, with no hand-off.
- Fix `mix_task_from_mail`: a task made from a mail must be a private task, made once, with no hand-off.
- Then run 5 on these two chats only.
- OA-10 must not start.

## Appendix: pass by scenario, all repeats

| Scenario | delegation | pack |
|---|---|---|
| pm_my_day | 6/6 | 6/6 |
| pm_project_view | 6/6 | 5/6 |
| pm_find_task | 3/3 | 3/3 |
| pm_capacity | 3/3 | 3/3 |
| pm_capacity_open | 3/3 | 3/3 |
| pm_stuck | 6/6 | 6/6 |
| pm_status | 2/3 | 3/3 |
| pm_tags | 3/3 | 3/3 |
| pm_archive | 3/6 | 6/6 |
| pm_new_tasks | 3/3 | 3/3 |
| pm_calendar | 2/3 | 3/3 |
| pm_private_task | 3/3 | 3/3 |
| pm_no_surface | 3/6 | 6/6 |
| pm_miss | 3/3 | 0/3 |
| mix_task_from_mail | 3/6 | 3/6 |
| mix_call_then_task | 3/6 | 6/6 |
