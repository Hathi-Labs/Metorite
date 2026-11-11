# Metorite building blocks: the seam inventory and the recommendations

**Status.** Read-only review, 2026-11-10. Nothing in the repo or in Metorite changed.

**Anchor.** `origin/main` at `4b6a8cf81`. The code claims re-read that commit.

**Legend.** **V** = verified on `origin/main`, with a path. **P** = planned, with a task number. "Agrees" means that the item extends the existing seam and adds no second one.

---

## 0. The answer

**Five blocks have one clean seam, and the new plans extend it.** Each needs at most a small fix in a task text:

- (b) the one task store and its lenses (`pm_tasks`, D52 to D54)
- (e) knowledge retrieval (`gateway/retrieval/`, D105), with four fixes in KS slices
- (j) AI model calls (the Router, `acb_llm.decide`), with one budget fix
- (k) files (`artifacts`, D162), if KS-4 uses it
- (n) payments (STU-O10, task 1515), if quote and booking links use STU-O4

**Four blocks have a built seam, and a new plan adds a second one.** They are (g) approvals, (i) the manifest envelope, (m) the feed and delivery, and (o) setup coaches.

**Six blocks have no single seam yet, and the plans multiply them.** They are (a) records, (c) principals and audience, (d) bots, (f) automation details, (h) connectors and (l) change records.

**The duplicates that the plans of 2026-11-10 made:**

| Block | The duplicate | Tasks |
|---|---|---|
| (a) records and objects | Three typed-field systems were added to five stores. Two association tables were planned on one day. Product and price have four owners | 2734, 2793, 2727, 2736 against Studio C1 (`studio_records`) and PKG-13 |
| (c) principals | The bot has its own "service principal" against D140.2 and Studio B6 (`run_as` member) | 2692 against 1413 |
| (c) audience | Three audience words: the publish audience, the Studio surface audience and the channel audience check | 2725, 2698, 1507, 2442 |
| (d) channels and bots | Two bot runtimes over an org number. Two "Mira in a WhatsApp group" slices | 2652, 2653, 2655 against 1511, 1512, 1513, 2422. 2659 against 2445 |
| (f), (m) proactive | A second nudge engine with its own states, cap and quiet hours | 2763, 2764, 2779 against My Day (MD-2), Reach (§6), AUTO-5 (1389) |
| (g) approvals | An Approvals chip that AP-Q11 already replaced | 2622 against 2303 |
| (h) connectors and import | Four import paths, two connector families, four "health" notions | 2735, 2807, 1419, Projects importer. 2706 against 1144 |
| (o) coaches | A second setup coach and score, a second tour and how-do-I | 2765, 2773, 2766 against 946, 951, 952, 949, assistant guide |

**Three plans break a decision that already binds.** These need no owner call. The task text must change.

1. BK-3 (2679) builds its own public seam. **D174.1** says one public door (STU-O1, 1506).
2. KS-5 (2719), KS-6 (2720), CX-24 (2782) and CX-25 (2787) add typed MCP tools. **D136.2** says four fixed tools serve every app.
3. CH-7 (2779) adds a "Needs your attention" lane. **AP-Q11** (task 2300) says one "Needs you".

**Five decisions need the owner** (§5). They are the bot principal, the object model, the owner of price, the bot runtime and the MCP surface.

---

## 1. The rule that this review applies

CLAUDE.md §4 names the seams: one DB engine, one entitlement intersect, one subject-grammar validator, one task store, one Center registry and one status colour vocabulary. "A second implementation of an existing seam is a defect."

D147 (`work_plan.md` §3) is the owner's goal in decision form. `package_host.md` §3.1 holds its rule: "One manifest, one schema, one validator."

The core seams that the review checked on `main`:

| Seam | Where (V) |
|---|---|
| DB engine and tenant session | `packages/acb_common/acb_common/db.py` (`get_db` line 266, `tenant_session` line 510) |
| Access intersect | `packages/acb_auth/acb_auth/permissions.py` (`EffectiveAccess.intersect` line 456) |
| Subject grammar | `permissions.py` (`normalize_grant_subject` line 291). Fence `tests/unit/test_grant_subject_grammar.py` |
| Task store | `pm_tasks`, `pm_task_personal`. Fence `tests/unit/test_no_gtd_table_names.py` |
| Center registry | `workbench/control_plane/src/lib/centers.ts` |
| Status colour | `workbench/control_plane/src/lib/statusAccent.ts` (`statusAccent` line 383) |

---

## 2. The inventory, block by block

### (a) Records and objects

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| `pm_custom_fields` | V `infra/postgres/155_projects_custom_fields.sql` | Admin fields on tasks. Task types in `projects_settings.md` PS-2 | Built |
| `crm_field_defs`, `crm_module_records`, `crm_field_sections` | V `244_crm_mirror_columns.sql`, `251_crm_module_records.sql` | CRM fields and the generic record store of custom modules. Field studio CRM-C4, dark | Built |
| Studio typed object store | V `323_studio_objects.sql`, `gateway/studio/objects_store.py` | `studio_objects`, `studio_object_fields`, `studio_records`, rules, state machines, formulas (D146) | Built |
| One value check | V `packages/acb_common/acb_common/studio_fields.py` | The rules of a value for A2, the object store and PKG-13 | Built, shared |
| Package extension fields | V `366_package_field_value.sql`, `gateway/package_host/field_values.py` | Fields that a package adds to `task`, `crm.lead`, `crm.contact`, `crm.company`, `crm.deal` | Built |
| Entity registry and page types | V `kb_entities` on main. `knowledge_curation.md` §6.1 | `product` is owned by **Knowledge** ("no table exists") | Spec |
| CX-1 custom objects | P 2734 | Typed fields, **associations with labels**, tags, lists, segments. Field studio to general use | ✖ third object system |
| CM-2 Shape model | P 2793 | `crm_shapes`, typed field definitions, **an association table with labels and cardinality** | ✖ second association table |
| KS-13 typed entity pages | P 2727 | Product, Service, Person, Price list page types. **Recommends the Knowledge price list as the source** | ✖ product owner |
| CX-3a product catalog | P 2736 | "The one source of price". Knowledge reads by pointer | ✖ product owner |
| Studio templates K3, K4 | P (ai_studio §3.7) | An inventory `item` object with product lines | ✖ product owner |

**Verdict.** Not one seam. Five stores are built. `package_host.md` line 424 keeps three of them on purpose ("Merging them is a non-goal").

**Duplicates.** CX-1 and CM-2 each plan an association table. KS-13, CX-3a, K3 and `knowledge_curation.md` §6.1 each claim "product".

**Risk.** A company that adds a "Site" object in CRM and a "Site" object in Studio gets two records with two rule engines. Field types drift between `crm_field_defs` and `studio_fields.py`. A bot quotes a price from the wrong table.

**Recommendation.**

1. **One field vocabulary and one value check now.** CX-1, CM-2 and KS-13 call `studio_fields.py`. Owner: `ai_studio.md` §3.1.
2. **One association table.** CX-1 builds it. CM-2 maps onto it and drops its own. Owner: CX-0 contract.
3. **Owner decision D205** (§5): a new custom object is a Studio object owned by the system package `crm`.
4. **Owner decision D207** (§5): the CRM product catalog (CX-3a) owns product and price. KS-13 drops its price table.

### (b) The one task store and its lenses

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| `pm_tasks`, `pm_task_personal` (D52 to D54) | V, fence `test_no_gtd_table_names.py` | Projects, Tasks and Calendar are lenses | Built |
| `calendar_entries`, `reminders` | V `331_calendar_entries.sql`, `307_reminders.sql` | Time that is not a to-do (D138, D114) | Built, separate by decision |
| `pm_decisions` | V `332_pm_decisions.sql`, `359_pm_decisions_person_ask.sql` | Decisions and person asks (AP-8) | Built |
| CX-4 activities | P 2739 | CRM activities move into the task store and Calendar | ✔ |
| CX-22 `create_followup_task` | P 2780 | A follow-up in the shared task store | ✔ |
| WB-5 handover | P 2656 | "Make a task for each handover" | ✔ |
| LL-5 lessons | P 2630 | A lesson is a task | ✔ |
| STU E4 human step | P (ai_studio §3.3) | A workflow step makes a `pm_tasks` row | ✔ |
| KS-10 ticket capture | P 2724 | A ticket links to an article. The ticket store is not named | ⚠ open |
| BK-4 booking record | P 2680 | A booking, with an event on the host calendar | ⚠ the event must be `calendar_entries` |

**Verdict.** One seam, and the new plans agree.

**Risk.** `crm_activities` (V `144_crm.sql`) still holds type `task`. CX-4 must move it by expand and contract (R6). A future ticket app could add a ticket table.

**Recommendation.** A ticket is a task with a task type. BK-4 writes its event to `calendar_entries`. Owner: `project_management_app.md` §12.

### (c) Identity, principals and grants

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| D12 two axes, `group:<slug>` | V `permissions.py`, `tenancy_and_visibility.md` §3 | Tenancy and visibility, no third axis | Built |
| D140.2 | V `work_plan.md` line 4863 | "The member is the only authority". `agent_coordination.md` line 62: "An agent gets no grants of its own" | Decision |
| MCP scopes per app | V `external_agent_access.md` §19.6 | `<app>:read`, `<app>:write`, the admin switch | Built for Knowledge |
| Studio B6 `run_as` | P 1413 | An install runs as one named member, cut to the package `uses`. Writes carry `system:package:<id>` | ✔ with D140.2 |
| Studio O2 surfaces | P 1507 | A guest acts as the install's service identity (B6). Audience `public`, `link`, `kiosk` | ✔ with B6 |
| WB-25 bot principal | P 2692 | "A service principal. It is never a member." Its own `group:<slug>` grants | ✖ D140.2, ✖ B6 |
| KS-11 publish audience | P 2725 | `internal`, `customer-safe`, `published` on a page. Owner gate KS-Q1 | ⚠ needs a D |
| Task 2698 | P 2698 | The customer-safe flag, the WB hand-off | ✖ duplicate of KS-11 (KS-11 says so) |
| CH-TEAM-1 audience check | P 2442 | A post shares a record only when every person in the channel can open it | ✔, a third audience idea |
| CX-11 field-level security, API keys | P 2753 | Field security, roles, teams, API keys | ✖ STU-C9 (1417), ⚠ API keys are a new credential kind |
| STU-C9 field sensitivity | P 1417 | A restricted field is encrypted, audited, kept from models | ✔ |
| CRM scopes (D165) | P 1434 | Mine, My team, Everyone | ✔ "Teams are groups" (D204.8) |

**Verdict.** One grant grammar. Two models for a non-human actor. Three audience words.

**Risk.** HIGH. A bot with its own grants breaks the one subject grammar (D140.2). A second field-protection list lets a hidden CRM field reach a model through a Studio relation.

**Recommendation.**

1. **Owner decision D206** (§5): a bot is an install. It acts as a `run_as` member, cut to its allow list (B6).
2. **One audience vocabulary** for pages, surfaces and bot reads: `internal`, `customer-safe`, `public`. KS-11 owns it. Task 2698 closes into KS-11.
3. **One field-protection mechanism.** CX-11 builds on STU-C9 (`restricted`). Owner: `ai_studio.md` §19.7.
4. **API keys wait.** CX-11 uses the MCP OAuth connection (D97) and the public door (O1). A key kind needs its own decision.

### (d) Channels and conversations

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| WhatsApp transport | V `routes/whatsapp/transport/webhook.py`, `send.py`, `templates.py`, `calls.py`, `wa_accounts` (`102_whatsapp.sql`) | The shared transport (WS-20). WA-C1 binds the tenant | Built |
| Member channel (WAC) | V `routes/whatsapp_channel/bot_run.py` | Mira on the member's own WhatsApp | Built |
| Email transport | V `routes/email/transport/*` | Mailboxes | Built |
| Team channels CH-TEAM | P 2442 to 2447 | A team channel record, the audience check, `channel.post`, Mira in a group, Slack later | Spec |
| Studio O5, O6, O7, O8, O18 | P 1510, 1511, 1512, 1513, 2422 | One contact store. A package claims an org number (`channels` point). `channel.message` on the bus. A conversation run on the workflow engine. Auto-reply rules | Spec, D174 |
| WB-1 channel driver | P 2652 | "Define the channel-driver interface. Build the Cloud API driver." Its own event table | ✖ the WS-20 transport, ✖ O7 |
| WB-2 bot registry | P 2653 | A bot record with a number binding, tools, audience, hours | ✖ O6 `channels` point |
| WB-4 runtime | P 2655 | Event, bot, Router, reply | ✖ O7, O8 conversation runs |
| WB-8 team bot in groups | P 2659 | Metorite-made group, @mention | ✖ CH-TEAM-4 (2445) |
| WB-32 staff console | P 2700 | List, assign, SLA, bot trace | ✔ the WS-20 inbox is on hold |
| CX-5b, CX-5c | P 2741, 2742 | WhatsApp and calls on the CRM record | ⚠ calls must use the built WhatsApp calls and `tier-stt` |

**Verdict.** Two runtimes and two group designs for one channel.

**Risk.** Two webhooks bind the tenant in two ways. A bot answers in a group outside the audience check. Forty-four WB slices build beside a Studio group that D174 already accepted.

**Recommendation.**

1. **One channel adapter per provider.** Inbound webhook, dedupe, tenant binding, media fetch and send stay in the WS-20 transport. Owner: `whatsapp_message_manager.md`. WB-1 extends `webhook.py`.
2. **Owner decision D208** (§5): a WhatsApp bot is a Studio package. It claims a number (O6) and runs as a conversation run (O7, O8). WB owns the product layer: templates, the builder UX, the staff console and governance.
3. **WB-8 merges into CH-TEAM-4** (2445). WB-9 (forward path) stays.

### (e) Knowledge and retrieval

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| The retrieval seam (D105) | V `gateway/retrieval/company.py`, `sources.py`, `acb_skills/company_search_tools.py` line 297 | Federated search over each app's own read | Built |
| Page visibility | V `routes/knowledge/core.py` `visible_pages_clause` line 240 | Space grants in the one grammar (D-KB-3) | Built |
| One index | V `273_kb_chunks.sql`, `gateway/kb_index/*` | Lexical and vector in one table (D-KB-2) | Built |
| Proposals, gaps, verification | V `kb_page_proposals` (292), `kb_gaps`, `303_kb_page_verification.sql` | An agent never publishes (D-KB-4) | Built |
| KS-1 consumer registry | P 2715 | Each app declares what it reads. "A second read path is a defect" | ✔ if it extends `sources.py` |
| KS-4 page files | P 2718 | "Add `kb_page_files`" | ⚠ `knowledge_base.md` §4.3 says the file is an `artifacts` row |
| KS-7 review states | P 2721 | Draft, in review, approved, rejected, and a review queue in My Day | ⚠ overlaps `kb_pages.state` and proposals |
| KS-9 verification | P 2723 | Owner, verifier, review interval | ⚠ overlaps migration 303 and `review_at` |
| KS-12 retire `email_knowledge` | P 2726 | Email reads Knowledge | ✔ removes a duplicate (V `26_email_knowledge_style.sql`) |
| KS-14 gaps | P 2728 | Which questions find nothing | ⚠ `kb_gaps` exists |
| WB-35 gap queue | P 2703 | Questions the bot could not answer | ✖ `kb_gaps`, KS-14 |
| KS-0 new spec | P 2714 | `specs/knowledge_shared.md` | ✖ a fifth Knowledge spec |

**Verdict.** One seam, and KS states the right rule. Four slices restate built tables.

**Recommendation.** KS-1 adds a `consumes` point to the package manifest and reads through `sources.py`. KS-4 registers each file through `acb_memory/artifacts.py`. WB-35 and KS-14 write `kb_gaps`. KS-0 becomes a section of `knowledge_base.md`. Owner: `knowledge_base.md` §20.

### (f) Workflows and automation

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| The engine (D6) | V `routes/workflows/scheduler.py`, `triggers.py`, `engine/*`, `unattended.py` | Triggers, the one scheduler, approval nodes | Built |
| The job queue and the capped headless turn | V `gateway/jobs/queue.py`, `jobs/unattended.py` (`run_guarded` line 369), `313_unattended_ledger.sql` | Unattended runs | Built |
| `proactive_agent.md` | Spec | "No second scheduler". "A standing watch is a template". "Reach and My Day are the only mouth" | Decision D161 |
| CX-7 CRM automation | P 2744 | Rules on "the one workflow seam", a cap, dry run, audit, kill switch | ✔ |
| HX-1 nudge engine | P 2763 | "One rule registry", evaluated by the AUTO scheduler | ⚠ the rule is an AUTO-5 watch template (1389) |
| WB-4 bot runtime | P 2655 | A runtime outside the engine | ✖ (d) |
| CX-17 delivery connection | P 2783 | Won deal to a project, with a field mapping and assignee rules | ⚠ a workflow template, not a second mapper |

**Verdict.** One engine. The new plans agree in words. HX-1, WB-4 and CX-17 drift in detail.

**Recommendation.** A nudge rule, a CRM rule and the won-deal hand-off are workflow templates with triggers. Owner: `workflows_app.md` (engine) and `proactive_agent.md` (unattended, D131).

### (g) Proposals, approvals and review queues

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| The waiting seam (AP-1a) | V `routes/shell/waiting.py`, fence `test_waiting_seam_r8.py` | One read over held writes, chat asks, app requests, decisions | Built, dark |
| Held writes | V `66_pending_actions.sql`, `action_broker/broker.py` `submit` line 613 | Action Broker | Built |
| App requests | V `routes/agent_requests.py`, `external_agent_access.md` §19.5 | A door write waits for the member | Built |
| AP-Q11 | V `waiting_on_me.md` §5.8 | One "Needs you". AP-17 (2302), AP-18 (2303) | Decision |
| Trust ladders | V D131 (automations), D164 (CRM set-up) | Two ladders, by decision | Decision |
| CX-10a capture queue | P 2749 | "ONE review queue" for CRM proposals | ⚠ an app proposal store (AD-8) is fine. It must be a waiting kind |
| CX-22 MCP writes | P 2780 | Every write lands in CX-10a. Auto-apply for a named class | ⚠ a third ladder |
| KS-7 review queue | P 2721 | A Knowledge review view and "a place in My Day" | ⚠ must be a waiting kind |
| WB-7 bot drafts | P 2658 | A bot write is a draft that a member approves | ✔ |
| HX nudges, HX-3 diff | P 2763, 2765 | Proposals | ✔ |
| Task 2622 | P 2622 | An "Approvals" chip in Needs attention | ✖ AP-18 (2303) replaces Needs attention |

**Verdict.** One read seam exists. Each new queue must register a provider in it.

**Recommendation.** Close 2622 into AP-18. CX-10a, KS-7 and the WB queues each add one kind to `routes/shell/waiting.py`. CX-22 auto-apply uses the D131 ladder, not a new one. Owner: `waiting_on_me.md`.

### (h) Connectors and integration connections

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| The registry (D96) | V `245_integration_connections.sql`, `gateway/connections/store.py`, `connections/mcp.py` | One row shape. The read model unions `email_accounts` and `wa_accounts` | Built |
| The credential rule | V `integrations_console.md` §3.3, fence `test_integration_env_scoping.py` | Fernet, never in the environment | Built |
| CRM connector seam (D95.4) | V `gateway/crm_sources/base.py` (`CrmSource` line 204) | One protocol, Zoho first | Built |
| Mapping plans | V `277_crm_mapping_plans.sql`, `evals/crm_mapping/` | The CRM mapping store | Built |
| Projects importer (D80) | V `routes/projects/importer/plan.py` | File import for Projects | Built |
| Freshness contract | P `company_context.md` §6 (its CX-5) | `last_new_at`, `connector_quiet` card | Spec |
| CM-1 neutral interface | P 2792 | Six methods over `CrmSource` | ✔ D95.4 |
| CM-3 mapping planner | P 2794 | "There is ONE mapping engine" (with CX-2) | ✔ inside CRM |
| CX-2 import | P 2735 | CSV and XLSX import with AI mapping | ✔ with CM-3 |
| CM-16 spreadsheet source | P 2807 | Any CSV is a connector | ✖ CX-2 is the same path |
| STU-C11 | P 1419 | A CSV into a Studio object | ✖ a third CSV path |
| WB-38 integration surface | P 2706 | Custom HTTP tools, connectors for ERP, CRM, HMS | ✖ STU-I (1144) and D96.4 (an MCP server is a connection) |
| Health | P 2798 (CM-7), 2673 (WB-22), 2802 (CM-11) | Sync health, number health, a health page | ✖ four health notions |

**Verdict.** One registry. Two connector families (CRM sources, app connections). Four import paths. Four health notions.

**Recommendation.** One pipeline serves every file or CRM source: Shape, mapping plan, dry run, approve, `import_run_id`, Undo. CM-0 owns its contract. CM-16 merges into CX-2, and STU-C11 calls the same planner.

WB-38 builds on STU-I1 (1197). Health is one set of columns on the read model. `integrations_console.md` owns it, with the freshness rule of `company_context.md` §6.

### (i) The app manifest family

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| Package manifest envelope | V `acb_common/studio_manifest.py`, fence `test_studio_manifest.py`. `package_host.md` §3.1 | "One manifest, one schema, one validator. Do not write a second model" | Built |
| Shell contributions (D89) | V `app_manifests/shell/*.json`, `shell_manifest.py`, fence `test_shell_manifests.py` | nav, jobs, search, needs, cards, agent | Built |
| Door manifests (D136) | V `app_manifests/base.py` (`door_ops` line 379), `knowledge.py`, `crm.py` | Classes A, B, C, X. One allowlist for Mira and the MCP | Built |
| Projects route manifest | V `skill_projects/manifest.py` | The Projects door (typed tools) | Built |
| Guides | V `app_manifests/guide_manifest.py`, `guides/*.json` | The how-to corpus | Built |
| Tool packs, chat parity | V `acb_skills/tool_packs.py`, `acb_skills/chat_parity.py` | Skills per turn, parity inventory | Built |
| HX-0 harness manifest | P 2762 | Six parts: setup, usage, nudges, insights, prompts, evals | ✖ a second manifest |
| KS-1 consumer declaration | P 2715 | "Each app declares in its manifest what it reads" | ⚠ which manifest is not named |
| WB-7 bot skill manifest | P 2658 | Typed tools with scopes and `needs_approval` | ✖ the action catalog already holds classes |
| CX-20 CRM door ops | P 2776 | Door ops on `crm.py` | ✔ |

**Verdict.** The envelope exists and has a fence. Three plans propose a manifest of their own.

**Recommendation.** HX-0, KS-1 and WB-7 add contribution points to `package_host.md` §4: `nudges`, `insights`, `consumes`, `setup`, `prompts`. A bot's tools are a subset of catalog actions (B1). `needs_approval` is class B or C. Owner: `package_host.md`.

### (j) AI model calls

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| Router, tiers | V `customer_console.md` CP-11, `ai_tier_routing.md` (D90) | Every AI call goes through the Router | Built |
| `decide` facade | V `packages/acb_llm/acb_llm/decide.py`, `gateway/decide_features.py` | One tenant facade. A second client is a defect | Built |
| Credit caps | V AUTO-8 headless cap (done). P STU J3, WB-21 (2672), WB-40 (2708), AUTO-8b-2 (2230) | Spend caps | ⚠ several caps |
| LL, KS-4, WB-4, VOX | P | "Through the router", "decide tier" | ✔ |

**Verdict.** One seam. The caps risk becoming four.

**Recommendation.** One budget key per usage name in the one meter (`usage_event`). Owner: `customer_console.md` §6A and `ai_metering_and_analytics.md`.

### (k) Files and media

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| One library (D162) | V `268_artifacts.sql`, `acb_memory/artifacts.py` | "No second file system exists" | Built |
| App joins | V `150_projects_attachments.sql`, `17_email_accounts.sql` (`email_attachments`), `52_gtd_attachments.sql`, `255_pm_task_refs.sql` | Older stores and joins | Built |
| `kb_page_files` | Spec `knowledge_base.md` §4.3 | A join over `artifacts` | Spec |
| KS-4 | P 2718 | Files, video, PDF, thumbnails, transcripts | ⚠ does not name `artifacts` |
| RC-4, WB-1 media, CX-5c recordings, STU D24 | P 2646, 2652, 2742, 2368 | Inline images, WhatsApp media, call recordings, photo capture | ⚠ the store is not named |

**Recommendation.** Every new stored file is an `artifacts` row. An app table holds the join only. Owner: `artifact_management.md` (D162).

### (l) Events, timeline and audit

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| CRM change history (D164.5) | V `341_crm_change_log.sql`, `routes/crm/change_log.py` (`log_change` line 303). Fence `test_crm_change_history.py` | Who, when, through what, before, after, Undo | Built |
| Other logs | V `audit_event` (`01_schema.sql`), `kb_page_versions` (272), `pm_activities`, `app_audit` (114), `pm_agent_run_activities` | Per-app history | Built |
| `meta.via` (D-PM-36) | Decision | A chat write says where it came from | Built |
| KS-6 provenance | P 2720 | Actor, on-behalf-of, client, model, run id, source refs | ⚠ a Knowledge-only shape |
| WB-28, WB-25 | P 2695, 2692 | Bot change log, versioned instructions | ⚠ a third shape |
| CX-2 recycle bin | P 2735 | Bulk delete lands in a bin | ⚠ next to archive (ARC) and artifact trash |

**Verdict.** No shared change-record shape. Each app grows its own.

**Recommendation.** One change-record shape (not one table): `actor`, `on_behalf_of`, `via`, `client`, `run_id`, `model`, `sources`, `before`, `after`, `undo_of`. `crm_conversational.md` §4 owns it until a cross-app owner exists, as `org_facts` did (`work_plan.md` §4). KS-6 and WB-28 adopt it.

### (m) Notifications and surfaces

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| Reach (D103) | V `gateway/reach/*`, `266_reach_outbox.sql` | The one outbound seam. Daily cap, quiet hours, batch window | Built |
| My Day (D100) | V `routes/shell/feed.py`, `needs.py`, `258_my_day_card_state.sql`. Fence `test_shell_feed.py::TestOneRegistry` | One registry. A card is seen, later, acted, snoozed, dismissed | Built |
| The brief | V `gateway/brief/*` | 07:30 and 18:30 | Built, dark |
| HX-1 | P 2763 | States new, seen, snoozed, done, dismissed. Per-member cap. Quiet hours | ✖ My Day §6 and Reach §6 |
| HX-2 | P 2764 | Nudge settings: thresholds, quiet hours, channels | ✖ Reach preferences |
| CH-7 | P 2779 | A "Needs your attention" lane in CRM and My Day | ✖ AP-Q11 |
| Digests | P 2772 (CH-2), 2444 (CH-TEAM-3), 2695 (WB-28), 2635 (LL-9) | Four weekly digests | ⚠ each must be a card kind and a Reach class |

**Recommendation.** A nudge is a My Day card kind from a registered provider. Reach owns the cap, quiet hours and channels. "Needs you" owns the list. HX-1 builds only the rule point and the batch key. Owner: `my_day_feed.md` and `proactive_reach.md`.

### (n) Payments

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| STU-O10 | P 1515, D174.3 | The org's own Razorpay as an `integration_connections` row. `payments.create_link` (B), `payments.get` (A), `payments.refund` (C). A signed webhook | The one door |
| BK-6, CX-3c | P 2682, 2738 | Both are blocked by 1515 (links checked) | ✔ |
| STU-O4 signed links | P 1509 | View, pay or cancel one record from a link | The one link seam |
| CX-3b accept link, BK reschedule and cancel | P 2737, 2680 | Their own links | ⚠ use O4 |

**Verdict.** One seam. Metorite's own billing stays in the Customer Console plane, and O10 says so.

### (o) Setup and onboarding coaches

| Place | State | What it defines | Agrees? |
|---|---|---|---|
| D196 | V `work_plan.md` line 5757 | "One seam": `setup_status` and the manifest `setup` field. One budget (D-PZ-13) | Decision |
| `setup_status` | **Not built** (V: no definition on main, `personalization.md` §18.3 agrees) | OB-1a (1341) builds it | Spec |
| SH epic | P 940: SH-1 946, SH-4 949, SH-6 951, SH-7 952, SH-9 2317 | Checks, score, copilot | Spec |
| Guide corpus | V `app_manifests/guide_manifest.py`, `guides/*.json`. `assistant_guide.md` (D167) | Tours and "how do I" | Built in part |
| HX-3 | P 2765 | Setup checks per app, configure-for-me with a diff, a setup score | ✖ SH-1, SH-6, SH-7 |
| CH-3 | P 2773 | CRM setup coach and score | ✖ SH-4 (949) |
| HX-4 | P 2766 | Tips, how-do-I chat, role tours | ✖ the guide (GD slices) |
| CX-18, WB-31, CM-11 | P 2784, 2699, 2802 | Per-app setup assistants and wizards | ⚠ each needs setup items |
| Guides | P 2665, 2805, 2730, 2639, 2760 | Five new guides | ⚠ each is a corpus entry |

**Recommendation.** HX-3 merges into SH-1 and SH-7. CH-3 becomes the CRM content of SH-4. HX-4 merges into the GD slices.

Each wizard declares setup items. Each guide is a file in `guides/`. Owner: `personalization.md` §18.

---

## 3. Cross-cutting findings

1. **Six new specs in one day.** KS-0 (2714), CX-0 (2733), WB-0 (2651), HX-0 (2762), CM-0 (2791), BK-0 (2676). Knowledge already has four specs and CRM has four. Recommendation: KS-0, CX-0, HX-0 and CM-0 become sections of the owning spec. WB-0 and BK-0 may be new product specs. The transport, the runtime and the public door stay with their owners.
2. **Two id collisions.** `company_context.md` uses CX-1 to CX-7, and its CX-1 is built. WS-CX uses CX-0 to CX-31. `whatsapp_assistant_channel.md` uses CH-TEAM-1 to 6, and WS-CH uses CH-0 to CH-7. Rename the WS-CX prefix before a spec cites it.
3. **Plans that miss a binding decision.** D174.1 (one public door) by BK-3. D174.7 (outside people in `crm_contacts`) is not cited by BK or WB-19 (2670). D140.2 by WB-25. D136.2 by KS-5, KS-6, CX-24, CX-25. D162 by KS-4. AP-Q11 by 2622 and CH-7. D196.2 by HX-3.
4. **Consent has four homes.** CRM-M11 (1439) is the consent and suppression store, and STU-O5 and O9 say so. WB-6 (2657) adds an opt-in ledger. CX-11 adds a consent ledger. BK-8 (2684) adds consent. Recommendation: CRM-M11 owns all four.
5. **The customer card is planned twice.** CX-14 (2756) duplicates CRM-SIG-1 (2435) and CRM-SIG-2 (2436). CX-24 `get_customer_360` is the MCP twin of the same read.
6. **A stale fact in CLAUDE.md.** It says pricing is flat at ₹500. D204.9 says the D152 seat tiers are final. CX-30 (2810) and WB-21 (2672) cite tiers. Agents read CLAUDE.md first.

---

## 4. The merge list

| Duplicate set | Keep | The other task becomes | Needs the owner? |
|---|---|---|---|
| 2698 and KS-11 (2725) | KS-11 | 2698 closes into KS-11 (KS-11 already says so) | KS-Q1 only |
| 2622 and AP-18 (2303) | AP-18 | 2622 closes. Its "approve in one tap on a phone" goes into AP-18 | No |
| CH-7 (2779) and AP-18, MD cards | AP-18, My Day | CH-7 renders CRM card kinds inside "Needs you" and My Day | No |
| HX-1 (2763), HX-2 (2764) and My Day, Reach, AUTO-5 (1389) | My Day registry, Reach budget, AUTO-5 template | HX-1 builds the `nudges` point and the batch key. HX-2 adds nudge kinds to Reach preferences | No |
| HX-3 (2765), CH-3 (2773) and SH-1, SH-4, SH-6, SH-7 | SH | HX-3 closes into SH-1 and SH-7. CH-3 is SH-4 content | No |
| HX-4 (2766) and GD slices | GD | HX-4 closes into the guide | No |
| HX-0 (2762) | `package_host.md` §4 | HX-0 writes contribution points, not a manifest spec | No |
| WB-7 (2658) | The action catalog (B1) | WB-7 is a skill pack of catalog actions | No |
| WB-8 (2659) and CH-TEAM-4 (2445) | CH-TEAM-4 | WB-8 closes into CH-TEAM-4 | No |
| WB-1, WB-2, WB-4 (2652, 2653, 2655) and O6, O7, O8 (1511 to 1513) | One runtime | See D208 | **Yes** |
| WB-25 (2692) and B6 (1413) | B6 | See D206 | **Yes** |
| BK-3 (2679) and STU-O1 (1506) | O1 | BK-3 is the booking surface on O1 | No (D174.1) |
| CM-16 (2807) and CX-2 (2735), STU-C11 (1419) | CX-2 with CM-3 | CM-16 closes. C11 calls the CM-3 planner | No |
| CX-1 (2734) association table and CM-2 (2793) | CX-1 | CM-2 maps onto it | No |
| CX-14 (2756) and CRM-SIG-1, 2 (2435, 2436) | CRM-SIG-1 and 2. Their spec is done (2430, `crm_conversational.md` §14) | CX-14 closes into them. CX-24 reads the same card | No |
| KS-13 (2727) prices and CX-3a (2736) | See D207 | | **Yes** |
| WB-38 (2706) and STU-I1 (1197) | STU-I1 | WB-38 is the bot use of app connections | No |
| WB-35 (2703), KS-14 (2728) and `kb_gaps` | `kb_gaps` | Both write one table | No |
| WB-6 opt-in ledger (2657), CX-11 consent, BK-8 (2684) | CRM-M11 (1439) | Each reads and writes CRM-M11 | No |
| KS-5, KS-6, CX-24, CX-25 typed tools | The four door tools | Each becomes door ops and resources. See D209 | **Yes** (resources) |

---

## 5. Decisions the owner must make

The highest D on `main` is D204. Re-check each number at merge, because the first merge wins.

| Proposed | Question | Options | Recommendation |
|---|---|---|---|
| **D205** | Where does a CRM custom object live? | (A) A Studio object of the system package `crm`, in `studio_records`. (B) A CRM module in `crm_module_records`, with the Studio field vocabulary | **(A)**. It gets rules, state machines, views, door ops and chat tools from Studio at no extra cost. `crm_module_records` stays for mirrored Zoho modules until CM-10 |
| **D206** | Who is a bot? | (A) An install that acts as a `run_as` member, cut to its allow list (B6, keeps D140.2). (B) A service principal with its own grants (WB-25, amends D140.2) | **(A)**. One principal keeps one subject grammar |
| **D207** | Who owns product and price? | (A) The CRM product catalog (CX-3a). (B) A Knowledge price list (KS-13). (C) A Studio item object (K3) | **(A)**. Knowledge pages and Studio items point to it. Amend `knowledge_curation.md` §6.1 |
| **D208** | Which runtime runs a WhatsApp bot? | (A) A Studio package on O6, O7, O8, with WB as the product layer. (B) The WB runtime, with Studio O6 to O8 closed | **(A)**. D174 already accepted the O group. One engine runs every unattended conversation |
| **D209** | How does an app give an outside agent more than door ops? | (A) Amend D136: the manifest also declares MCP resources and prompts. Tools stay four. (B) Allow typed tools per app | **(A)**. `kb://` and `crm://` addresses and the playbook prompts join the manifest. The tool count stays fixed |

Also open, already named by its epic: **KS-Q1** (the publish audience as a page property, a new D).

---

## 6. What this review did not read

- The bodies of most slices. I read every epic named in the brief, and the 52 slices below. For the other slices I read the titles only.

```text
2652 2653 2656 2658 2692 2693 2700 2703 2706 2679 2682 2715 2717 2718 2719 2720 2721
2724 2725 2727 2734 2735 2736 2738 2744 2749 2753 2755 2756 2776 2779 2780 2782 2783
2787 2762 2763 2765 2792 2793 2794 2807 1506 1507 1510 1511 1512 1514 1515 2422 1144 2622
```

- The research comments on each epic, and every "could not verify" list.
- The full text of the specs. I read targeted sections of 34 specs. I did not read `crm_conversational.md`, `one_record_card.md`, `generative_ui_2.md` or `customer_console.md` §6A in full.
- Code bodies. I verified definitions and table creates only. I ran no test.
- Open branches and worktrees. A spec change on a branch is not in this review.
- `HANDOFF.md` and the Metorite agent runs.
