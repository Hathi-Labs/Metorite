# Metorite building blocks: one company OS that adapts to any company

**Status: ACTIVE, 2026-11-11 (D205).** This is the product shape of Metorite and the contract of its seams. Read it before you add a table, a registry, a manifest, a queue, an engine or a spec. Points 1 to 4 of D205 are the owner's. Points 5 to 10 are proposed, owner review (§7.2).

**D-numbers.** `main` held D204 on 2026-11-11. D205 holds every decision of this spec. Re-check the number at merge, because the first merge wins.

**Tier: STRICT.** It tells agents what to build and what not to build.

**Anchor.** `origin/main` at `4b6a8cf81`. **V** = verified on `main`, with a path. **P** = planned, with a task number.

**Companion.** `blocks_inventory.md` of the same review holds the evidence for each row.

---

## 1. The promise in one screen

**A company shapes Metorite to its own work. The core stays the same for every company.**

A company configures eight things:

1. **Objects.** Its own record kinds and fields, such as a site, a patient visit or a machine.
2. **Pipelines.** Its own stages for deals, leads and work.
3. **Apps.** Apps that it builds in AI Studio, beside the built-in apps.
4. **Bots.** Bots on its own WhatsApp number, for customers and for the team.
5. **Workflows.** Rules, schedules and hand-offs between apps.
6. **Knowledge.** Its own pages, files and connected folders.
7. **Connectors.** Its old CRM, its drives, its mail and its payment account.
8. **Harness.** The setup items, nudges and guides that coach its people.

The core never changes for one company:

- one tenant boundary (D15) and one grant grammar (D12)
- one task store, one file library and one search
- one assistant (Mira), one review list ("Needs you") and one outbound voice (Reach)
- one workflow engine, one AI router and one look

**Why this works.** Every app is a package (D147). A package declares what it uses in one manifest. The core reads the manifest and gives the app its place in the shell, the chat, the MCP door, search, My Day and the audit. So a new app costs a manifest, and not a new seam.

**The owner's words (D147).** "Metorite can ... over time, mutate around the person and the organization."

---

## 2. The building blocks

Each block has one seam and one owner spec. A second implementation of a block is a defect (CLAUDE.md §4).

| Block | What it is | The one seam (owner spec) | Apps that use it | The rule that keeps it single |
|---|---|---|---|---|
| **Tenant and session** | Which company a row belongs to | `get_db()` and `tenant_session()` (V `acb_common/db.py`). `saas_multitenancy.md` (D15) | Every app | FORCE row level security on each new table. Fence `test_tenancy_boundary.py` (V) |
| **Members, groups, grants** | Who inside the company may see a row | `normalize_grant_subject` and `EffectiveAccess.intersect` (V `acb_auth/permissions.py`). `tenancy_and_visibility.md` (D12) | Every app | Two axes, no third. Fence `test_grant_subject_grammar.py` (V) |
| **Principals** | Who acts | The member is the only authority (D140.2). An agent or an install acts as a named member, cut down to its scopes (`ai_studio.md` B6, P 1413) | Mira, MCP agents, workflows, Studio apps, bots | No actor gets grants of its own. See D205.6 |
| **Audience** | Who outside the member list may receive a thing | One vocabulary: `internal`, `customer-safe`, `public` (P 2725, KS-11) | Knowledge, Studio surfaces, bots, team channels | Audience narrows. It never widens D12 |
| **Records and objects** | A typed record kind with fields | The typed object store (V `323_studio_objects.sql`, `studio/objects_store.py`). The one value check (V `acb_common/studio_fields.py`). `ai_studio.md` §3.1 | Studio apps, CRM custom objects (P 2734), extension fields (V `package_host/field_values.py`) | One field vocabulary. One association table. See D205.5 |
| **Outside people** | A customer, a patient, a guest | `crm_contacts` (V `144_crm.sql`), D174.7 | CRM, bots, booking, forms | No second contact store. Consent lives in CRM-M11 (P 1439) |
| **Products and prices** | What the company sells, at what price, from when | The CRM product catalog (P 2736, CRX-3a) | CRM quotes, bots, Knowledge, email | Others point to it. See D205.7 |
| **The task store** | Every task and its lenses | `pm_tasks` and `pm_task_personal` (V, D52 to D54). `project_management_app.md` §12 | Projects, Tasks, Calendar, My Day, CRM activities (P 2739), bot hand-overs (P 2656) | Fence `test_no_gtd_table_names.py` (V). A ticket is a task type |
| **Time** | Events and reminders that are not to-dos | `calendar_entries` (V `331_calendar_entries.sql`), `reminders` (V `307_reminders.sql`) | Calendar, booking (P 2680) | A booking event is a calendar entry |
| **Files** | Every stored file | `artifacts` and its one writer (V `268_artifacts.sql`, `acb_memory/artifacts.py`). `artifact_management.md` (D162) | Chat, Knowledge (P 2718), email bodies (P 2646), WhatsApp media, Studio file fields | An app table holds a join, never bytes |
| **Knowledge and retrieval** | Company facts that people and agents read | `gateway/retrieval/` (V `company.py`, `sources.py`). `visible_pages_clause` (V `routes/knowledge/core.py`). `knowledge_base.md` §20 (D105) | Chat, email, CRM, bots, MCP, Studio | A second read path is a defect (P 2715) |
| **Channels and conversations** | A message in or out of email, WhatsApp, Slack or a phone | The shared transport per provider (V `routes/whatsapp/transport/*`, `routes/email/transport/*`). `whatsapp_message_manager.md` | Email, My WhatsApp, team channels (P 2442), bots | One adapter per provider. See D205.8 |
| **Workflows and automation** | A trigger, steps and approvals | The workflow engine (V `routes/workflows/scheduler.py`, `engine/*`). The capped headless turn (V `jobs/unattended.py`). `workflows_app.md` (D6), `proactive_agent.md` | CRM rules (P 2744), nudges (P 2763), Studio apps, bots | No second scheduler. A watch is a template |
| **Actions and the door** | One typed capability of an app, with a class A, B, C or X | App manifests (V `acb_common/app_manifests/base.py`). The Studio action catalog (V `routes/studio/actions.py`). `external_agent_access.md` §19 (D136) | Mira, the MCP, Studio views, workflows, bots | Four MCP tools serve every app. See D205.9 |
| **Proposals and approvals** | A change that waits for a person | The waiting seam (V `routes/shell/waiting.py`). `waiting_on_me.md` | Every app with a held write, an ask or a draft | One "Needs you" (AP-Q11). Each queue is a kind of the seam |
| **Signals and delivery** | What Metorite tells a member, and when | My Day registry (V `routes/shell/feed.py`, `needs.py`). Reach (V `gateway/reach/*`). `my_day_feed.md` (D100), `proactive_reach.md` (D103) | Every app, nudges, the brief, digests | One registry. One budget for caps and quiet hours. Fence `test_shell_feed.py` (V) |
| **Connections** | A link to an outside system and its credential | `integration_connections` (V `245_integration_connections.sql`, `connections/store.py`). `integrations_console.md` (D96) | CRM mirrors, MCP servers, GitHub, payments, Drive | A credential never enters the environment. Fence `test_integration_env_scoping.py` (V) |
| **Import and mapping** | Records that come from a file or an old system | The CRM source seam (V `crm_sources/base.py`) and mapping plans (V `277_crm_mapping_plans.sql`). `crm_platform.md` §5 (D95.4) | CRM (P 2735, 2792 to 2807), Studio objects (P 1419) | One path: Shape, plan, dry run, approve, Undo |
| **AI calls and credits** | Every model call and its cost | The Router and `acb_llm.decide` (V `acb_llm/decide.py`). `customer_console.md` CP-11, `ai_tier_routing.md` (D90) | Every AI feature | No direct vendor call. One budget key for each usage name |
| **Payments** | Money from a company's customer to the company | STU-O10 (P 1515), D174.3 | Booking (P 2682), CRM invoices (P 2738), Studio apps | The org's own account. Metorite holds no money |
| **The public door** | A page or form that a guest uses with no sign-in | STU-O1 (P 1506), D174.1. Signed record links STU-O4 (P 1509) | Booking (P 2679), CRM forms (P 1438), Studio surfaces (P 1507) | No app builds its own public seam |
| **Change record** | Who changed what, through what, and how to undo it | CRM change history (V `routes/crm/change_log.py`, D164.5). `crm_conversational.md` §4 | CRM, Knowledge provenance (P 2720), bots (P 2695), imports | One shape for every app. Each app may keep its own table |
| **Setup items** | What "set up well" means for each app | `setup_status` and the manifest `setup` field (P 1341, D196). `personalization.md` §18 | Every live app, wizards, the setup coach | One registry and one budget (D-PZ-13) |
| **The manifest** | What an app declares to the core | One envelope, one schema, one validator (V `acb_common/studio_manifest.py`). `package_host.md` §3 (D147) | Every app | A new need is a contribution point, never a new manifest |

---

## 3. How an app is made from blocks

**Every app is a projection.** It owns its screens and its domain rules. It borrows every other part from the blocks of §2.

### 3.1 The manifest of an app

An app ships one manifest. `package_host.md` §3.1 owns the envelope. Each section keeps the seam that reads it.

| Manifest section | What the app declares | The block that reads it | State |
|---|---|---|---|
| `objects` | Its record kinds and fields | Records and objects | V (Studio), P 2734 (CRM) |
| `actions` | Its door ops, each with a class | Actions and the door | V `app_manifests/knowledge.py`, `crm.py` |
| `views` | Its screens, as blocks | The Studio renderer | V (Studio blocks D2, D3) |
| `workflows` | Its triggers and steps | The workflow engine | V engine, P Studio E3 |
| `skill` | When Mira uses the app, and which actions she gets | Tool packs (V `acb_skills/tool_packs.py`) | V |
| `contributes.nav`, `jobs`, `search`, `needs`, `cards`, `agent` | Its place in the shell | The shell (D89) | V `app_manifests/shell/*.json` |
| `contributes.fields`, `slots` | Fields and panels on another app's record | The package host | V `package_host/field_values.py` |
| `consumes` (new point) | What it reads from Knowledge, with an audience and a staleness limit | Retrieval | P 2715 |
| `setup` (new point) | Its setup items | Setup items | P 1341, D196 |
| `nudges`, `insights` (new points) | Its signal rules, each with a reason and a batch key | Signals and delivery | P 2762, 2763 |
| `prompts`, `resources` (new points) | Its MCP prompts and `app://` addresses | Actions and the door | P 2719, 2776. See D205.9 |
| `channels`, `surfaces` (new points) | The numbers and the public pages it claims | Channels, the public door | P 1511, 1507 |
| `guides` | Its how-to pages | The guide corpus (V `app_manifests/guides/*.json`) | V in part |

### 3.2 The rules that hold an app together

1. **Every layer reaches data through an action** (`ai_studio.md` §3). One permission check, one approval path and one audit row cover every way in.
2. **An action has one class.** A is a read. B is a write that the app can undo. C is hard to undo. X is on neither door.
3. **Each act in the app has a chat twin** (V `acb_skills/chat_parity.py`, fence `test_chat_parity.py`).
4. **The app joins the shell through its manifest.** It mounts no palette, bell or dock (fence `src/lib/shell/seams.test.ts`, V).
5. **The app reads other apps through their seams.** It never copies their rows. It never writes its own visibility predicate.
6. **A new need becomes a contribution point.** It never becomes a second manifest or a second registry.

### 3.3 Two examples

**The CRM.** Its objects are leads, contacts, companies and deals, plus custom objects (P 2734). Its door ops live in `crm.py` (V, P 2776). Its activities are tasks (P 2739). Its timeline reads email and WhatsApp through their transports.

The CRM rules run on the workflow engine (P 2744). Its nudges are My Day card kinds (P 2771). Its prices come from its catalog (P 2736).

**A clinic front-desk bot.** It is a Studio package. It claims the clinic's number (P 1511). A message starts a conversation run (P 1512). The run acts as the bot's `run_as` member, cut down to its allow list (P 1413).

The run answers from Knowledge pages with the `customer-safe` audience (P 2725). It books through the booking API (P 2687) on the public door (P 1506). It takes payment through STU-O10 (P 1515). A hand-over makes a task (P 2656).

---

## 4. How a company adapts Metorite

### 4.1 Custom objects and fields

- An admin adds fields to a built-in record. Projects keeps `pm_custom_fields` (V). The CRM keeps `crm_field_defs` (V). A package adds fields through `package_field_value` (V).
- An admin or a builder adds a new object. A new object is a Studio object (D205.5, proposed). It gets fields, rules, state machines, formulas, views, door ops and chat tools.
- Every field uses one type vocabulary and one value check (V `studio_fields.py`).
- Records link through one association table with labels (P 2734).

### 4.2 Presets by industry

A preset is a set of packages, settings, templates and setup items. It is data, not code. A company picks one at onboarding and can change each part later.

| Preset | What it turns on | State |
|---|---|---|
| **Clinic** | Appointment types and pools, a front-desk bot, intake and consent, recalls, a service-business CRM. Not an EMR | P 2754, 2669, 2684, 2683 |
| **Manufacturer** | Inventory, procurement and MRP-lite apps, a deliveries project for won deals, AMC renewals | P Studio K3, K4. P 2783, 2754 |
| **Agency** | Projects with phases, time as a typed field (D197.3), deal to project, client knowledge spaces | P 2755, 2716 |
| **Installer** | Field visits app, site photos, a job hand-off from a won deal, warranty fields on a deal | P Studio §3.6 example, P 2368, 2783 |

A preset never adds a table. It installs packages and fills settings.

### 4.3 AI Studio apps

A company builds an app in four shapes: a skill, a workflow, an app, or an app with a skill (`ai_studio.md` §2). The app uses the blocks of §2 only. A logic module returns proposed writes, and the platform applies them (`ai_studio.md` §3.7).

### 4.4 Connectors and mirrors

- A company connects an outside system in the app that needs it (D96.1). Integrations lists every connection (V).
- An old CRM comes in by one of three modes: federated, mirror or migrate (P 2790). Mirror is the default and reads only.
- A file from any tool comes in on the same path: Shape, mapping plan, dry run, approve, Undo (P 2794, 2735).
- Each connection shows one health state: connected, quiet, failing or needs consent (`integrations_console.md` read model, `company_context.md` §6).

### 4.5 Bots

A bot is a Studio package with a number, instructions, an allow list of actions and an audience (D205.6, D205.8 proposed). The bot owner picks a template (P 2694) and tests it in a sandbox (P 2671). A reviewer approves each version of the instructions (P 2692). A bot write is a draft that a member approves (P 2658).

### 4.6 Workflows

A company adds rules, schedules and hand-offs on the one engine. Examples: a stage-exit rule (P 2744), a won deal makes a delivery task (P 2783), a daily standup post (P 2444). An automation climbs the trust ladder of D131.

### 4.7 Knowledge

A company writes pages, links folders and sets an audience on a page. Each project gets a home article (P 2716). An agent writes a draft or a proposal only (D-KB-4).

### 4.8 The setup coach

Each app declares its setup items (D196). The chat raises one missing item at a time, inside one budget (D-PZ-13). A fix runs in the chat with a preview and Undo. A score per app follows in phase B (P 951).

### 4.9 What a company cannot change

| Fixed part | Why | Source |
|---|---|---|
| Tenancy | A row belongs to one company. RLS binds it at the session | D15 |
| The grant model | Two axes and one grammar. No company adds a third axis | D12, D140.2 |
| The four acts that always ask | A delete, money, a member or role write, a send to an outside person | D131.7, D164.4 |
| The door classes | A, B, C and X mean the same in every app | D136 |
| The one look | Glass only. No app palette | D143, D149 |
| The one assistant and the one review list | Mira, and "Needs you" | D99, AP-Q11 |
| The AI router and the meter | The Router routes and meters every model call | D90, CP-11 |
| The audit | Every change leaves a change record | D164.5 |

---

## 5. The user experience principles

1. **Few terms.** A member sees Goal, Epic and Task (D199). A builder sees App, Object, Field, Action, Workflow and Skill. §8 is the full list.
2. **Obvious structure.** Personal Center, Apps, AI Studio and Admin (`launch_surface.md` §2). A team view is a filter, never a Center (D87).
3. **Chat first, full parity.** Each click has a chat twin. Each chat act shows the same card as the app (D109).
4. **Setup screens show the result.** A setup step shows what changes before the member saves it. A fix shows the score change.
5. **AI proposes, and a person confirms.** An AI write is a card, a draft or a proposal. Undo stays on every confirmed change.
6. **One review list.** Every wait shows in "Needs you", in My Day and in the nav count (AP-Q11). No app builds its own approvals lane.
7. **Nudges that the member controls.** Reach sends nudges in batches, inside one cap and one set of quiet hours (Reach §6). Each nudge gives its reason. The member can snooze, dismiss or mute a kind.
8. **Failure copy explains.** A refusal says what happened, why, and the next step. It never shows a raw error.
9. **Mobile.** Each screen works at 390 px. The bar always shows (`navigation_shell.md` §9.1).

---

## 6. The rules for agents

### 6.1 Extend a seam. Never add a parallel one.

Before you add a table, a registry, a manifest, a queue, an engine or a spec, do these steps:

1. Find the block in §2.
2. Read its owner spec.
3. Extend its seam. Add a provider, a kind, a contribution point or a column.
4. If the seam cannot hold the need, stop. Ask the owner on the task.

### 6.2 The seams and their fences

| Seam | Fence | State |
|---|---|---|
| Tenant boundary | `tests/unit/test_tenancy_boundary.py` | V |
| Grant grammar | `tests/unit/test_grant_subject_grammar.py` | V |
| No `gtd_` table | `tests/unit/test_no_gtd_table_names.py` | V |
| Shell seams | `workbench/control_plane/src/lib/shell/seams.test.ts` | V |
| Manifest schema | `tests/unit/test_studio_manifest.py`, `tests/unit/test_shell_manifests.py` | V |
| One field value check | `tests/unit/test_studio_fields.py`, `tests/unit/test_package_field_values.py` | V |
| Door coverage | `tests/unit/test_mcp_edge_coverage.py`, `tests/unit/test_knowledge_chat_coverage.py` | V |
| Chat parity | `tests/unit/test_chat_parity.py` | V |
| One feed registry | `tests/unit/test_shell_feed.py` | V |
| Waiting seam | `tests/unit/test_waiting_seam_r8.py` | V |
| Retrieval | `tests/unit/test_company_search_sources.py` | V |
| CRM change history | `tests/unit/test_crm_change_history.py` | V |
| Credentials out of the environment | `tests/unit/test_integration_env_scoping.py` | V |
| One contribution point per need | advisory until a fence exists | P |
| One change-record shape | advisory until a fence exists | P |

### 6.3 Writes by an agent

- An agent write is a proposal, a draft or a door request. It never applies in silence.
- The member decides on a card in Metorite (`external_agent_access.md` §19.5).
- An automation may act alone only inside a scope and a cap that the member approved once (D131.3).

### 6.4 Provenance

- The server fills every change record. A client cannot forge it.
- The record holds: `actor`, `on_behalf_of`, `via`, `client`, `run_id`, `model`, `sources`, `before`, `after`, `undo_of`.
- Metorite treats text from a document, a customer or a connector as data. That text never becomes an instruction.

---

## 7. The conflicts found and the decisions needed

### 7.0 The owner's decisions of 2026-11-10 (D205 points 1 to 4)

1. **This spec is the product shape.** A company adapts Metorite through objects, pipelines, apps, bots, workflows, knowledge, connectors and the harness. A vertical (a clinic, a manufacturer, an agency, an installer) is a preset over the core blocks, never a fork. An agent extends a seam and never adds a parallel one (§6.1).
2. **Booking is in scope.** A guest books a person's free time from a public page or a bot. The guest pays the company through the company's own payment account. This narrows `calendar_events_reminders.md` §12 for booking only: a guest sees free slots and never a calendar view. Rooms and resources stay out. Metorite's own mail sender stays off. The booking API lives in the Calendar app (epic task 2675). The Studio Bookings block (`ai_studio.md` O11) uses that API and builds no second one.
3. **WhatsApp is bots.** A company sets up its own number in Metorite and runs bots on it (epic task 2650). The inbox and ingest direction of `whatsapp_message_manager.md` is on hold. Its transport, webhook and Embedded Signup stay the base of the bots.
4. **The CRM becomes complete** (epic task 2732). It gets quotes, invoices, and a forecast for sales, delivery and cash. AI covers every part. A mirror reads other CRMs (epic task 2790), and the CRM joins the MCP. This reverses the non-goal on quotes in `crm_app.md`. `crm_platform.md` CRM-S9 is the base.

### 7.1 Conflicts that existing decisions settle

These need no owner call. The task text must change before a build starts.

| Conflict | The rule that settles it | Tasks to change |
|---|---|---|
| A booking page builds its own public seam | D174.1, one public door | 2679 uses 1506 |
| Typed MCP tools for Knowledge and CRM | D136.2, four tools | 2719, 2720, 2782, 2787 become door ops |
| A CRM "Needs your attention" lane, and an Approvals chip | AP-Q11, one "Needs you" | 2779, 2622 close into 2303 |
| A nudge engine with its own states, cap and quiet hours | D100, D103, D161 | 2763, 2764 |
| A second setup coach and score | D196.2 | 2765, 2773 close into 946, 949, 952 |
| A Knowledge file table outside the library | D162 | 2718 |
| Two "Mira in a WhatsApp group" slices | `whatsapp_assistant_channel.md` §19 | 2659 closes into 2445 |
| The customer-safe flag twice | KS-11 says it owns it | 2698 closes into 2725 |
| The customer card twice | `crm_conversational.md` §14 | 2756 closes into 2435, 2436 |
| Four consent stores | D174.7, CRM-M11 | 2657, 2753, 2684 use 1439 |
| Id prefixes clash: CX (`company_context.md`), CH (`skills_registry.md`), WS-CM (Console and Money), WB-1 and WB-2 (`hardening_backlog.md`) | R2, an id is never reused | The CRM epic uses CRX-n, the sales harness uses SLH-n, the mirror epic is WS-MIR with MIR-n, and the two web rows become WEBH-1 and WEBH-2 |

### 7.2 Decisions for the owner

| Proposed | Question | Recommendation | Why |
|---|---|---|---|
| **D205.5** | Where does a CRM custom object live? | A Studio object of the system package `crm` | It gets fields, rules, views, door ops and chat tools from Studio. One object store stays one store |
| **D205.6** | Who is a bot? | An install that acts as a `run_as` member, cut down to its allow list and its audience | D140.2 keeps one principal. A second principal breaks the one grammar |
| **D205.7** | Who owns product and price? | The CRM product catalog (2736) | One source of price. Knowledge pages and Studio items point to it |
| **D205.8** | Which runtime runs a WhatsApp bot? | A Studio package on O6, O7 and O8. WS-WB is the product layer | D174 accepted the O group. One engine runs every unattended conversation |
| **D205.9** | How does an app give an outside agent more than door ops? | The manifest also declares MCP resources and prompts. The tools stay four | `kb://` and `crm://` addresses arrive with no new tool |
| **D205.10** | Is the publish audience a page property? (KS-Q1) | Yes. The audience of §2: `internal`, `customer-safe`, `public`. It narrows and never widens D12 | Its epic names it as an owner gate. A page property inside the grant grammar is not a third axis |

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **Organization** | One company. The tenant |
| **Member** | A person in an organization. The only principal |
| **Group** | A set of members. A team is a group (D204.8) |
| **Space** | A root of the Projects tree, or a Knowledge area |
| **Goal, Epic, Task** | The three levels of work that a member sees (D199) |
| **App** | A package that the shell shows. Built-in or made in AI Studio |
| **Object** | A record kind, such as a visit or a deal |
| **Record** | One row of an object |
| **Field** | One typed value on a record |
| **Action** | One typed capability of an app, with a class |
| **Workflow** | A trigger, steps and approvals on the one engine |
| **Skill** | What tells Mira when to use an app |
| **Bot** | A package that answers on a channel |
| **Channel** | Email, WhatsApp, a team channel or Slack |
| **Contact** | A person outside the organization |
| **Connection** | A link to an outside system |
| **Mirror** | A read-only copy of an outside system |
| **Proposal** | A change that waits for a person |
| **Needs you** | The one list of what waits on a member |
| **My Day** | The one feed of what matters today |
| **Reach** | How Metorite messages a member first |
| **Setup item** | One thing an app needs to work well |
| **Audience** | Who outside the member list may receive a thing |
| **Change history** | The record of each change, with Undo |

---

## 9. The epics of 2026-11-10 and the blocks they extend

Each epic writes its owning spec in its first slice. Until then this table and the epic text are the source. An agent builds no slice before that spec exists, except a slice of the spec itself.

| Epic (task) | Id prefix | Owning spec (planned, written by) | Blocks it extends |
|---|---|---|---|
| WS-LL learning loop (2624) | LL-n | `orchestration_playbook.md` §6c and §7a (written) | Signals and delivery, the change record, the setup items |
| Agent kit (2547) | KIT-n | `orchestration_playbook.md` §6a (written) | Actions and the door |
| Rich email composer (2640) | RC-n | `email_app_master_plan.md`, new section (RC-0, task 2641) | Files, channels |
| WS-WB WhatsApp bots (2650) | WB-n | `whatsapp_bots.md` (WB-0, task 2651) | Channels, principals, actions, the public door (D205.6, D205.8) |
| WS-BK booking (2675) | BK-n | the booking section of `calendar_events_reminders.md` (BK-0, task 2676) | Time, the public door, payments |
| WS-KS knowledge everywhere (2713) | KS-n | `knowledge_shared.md` (KS-0, task 2714) | Knowledge and retrieval, files, audience (D205.10) |
| WS-CX CRM complete (2732) | CRX-n | `crm_complete.md` (CRX-0, task 2733) | Records and objects, products and prices, the task store, workflows (D205.5, D205.7) |
| WS-HX app harness (2757) | HX-n | a contribution point section of `package_host.md` (HX-0, task 2762) | Signals and delivery, setup items, the manifest |
| WS-CH CRM sales harness (2758) | SLH-n | `crm_complete.md` (SLH-0, task 2770) | Signals and delivery, setup items |
| WS-MIR CRM mirror and connectors (2790) | MIR-n | `crm_connectors.md` (MIR-0, task 2791) | Import and mapping, connections |
| SCALE (2566) | SC-n | `agent_coordination.md` and `dev_capacity.md` | none (process) |

