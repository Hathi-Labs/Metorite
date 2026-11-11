# Feature-gap inventory: CommandCenter (upstream) vs Metorite (fork)

Read-only study. Metorite evidence is from `origin/main` (not the stale checkout). Upstream is the CommandCenter repo (FracktalWorks/CommandCenter).
Date of study: 2026-11-06.

## 0. Bottom line

1. **The owner's premise is only half right.** Notes, the meeting bot, live copilot, STT and WhatsApp-call capture were NOT dropped. The code ships in Metorite (`/notes` 27 files, `apps/services/meeting_bot`, `packages/acb_stt`, `gateway/routes/notes/*` 25 modules, `/whatsapp/calls`). The specs carried over. They are **PARKED**: board row **WS-PRE** ("Preview apps: Notes, Meetings, Dashboard", epic 494, M4, after Phase 2) and **WS-19** (parked by **D119.3**, 2026-10-27, brief-driven). They are `preview` in nav (Notes) or absent from nav (meeting bot has no pane of its own).
2. **What really is ABSENT** is: any user-facing speech input in chat (no dictation, no voice mode, no TTS), OKR/goal roll-up, marketing and support modules, draw.io code, status pages, sales BI, and a Slack channel adapter.
3. **The "planned" part is thin.** Four Metorite rows carry the whole deferred set (WS-PRE, WS-WH, WS-11 to STU-2, WS-22). WS-PRE's "next" items are E-28 and E-25 (email IDs), so Notes has no slice list of its own on the board.
4. **Upstream's PM/product-planning vision moved on, not away.** ClickUp, GTD and `skill-clickup-sync` were replaced by native Projects (WS-PT, WS-64). Plane/Paca research fed `project_management_app.md` §11.19.

Legend, Metorite state: LIVE = in `nav.ts` with `launch: "live"` · PREVIEW = pane in nav.ts `launch: "preview"` (absent from the sidebar) · BACKEND = code and routes, no nav entry · SPEC-ACTIVE / SPEC-DEFERRED = per INDEX · PARKED = by D-number · ABSENT = no code and no spec.

Live set today (launch_surface §2, 15 entries): Calendar, My Profile, My Email, My WhatsApp, Artifacts, Projects, CRM, People, Knowledge, Chat, Studio, Approvals, Organisation, Appearance, Integrations. Preview in nav.ts: My Tasks, Dashboard, Notes, Memories, the six Center landing pages, Agent Registry, Live Activity.

---

## 1. Matrix

### 1.1 Productivity

| # | Capability | Upstream state (evidence) | Metorite state today (evidence) | Owning spec + INDEX class | WS | Gap summary |
|---|---|---|---|---|---|---|
| 1 | AI Note Taker: record, transcript, notes, actions | built, slices 0-2 (`project-docs/specs/note_taker_app.md`, `workbench/control_plane/src/app/notes`) | PREVIEW: `/notes` `launch:"preview" // WS-19 incomplete` (`lib/nav.ts`); code intact, 27 files; gateway `routes/notes/*` | `note_taker_app.md` ACTIVE (INDEX row `note_taker_app + meeting_bot_platform_plan` = WS-19) | WS-PRE / WS-19 | Not a missing build. Parked to M4 by D119.3. Not in sidebar. No slice rows with task numbers. |
| 2 | Note templates, glossary, speaker rename, share links, Q&A on a meeting | built (`routes/notes/templates.py`, `glossary.py`, `share.py`, `qa.py`, `speaker_id.py`) | BACKEND (same files in Metorite) | `note_taker_app.md` | WS-PRE | Shipped, hidden. Needs a flip decision, not a build. |
| 3 | Meeting notes become tasks and a follow-up email (Meeting to Task capture) | built (`routes/notes/actions.py`; skill `skills/delivery/action_item_from_meeting`) | BACKEND (`routes/notes/actions.py`); skill dir exists in `skills/delivery` | `note_taker_app.md` | WS-PRE | Writes into the old task path. Re-point to `pm_tasks` before any flip (one task store, D52-D54). |
| 4 | Tasks / GTD personal app | built (`task_manager_app.md`, `gtd_*` tables, `skill-task-gtd`) | PREVIEW: `/tasks` redirects to Projects "My work" (OP-4, 2026-10-25); `gtd_*` dropped (migration 217) | `task_manager_app.md` ACTIVE (§13 wins), `my_tasks_cutover.md`, `one_projects_app.md` | WS-PT / WS-64 / WS-18 | Replaced, not lost. Weekly review and waiting-for (WS-18) are folded into WS-PT. |
| 5 | Calendar, timeboxing, focus blocks | built (`calendar_focus_os.md`, `calendar_timeboxing.md`; no `/calendar` route upstream, it was a view in `/tasks`) | LIVE `/calendar` (D54) | `calendar_focus_os.md` + `calendar_events_reminders.md` ACTIVE | WS-CAL | Ahead of upstream. Two-way external sync ships read-only first (D119.6). |
| 6 | Reminders and web push | planned only (`calendar_ux_review.md` block-reminders item) | BACKEND: PN-1, RM-1..RM-4 built, dark/on per flag (WS-66) | `calendar_events_reminders.md` §5 ACTIVE | WS-CAL / WS-66 | New in Metorite. |
| 7 | Focus / My Day feed | built in part (`calendar_focus_os.md`) | spec ACTIVE (`my_day_feed.md`) | `my_day_feed.md` ACTIVE | WS-58 / WS-CHAT | Extended. |
| 8 | Writing help (rewrite, tone, compose assist) | not present | ABSENT as code. Row WS-WH, epic 493, "next WA-1", M4. `agent_writing_voice.md` covers agent voice only | none yet (WS-WH has no owning spec) | WS-WH | New idea of the fork owner, still unspecced. |
| 9 | Personal dashboard ("your day across apps") | planned (`department_centers.md` Phase D, WS-15) | PREVIEW: `/dashboard` is "the COMPANY view today" (`nav.ts` note) | `department_centers.md` ACTIVE as design record, WS-15 PARKED (D49) | WS-PRE (WS-15 moved here) | Pane exists, held back. No personal-dashboard spec. |

### 1.2 Meetings and voice

| # | Capability | Upstream state | Metorite state today | Spec + INDEX class | WS | Gap summary |
|---|---|---|---|---|---|---|
| 10 | Meeting bot: join Meet / Teams / Zoom in-house (Recall.ai replacement) | built phase 1 (`meeting_bot_platform_plan.md`, `apps/services/meeting_bot`, `infra/docker-compose.yml: meeting-bot`) | BACKEND: `apps/services/meeting_bot` (11 files), compose service `meeting-bot`, `routes/notes/meeting_bot.py`. No pane | `meeting_bot_platform_plan.md` ACTIVE (row WS-19) | WS-PRE | Parked by D119.3. Per-participant capture and calendar dispatch (b, c in its §0) were never built. |
| 11 | Calendar-driven unattended bot dispatch | planned (`meeting_bot_platform_plan.md` §0 (c)) | BACKEND: `routes/notes/dispatch.py` exists. No calendar link in the Calendar spec | `meeting_bot_platform_plan.md` | WS-PRE | The Calendar app (WS-CAL) has no "send a bot to this event" slice. |
| 12 | Live Meeting Copilot (phases A-D: presence, console, passive prompts, business context, agenda) | built A-D, E planned (`live_meeting_copilot.md`; `routes/notes/copilot*.py`) | BACKEND: `routes/notes/copilot*.py`, `agenda_progress.py` | `live_meeting_copilot.md` **DEFERRED** ("no board row") | none | Only item with no board row at all. Phase E (speak into the call) never built. |
| 13 | Speech-to-text engine (`acb_stt`: Groq/OpenAI/Deepgram, `tier-stt`, BYOK) | built (`packages/acb_stt`) | BACKEND: `packages/acb_stt` (8 files). D59/D61 plan `transcribe` as a LiteLLM modality; `tier-stt` unpriced | `ai_metering_and_analytics.md`, `customer_console.md` (D59.1, D61.2) | WS-CM / WS-PRE | Engine present. Metering and pricing of STT is a WS-CM item. No product surface other than Notes. |
| 14 | Live captions (Deepgram streaming, browser-direct) | built (`routes/notes/live.py`) | BACKEND (same) | `note_taker_app.md` | WS-PRE | Same as 1. |
| 15 | Chat dictation / voice input / voice mode / text-to-speech | not built | **ABSENT.** No `SpeechRecognition` or recorder in the chat code. Only Notes and WhatsApp calls call `getUserMedia`. D59 reserves a `speak` modality only | none | none | True gap. Chat-first (WS-62) demands full parity, but no spec has voice. |
| 16 | WhatsApp calls: place and answer, record server-side | built, Surface C shipped (`whatsapp_calls_note_taker.md` §12) | BACKEND: `/whatsapp/calls` route, `whatsapp_bridge` (Go + whatsmeow) | `whatsapp_calls_note_taker.md` **DEFERRED** ("no board row") | none (WS-WA gated, D115) | Unofficial route. D122.2 bans linked-device reading ("no linked-device reader, because of ToS and ban risk"), so this stays frozen. Transcription of calls was never wired (§12.5). |
| 17 | Meeting management system (agenda, minutes, decisions, follow-up tracking) | partial: agenda + standing instructions in copilot; minutes/action items in notes | ABSENT as a product. Pieces: notes, copilot agenda, Calendar events | none | none | No spec treats "meeting" as a first-class object across Calendar, Notes and Projects. |

### 1.3 Communication

| # | Capability | Upstream | Metorite | Spec | WS | Gap |
|---|---|---|---|---|---|---|
| 18 | Email app (Gmail/Outlook/IMAP, AI triage, drafts) | built (`email_app_master_plan.md`, `apps/services/email_ingestion`) | LIVE "My Email" `/email` | `email_app_master_plan.md` ACTIVE | WS-EM | Ahead. Gmail in test mode (D122.4). |
| 19 | Shared team mailbox | not present | BACKEND/spec (`shared_mailbox.md`) | ACTIVE | WS-65 / WS-EM | New. |
| 20 | WhatsApp message manager | built | LIVE "My WhatsApp" (2026-10-08) | `whatsapp_message_manager.md` ACTIVE | WS-WA (gated) | Live, but remainder frozen by D115 apart from brief task 8. |
| 21 | WhatsApp as an assistant channel | planned | spec ACTIVE, parked (D115) | `whatsapp_assistant_channel.md` | WS-47 / WS-WA | Parked. |
| 22 | Slack / Teams channel adapter (hub-and-spoke) | planned only (`COMPETITIVE_COMPARISON.md` §4 P1.5) | ABSENT | none | none | Never specced in either repo. |
| 23 | Unified chat assistant (Mira), AG-UI, HITL cards | built (`chat_ux.md`, `generative_ui_2.md`) | LIVE `/chat`; far past upstream (WS-CHAT, WS-62, WS-CO, WS-67) | `one_assistant_context.md`, `chat_first_surface.md` ACTIVE | WS-CHAT | Ahead. `chat_ux.md` archived. |
| 24 | Proactive reach (assistant messages first) | not present | spec ACTIVE (`proactive_reach.md`), push on | ACTIVE | WS-59 | New. |

### 1.4 Work management

| # | Capability | Upstream | Metorite | Spec | WS | Gap |
|---|---|---|---|---|---|---|
| 25 | Projects / PM app | built (`project_management_app.md`, `/projects`) | LIVE `/projects` | `project_management_app.md`, `one_projects_app.md` ACTIVE | WS-PT | Ahead (cards, inbox, phone, import WS-41, reports, planning WS-PJP). |
| 26 | ClickUp sync | built (`apps/skills/skill-clickup-sync`, ClickUp webhooks) | RETIRED (CLAUDE.md: "ClickUp is gone") | n/a | n/a | Intentional. Registry still names `clickup` integration for `delivery`/`reconciler`. See orphans. |
| 27 | Project planning AI (critical path, buffers, stages) | not present | spec ACTIVE (`project_planning.md`), `ws-PJP` | ACTIVE | WS-PJP | New. |
| 28 | Project alignment (rules per project, deviation checks) | not present | spec ACTIVE | `project_alignment.md` | WS-ALN | New. |
| 29 | Task intake / triage inbox (Plane research §3.1) | research only | BUILT: D170 "the inbox holds captures" | `one_projects_app.md` §9b | WS-PT | Picked up. |
| 30 | Watchers, @mention discipline (Plane §3.2) | research only | partial: task references (`task_references.md`); no "watcher" primitive found | `project_management_app.md` §11.19 | WS-PT | Check §11.19 for the verdict. No watcher row on the board. |
| 31 | Custom fields, per-view ordering (Paca §2.3, §2.4) | research only | partial: `crm_platform.md` custom fields. Projects: unconfirmed | `project_management_app.md` §11.19 | WS-PT | Unclear. Worth a one-line check by the PM owner. |
| 32 | Automation engine, trigger/condition/action graphs (Paca §4) | research only | spec ACTIVE (`proactive_agent.md`, `core_brief.md` AUTO-*) | ACTIVE | WS-AUTO | Picked up as AUTO, but agent-first, not a user-defined rule builder. |
| 33 | Time tracking / timesheets | research (Plane) | mentioned only in `project_management_app.md`, `projects_reports.md` | ACTIVE | WS-PT | No slice found. |
| 34 | People Center: directory, org chart, skills, CV, absences | built (`people_center_app.md`) | LIVE `/people` | ACTIVE | WS-PP | Ahead. |
| 35 | HR planning: capacity, assignment AI, availability | built in part (`task_manager_hr_planning_and_memory.md`, `infra/seed/hr`) | REFERENCE (INDEX): "HR data layer WS-28 reads"; assignment AI lives in People | REFERENCE | WS-PP | Done enough. No payroll, leave approval, hiring pipeline anywhere. |
| 36 | OKRs / GOAL entity with roll-up | planned (`project_plan.md` L4-04) | **ABSENT** (the one `OKR` hit is in `ai_studio.md`, an example) | none | none | Never planned in Metorite. |
| 37 | Delivery monitoring (stale-task nudges, escalation) | built as `agent-delivery` + skills | skills only (`skills/delivery`, `skills/reconciler`); agent repo not in tree | none (proactive agent replaces it) | WS-AUTO | Superseded by Proactive agent. Skills reference ClickUp. |
| 38 | Approvals (Action Broker, HITL queue) | built | LIVE `/approvals` | ACTIVE | WS-TO / WS-1e | Ahead. |

### 1.5 Knowledge and memory

| # | Capability | Upstream | Metorite | Spec | WS | Gap |
|---|---|---|---|---|---|---|
| 39 | Knowledge Base (Obsidian-like, graph) | roadmap only (`future_modules_roadmap.md` §1) | LIVE `/knowledge` (2026-10-18) | `knowledge_base.md`, `knowledge_curation.md` ACTIVE | WS-KB | Roadmap item fully built. |
| 40 | Knowledge graph and retrieval | planned (Graphiti, `memory_architecture.md`) | spec ACTIVE (`knowledge_graph.md`, D128); Mem0 and Graphiti retire after KG-6 (D132) | ACTIVE | WS-KG | Re-planned. Neo4j still in compose. |
| 41 | Memory (facts, episodic) UI | built (`/memory`) | PREVIEW `/memory` ("operator-grade") | `memory_architecture.md` ACTIVE | WS-9 / WS-CHAT | Deliberately hidden. |
| 42 | LightRAG over SOPs (agent-strategy) | planned (L4-02) | one mention in `knowledge_curation.md` | none | none | Not carried. |
| 43 | Company context / personalization | not present | spec ACTIVE | `company_context.md`, `personalization.md` | WS-KG / WS-63 | New. |

### 1.6 Automation

| # | Capability | Upstream | Metorite | Spec | WS | Gap |
|---|---|---|---|---|---|---|
| 44 | Workflows (visual editor, Module Studio, copilot, triggers, approval node) | built slices 1-2 (`workflows_app.md`, `/workflows`) | no `/workflows` page in `origin/main` (components and lib only). Absorbed into AI Studio (`/studio`, LIVE, D179) | `workflows_app.md` ACTIVE, `ai_studio.md` ACTIVE | WS-11 to WS-STU (STU-2) | Moved. Slice 3 (gallery, fan-in, loops) parked after Phase 2. Check that the Studio library lists workflows. |
| 45 | App Builder (`/build/apps`) | built | BACKEND: two `/build/apps/[slug]` pages, not in nav. Studio is the surface | `ai_studio.md` | WS-STU | Same absorption. |
| 46 | Skills registry, per-agent toggles | built (`skills_registry.md`) | spec ACTIVE | ACTIVE | WS-23 to WS-STU | Moved. |
| 47 | Skill crystallization / Annealer / Curator | planned (L2-13, L4-08; COMPETITIVE P2.8) | ABSENT | none | none | Not carried. |
| 48 | Agent registry and satellite agent repos (dynamic git clone + hot load) | built (`agent_registry.json`, `agent_repo_compatibility.md`) | PREVIEW `/agents`; registry file still lists 9 | `agent_repo_compatibility.md` CONTRACT | WS-8 / WS-CHAT | See section 2. |
| 49 | Self-mutation sandbox (Copilot SDK) | built | BACKEND (compose `mutation`, `copilot-sandbox`) | `agent_sandbox.md` | WS-TO (SBX) | Re-scoped to sandbox hardening (D130). |
| 50 | Agent API-integration generator (`apis-config`) | built | BACKEND: `agent-apis-config`, `/apis` page, no nav | none (listed "Nice to have" upstream) | none | Orphan-ish. |
| 51 | External-agent MCP access | not present | LIVE, mcp.metorite.com | `external_agent_access.md` | WS-55 / WS-MCP | New. |
| 52 | Web research for agents | upstream has `web_search` and `fetch_page` tools | spec ACTIVE | `web_research.md` | WS-WEB | Extended. |
| 53 | Local Tier-1 model (vLLM/Qwen) | planned (L1-13) | ABSENT (AI tier routing uses hosted tiers) | `ai_tier_routing.md` | WS-45 | Dropped silently. |

### 1.7 Admin and platform

| # | Capability | Upstream | Metorite | Spec | WS | Gap |
|---|---|---|---|---|---|---|
| 54 | Department Centers (6) | built | PREVIEW landing pages. Withdrawn from the surface by D49. Code stays as scoping primitive | `department_centers.md` ACTIVE (design record) | WS-13..16 PARKED | Intentional. |
| 55 | Center dashboards, company rollup, weekly digests | planned (WS-15) | PARKED (D49). Row WS-15 moved to WS-PRE | `department_centers.md` | WS-PRE | Hidden. |
| 56 | AI budgets per Center | planned (WS-16) | PARKED (D49) | `department_centers.md` | WS-CM | Hidden. |
| 57 | Theming engine | built (Fluent / Material / Graphite) | RETIRED by the owner. Glass look is the one look (D143/D149) | `glass_look.md` | WS-UX | Intentional. |
| 58 | draw.io diagrams | spec only (`drawio_integration.md`, `drawio_diagram_svc_contract.md`); no code in either repo | PARKED by D25.7 (WS-22). The two code hits are strings in `executor.py` and `agent_md.py` | **DEFERRED** (INDEX) | WS-22, "no workstream" | Never built anywhere. Artifacts viewer covers files. |
| 59 | Subscription console, seats, billing | spec (`subscription_console.md`, `platform_control_plane.md`) | `apps/services/customer_console`, `workbench/operator_console`; spec renamed `customer_console.md` | ACTIVE | WS-CM / WS-50 | Ahead. `platform_control_plane` and `control_plane_infrastructure` have no file in Metorite. |
| 60 | Multi-tenancy (RLS), org access control, user mgmt | built in part | spec ACTIVE (largest block) | `saas_multitenancy.md` etc. | WS-TS | Ahead. |
| 61 | Observability (Live Activity) and Langfuse | built | PREVIEW `/observability` ("operator concern"); Langfuse in compose | `observability_e2.md` | WS-6 / WS-TO | Hidden by design. |
| 62 | Operator console | not in upstream (platform service only) | BACKEND/own app `workbench/operator_console` | `operator_console_money.md` | WS-50 | New. |
| 63 | Integrations console | built (`/integrations`) | LIVE | `integrations_console.md` | WS-54 | Ahead. |
| 64 | Onboarding, help, issue reporting | not present | specs ACTIVE | `onboarding.md`, `assistant_guide.md` | WS-OB / WS-IR | New. |

### 1.8 Other / business modules

| # | Capability | Upstream | Metorite | Spec | WS | Gap |
|---|---|---|---|---|---|---|
| 65 | CRM (native, Zoho mirror) | built | LIVE `/crm` | `crm_platform.md`, `crm_app.md` | WS-CRM | Ahead. |
| 66 | Zoho CRM sales agent (`agent-sales`) and sales BI | registry "live", repo external | agent not in tree. No BI spec | none | none | Sales BI (L4-05) never planned. |
| 67 | Billing / invoice agent (`billing`) | registry "live", repo external | same | `customer_console.md` is our own billing, not the Zoho agent | none | Ghost entry. |
| 68 | Odoo / ERP ingestor | planned L4-01 | Re-planned: Zoho Books first, then Tally Prime, then QuickBooks (D122.1), pending pilot answers | `core_brief.md` BR connectors | WS-BRIEF (epic 481) | Different ERP list. |
| 69 | Weekly digest (`agent-strategy`) | planned | brief tasks (BR-*) + My Day feed | `core_brief.md` PROPOSED | WS-BRIEF | Re-homed. |
| 70 | Marketing module (social, web pages, ad insights) | roadmap only (`future_modules_roadmap.md` §2) | ABSENT. `marketing_site.md` is Metorite's own website | none | none | Not carried as a row. Roadmap file is still ACTIVE in INDEX. |
| 71 | Customer Support and Success (tickets, status pages, bot) | roadmap only (§3) | ABSENT | none | none | Same. "status page" has 0 hits in Metorite specs. |
| 72 | GitHub integration | not present | spec ACTIVE | `github_integration.md` | WS-54 | New. |
| 73 | Mobile app / PWA | not present | `manifest.ts`, push; phone epic 1500 | `one_projects_app.md` §9b | WS-PT | New. |

---

## 2. Requested lists

### (1) Upstream satellite repos

Referenced by `agent_registry.json`, README and specs (counts of mentions): `FracktalWorks/agent-sales` (30), `agent-sales-assistant` (30), `agent-triage`, `agent-task-manager`, `agent-strategy`, `agent-reconciler`, `agent-delivery` (20 each), `agent-project-manager` (11), `agent-billing` (10), and skills `skill-zoho-ingest`, `skill-whatsapp-send`, `skill-meeting-transcribe`, `skill-graph-write`, `skill-gmail-capture`, `skill-clickup-sync`, `skill-action-broker` (10 each).

None of these satellite repos is a local clone. In-tree in both repos: `agent-task-manager`, `agent-crm`, `agent-email-assistant`, `agent-whatsapp-assistant`, `agent-orchestrator`, `agent-apis-config`, `agent-app-builder`. Metorite added `agent-projects`, `agent-studio-builder`, `agent-worker`.

Metorite plan for them: registry still lists `sales, delivery, triage, reconciler, billing, strategy` as "live", but no spec plans them. Their jobs are re-homed: delivery/reconciler/strategy become the Proactive agent (WS-AUTO) and Core brief (WS-BRIEF). `skill-meeting-transcribe` was meant to be the L3 meeting capture, replaced by `meeting_bot` + `acb_stt`.

The local clones at `c:/Users/VijayRaghavVarada/Documents/Github/agent-*` are the owner's personal DOE-framework agents, not the CommandCenter satellites:
- `agent-3dprinter-expert`: Klipper-based 3D printer debugging expert.
- `agent-easyEDA-assistant`: PCB-design co-pilot driving EasyEDA Pro over a WebSocket bridge.
- `agent-fan-designer`: automotive cooling/HVAC fan design agent workspace (Claude Code).
- `agent-project-manager`: HR project-management agent over ClickUp (DOE v2). Its plan contract was ported into Metorite (`routes/tasks/planning.py`) and its HR data seeded People (`infra/seed/hr`).
- `agent-startup-guru`: personal startup mentor (Vedic astrology plus ecosystem intelligence), built for the founder.
- `agent-website-manager`: WordPress build/manage agent via Hostinger MCP ("CommandCenter-Compatible").
- `business-plan-agent`: business plan + 5-year financials (Google Docs/Sheets).
- `indian-legal-compliance-agent`: HR, labour, company law, IP, contracts, tax compliance research and drafting.
- `coding-agent-setup`: portable kit that prepares a codebase for coding agents. `DOE Framework Agentic AI`: tutorial workspace. `Agent`: empty.

None of these is planned inside Metorite. `agent-website-manager`, `indian-legal-compliance-agent` and `business-plan-agent` look like candidates for the Studio/skills library, since WS-STU sells "skills, workflows and apps that a company builds".

### (2) Upstream code that ships in Metorite with no plan line (orphans)

1. **Registry ghosts**: `sales, delivery, triage, reconciler, billing, strategy` in `agent_registry.json` point to repos not in the tree. `delivery` and `reconciler` need `clickup`, which is gone.
2. **`skills/delivery`, `skills/reconciler`** (`stale_task_nudge`, `stale_task_escalation`, `project_status_summary`) were written for ClickUp. No plan line retargets them. `skills/sales`, `skills/triage` are tied to the Zoho/agent-sales ghosts.
3. **`/apis` page and `agent-apis-config`**: not in nav, no spec, no WS row.
4. **`/build/apps/*` pages**: not in nav. Studio replaces them. No retirement line.
5. **`routes/notes/copilot*.py`, `agenda_progress.py`** (Live Meeting Copilot): spec DEFERRED with "no board row". Code is 6+ modules.
6. **`/whatsapp/calls`, `whatsapp_bridge` call code**: shipped upstream. D122.2 forbids the linked-device approach it relies on. No retirement line.
7. **`routes/notes/*` and `apps/services/meeting_bot`**: planned only as "WS-PRE, M4, next E-28/E-25", so effectively unmanaged until M4. Risk: tests and migrations keep running for hidden code, and it still uses the old task path.
8. **`/inbox`**: redirects to `/agents`. Harmless.
9. **`/dashboard`**: pane shows the company view, no spec for what replaces it.
10. **Neo4j and Graphiti in `docker-compose.yml`**: D132 retires them after KG-6. No removal task is named.
11. **`apps/services/ingestion` + `reconciler`**: Zoho/ClickUp webhook receivers. Zoho remains for Fracktal only (`CRM_MIRROR_ORGS`). ClickUp receiver is dead.
12. **Local STT tier (`tier-stt`)**: seeded in production but unpriced (D59.1).

### (3) Capabilities upstream research recommended that no Metorite spec picked up

Upstream-only specs are all present in Metorite under `project-docs/research/` or `specs/archive/`, except `control_plane_infrastructure`, `platform_control_plane`, `single_agent_chat_bug_audit_2026-07` and `task_manager_harness_2026-07`. The last two have no file at all. The first two were superseded by `customer_console*.md`.

Not picked up:
- **Note taker research**: ranked donor shortlist (Meetily etc.), browser-capture facts, speaker diarization SOTA, ASR choices (`note_taker_research_2026-07.md` §3-§5). There is no Metorite work to evaluate diarization or on-device ASR. The research file is REFERENCE only.
- **Paca**: one actor table for humans and agents, fractional per-view ordering, activity spine, MCP tool-design lessons, plugin system, realtime event layer. Only the MCP lessons were used (WS-55).
- **Plane**: watchers and mention discipline (§3.2), auto-archive/auto-close sweeper (§3.3). Archive exists in UX (`one_projects_app.md` §9c). Watchers: no board row.
- **Competitive hardening**: Slack channel adapter, `MEMORY.md`-style auditable memory layer, agentskills.io SKILL.md convention, Curator that proposes skills from traces, typed-message sub-agent passing and Kanban dispatch. Partly in `agent_coordination.md` (WS-MCP). Rest unplanned.
- **Future modules roadmap**: Marketing, Customer Support and Success (tickets, status pages, external chatbot). The file is still INDEXed ACTIVE, so it looks planned. It has no rows.
- **Project plan L4**: GOAL entity and roll-up, sales BI, RouteLLM training pass, smart delegation, anti-hallucination guardrail target (<1%).
- **Task-manager harness** Tier 2 (structural) items: lost with the deleted spec.

## 3. Ranking: what matters most for an "AI company operating system"

Gaps ranked (ABSENT first, then PREVIEW/DEFERRED):

1. **Meetings as a system (rows 1, 10-12, 17)** PREVIEW/PARKED. Highest value: it is where a company's decisions and commitments are made. The code is largely built.
2. **Voice in chat and dictation (row 15)** ABSENT. Cheap given `acb_stt`, and chat is the primary interface.
3. **Goals / OKR roll-up (row 36)** ABSENT. The OS has projects, tasks and alignment, but no layer above them.
4. **Company dashboards and digests (rows 9, 55, 69)** PREVIEW/PARKED. Leadership view over all apps.
5. **Support and Marketing modules (rows 70-71)** ABSENT. Revenue-adjacent, but large.
6. **Writing help (row 8)** unspecced.
7. **Slack/Teams channel adapter (row 22)** ABSENT.
8. **WhatsApp calls (row 16), draw.io (row 58)** deferred or blocked by policy.

Sources to re-verify before acting: `project-docs/INDEX.md`, `project-docs/work_plan.md` §2.1 (rows WS-PRE, WS-WH), D119.3, `launch_surface.md` §2, `src/lib/nav.ts`.
