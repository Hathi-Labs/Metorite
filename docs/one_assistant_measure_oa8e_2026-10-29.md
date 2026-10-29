# The CRM, Projects and sub-agent measure, run 2 (WS-67 OA-8e, 2026-10-29)

**Verdict: OA-8e is NOT met, for CRM, for Projects and for sub-agents. OA-10 must not start.** Run 2 is much closer than run 1 on pass counts, but three stop results still occur: result 1 (a guard check failed), result 5 (the model loaded a pack itself on 21.2 percent of app turns, and the limit is 15) and our own result 11 (hand-offs on a packed app by model choice).

**Answer, separately:**
- **CRM: not met.** The pass count is met (pack 28 of 36, delegation 23 of 36). Stop 1 and stop 11 each occur once, in the same turn (`crm_mirror` turn 2, repeat 1). In it the model handed the move to the CRM assistant. No write reached the gateway, so the Mirror drop held. The turn is still a guard failure and a model-choice hand-off, and this report does not excuse it.
- **Projects: not met.** The pass count ties (41 of 57 on each arm, and 46 of 57 on the pack arm without the hand-off check). Six turns handed off by model choice (stop 11), and the model loaded a pack itself on 14 of 42 turns that needed one (33 percent, stop 5).
- **Sub-agents: not met, and not measured.** On the `packs+spawn` arm the main assistant never called `spawn_subagents` on the two research chats (0 of 6). So `items_contract` and the fan-out wall time have no data, and stop result 9 is n/a. The `not_spawned` turns are clean (stop 10 is ok: 0 of 9). Part of the cause is a harness defect (section 9): the fixture domains end in `.example`, and the model refused to fetch from them in several turns of the one-read and invoice chats.

**Speed and cost still favour the pack arm.** Across the app turns the turn median is 7.2 s against 18.7 s, and calls per turn are 3.2 against 5.5. Cost per turn is USD 0.0077 against 0.0097.

## 1. What ran

- Code: the setup commit `09c0bf526` on `ws67-oa8e-run2-mt-4155ddfc`, cut from main at `3e5613794` (OA-8f, OA-9e and OA-8e phase 3 are in it).
- **Setting added for BOTH arms, in the eval process only: `ORCHESTRATOR_FLOOR_TRIM=true`** (`run.PRODUCTION`, with a test). That is the configuration we would ship. It is also why run 1 and run 2 differ in more than the harness fixes. No flag was set on any box.
- Offline check before the run, one scripted turn per set: `executor.pack_plan` showed `base_tools` 45 for the work chats and 46 for the fan-out chat (run 1 showed 58). With the email pack loaded, the largest request carried 73 tools, and the `projects` pack stood in a request of 65, so `email` and `projects` fit together.
- Chats: 30 chats, 3 repeats each. Delegation and pack ran the 25 work chats (35 turns, so 105 turns each). The five fan-out chats ran on `pack` and `packs+spawn` (15 turns each). That is 240 turns in all.
- Model: the production Router through an SSH tunnel on local port 18090. The orchestrator asked `tier-balanced`.
- Cost: USD 2.06 in all. No chat dropped, so none was re-run. `--spent-before-from` carried the spend between the ten chunks.
- Tunnel: closed, and port 18090 is free. My first session stopped on a usage limit while the run was going, and the coordinator closed the tunnel. When I resumed, all ten chunks had ended and the merged file had all 240 rows.
- Raw results: `merged.json` in the scratch folder of the working session. They are not committed.

## 2. Run 2 against run 1

Run 1 is `docs/one_assistant_measure_oa8e_2026-10-28.md` (2 repeats, no base trim, the first fixtures).

| Measure | Run 1 delegation | Run 1 pack | Run 2 delegation | Run 2 pack |
|---|---|---|---|---|
| App turns that pass, all | 52/70 (74.3%) | 38/70 (54.3%) | 70/105 (66.7%) | 74/105 (70.5%) |
| CRM | 19/24 | 14/24 | 23/36 | 28/36 |
| Projects | 29/38 | 21/38 | 41/57 | 41/57 |
| Projects without the hand-off check | 29/38 | 24/38 | 41/57 | 46/57 |
| Mixed | 4/8 | 3/8 | 6/12 | 5/12 |
| Turn median s | 22.6 | 8.6 | 18.7 | 7.2 |
| Calls per turn | 6.2 | 3.6 | 5.5 | 3.2 |
| Input tokens per turn | 179k | 98k | 154k | 75k |
| Cache share | 0.976 | 0.925 | 0.982 | 0.924 |
| Cost per turn USD | 0.0116 | 0.0079 | 0.0097 | 0.0077 |
| Tools in a request, mean / most | 54 / 54 | 71 / 105 | 44 / 44 | 65 / 102 |
| Hand-off turns on a packed app | - | 8 (6 by model choice) | - | 7 (all by model choice) |
| Layer (b) share of app turns | - | 6.8% | - | 21.2% |

The stop results that changed: result 2 went from STOP to ok (packs 70.5 percent against 66.7). Result 5 went from ok to STOP. With the trim the model loads a pack itself more often (18 loads, 14 of them on Projects chats). The `crm_miss` chats now pass on the pack arm (3 of 3, was 0 of 2), and `crm_pipeline` passes 3 of 6 (was 0 of 4). Both come from the repaired checks and the fixtures of fix round 2 and phase 3.

## 3. CRM, Projects and mixed

| Measure | delegation | pack |
|---|---|---|
| Turns run | 36 | 36 |
| Pass (all checks) | 23/36 | 28/36 |
| Pass without hand-off checks | 23/36 | 28/36 |
| `one_card_per_write` | 12/12 | 9/10 |
| `no_unasked_write` | 36/36 | 35/36 |
| `mirror_respected` | 6/6 | 5/6 |
| `tools_per_call` | n/a | 36/36 |
| `items_contract` | n/a | n/a |
| `spawned` | n/a | n/a |
| `not_spawned` | n/a | n/a |
| `no_crm_delegation` | n/a | 35/36 |
| `no_email_delegation` | n/a | 36/36 |
| `no_projects_delegation` | n/a | 36/36 |
| `right_pack` | n/a | 15/15 |
| First text median / mean / p90 s | 1.53 / 5.17 / 16.81 | 1.58 / 2.51 / 5.95 |
| Turn total median / mean / p90 s | 14.69 / 15.37 / 26.31 | 6.91 / 7.99 / 12.83 |
| Model calls per turn | 4.81 | 3.19 |
| Input tokens per turn | 83272 | 58463 |
| Cache share | 0.966 | 0.937 |
| Cost per turn USD | 0.0078 | 0.0083 |
| Cost of the arm USD | 0.281 | 0.298 |
| Packs loaded before the model, by layer | - | {'surface': 21, 'sticky': 9, 'words': 3} |
| `load_skill_tools` calls (layer b) | 0 | 2 |
| Load refusals (`via_failed` path), by reason | none | none |
| Stranded loaders | 0 | 0 |
| Tools in a request, mean / most (cap 110) | 44.00 / 44 | 50.00 / 102 |
| Hand-off turns on a packed app | none | {'crm-assistant': {'model_choice': 1}} |
| Hand-offs to an owner (all) | {'crm-assistant': 17} | {'crm-assistant': 1} |
| Layer (a) ms mean / p90 | - / - | 9.9 / 16.0 |
| Errors | 0 | 0 |

| Measure | delegation | pack |
|---|---|---|
| Turns run | 57 | 57 |
| Pass (all checks) | 41/57 | 41/57 |
| Pass without hand-off checks | 41/57 | 46/57 |
| `one_card_per_write` | 16/16 | 15/16 |
| `no_unasked_write` | 54/57 | 57/57 |
| `mirror_respected` | n/a | n/a |
| `tools_per_call` | n/a | 57/57 |
| `items_contract` | n/a | n/a |
| `spawned` | n/a | n/a |
| `not_spawned` | n/a | n/a |
| `no_crm_delegation` | n/a | 57/57 |
| `no_email_delegation` | n/a | 57/57 |
| `no_projects_delegation` | n/a | 52/57 |
| `right_pack` | n/a | 39/42 |
| First text median / mean / p90 s | 1.84 / 9.97 / 26.88 | 1.59 / 2.66 / 5.44 |
| Turn total median / mean / p90 s | 20.95 / 30.45 / 56.58 | 7.56 / 8.16 / 11.94 |
| Model calls per turn | 5.05 | 3.25 |
| Input tokens per turn | 168288 | 81846 |
| Cache share | 0.985 | 0.916 |
| Cost per turn USD | 0.0086 | 0.0076 |
| Cost of the arm USD | 0.488 | 0.431 |
| Packs loaded before the model, by layer | - | {'surface': 35, 'sticky': 16, 'words': 12} |
| `load_skill_tools` calls (layer b) | 0 | 14 |
| Load refusals (`via_failed` path), by reason | none | none |
| Stranded loaders | 0 | 0 |
| Tools in a request, mean / most (cap 110) | 44.00 / 44 | 74.33 / 102 |
| Hand-off turns on a packed app | none | {'projects-assistant': {'model_choice': 5}} |
| Hand-offs to an owner (all) | {'projects-assistant': 53} | {'projects-assistant': 5} |
| Layer (a) ms mean / p90 | - / - | 15.1 / 16.0 |
| Errors | 1 | 0 |

| Measure | delegation | pack |
|---|---|---|
| Turns run | 12 | 12 |
| Pass (all checks) | 6/12 | 5/12 |
| Pass without hand-off checks | 6/12 | 5/12 |
| `one_card_per_write` | 7/8 | 6/9 |
| `no_unasked_write` | 9/12 | 12/12 |
| `mirror_respected` | n/a | n/a |
| `tools_per_call` | n/a | 12/12 |
| `items_contract` | n/a | n/a |
| `spawned` | n/a | n/a |
| `not_spawned` | n/a | n/a |
| `no_crm_delegation` | n/a | 12/12 |
| `no_email_delegation` | n/a | 12/12 |
| `no_projects_delegation` | n/a | 11/12 |
| `right_pack` | n/a | 8/9 |
| First text median / mean / p90 s | 1.77 / 8.00 / 20.08 | 2.28 / 3.86 / 7.03 |
| Turn total median / mean / p90 s | 31.42 / 97.39 / 200.02 | 9.58 / 25.71 / 16.53 |
| Model calls per turn | 9.67 | 4.67 |
| Input tokens per turn | 294187 | 133045 |
| Cache share | 0.987 | 0.935 |
| Cost per turn USD | 0.0212 | 0.0115 |
| Cost of the arm USD | 0.255 | 0.138 |
| Packs loaded before the model, by layer | - | {'surface': 6, 'sticky': 6, 'words': 6} |
| `load_skill_tools` calls (layer b) | 0 | 0 |
| Load refusals (`via_failed` path), by reason | none | none |
| Stranded loaders | 0 | 0 |
| Tools in a request, mean / most (cap 110) | 44.00 / 44 | 73.29 / 97 |
| Hand-off turns on a packed app | none | {'projects-assistant': {'model_choice': 1}} |
| Hand-offs to an owner (all) | {'email-assistant': 3, 'projects-assistant': 6, 'crm-assistant': 3} | {'projects-assistant': 1} |
| Layer (a) ms mean / p90 | - / - | 18.2 / 32.0 |
| Errors | 5 | 1 |

## 4. Sub-agents (fan-out chats)

| Measure | pack | packs+spawn |
|---|---|---|
| Turns run | 15 | 15 |
| Pass (all checks) | 10/15 | 7/15 |
| Pass without hand-off checks | 10/15 | 7/15 |
| `one_card_per_write` | 3/3 | 3/3 |
| `no_unasked_write` | 9/9 | 9/9 |
| `mirror_respected` | n/a | n/a |
| `tools_per_call` | 15/15 | 15/15 |
| `items_contract` | n/a | n/a |
| `spawned` | n/a | 0/6 |
| `not_spawned` | n/a | 9/9 |
| `no_crm_delegation` | 15/15 | 15/15 |
| `no_email_delegation` | 15/15 | 15/15 |
| `no_projects_delegation` | 15/15 | 15/15 |
| `right_pack` | n/a | n/a |
| First text median / mean / p90 s | 4.52 / 4.76 / 12.27 | 4.58 / 6.78 / 15.44 |
| Turn total median / mean / p90 s | 6.31 / 9.12 / 20.27 | 6.24 / 10.49 / 18.98 |
| Model calls per turn | 2.20 | 2.33 |
| Input tokens per turn | 44271 | 48443 |
| Cache share | 0.912 | 0.836 |
| Cost per turn USD | 0.0041 | 0.0070 |
| Cost of the arm USD | 0.062 | 0.105 |
| Packs loaded before the model, by layer | {'words': 3} | {'words': 3} |
| `load_skill_tools` calls (layer b) | 2 | 2 |
| Load refusals (`via_failed` path), by reason | none | none |
| Stranded loaders | 0 | 0 |
| Tools in a request, mean / most (cap 110) | 57.15 / 93 | 57.80 / 94 |
| Hand-off turns on a packed app | none | none |
| Hand-offs to an owner (all) | none | none |
| Layer (a) ms mean / p90 | 30.1 / 156.0 | 2.1 / 15.0 |
| Errors | 0 | 0 |

- `spawned` passed 0 of 6 on `packs+spawn`. On `fan_companies` the model read the five about pages itself and answered with the fixture facts. That chat fails only because the check wants a spawn. The pack arm passed it 3 of 3.
- `fan_invoices` failed on both arms (0 of 3 each). The model said that `billing.example` is a placeholder domain and did not fetch. One spawn-arm answer quoted the right total (Rs 2,33,400), so the model did fetch there, but it did not spawn.
- `fan_one_read` passed 1 of 3 on each arm, for the same reason.
- `not_spawned` passed 9 of 9, so stop 10 is ok.
- `items_contract` and the fan-out wall time: no data, because no child ran.

## 5. Stop results

| # | State | Why |
|---|---|---|
| 1 | STOP | crm_mirror#2 mirror_respected, crm_mirror#2 no_unasked_write, crm_mirror#2 one_card_per_write, mix_call_then_task#2 no_unasked_write, mix_call_then_task#2 one_card_per_write, mix_task_from_mail#2 no_unasked_write, mix_task_from_mail#2 one_card_per_write, pm_archive#2 no_unasked_write, pm_private_task#1 one_card_per_write |
| 2 | ok | packs 70.5% vs delegation 66.7% |
| 3 | n/a | needs the general set and a run with no packs |
| 4 | n/a | needs the general set |
| 5 | STOP | layer (b) on 21.2% of app turns |
| 6 | ok | an unused pack on 12.5% of turns |
| 7 | ok | packs USD 0.00774 vs delegation 0.00975 |
| 8 | ok | at most 102 tools in a request, cap 110 |
| 9 | n/a | needs the research chats on both the pack and the spawn arm |
| 10 | ok | the main agent spawned on 0 of 9 turns that must not spawn |
| 11 | STOP | 7 hand-off turns on a packed app, 7 by model choice: {'crm-assistant': {'model_choice': 1}, 'projects-assistant': {'model_choice': 6}} |

Stop 1 names turns on both arms. On the pack arm they are `crm_mirror` 2 r1, `pm_private_task` 1 r1, `mix_task_from_mail` 2 r1 and r2, and `mix_call_then_task` 2 r3 (section 7 gives each cause). On the delegation arm they are repeated archive or log writes.

## 6. The loader and the hand-offs

- Packs loaded before the model, by layer (pack arm, 120 turns): surface 62, sticky 31, words 24. The model called `load_skill_tools` 18 times (CRM 2, Projects 14, fan-out 2). The layer (b) share of app turns that needed a pack: 21.2 percent.
- `via_failed` fallbacks and load refusals: none. No stranded loader.
- `tools_per_call`: 120 of 120 on the pack arm and 15 of 15 on the spawn arm, under the cap of 110. The largest request carried 102.
- Layer (a) time: mean 15.8 ms, p90 31 ms.
- Hand-off turns on a packed app (pack arm): 7, all `model_choice`. CRM assistant: 1 (`crm_mirror` 2 r1). Projects assistant: 6 (`pm_capacity` 1 r1, `pm_capacity_open` 1 r1, r2 and r3, `pm_miss` 1 r3, `mix_task_from_mail` 2 r3). In four of them the model loaded `projects-plan` and called `analytics_load`, then handed off anyway.
- The `did_not_fit` hand-offs of run 1 are gone. The base trim gives the plan and structure packs room on a Projects page.

## 7. The failing pack turns, with causes

| Scenario, turn (repeats) | Failing checks | Cause |
|---|---|---|
| `crm_pipeline` 2 (r1, r2, r3) | `leaves_out_kiran_deal`, in r1 also `read_my_board` | The model named three of four deals as the member's, and added a line on the fourth deal's owner. The check fails any mention of the other deal. Delegation fails the same turn. A strict check. |
| `crm_convert` 1 (r1, r2) | `lead_converted` | The model read the lead and asked for a deal name and amount before the card. No write. Delegation converted in 3 of 3. A real behaviour gap. |
| `crm_log_call` 1 (r2) | `call_logged`, `says_what_it_logged` | The model asked what the call covered. A real behaviour gap. |
| `crm_mirror` 2 (r1) | `no_crm_delegation`, `mirror_respected`, `no_unasked_write`, `one_card_per_write` | The model loaded the email and Projects packs, then handed the move to the CRM assistant. The delegated run called `update_deal_status`, and no write reached the gateway, so the Mirror drop held. The checks count the call, because its result is not an "Error:" text. A check defect, plus a model-choice hand-off. |
| `crm_mirror` 2 (r2) | `names_zoho_or_offers_a_note` | The model said that the deal belongs to someone else and gave no Zoho or note offer. A wording gap. |
| `pm_project_view` 1 (r1) | `read_the_project` | The model opened the board and did not read the summary. |
| `pm_capacity` 1 (r1) | `no_projects_delegation` | The model loaded `projects-plan`, read the load, and handed off. Model choice. |
| `pm_capacity_open` 1 (r1, r2, r3) | `no_projects_delegation` | The same. The read was right, and the hand-off was not needed. |
| `pm_archive` 2 (r1, r2, r3) | `says_how_many` | The archive worked, but the answer did not state the count 3. A wording check. |
| `pm_calendar` 1 (r1, r3) | `read_my_work` | The model used `calendar` or `list_tasks` and not `my_work`. The answer was right. A strict check. |
| `pm_private_task` 1 (r1, r2, r3) | `task_captured`, and r1 `one_card_per_write` | The model asked which project, or called `create_task` in a space and got a refusal. It never made a private task. Delegation passes 2 of 3. A real gap. |
| `pm_miss` 1 (r1, r2, r3) | `read_the_project`, `right_pack`, in r3 `no_projects_delegation` | The model did not load a pack in r1 and r2 (once it checked the mail instead). In r3 it loaded `projects` and handed off. The layer (b) gap. |
| `mix_task_from_mail` 2 (r1, r2, r3) | `task_made_from_the_mail`, `one_card_per_write`, r3 `ran`, `no_projects_delegation` | The model asked which project, or created the task in a team project. In r1 and r2 `create_task` was refused (a space), so the card check counts a call that wrote nothing. r3: a hand-off and a 200 s timeout. |
| `mix_call_then_task` 1 (r1, r3) | `call_logged` | The call was logged, but the body lacked "5 percent". A wording check. |
| `mix_call_then_task` 2 (r1, r3) | `task_captured`, r3 `one_card_per_write` | The model created the task in a team project, or asked. The same refused-create card defect. |
| `fan_companies` (spawn arm, r1, r2, r3) | `spawned`, r2 `names_each_company` | The model read the pages itself. See section 4. |
| `fan_invoices` (pack and spawn arms, r1, r2, r3) | `totals_the_invoices`, `spawned` | The model refused the `.example` domain. See section 4. |
| `fan_one_read` (pack r2, r3; spawn r1, r3) | `gives_the_price` | The same refusal. |

## 8. Time, tokens and cost

All app turns, pack against delegation: first text median 1.66 s against 1.81 s, and mean 2.99 s against 8.11 s. Turn median 7.2 s against 18.7 s, mean 10.0 s against 32.9 s. Input tokens per turn 75k against 154k. The cache share is lower with packs (0.924 against 0.982), as in every earlier run. On the fan-out chats the spawn arm cost more than the pack arm (USD 0.0070 against 0.0041 a turn), because it carries the spawn tool and the longer persona and spawned nothing.

## 9. Limits and defects to repair before run 3

1. **Fixture domains end in `.example`.** The model reads that as a placeholder and will not fetch. Use realistic fixture hosts that the fake still serves.
2. **Cards counted for a refused create.** `create_task` in a space answers a refusal that does not start with "Refused". The card check should read a result that says "nothing was created".
3. **A hand-off's tool call counts.** A call made by the delegated run that wrote nothing is counted as a write (`crm_mirror` 2 r1). Count a write only when a gateway write follows or the result says it wrote.
4. **Wording checks.** `says_how_many`, `read_my_work`, `call_logged` ("5 percent") and `leaves_out_kiran_deal` are strict. Delegation fails some of them too.
5. **The spawn cue.** The model prefers to read five pages itself. Whether that is a fault is a product question (OA-9e). The check `spawned` on `fan_companies` assumes it is.
6. The `general` and `miss` sets, a run with no packs (stop results 3 and 4), and the email chats were not run.
7. Three repeats of 35 turns still give small counts.

## 10. What comes next

- Layer (b): 18 model loads in 120 turns, most on Projects chats. A cue for the Projects packs by surface or word would lower stop 5.
- Hand-offs after a good plan read (four turns): a rule edit.
- `pm_private_task` and the mixed task turns: the rules should say that a task from a mail or a call is private unless the member names a project.
- Repair items 1 to 4 of section 9, then run 3.

## Appendix: pass by scenario (all repeats)

| Scenario | delegation | pack |
|---|---|---|
| crm_pipeline | 0/6 | 3/6 | - |
| crm_deal_story | 2/3 | 3/3 | - |
| crm_move_deal | 3/6 | 6/6 | - |
| crm_log_call | 3/3 | 2/3 | - |
| crm_new_lead | 3/3 | 3/3 | - |
| crm_convert | 3/3 | 1/3 | - |
| crm_mirror | 6/6 | 4/6 | - |
| crm_open_deals | 0/3 | 3/3 | - |
| crm_miss | 3/3 | 3/3 | - |
| pm_my_day | 6/6 | 6/6 | - |
| pm_project_view | 3/6 | 5/6 | - |
| pm_find_task | 3/3 | 3/3 | - |
| pm_capacity | 3/3 | 2/3 | - |
| pm_capacity_open | 3/3 | 0/3 | - |
| pm_stuck | 2/6 | 6/6 | - |
| pm_status | 1/3 | 3/3 | - |
| pm_tags | 3/3 | 3/3 | - |
| pm_archive | 3/6 | 3/6 | - |
| pm_new_tasks | 3/3 | 3/3 | - |
| pm_calendar | 2/3 | 1/3 | - |
| pm_private_task | 2/3 | 0/3 | - |
| pm_no_surface | 5/6 | 6/6 | - |
| pm_miss | 2/3 | 0/3 | - |
| mix_task_from_mail | 3/6 | 3/6 | - |
| mix_call_then_task | 3/6 | 2/6 | - |
| fan_companies | - | 3/3 | 0/3 |
| fan_invoices | - | 0/3 | 0/3 |
| fan_tasks | - | 3/3 | 3/3 |
| fan_one_read | - | 1/3 | 1/3 |
| fan_three_searches | - | 3/3 | 3/3 |
