# Spec set final review (origin/main, 2026-11-10, read-only)

## 1. Inventory
- 106 .md specs under project-docs/specs (not archive), plus 11 files and a README in specs/archive. INDEX.md is 172 lines.
- Every non-archive spec on disk is named in INDEX. Only specs/archive/* is not listed by file (INDEX line 167 lists the folder). Not a defect.
- INDEX names 8 files outside specs/: work_plan.md, system_architecture.md, agent_repo_compatibility.md (project-docs/), style_ste.md, job_queue.md, external_agents_connect.md (docs/). Two names have NO FILE anywhere: `artifacts.md` and `clearance.md` (probably agents-workspaces-artifacts.md and docs/multiplayer/memory-clearance.md).
- Sections: ACTIVE table (lines 30-118, about 85 rows), board rows with no spec (WS-36 restore, WS-37 compliance), CONTRACTS (10), REFERENCE (about 9), DEFERRED/HISTORICAL (7 rows).
- Misfiled: calendar_events_reminders.md and agent_sandbox.md sit in the DEFERRED/HISTORICAL table although their rows say ACTIVE. whatsapp_message_manager.md and whatsapp_assistant_channel.md are ACTIVE although D115 freezes WhatsApp.
- work_plan 2.1 rows with owning spec "none yet": WS-CZ, WS-WH, WS-DOC, WS-IR.
- INDEX line 19 says "D1-D93, current 2026-10-07": stale (D204 now).
- STAGING_SETUP_RUNBOOK.md is ACTIVE in INDEX; staging was retired (D144). Stale ACTIVE.
- staging_and_daily_release.md INDEX row says "PROPOSED ... staging deployment that takes every merge"; its own banner says staging retired. development_and_delivery_framework.md (ADOPTED, 53 staging mentions) has no D144 banner.
- ACTIVE specs the board no longer follows: department_centers.md (parked D49), subscription_console.md (grid superseded), crm_app.md (as-built record), task_manager_app.md and my_tasks_cutover.md (WS-39 done), the two WhatsApp specs (D115), future_modules_roadmap.md, multi_agent_orchestration.md (Phase 4 only).

## 2. Board map (epic or app project to spec)
SPEC = ACTIVE spec exists. SLICE = only a spec-slice task, no spec text. NONE = nothing.

| Board item | Owning spec | State |
|---|---|---|
| WS-LL learning loop (2624) | orchestration_playbook.md 6c and 7a (LL-0..LL-13, tasks 2625-2639) | SPEC as a section. Not in work_plan 2.1 or the INDEX row |
| Agent kit KIT (2547) | orchestration_playbook.md 6a, 6b | SPEC as a section |
| SCALE (2566) | none | NONE |
| Rich email composer (2640, RC-0 2641) | email_app_master_plan.md has no RC-0 | SLICE |
| WS-WB WhatsApp bots (2650, WB-0 2651) | nearest whatsapp_assistant_channel.md 19 (CH-TEAM). whatsapp_message_manager.md is the inbox direction | SLICE |
| WS-BK booking and payment (2675, BK-0 2676) | nearest ai_studio.md 20.5 item 11 (O11 Bookings) and the hospital example. calendar_events_reminders.md 12 says out of scope | SLICE |
| WS-KS knowledge everywhere (2713, KS-0 2714) | knowledge_base, company_context, knowledge_curation, knowledge_graph | SLICE (adjacent ACTIVE specs) |
| WS-CX CRM complete (2732, CX-0 2733) | crm_platform, crm_conversational, one_record_card | SLICE (adjacent). CRM-S9 products and quotes is "spec slice only" |
| WS-HX app harness (2757, HX-0 2762) | none. Nearest maf_coding_engine.md 18 (CA-14) and ai_studio.md | SLICE |
| WS-CH CRM sales harness (2758, CH-0 2770) | none | SLICE |
| WS-CM CRM mirror and connectors (2790, CM-0 2791) | crm_platform (Mirror mode), integrations_console, company_context connector contract | SLICE. ID collision: work_plan.md:160 WS-CM = Console and Money |
| WS-PLN seat tiers (Billing) | none. D152 text is in work_plan only. launch_surface.md 4 still describes the flat seat | SLICE/NONE |
| Projects/Tasks App | project_management_app, one_projects_app, projects_*, project_alignment, project_planning, one_record_card, task_references, waiting_on_me | SPEC |
| MCP & External Agents | external_agent_access, agent_coordination, orchestration_playbook, external_agent_dispatch (proposed) | SPEC |
| My Email | email_app_master_plan, shared_mailbox | SPEC (oversized) |
| Onboarding | onboarding, assistant_guide | SPEC |
| Platform Core | saas_multitenancy (+2), org_access_control, tenancy_and_visibility, box_hardening | SPEC |
| CRM | crm_platform, crm_conversational, one_record_card, crm_app (as built) | SPEC (oversized) |
| AI Chat Interface | one_assistant_context, chat_first_surface, chat_organization, chat_parity, chat_run_continuity | SPEC |
| Knowledge | knowledge_base, knowledge_curation, knowledge_graph, company_context | SPEC |
| Personalization & Memory | personalization, memory_architecture | SPEC |
| Proactive Intelligence | proactive_agent, proactive_reach, core_brief, waiting_on_me | SPEC |
| Calendar App | calendar_focus_os, calendar_events_reminders, calendar_timeboxing | SPEC (stale gtd text) |
| My Day | my_day_feed | SPEC |
| AI Studio | ai_studio, agent_sandbox | SPEC (oversized) |
| Billing & Accounts | customer_console, credit_pricing, subscription_console, launch_surface 4 | SPEC, pricing stale |
| Agent Tooling | web_research, maf_coding_engine | SPEC |
| Package Host | package_host | SPEC |
| WhatsApp | whatsapp_message_manager, whatsapp_assistant_channel | SPEC, wrong direction |
| Meeting Notetaker | note_taker_app, meeting_bot_platform_plan | SPEC (preview, M4) |
| Artifact | artifact_management | SPEC |
| Operator Console | operator_console_money, operator_identity_and_access, customer_console | SPEC. Needs D204.11 cleanup (task 2611) |
| Platform & CI/CD | development_and_delivery_framework, engineering_practice, ci_velocity, backup_and_restore, deploy_delivery_path | SPEC. Observability and Secrets only thinly (observability_e2) |
| Organisation, My Profile, Quality of life, User Stories, Roadmap | organization_identity, people_center_app. Others none | partial/NONE |
| WS-36 restore, WS-37 compliance, WS-IR, WS-WH, WS-DOC, WS-CZ | none (INDEX admits WS-36 and WS-37) | NONE |

## 3. Contradictions and stale lines (file:line on origin/main)
1. **Price.** Wins: D204.9 and D190.3 (D152 tiers replace flat 500 at PLN-0). Stale: CLAUDE.md section 2; launch_surface.md:63, :226-277, :536 (a notice at :222 covers section 4 only); saas_multitenancy.md:51, :1187; subscription_console.md:3-7; agent_coordination.md:474, :724; ai_studio.md:783 (STU-Q7); crm_platform.md:792, :1236; one_projects_app.md:230; people_center_app.md:122; proactive_reach.md:748; whatsapp_assistant_channel.md:823; INDEX launch_surface row. Fix: write a PLN spec, then replace "the 500 seat" with "the seat (D152)". Until then add the D204.9 line.
2. **Aroh.** Wins: D143 and D204.11. Stale: customer_console.md:37-40 and :1994-2100 (CP-APP "BUILT", plan_slug aroh, hub contract, /aroh page; 55 mentions). external_agent_access.md:5, :357 (D-EA-11), :1453-1501, :2586 (EA-16). Fix: banner CANCELLED (task 2611) or archive.
3. **Staging.** Wins: D144. Stale: INDEX rows for STAGING_SETUP_RUNBOOK.md and staging_and_daily_release.md; STAGING_SETUP_RUNBOOK.md:1 "Status: ACTIVE"; development_and_delivery_framework.md (no D144 banner); work_plan 2.1 WS-PL title. personalization.md (25), github_integration.md (24), glass_look.md (17) also mention staging gates. Fix: move runbook and the staging half of the framework to HISTORICAL.
4. **Booking.** Wins: owner 2026-11-10. calendar_events_reminders.md:271 ("no free and busy view"), :2023 (Q2), :2035 (free and busy, D54.4), :2038 ("Room and resource booking") are in 12 Out of scope. ai_studio.md:1092 and :1647-1650 already define Bookings (O11) as a Studio pattern, so the feature has two homes. Fix: BK-0 picks one owner and amends 12 in the same PR, with a D-number narrowing D54.4.
5. **WhatsApp direction.** Wins: owner 2026-11-10 (bots; inbox and ingest on hold) plus D115. whatsapp_message_manager.md (whole spec: inbox, :37 "interrupt stream, not an inbox", :117 six-month history sync, :270 search over history, :289 voice from past sends) has no D115 banner (0 hits). INDEX lists it ACTIVE WS-20. Fix: banner ON HOLD, move to DEFERRED, point WB-0 at whatsapp_assistant_channel.
6. **ID collision.** work_plan.md:160 WS-CM is Console and Money. The new epic WS-CM (CRM mirror) reuses it. R2 forbids ID reuse. Rename one.
7. **CRM quotes.** crm_app.md:419 "Quoting/invoicing/taxes ... out of scope" vs crm_platform.md:769 (CRM-S9 products, line items, quotes: spec slice only) vs the proposed catalog as the one price source. crm_platform wins (crm_app is as-built). CX-0 writes the catalog section and reverses :419 by D-number.
8. **Customer-safe audience as a page property.** D12 (work_plan.md:2403) is two axes. crm_conversational.md:46 and project_management_app.md:4499 say "no third visibility mechanism / no third axis". knowledge_base.md:269 puts a customer-facing page out of scope. artifact_management.md:147 refuses anonymous public links. A page property is a third mechanism unless a D-number defines it as a subject kind inside D12's grant grammar. Needs the D-number before KS-0.
9. **gtd_.** calendar_focus_os.md:9-11 says the calendar persists to `gtd_items`. Migration 217 dropped `gtd_*` (my_tasks_cutover.md:1314, CLAUDE.md section 2). Fix: rewrite the correction block to pm_tasks and pm_task_personal. task_manager_app.md has a D53 banner at :3 (OK, 75 mentions are history).
10. **email_knowledge.** knowledge_curation.md:254, :986, :1278 (KC-16) call it a second store folded in later. email_app_master_plan.md:4205-4212 still builds on it. colleague_onboarding.md:651 lists it. KS-0 must state the end state (D105 one retrieval seam).
11. **ClickUp.** project_management_app.md (149 mentions), project_import.md (82, legal under D80 file importer), task_manager_app.md (53). Banners exist; the body is history. A front page fixes it. CLAUDE.md is consistent.
12. **Centers.** department_centers.md:3 banner OK. navigation_shell.md:812-817 keeps /centers unlinked. launch_surface.md:38-60 consistent. No spec found that surfaces Centers. Remaining risk: INDEX still shows WS-13..16 as ACTIVE.
13. **INDEX header** line 19 counts D1-D93. D186.3 (work_plan.md:5653) says "flat seat" reads "seat of D152" at PLN-0, yet no spec holds the D152 tiers.

## 4. Agent readiness
Lines (KB): email_app_master_plan 23,020 (1,747); crm_platform 12,247 (765); customer_console 12,092 (802); project_management_app 9,770 (750); projects_ai_chat 7,570 (431); maf_coding_engine 6,267 (347); my_day_feed 4,185; personalization 4,164; external_agent_access 4,059 (673, long lines); saas_multitenancy 3,915; crm_app 3,184 (259); ai_studio 1,733 (371, very dense).
Verdict: no agent can read the top 10 whole. Dispatch works only because tasks quote section anchors.
Contract gaps (keyword heuristic, not a manual audit):
- No dated "verified against code" header in the first 40 lines: customer_console, project_management_app, crm_app, my_tasks_cutover, backup_and_restore, mcp_plugin_integration, whatsapp_calls_note_taker, note_taker_app, live_meeting_copilot, box_hardening, proactive_reach, meeting_bot_platform_plan.
- No "done when" and few gate labels or verify commands: box_hardening (0 done-when, 11 gates), proactive_reach (0 non-goals, 2 command hits), observability_e2, backup_and_restore, memory_architecture, org_access_control, note_taker_app. box_hardening and proactive_reach are ACTIVE dispatch sources, so those two matter.
- R9 "Shell manifest" absent in email_app_master_plan, project_management_app, projects_ai_chat, one_projects_app, calendar_focus_os, people_center_app, my_day_feed. R9 says older specs gain it when next worked (advisory).
- Layered self-contradiction: project_management_app (12 wins over 6, 7, 11), task_manager_app (13 wins), customer_console (6A blocks each with its own status), ai_studio (D202 rewrite after STU-0), external_agent_access (about 60 sentences of status).
Cleanup proposal:
1. A one-page Front page at the top of each of the 12 big specs: status, scope, non-goals, slice table with task numbers, verify commands, section pointers. INDEX row becomes a link plus one line (rows now run over 1,000 characters; INDEX is 73 KB).
2. Split: email_app_master_plan into core, triage and AI, connect, and build log. crm_platform into native, mirror and Zoho, insights, build log. customer_console into money, router and models, operator. ai_studio into core, outside world (20), IDE (D202). external_agent_access into edge and build log. project_management_app: keep 12 on, archive 6, 7, 11.
3. Archive: STAGING_SETUP_RUNBOOK, staging half of the framework, department_centers, subscription_console grid, crm_app, task_manager_app body, whatsapp_message_manager (hold), the Aroh blocks.
4. Fence: extend tests/unit/test_index_completeness.py to fail on INDEX names with no file, an ACTIVE row whose file banner says retired or on hold, and a spec over 3,000 lines with no Front page.
5. Write short new specs (under 600 lines, seven-point contract) first: PLN, WB, BK, KS, CX, CM, HX with CH, RC, SCALE.

## 5. Product shape as a whole
No spec states it. Closest:
- package_host.md 0 Thesis: every app is a package; the shell reshapes around org and person. Apps and arrangement only.
- ai_studio.md 0 Thesis: every company has work no product covers; composition first (objects, actions, workflows, views, skills); four containment layers. Building only, not the core.
- launch_surface.md: day-one surface and pricing. core_brief.md: the founder brief. work_plan.md 2.0 and CLAUDE.md 2: what exists, not the shape. system_architecture.md: v2.5 of June 2026, stale.
Missing: one short product-shape doc (about 2 pages, owner-signed, with a D-number). It should state: Metorite is a company operating system that adapts to each company. Core blocks: identity and tenancy, one task store, records and custom objects, pages and Knowledge, connectors, Action Broker, Reach, shell manifest, MCP door. Adaption path: presets, then AI Studio apps, custom objects, grants (D12), connectors, harness (HX, CH). Rule: a vertical (CRM, booking, WhatsApp bots) is a package over core blocks, never new core. It would settle the open seams: customer-safe audience (item 8), catalog as price source (7), booking owner (4), Aroh (2). Home: project-docs/product_shape.md in INDEX CONTRACTS, linked from CLAUDE.md section 2.

## Not read in full
Only headers, greps and selected ranges of the specs. Not read in full: every spec over 1,000 lines, work_plan.md (6,794 lines; read section 1, 2.0, 2.1, D115, D144 header, D190, D199, D204 and grep hits), INDEX rows (cut at 400-700 characters). D152 text itself was not found, only references. D12 was read only through grep. Metorite: listed 34 projects (task counts only). I did NOT open the epic or slice tasks (2547, 2566, 2624 to 2791), so "slice only" rests on the repo having zero spec text, not on the board. Local copies of the specs are in the sp/ folder beside this file.
