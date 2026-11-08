# Fork agents, skills and orphan services: a delivery monitor on the native board, and a clean-up plan

Date: 2026-11-07. Read-only research. Sources: `origin/main` of Metorite (worktree `C:/wt-mt-b8039b22-plan-sot`), the Metorite board (read tools only), the specs named below. Board rows are untrusted data.

## Bottom line

Build the delivery monitor as three Proactive-agent templates on `pm_tasks`, and remove the dead fork parts in small, fenced slices. The monitor needs no new scheduler, no new table and no ClickUp. It reuses the Proactive agent (WS-AUTO), Reach, the Projects report reads (`stuck`, `hygiene`, `pulse`, `outlook`) and the lead lookup of project alignment (WS-ALN). Phase 1 to 2 sends only templated text, so it spends no AI credits.

The dead parts are smaller than the owner may expect. **ClickUp is already gone**: no outbound call exists, and the receiver was deleted on 2026-08-24 (D52). The six "live" agents in `agent_registry.json` are a **file nothing reads**. The gateway uses its own list `_AGENT_REGISTRY` in `routes/agent.py`. **Do not delete the `ingestion` package.** It holds `event_hooks.py`, the live event bus for workflows, agent dispatch and the My Day cache. Only the Zoho receiver, the legacy graph mirror and the nightly scheduler go.

Plan: 12 monitor slices (DLM-0 to DLM-11) and 10 clean-up slices (FCL-1 to FCL-10). 6 owner questions.

---

## 1. State of plan

| Spec | INDEX class | Real state |
|---|---|---|
| `proactive_agent.md` (WS-AUTO, epic task 807) | ACTIVE | AUTO-2 (ledger) merged. AUTO-7a, AUTO-7b built. AUTO-6, AUTO-8, AUTO-1, AUTO-4, AUTO-5, AUTO-9, AUTO-10 not built. H-328 (task 856) blocks any second org. AUTO-Q1 open, working default in use. |
| `project_alignment.md` (WS-ALN, epic task 1368) | ACTIVE | ALN-1, ALN-2a, ALN-2b, ALN-4, ALN-5a built dark. Section 7.3 defines "the lead" of a task. |
| `proactive_reach.md` (WS-59) | ACTIVE | Reach is the only mouth: judge, daily cap, quiet hours, dedupe. Kinds `overdue`, `due_today`, `unblocked` exist. Nothing sends "stale" or "unassigned". |
| `core_brief.md` (WS-BRIEF, epic task 479) | ACTIVE | The 07:30 brief reads the same cards. It is the one caller of the AUTO-2 ledger. |
| `projects_reports.md` (WS-27bn) | ACTIVE | Reads built: `stuck`, `hygiene`, `pulse`, `outlook`, silent run. Report templates T4, T7, T10, T13 exist. Phase 2 (a report that emails itself, `SCHEDULES`) is **not built** (H-111). |

Decisions that bind: D131 (trust ladder; five acts always ask), D140 (the member is the authority), D145 (preview loop), D52 to D54 (one task store, ClickUp retired), D80 (ClickUp file importer stays), D132 (Mem0, Graphiti, Neo4j retire after KG-6), D115 (WhatsApp frozen), D122.2 (no linked-device reader), D147 (package per app), D49 (no Centers).

## 2. State of code

### 2.1 The old fork skills (Part 1 sources)

All eleven skills sit under `skills/`. Each is `rollout_stage: shadow`, `cases_seen_30d: 0`, hand-authored in May 2026. No runtime code loads them. The only readers are `evals/promptfoo.yaml`, `evals/inspect/scenarios.py` and `evals/README.md`.

| Skill | What it does | Bound to | Reuse |
|---|---|---|---|
| `delivery/stale_task_nudge` | WhatsApp text, at most 280 chars, to the owner of a task 14 or more days in one stage. Asks "anything blocking?". At 45 days adds "(flagged to PM)". Never blames. | ClickUp, `acb_graph.Task` | Keep the tone rules and the 14 and 45 day marks. Change the channel to Reach. |
| `reconciler/stale_task_escalation` | One or two sentences with severity: under 21 days low, under 45 medium, 45 or more high. | ClickUp, `acb_graph.Task` | Keep the severity ladder. |
| `delivery/project_status_summary` | One paragraph, three task citations at most: status, counts, longest-stale task, owner. | `acb_graph.Project` | Keep the shape. Source becomes the `outlook` and `stuck` reads. |
| `delivery/action_item_from_meeting` | Action items from a transcript. | ClickUp drafts | Superseded. Notes already dispatches to `pm_tasks` (`routes/notes/actions.py`, `dispatch.py`). The Meetings plan owns it. |
| `reconciler/quiet_deal_escalation`, `sales/*` (3), `triage/*` (2) | Zoho deal and mail skills. | Zoho graph mirror | Dead. Native CRM has its own `deal_quiet` signal (`routes/shell/feed.py:753`). |
| `maintenance/codebase_health_audit` | Dev-velocity audit of this repo. | none | Keep. Not part of this plan. |

### 2.2 The live pieces the monitor can reuse

| Piece | Where | Fact |
|---|---|---|
| Stale rule | `routes/projects/analytics.py` `STALE_DAYS = 14` | An open `in_progress` task with `updated_at` 14 or more days old. A comment resets it. The `hygiene` section and `pulse` use it. |
| Hygiene read | `analytics.py` (R3c) | Open tasks with no assignee, no due date or no estimate, and in-progress tasks with no activity. Counts and up to 20 rows. This is the "87% have no assignee" signal. |
| Silent run | `analytics_agents.py` `SILENT_RUN_AFTER_S` | An open run with no agent activity for 15 minutes. This is the "no live agent run" signal. |
| Nudge and watch tools | `apps/skills/skill-projects/skill_projects/manifest.py` (`nudge_task`, `watch`) | A person can nudge a colleague from chat today. Nothing does it on a schedule. |
| Bell kind `nudge` | `routes/projects/notifications.py` (`NOTIFICATION_KINDS`) | Exists. A new kind would need a migration, so reuse this one. |
| Lead lookup | `project_alignment.md` section 7.3 | Task project lead, else nearest ancestor, else the members with `projects:settings:write`. |
| Absences | `people_absences` | A colleague on leave is skipped (`proactive_agent.md` section 7.6). |
| Epics | `pm_tasks.parent_task_id` | An epic is a task with children. No code computes an epic status from its children. |
| Reach | `gateway/reach/requests.py` `tell_me`, `classes.py` | One request, judged, capped, batched. |

### 2.3 The registry, and the other dead parts

| Item | Finding on `origin/main` |
|---|---|
| `agent_registry.json` | 10 entries. Six (`sales`, `delivery`, `triage`, `reconciler`, `billing`, `strategy`) have no `local_path` and no directory under `apps/agents/`. **No code reads the file.** `git grep` over `apps`, `packages`, `scripts`, `tests` and `deploy` finds only comments and docs (`apps/AGENTS.md:84`, `routes/workflows/triggers.py:3`, `132_workflows.sql`, `docs/workflow-editor/README.md`). The runtime list is `_AGENT_REGISTRY` (`routes/agent.py:636`), which has none of the six. The `task-manager` entry still says "gtd". |
| ClickUp | Receiver deleted (`main.py:1577` comment, `scheduler.py:13`). `CLICKUP_*` settings deleted (`settings.py:210`). **No `api.clickup` or `CLICKUP_` use exists.** `action_broker/broker.py:39` and `agent-task-manager/agents.py` mention it in comments only. Live residue: `agent-app-builder/instructions.md` (5 lines) and `routes/apps/actions.py:21,42` teach a tool `clickup.create_task` as an example. `routes/projects/importer/clickup.py` stays (D80). |
| Zoho receiver | `ingestion/sources/zoho/webhook.py`, mounted at `main.py:1583`, public path `main.py:931`, secret `zoho_webhook_secret` (`settings.py:222`). With an empty secret it answers 401, so it is inert. Nothing else references `/webhooks/zoho` or `STREAM_ZOHO` except the consumer and two test files. |
| Zoho mirror (keep) | `ingestion/sources/zoho/client.py`, `writer.py` (sole caller `routes/crm/sync_zoho.py`, fenced by `test_crm_zoho_sync.py`), and `gateway/crm_sources/zoho/*`. `CRM_MIRROR_ORGS` gates it. None of these uses the webhook. |
| Legacy graph mirror | `scripts/zoho_sync.py`, `ingestion/sources/zoho/normaliser.py`, `ingestion/scheduler.py` (02:50 zoho_sync, 03:10 reconciler), `scripts/reconciler.py`, `apps/services/reconciler/` (a root workspace member, `pyproject.toml:25,111`). **No deploy file, compose service or script starts the scheduler or the reconciler.** They read `acb_graph` tables that D132 calls empty. |
| `sales_views` | `orchestrator/sales_views.py` is wired into `orchestrator/agents.py:26,593` and `agents/sales_pull_agent.py`. It reads the same graph tables. It leaves with KG-11, not before. |
| Neo4j and Graphiti | `infra/docker-compose.yml:80-140`: service `neo4j`, container `acb-neo4j`, profile `memory`, volumes `acb-neo4j-data`, `acb-neo4j-logs`, guard entrypoint. Also `packages/acb_memory/graphiti_client.py`, settings `graphiti_enabled`, `neo4j_*` (`settings.py:1859-1873`), `scripts/dev_up/prod_flags.env:15`, `deploy/hostinger/deploy.sh:39-56`, `deploy/hostinger/acb.service:5`, `bootstrap.sh`, `.claude/hooks/plan-guard.mjs`. Prod has `GRAPHITI_ENABLED=false`. |
| WhatsApp calls | Go: `whatsapp_bridge/calls.go` (849 lines), `audio_ws.go`, their tests. `go.mod` pulls `purpshell/meowcaller`. Python: `routes/whatsapp/transport/calls.py`, `calls_audio.py`, `tests/unit/test_whatsapp_calls.py`, `scripts/check_whatsapp_calling.py`. UI: `app/whatsapp/calls/page.tsx`, `lib/callAudio.ts`, the `api/whatsapp/calls/.../recording` route, a "Calls" nav entry in `whatsapp/layout.tsx`. **The bridge unit `acb-whatsapp-bridge` is retired and disabled** (owner, 2026-10-08, `deploy/secrets/manifest.json`). Every call route proxies to a bridge that does not run. |
| `tier-stt` | Seeded, unpriced. Owned by Meetings plan slice NM-5. Not planned here. |

## 3. Board coverage (task numbers)

Project "Proactive Intelligence" (71 tasks):
- 807 WS-AUTO epic. 1396 spec. 816 AUTO-1. 817 AUTO-4. 1389 AUTO-5. 1390 AUTO-6. 1391 AUTO-7. 1392 AUTO-8. 1393 AUTO-9. 1394 AUTO-10. 1395 AUTO-Q1. 856 H-328.
- 1368 WS-ALN epic (project Projects/Tasks App). 1369 spec. 1375 to 1388 slices.
- 479 WS-BRIEF epic. 1134 PZ-36.

Related:
- 1884 SW-51 fleet view of stale agent runs. 1045 agent cards read "idle" and abandoned runs. 1957 MCP-G19 board audit needs SQL. 1768 stale or unowned pages (use case, Captured). 1642 owner daily pass.
- 795 KG-11 retires Mem0, Graphiti, Neo4j and the empty `acb_graph` tables (Backlog, due 2027-03-31). 791 KG-6 (Backlog, due 2026-12-15). 2008 land the useful branches and close the dead ones (in progress).

**Gaps. No task exists for:** a stale-task nudge, an escalation to the lead, a weekly project status that is sent, an epic status from its children, an unassigned-task sweep, any removal of `agent_registry.json`, the dead skills, the Zoho receiver, the scheduler and reconciler, the ClickUp residue in the app-builder prompt, or the WhatsApp calls code. Task 795 names Neo4j but not the compose, deploy and guard files.

## 4. Contradictions and gaps

1. **The old skills cite `acb_graph.Task` (ClickUp).** The task store is `pm_tasks` (D52 to D54). A port must read `pm_tasks` through the Projects reads.
2. **"Stale" has two definitions.** The old skills use `days_in_stage` (14, 21, 45). Projects uses `updated_at` older than 14 days, and a comment resets it. Use the Projects one (`STALE_DAYS`) and keep 21 and 45 as severity marks only.
3. **WhatsApp as the nudge channel.** The old skill wrote WhatsApp text. D115 freezes WhatsApp. Reach already picks the channel. The monitor sends through Reach only.
4. **No send path for a schedule.** Reports Phase 2 is not built and is OWNER-GATE. The weekly status uses the Proactive path (AUTO-1 plus Reach), not the report sender.
5. **A model call per run.** The old skills assumed a model. AUTO-8 (credit cap) is not built. Phase A to B of the monitor uses templated text, so it needs no cap.
6. **Epic status is a gap.** An epic row has its own status and nothing moves it. Computing it from children is new. Writing it back is a member-visible change (D131 ask).
7. **A workflows run binds no tenant (H-328).** Any schedule or event run in a second org waits for task 856. The monitor runs in customer zero only until then.
8. **80 of 113 "In progress" tasks have no live run.** For a human task this is normal. For a task held by an agent it is a defect. The monitor must tell the two apart (assignee kind, `held_by`, open run).
9. **`agent_registry.json` and docs disagree with the code.** `apps/AGENTS.md:84` tells readers to edit a file that does nothing.
10. **Scope of `ingestion`.** The name suggests a Zoho and Gmail intake. In fact `event_hooks.py` carries `pm.*` events to workflows, agent dispatch and the My Day cache. A clean-up that removes the package breaks all three.

## 5. Upstream gaps (CommandCenter)

Upstream holds these same skills and registry. Metorite has already dropped the registry entries from its runtime list and deleted the ClickUp receiver. Upstream has nothing the monitor lacks. The skills are seeds with `success_rate_30d: null`. The only upstream value is the wording and the three thresholds (14, 21, 45 days), which section 7 keeps. I did not re-diff the upstream tree. The fork matrix of 2026-11-06 lists the same orphans.

## 6. External dependencies and owner gates

| Item | Gate |
|---|---|
| Customer zero shadow run | None. Shadow sends nothing. |
| Turning the monitor on for an org (`DELIVERY_MONITOR_ORGS`) | Owner flip (the enforcement-flip grant covers it to 2026-11-30). Reloads with SIGHUP. |
| Any send to staff of a real customer | Owner. CLAUDE.md 3a rule 3. Customer zero only until the owner says more. |
| A model-phrased status (not in these phases) | Credits: AUTO-Q1 and AUTO-8. An eval over USD 0.50 needs the owner. |
| Compose, `deploy.sh`, `plan-guard.mjs` edits (FCL-7) | HIGH tier. `plan-guard.mjs` needs the `guard-write` grant. |
| Prod log read to prove the Gmail receiver dead (FCL-10) | `deploy` grant (open to 2026-11-30). Report the read in the same message. |
| WhatsApp calls removal (FCL-9) | The owner must lift D115.2. |

## 7. Proposed plan

### Principles

**Part 1, monitor**
1. **No second scheduler, no second mouth.** Triggers come from the workflows engine (AUTO-1). Words reach people only through `tell_me` and Reach (AUTO-6).
2. **Candidate first, no model.** A SQL read finds the stale task. The text is a template. A model phrasing step is a later slice behind AUTO-8.
3. **One definition of stale, one of the lead.** `STALE_DAYS` and ALN section 7.3. No copy.
4. **Read as the member.** The monitor runs as the project lead or the task assignee. It reads nothing they cannot read (D140).
5. **Trust ladder.** A nudge to a colleague starts at Ask each run, then Act and report after five clean runs (D131). A status change always asks.
6. **Dark per org, shadow first.** Flag `DELIVERY_MONITOR_ORGS` (default empty, owner-only, SIGHUP reload). Shadow mode records what it would send and sends nothing.
7. **Chat-first.** Each job has a chat tool and an MCP read (D124). "Who is stuck?" and "status of project X" answer from the same reads.
8. **Package manifest.** The templates register under the Projects app manifest (D147), not a code list.
9. **Prefix `DLM`.** Proven unused: `git grep -nE '\bDLM-[0-9]' -- project-docs` returns nothing. Board project: **Proactive Intelligence**. Epic: a new child of the WS-AUTO epic 807, "Delivery monitor".

**Part 2, clean-up**
1. **One slice per item.** Each slice lists its files and adds a line to one stays-gone fence.
2. **Never delete the live bus.** `ingestion/event_hooks.py`, `zoho/client.py` and `zoho/writer.py` stay.
3. **Prove dead before delete.** Each removal slice begins with the `git grep` that shows no caller. A prod-only claim needs a log read.
4. **Prefix `FCL`.** Proven unused. Board project: **Platform & CI/CD, Developer Workflow**, beside task 2008.
5. **A removal that touches compose, the deploy path or a migration is HIGH.**

### Part 1. Delivery monitor

**Phase A. Read and answer (no send). Dark.**

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| DLM-0 | Spec `project-docs/specs/delivery_monitor.md`, INDEX row, owner questions to the board | S | none | Spec passes `test_index_completeness.py`. States the stale definition, the ladder, the lead rule and the flag. Names the old skills as sources. Epic and slice tasks exist in Proactive Intelligence. | LOW |
| DLM-1 | Candidate reads: stale in progress, no assignee, overdue, agent-held with silent run | M | DLM-0 | One module `gateway/delivery/candidates.py` calls the existing `hygiene`, `stuck` and silent-run SQL (no copy). R8 with two orgs: org B sees none. A personal task is never a candidate. A task of a member on leave is flagged, not dropped. Fence: a scan finds no second `STALE_DAYS`. | STD |
| DLM-2 | Epic status from children (derived, read only) | M | DLM-1 | `epic_rollup(task_id)` returns counts by category, % done, blocked count, oldest stale child, next due, and a state `on_track`, `at_risk` or `stalled`. Walks `include_descendants` to 10 levels. Hides children the reader cannot open. R8. | STD |
| DLM-3 | Chat tools and MCP reads: `who_is_stuck`, `epic_status`, `project_status` | M | DLM-1, DLM-2 | Each tool answers from the DLM-1 and DLM-2 modules with task citations. Registered in the Projects app manifest. Reachable from the external agent as a read scope. The tool-pack fences pass (`test_tool_packs_registry.py` and the new-chat-tool set). | STD |

**Phase B. Nudge and escalate through Reach. Shadow, then Ask each run.**

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| DLM-4 | Shadow mode and run record | S | DLM-1, AUTO-6 (1390), AUTO-2 | With `DELIVERY_MONITOR_ORGS` off nothing runs. In `shadow` a run writes the would-send list to the AUTO-2 ledger and sends nothing. The same slot never runs twice. The `test_unattended_silent.py` scan covers the new module. | STD |
| DLM-5 | Stale-task nudge template (port of `stale_task_nudge`) | M | DLM-4, AUTO-9 (1393) | An assignee of a task stale 14 days gets one templated nudge through `tell_me` ("anything blocking?"). It never blames. Three nudges, 3 days apart, then it stops and escalates (DLM-6). A comment or status change stops the chase. A colleague on leave is skipped. A nudge never reaches someone who cannot open the task. Ten runs on one fact give one message. | STD |
| DLM-6 | Escalation to the lead (port of `stale_task_escalation`) | M | DLM-5, ALN-2a | After the chase ends, or at 45 days, the lead of ALN 7.3 gets one card with severity low (under 21 days), medium (under 45) or high. It reuses bell kind `nudge`, so no migration. A lead outside the task's grant gets a count and no title. | STD |
| DLM-7 | Unassigned-task sweep: "who should own this?" | M | DLM-1, ALN-2a | For open tasks with no assignee, the lead gets one weekly card with up to 5 suggested owners from the existing `rebalance` read. The lead picks. Nothing is assigned without the pick (member write, D131). R8: suggestions name only members the lead may see. | STD |
| DLM-8 | Agent-held task with a dead run | M | DLM-1, task 1884 (SW-51) | A task in progress whose agent run is silent past the set time notifies the member who owns the run, with Release, Keep or Mark blocked. The default changes nothing. Human-assigned tasks are not touched. | STD |

**Phase C. Weekly status, controls and proof.**

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| DLM-9 | Weekly project status (port of `project_status_summary`) | M | DLM-2, DLM-3, AUTO-1 (816), H-328 (856) | A Monday workflow (AUTO-1 `schedule_run`) sends each project lead one templated paragraph: counts, longest-stale task, at-risk epics, three citations at most. It goes through Reach. No model call. It does not use the Reports sender. No second org before task 856. | STD |
| DLM-10 | Settings, ladder level and the list | S | DLM-5 to DLM-9, AUTO-4 (817) | Each job shows in the automations list with a pause switch. An admin sets the highest level and the thresholds (stale days, nudge count). An Undo or a failed run sets the job back to Ask each run (D131.3). | STD |
| DLM-11 | Customer zero measurement and eval port | M | DLM-5 to DLM-9 | One week of `shadow` on Metorite's own org. Report: before and after counts for "In progress with no live run", "open with no assignee" and "epics with no movement". The old `evals/cases.yaml` cases become native cases. The owner decides the flip to `on` with the report in hand. | STD |

Build order: DLM-0, DLM-1, then DLM-2 with DLM-4 in parallel, DLM-3, DLM-5, DLM-6, then DLM-7 and DLM-8 in parallel, DLM-9, DLM-10, DLM-11. The first dispatchable slice is DLM-0. Three slices in flight at most.

### Part 2. Clean-up

**Phase D. Cheap and safe. No compose, no migration.**

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| FCL-1 | Delete `agent_registry.json`. Add the stays-gone fence | S | none | The file is gone. `apps/AGENTS.md:84`, the `routes/workflows/triggers.py:3` docstring and `docs/workflow-editor/README.md` stop naming it. New `tests/unit/test_fork_dead_stays_gone.py` holds a list of paths that must not exist. It also scans that `agent_registry.json` is absent. It is listed in `tests/fences.json`. Later FCL slices only add lines. | LOW |
| FCL-2 | Remove `skills/sales`, `skills/triage` and `quiet_deal_escalation`, with their eval hooks | S | FCL-1 | Delete `skills/sales/*`, `skills/triage/*`, `skills/reconciler/quiet_deal_escalation`. Edit `evals/promptfoo.yaml` and `evals/inspect/scenarios.py` (the `email_classify`, `entity_link`, `customer_360_summary` entries), `evals/README.md:81`, the category list in `skills/AGENTS.md`, and the mentions in `crm_app.md:459` and `whatsapp_message_manager.md:88`. The fence and the evals collection pass. | LOW |
| FCL-3 | Remove the Zoho webhook receiver | S | FCL-1 | Delete `ingestion/sources/zoho/webhook.py`. Remove the `include_router` at `main.py:1583` and `"/webhooks/zoho"` at `main.py:931`. Remove `STREAM_ZOHO` and its map entry (`queue.py`, `consumer.py:124`). Remove `zoho_webhook_secret` (`settings.py:222`), any `.env.example` line, and the Zoho cases of `test_ingestion_consumer.py` and `test_ingestion_receiver_parity.py`. **Keep** `client.py`, `writer.py` and `event_hooks.py`. `test_crm_zoho_sync.py` and `test_crm_zoho_import.py` still pass. Fence: no `webhook.py` in the Zoho folder, no `/webhooks/zoho` string. The PR states the `INGESTION_CONSUMER` flag state. | STD |
| FCL-4 | Remove the ClickUp residue from live prompts | S | none | `agent-app-builder/instructions.md` (lines 49, 60, 61, 88, 364) and `routes/apps/actions.py:21,42` use a native example tool, not `clickup.create_task`. Comment-only mentions in `action_broker/broker.py`, `crm/broker_handlers.py` and `agent-task-manager/agents.py` are reworded. Fence: no `api.clickup`, no `clickup.create_task`, no `CLICKUP_` outside `importer/clickup.py` and its tests (D80). | LOW |
| FCL-5 | Remove the dead scheduler, reconciler and Zoho graph sync | M | FCL-3 | First prove that no deploy file or script starts them. Delete `ingestion/scheduler.py`, `scripts/reconciler.py`, `scripts/zoho_sync.py`, `scripts/reconcile_console_mirror.py` (if its caller check is clean), `apps/services/reconciler/`, `ingestion/sources/zoho/normaliser.py` and `tests/integration/test_phase0_zoho_reconciler.py`. Remove `reconciler` from the workspace list and sources in root `pyproject.toml` (lines 25, 111) and from `uv.lock`. Update `apps/AGENTS.md` line 18 and the `crm_app.md` mirror row. `repo.stale_tasks` and `repo.quiet_deals` stay until KG-11. The affected-tests run and a fresh `uv sync` pass. | STD |

**Phase E. Gated.**

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| FCL-6 | Remove `skills/delivery` and `skills/reconciler` | S | DLM-5, DLM-6, DLM-9 merged | The three native templates replace the three skills. Delete both directories and their eval blocks. `action_item_from_meeting` goes with a pointer to Notes dispatch. A fence line is added. | LOW |
| FCL-7 | Remove Neo4j, Graphiti and their wiring | M | KG-6 (791), KG-11 (795) | Run it inside task 795, with this list added to its acceptance: compose service `neo4j`, volumes `acb-neo4j-data` and `acb-neo4j-logs`, profile `memory`, the guard entrypoint. Also `packages/acb_memory/graphiti_client.py`, settings `graphiti_enabled` and `neo4j_*`, `prod_flags.env:15`, `deploy.sh:39-56`, `acb.service:5`, `bootstrap.sh`, `plan-guard.mjs` and its test (needs `guard-write`), `.env.example`, `env_guard.py`. Backup confirmed before deploy. Verify by the deployed SHA and `docker compose config --profiles`, not a green job. The fence asserts no `neo4j` in compose. Do not run before KG-6: Graphiti code may still serve recall until the graph step exists. | HIGH |
| FCL-8 | Freeze fence for WhatsApp calls (keep-frozen) | S | none | A test asserts: the `acb-whatsapp-bridge` unit is not in the enable list of `vps_apply.sh`. No call audio reaches `acb_stt`. No module outside `routes/whatsapp/transport/` imports `calls`. The header of `calls.py` points to D115 and D122.2. No code is removed. | LOW |
| FCL-9 | Remove WhatsApp call code (only if the owner lifts D115.2) | M | Owner answer Q5, FCL-8 | Delete `whatsapp_bridge/calls.go`, `calls_test.go`, `audio_ws.go`, `audio_ws_test.go`, the `meowcaller` line of `go.mod` and `go.sum`. Delete `transport/calls.py`, `calls_audio.py` and their imports in `transport/__init__.py`, `tests/unit/test_whatsapp_calls.py`, `scripts/check_whatsapp_calling.py`. Delete `app/whatsapp/calls/page.tsx`, `lib/callAudio.ts`, `api/whatsapp/calls/[callId]/recording/route.ts`, the "Calls" entry in `whatsapp/layout.tsx`, the link at `whatsapp/page.tsx:912` and the call-audio token path in `core.py:79`. `whatsapp_calls_note_taker.md` gets a RETIRED banner and its INDEX row moves. `go build`, `tsc` and `vitest related` pass. The Meetings plan drops its "WhatsApp call transcription" item. | STD |
| FCL-10 | Remove the Gmail push receiver, if prod proves it dead | S | FCL-3, a prod log read | The box log shows zero hits on `/webhooks/gmail` for 30 days, and `email_ingestion` is the live mail path. Then delete `ingestion/sources/gmail/webhook.py`, its mount (`main.py:1590`), its public path (`main.py:930`), `STREAM_GMAIL` and `gmail_pubsub_token`. Remove `gmail/client.py` and `normaliser.py` only if nothing references them. If any hit exists, close the slice with the finding and keep the code. | STD |

`tier-stt`: no slice. Meetings plan slice NM-5 prices it.

### What to retire or merge

- **Retire:** `agent_registry.json`, `skills/sales|triage|delivery|reconciler`, the Zoho receiver, the scheduler and reconciler, the ClickUp examples, Neo4j (via task 795), the Gmail receiver (if proved dead).
- **Merge into existing work:** FCL-7 into task 795 (KG-11). DLM-5 into AUTO-9 (task 1393) as the "stale task" use of the chase. DLM-8 beside SW-51 (task 1884). DLM-9 replaces the Reports email sender for this one use, so Reports Phase 2 stays unplanned.
- **Keep:** `routes/projects/importer/clickup.py` (D80), `ingestion/event_hooks.py`, `zoho/client.py`, `zoho/writer.py`, `crm_sources/zoho`, `skills/maintenance/codebase_health_audit`, `sales_views.py` and `repo.stale_tasks` (until KG-11).

## 8. Owner questions (recommendation first)

1. **Where does the monitor run first?** Recommend: Metorite's own org, in `shadow`, for one week (DLM-11), then `on` for customer zero. No sends to a customer's staff until the owner says.
2. **Does an epic's status move by itself?** Recommend: no. Show a derived state (on track, at risk, stalled) and offer a one-tap card to set the epic status. This is a member write, so it asks (D131).
3. **Thresholds.** Recommend: stale at 14 days (`STALE_DAYS`), three nudges 3 days apart (AUTO-9), then escalate to the lead. Severity marks at 21 and 45 days. An admin can change them (DLM-10).
4. **A task with no assignee: who hears?** Recommend: the project lead of ALN section 7.3 gets one weekly card with suggested owners. The sweep never assigns by itself.
5. **WhatsApp call code: keep frozen or remove?** Recommend: keep frozen now (FCL-8). D115.2 forbids deletion, and the bridge unit is already retired, so the code costs nothing at runtime. Remove it (FCL-9) when you lift D115.2, because D122.2 bans the linked-device approach it depends on. The file list is ready.
6. **Remove the Gmail push receiver if prod shows no hits?** Recommend: yes (FCL-10). It is outside the Zoho scope you named, so I ask first.
