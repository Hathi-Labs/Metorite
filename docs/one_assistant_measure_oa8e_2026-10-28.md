# The CRM and Projects measure on real code (WS-67 OA-8e, phase 2, 2026-10-28)

**Verdict: OA-8e is NOT met, for CRM and for Projects. OA-10 must not start.** Three stop results of section 4.3.3 occur: result 1 (a guard check failed), result 2 (the pack arm passes more than 2 points below delegation) and our own result 11 (hand-offs on a packed app). On the 70 app turns that are not email, the pack arm passes 38 and delegation passes 52 (54.3 percent against 74.3 percent). CRM alone: pack 14 of 24, delegation 19 of 24. Projects alone: pack 21 of 38, delegation 29 of 38.

**The pack arm is faster and cheaper, and it loses on quality.** The median turn takes 8.6 s on the pack arm and 22.6 s on delegation. A turn costs USD 0.0079 against 0.0116. The pack arm makes 3.6 model calls a turn against 6.2.

**Read the numbers with the confounds of section 9 in mind.** The fake gateway lacks routes that the live models call, so both arms hit 404 answers. Some checks count a call that the run refused. Some checks fail on wording, such as "4.5 lakh" for "450,000". These hit delegation too, and section 7 lists them. The harness code at `9912eee5a` was NOT changed for this run. The numbers are as the checks scored them.

## 1. What ran

- Code: `9912eee5a` on `ws67-oa8e-measure-mt-4155ddfc` (OA-8e phase 1 and fix round 1). Main had moved on, and OA-9b was not merged in. The `packs+spawn` arm and the three fan-out chats were skipped.
- Chats: 25 work chats, 35 member turns, twice on each arm. That is 70 turns on each arm and 140 in all. The set holds 12 CRM turns, 19 Projects turns and 4 mixed turns. The email chats were not run in this phase.
- Model: the production Router through an SSH tunnel on local port 18090. The orchestrator asked `tier-balanced`.
- Settings, in the eval process only: `PRODUCTION` of `run.py`, and `TOOL_PACKS` with every pack of the email, CRM and Projects owners for the eval org (`run.pack_flag(owners)`). No flag was set on any box.
- Cost: USD 1.36 in all (delegation 0.81, pack 0.55). The runner printed a higher "spent" figure, because my chunk driver passed the cumulative spend of each chunk as `--spent-before` again. That over-count never reached the cap of USD 8.
- Tunnel: it dropped once, during `crm_miss` delegation repeat 2. The runner dropped that chat and stopped. I re-opened the tunnel and re-ran only that chat, as a whole chat (`--arms delegation --first-repeat 2`). The merged file holds all 140 rows, and `dropped` still names the dropped attempt. The tunnel is closed and port 18090 is free.
- Raw results: `scratchpad/oa8e/live/merged.json` of the working session, with the chunks beside it. They are not committed.
- **Data caution.** In the `crm_miss` and `pm_miss` chats the model used the memory and knowledge tools of the orchestrator (`retrieve_entity_context`, `retrieve_sales_context`, `search_timeline`). They read the LOCAL dev database of the PC, and the answers named records that are not in the fixtures. That text went to the Router as tool results. It is dev data, not production member data, and this report leaves it out. A later run must turn those tools off in the harness.

## 2. The acceptance lines of section 4.3.3

| Line | Delegation | Pack | Met |
|---|---|---|---|
| The pack arm passes at least as many turns as delegation, CRM | 19/24 | 14/24 | **No** |
| Same, Projects | 29/38 | 21/38 | **No** |
| Same, Projects, with the hand-off check left out | 29/38 | 24/38 | **No** |
| Same, mixed | 4/8 | 3/8 | No (8 turns, too few to judge) |
| Stop 1: no guard check fails, on any arm | 6 turns fail a guard | 7 turns fail a guard | **No** |
| Stop 2: pack no more than 2 points below delegation | 74.3% | 54.3% | **No** |
| Stop 5: layer (b) on at most 15 percent of app turns | n/a | 6.8% | Yes |
| Stop 6: an unused pack on at most 20 percent of turns | n/a | 4.3% | Yes |
| Stop 7: warm cost not above delegation | USD 0.0116 | USD 0.0079 | Yes |
| Stop 8: no request over the cap of 110 tools | n/a | most 105 | Yes |
| Stops 3, 4, 9, 10 | | | not measured (they need the `general` set, a run with no packs and the spawn arm) |
| Stop 11 (ours): no hand-off by model choice on a packed app | | 8 turns, 6 by model choice | **No** |

The guard failures split like this. Pack arm, 7 turns: 2 are checks that count a refused call (`crm_mirror` 2 r2 and `pm_my_day` 2 r2), and 5 are model behaviour (`pm_new_tasks` r1 and r2: three `create_task` calls and three cards instead of one `create_tasks` card; `pm_archive` 2 r1 and r2: repeated `archive_task` or `bulk_update` calls on task numbers; `pm_tags` r1: `create_tags` called twice). Delegation arm, 6 turns: 3 are the `crm_report` false positive (section 9, item 3), and 3 are repeated writes (`pm_status` r1, `pm_archive` 2 r2, `mix_call_then_task` 2 r2).

## 3. CRM (12 turns, twice)

| Measure | delegation | pack |
|---|---|---|
| Turns run | 24 | 24 |
| Pass (all checks) | 19/24 | 14/24 |
| Pass without hand-off checks | 19/24 | 14/24 |
| `one_card_per_write` | 8/10 | 6/7 |
| `no_unasked_write` | 22/24 | 23/24 |
| `mirror_respected` | 4/4 | 3/4 |
| `tools_per_call` | n/a | 24/24 |
| `no_crm_delegation` | n/a | 24/24 |
| `no_email_delegation` | n/a | 24/24 |
| `no_projects_delegation` | n/a | 24/24 |
| `right_pack` | n/a | 8/10 |
| First text median / mean / p90 s | 1.84 / 7.27 / 19.42 | 1.81 / 2.86 / 6.22 |
| Turn total median / mean / p90 s | 19.96 / 24.62 / 50.23 | 8.37 / 16.99 / 15.56 |
| Model calls per turn | 6.96 | 3.46 |
| Input tokens per turn | 139102 | 75445 |
| Cache share | 0.970 | 0.930 |
| Cost per turn USD | 0.0082 | 0.0056 |
| Cost of the arm USD | 0.196 | 0.134 |
| Packs loaded before the model, by layer | - | {'surface': 14, 'sticky': 6, 'words': 2} |
| `load_skill_tools` calls (layer b) | 0 | 0 |
| Load refusals (`via_failed` path), by reason | none | none |
| Stranded loaders | 0 | 0 |
| Tools in a request, mean / most (cap 110) | 54.00 / 54 | 59.27 / 61 |
| Hand-off turns on a packed app | none | none |
| Hand-offs to an owner (all) | {'crm-assistant': 19} | none |
| Layer (a) ms mean / p90 | - / - | 10.4 / 16.0 |
| Errors | 0 | 1 |

**Verdict for CRM: not met.** Pack 14 of 24, delegation 19 of 24. The pack arm never handed work to the CRM assistant (24 of 24 on `no_crm_delegation`). The `mirror_respected` failure on the pack arm is a check defect: the pack dropped `update_deal_status`, the model called it, the run answered "not found", and no write reached the gateway.

## 4. Projects (19 turns, twice)

| Measure | delegation | pack |
|---|---|---|
| Turns run | 38 | 38 |
| Pass (all checks) | 29/38 | 21/38 |
| Pass without hand-off checks | 29/38 | 24/38 |
| `one_card_per_write` | 10/11 | 6/12 |
| `no_unasked_write` | 35/38 | 32/38 |
| `mirror_respected` | n/a | n/a |
| `tools_per_call` | n/a | 38/38 |
| `no_crm_delegation` | n/a | 38/38 |
| `no_email_delegation` | n/a | 38/38 |
| `no_projects_delegation` | n/a | 33/38 |
| `right_pack` | n/a | 22/28 |
| First text median / mean / p90 s | 1.68 / 9.10 / 34.14 | 1.97 / 3.23 / 7.16 |
| Turn total median / mean / p90 s | 21.91 / 34.36 / 64.98 | 8.77 / 11.91 / 26.52 |
| Model calls per turn | 5.34 | 3.63 |
| Input tokens per turn | 189120 | 107559 |
| Cache share | 0.978 | 0.931 |
| Cost per turn USD | 0.0114 | 0.0080 |
| Cost of the arm USD | 0.431 | 0.303 |
| Packs loaded before the model, by layer | - | {'surface': 20, 'sticky': 8, 'words': 8} |
| `load_skill_tools` calls (layer b) | 0 | 3 |
| Load refusals (`via_failed` path), by reason | none | none |
| Stranded loaders | 0 | 0 |
| Tools in a request, mean / most (cap 110) | 54.00 / 54 | 78.90 / 105 |
| Hand-off turns on a packed app | none | {'projects-assistant': {'model_choice': 5, 'did_not_fit': 2}} |
| Hand-offs to an owner (all) | {'projects-assistant': 35, 'crm-assistant': 1} | {'projects-assistant': 7} |
| Layer (a) ms mean / p90 | - / - | 19.8 / 109.0 |
| Errors | 1 | 0 |

**Verdict for Projects: not met.** Pack 21 of 38, delegation 29 of 38. Seven pack turns handed off to the Projects assistant. Two are the legal "no room" hand-offs (`pm_status`, both repeats: the structure pack does not fit beside the core pack on a Projects page, and both turns passed). Five are model choices (section 6).

## 5. Mixed (4 turns, twice)

| Measure | delegation | pack |
|---|---|---|
| Turns run | 8 | 8 |
| Pass (all checks) | 4/8 | 3/8 |
| Pass without hand-off checks | 4/8 | 3/8 |
| `one_card_per_write` | 3/4 | 3/3 |
| `no_unasked_write` | 8/8 | 8/8 |
| `mirror_respected` | n/a | n/a |
| `tools_per_call` | n/a | 8/8 |
| `no_crm_delegation` | n/a | 8/8 |
| `no_email_delegation` | n/a | 7/8 |
| `no_projects_delegation` | n/a | 8/8 |
| `right_pack` | n/a | 6/6 |
| First text median / mean / p90 s | 1.62 / 1.80 / 2.45 | 3.42 / 4.02 / 6.91 |
| Turn total median / mean / p90 s | 115.19 / 113.07 / 200.01 | 9.85 / 33.00 / 11.11 |
| Model calls per turn | 8.38 | 4.25 |
| Input tokens per turn | 254526 | 121364 |
| Cache share | 0.976 | 0.891 |
| Cost per turn USD | 0.0228 | 0.0142 |
| Cost of the arm USD | 0.183 | 0.114 |
| Packs loaded before the model, by layer | - | {'surface': 4, 'words': 4, 'sticky': 2} |
| `load_skill_tools` calls (layer b) | 0 | 0 |
| Load refusals (`via_failed` path), by reason | none | none |
| Stranded loaders | 0 | 0 |
| Tools in a request, mean / most (cap 110) | 54.00 / 54 | 74.52 / 85 |
| Hand-off turns on a packed app | none | {'email-assistant': {'model_choice': 1}} |
| Hand-offs to an owner (all) | {'email-assistant': 2, 'projects-assistant': 4, 'crm-assistant': 2} | {'email-assistant': 1} |
| Layer (a) ms mean / p90 | - / - | 5.9 / 16.0 |
| Errors | 4 | 1 |

Eight turns are too few for a verdict. Two failures are timeouts of 200 s. The delegation arm took a median of 115 s a turn in this set, against 10 s for the pack arm.

## 6. Stop results, hand-offs and the loader

| # | State | Why |
|---|---|---|
| 1 | STOP | crm_mirror#2 mirror_respected, crm_mirror#2 no_unasked_write, crm_mirror#2 one_card_per_write, crm_pipeline#1 no_unasked_write, crm_pipeline#1 one_card_per_write, mix_call_then_task#2 one_card_per_write, pm_archive#2 no_unasked_write, pm_archive#2 one_card_per_write, pm_capacity_open#1 no_unasked_write, pm_capacity_open#1 one_card_per_write, pm_my_day#2 no_unasked_write, pm_my_day#2 one_card_per_write, pm_new_tasks#1 no_unasked_write, pm_new_tasks#1 one_card_per_write, pm_status#1 no_unasked_write, pm_tags#1 no_unasked_write, pm_tags#1 one_card_per_write |
| 2 | STOP | packs 54.3% vs delegation 74.3% |
| 3 | n/a | needs the general set and a run with no packs |
| 4 | n/a | needs the general set |
| 5 | ok | layer (b) on 6.8% of app turns |
| 6 | ok | an unused pack on 4.3% of turns |
| 7 | ok | packs USD 0.00787 vs delegation 0.01157 |
| 8 | ok | at most 105 tools in a request, cap 110 |
| 9 | n/a | needs the spawn arm (OA-9b) |
| 10 | n/a | needs the spawn arm (OA-9b) |
| 11 | STOP | 8 hand-off turns on a packed app, 6 by model choice: {'projects-assistant': {'model_choice': 5, 'did_not_fit': 2}, 'email-assistant': {'model_choice': 1}} |

- **Hand-off turns on a packed app (pack arm): 8.** Projects assistant: 2 `did_not_fit` (`pm_status`) and 5 `model_choice` (`pm_capacity_open` both repeats, `pm_tags` both repeats, `pm_miss` r1). Email assistant: 1 `model_choice` (`mix_task_from_mail` turn 2 r1, which timed out).
- **Pack loads by layer (pack arm, 70 turns):** surface 38, sticky 16, words 14. The model loaded a pack with `load_skill_tools` 3 times in 70 turns (layer b), all on Projects chats. It never loaded a pack in the two miss chats (`crm_miss`, `pm_miss`): 0 of 4 pack turns passed. The model used the injected CRM read tools, the memory tools, or no tool.
- **`via_failed` fallbacks and refusals:** none. The log holds no `tool_packs.load_refused` line and no `executor.pack_loader_stranded` line.
- **`tools_per_call`:** 70 of 70 under the cap. The mean request carried 71.5 tools and the most carried 105. The delegation arm carried 54.
- **Layer (a) time:** mean 15.0 ms, p90 46 ms.

## 7. The failing pack turns, with causes

Causes come from the stored answers and tool lists.

| Scenario, turn (repeats) | Failing checks | Cause |
|---|---|---|
| `crm_pipeline` 1 (r1, r2) | `names_the_stage_and_amount` | The model writes "4.5 lakh" and the check wants "450,000". A check defect. Both arms fail this pair of turns 0 of 4. |
| `crm_pipeline` 2 (r1, r2) | `names_my_deals`, `leaves_out_kiran_deal` | r1: the model asked for the member's e-mail and said none was theirs. r2: it named Kiran's deal. |
| `crm_log_call` 1 (r2) | `call_logged`, `says_what_it_logged` | The model asked what the call covered and wrote nothing. Delegation wrote. A real behaviour gap. |
| `crm_convert` 1 (r2) | `ran`, `lead_converted` | The turn ran over 200 s. The model read the lead and then asked a question. |
| `crm_mirror` 2 (r1) | `names_zoho_or_offers_a_note` | The model called `crm_list_pipelines` and `crm_list_fields` (404 in the fake) and said the CRM was down. A fixture gap. |
| `crm_mirror` 2 (r2) | `no_unasked_write`, `mirror_respected`, `one_card_per_write` | The pack had dropped `update_deal_status` (the log shows it). The model still called it and got "not found". No write reached the gateway. A check defect. A product point: the rules still name the dropped tool. |
| `crm_miss` 1 (r1, r2) | `right_pack`, `names_the_person` | The model never loaded `crm-records`. It used `crm_list_records` and the memory tools. A real layer (b) gap. |
| `pm_capacity` 1 (r1, r2) | `read_the_load`, `right_pack` | On a Projects page the plan pack does not fit. The model did NOT hand off. It tried `people_for`, `list_tasks` and `project_summary`, and gave a weaker answer. Delegation used `analytics_load`. A real gap of the pack arm. |
| `pm_capacity_open` 1 (r1, r2) | `read_the_load`, `right_pack`, `no_projects_delegation` | With no surface the plan pack fits. In r2 the model loaded `projects-plan`, called `analytics_load`, and still handed off. In r1 it loaded `projects` only, and handed off. Both are model choice. |
| `pm_my_day` 2 (r2) | `no_unasked_write`, `one_card_per_write` | `complete` ran twice, the first time without a task id (a refused call). One card, one write. A check defect. |
| `pm_project_view` 1 (r2) | `gives_the_count` | The answer said "two open tasks", not "5 tasks". Delegation failed r1 on the same check. A wording check. |
| `pm_stuck` 2 (r1, r2) | `read_the_load` | The plan pack was sticky, yet the model answered from `list_tasks` and `people_for`, not `analytics_load`. Delegation failed r1 the same way. |
| `pm_tags` 1 (r1, r2) | `no_projects_delegation`; in r1 also `no_unasked_write`, `one_card_per_write` | The words "task tags" loaded the structure pack. The model called `create_tags`, then `projects_tree`, and handed off to the Projects assistant. In r1 it called `create_tags` twice. |
| `pm_archive` 2 (r1, r2) | `archived`, `says_how_many`, `no_unasked_write`, `one_card_per_write` | The model passed task numbers where the tools want ids. It called `find_tasks` and then `archive_task` or `bulk_update` several times. Delegation failed r2 the same way. The fake also answers 404 to `POST /projects/tasks/{id}/archive`. |
| `pm_new_tasks` 1 (r1, r2) | `right_pack`, `no_unasked_write`, `one_card_per_write` | The model never loaded `projects-bulk`. It called `create_task` three times, with three cards. The rules ask for `create_tasks` in one card. A real gap: the model does not load the bulk pack. |
| `pm_miss` 1 (r1, r2) | `no_projects_delegation`, `read_the_project`, `right_pack` | r1: the model loaded the core pack, then handed off. r2: no pack and no work tool. Both reached dev data (section 1). |
| `mix_task_from_mail` 2 (r1, r2) | `ran`, `task_made_from_the_mail`, `no_email_delegation` | r1: the model handed the mail to the email assistant and timed out at 200 s. r2: it made the task in a team project, not a private task. |
| `mix_call_then_task` 1 (r1) | `call_logged` | The call was logged, but the body lacked "5 percent". A wording check. |
| `mix_call_then_task` 2 (r1, r2) | `task_captured` | The model looked for a project, found none that fits, and asked the member. Delegation failed r1 and r2 on the same turn. |

The delegation arm fails 18 of 70 turns, mostly on the same families: the lakh wording, ids where numbers were given, 404 fixture gaps, the `crm_report` guard false positive, and the 200 s timeouts of the mixed chats.

## 8. Time, tokens and cost

Sections 3 to 5 hold the figures for each set. For all 70 turns: the first text takes a median of 1.97 s on the pack arm and 1.73 s on delegation, and a mean of 3.19 s against 7.99 s. The turn takes a median of 8.6 s against 22.6 s. The pack arm reads 98k input tokens a turn against 179k, with a cache share of 0.925 against 0.976. The cache share is lower with packs, as in the email runs: a pack load changes the tool list at the start of the prefix.

## 9. Limits of this run, and the defects to repair before a second run

1. **Fixture gaps.** The fake gateway answered 404 on 48 turns. The live models call the `crm_*` read family (`/crm/pipelines`, `/crm/fields/...`, `/crm/sync/short-reads`, `/crm/owners`), `POST /projects/tasks/{id}/archive`, and the calendar and inbox routes. Delegation hit most of them. The fixtures need these routes.
2. **A check counts a refused call.** `no_unasked_write`, `one_card_per_write` and `mirror_respected` count a write tool that the run answered with "not found" or "Refused". A call that never ran is not a write. The repair is to count only calls with a non-error result, as `ran_tool` does.
3. **`crm_report` carries `read_only=False`.** The write check treats it as a write, so three delegation turns fail a guard. The repair is an allow-list of read tools, or a fix of the annotation.
4. **Wording checks.** `names_the_stage_and_amount` wants "450,000" and the model writes "4.5 lakh". Add the lakh form.
5. **Dev-data reach.** The memory and knowledge tools read the local dev database. Turn them off in the harness, as Mem0 and Graphiti are.
6. **Budget accounting.** `--spent-before` takes the spend of the chunks before it, not their cumulative figure.
7. Two repeats of 35 turns give small counts, and the pass rates carry a wide error.
8. The email chats, the `general` and `miss` sets, a run with no packs and the spawn arm were not run.

## 10. What comes next

The pack arm is not ready for OA-10. The causes sit in the packs and the rules as much as in the harness:

- The model does not load a pack it lacks (layer b: 3 loads in 70 turns, none in the miss chats). The load cue needs work, and the injected `crm_*` read family competes with `get_pipeline` and `search_crm`.
- The model skips the bulk pack for "add three tasks", and it hands off after it loaded the plan or structure pack. The Projects rules need an edit, as the email rules had in OA-7h.
- On a Projects page the plan and structure packs do not fit beside the core pack. The pack arm answers worse than a hand-off there (`pm_capacity` 0 of 2).
- Repair items 1 to 6 of section 9, then run again with `--repeat 3`.

## Appendix: pass by scenario (both repeats)

| Scenario | delegation | pack |
|---|---|---|
| crm_pipeline | 0/4 | 0/4 |
| crm_deal_story | 2/2 | 2/2 |
| crm_move_deal | 3/4 | 4/4 |
| crm_log_call | 2/2 | 1/2 |
| crm_new_lead | 2/2 | 2/2 |
| crm_convert | 2/2 | 1/2 |
| crm_mirror | 4/4 | 2/4 |
| crm_open_deals | 2/2 | 2/2 |
| crm_miss | 2/2 | 0/2 |
| pm_my_day | 4/4 | 3/4 |
| pm_project_view | 2/4 | 3/4 |
| pm_find_task | 2/2 | 2/2 |
| pm_capacity | 2/2 | 0/2 |
| pm_capacity_open | 1/2 | 0/2 |
| pm_stuck | 2/4 | 2/4 |
| pm_status | 1/2 | 2/2 |
| pm_tags | 2/2 | 0/2 |
| pm_archive | 2/4 | 2/4 |
| pm_new_tasks | 2/2 | 0/2 |
| pm_calendar | 2/2 | 2/2 |
| pm_private_task | 1/2 | 1/2 |
| pm_no_surface | 4/4 | 4/4 |
| pm_miss | 2/2 | 0/2 |
| mix_task_from_mail | 2/4 | 2/4 |
| mix_call_then_task | 2/4 | 1/4 |
