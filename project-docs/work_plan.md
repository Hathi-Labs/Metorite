# Work Plan of Record

**Owner:** vjvarada · **Reset:** 2026-10-27, by D118.

**Purpose.** This file holds the rules, the decisions and the owner gates of record. §1 is the spec contract and the standing rules. §3 holds the decisions. §4 names the owner of duplicated work. §6 holds the owner gates.

**The authority split (D118).**
- **Metorite holds the state.** It holds the status, the priority, the owner and the order of every work item.
- **This repository holds the rules.** It holds the specs, the decisions, the standing rules and the owner gates.
- **§2 is an index.** It keeps one short row for each workstream, with its owning spec and its Metorite epic.

For what to build and how, the owning spec wins. For the order and the owner of a work item, Metorite wins.

**Where the state lives.** The state lives in the HathiLabs project **Metorite**, in the Metorite app. §2.1 names the epic or the project of each workstream.

**How an agent finds work.**
1. Call `my_work` through the Metorite MCP, and take the top row.
2. Call `claim_task` on that row before you start the work.
3. For the workstream index, run `uv run python scripts/board.py`. Add `--row WS-CZ` to read one row.

**Navigation.** `project-docs/INDEX.md` is the classification of record (D26). Build only from a spec that INDEX lists ACTIVE.

**History.** The slice histories, the earlier passes and the old §5 backlog are in git. Read them with `git log -p project-docs/work_plan.md`.

---

## 1. The agent-ready spec contract

A workstream may be handed to an independent agent only when its owning spec
carries all seven. (Exemplars: `permissions_sandbox_b6.md` Tier 0 for tests +
decision table; `drawio_integration.md` for per-ticket "done when";
`task_manager_app.md` §9.3 for the runbook; `observability_e2.md` for
verification commands.)

1. **Status header** — dated, with "verified against code on <date>". A header
   that contradicts the body (WhatsApp, task-manager) is worse than none.
2. **Scope and non-goals** — explicit, like email master §1.
3. **Acceptance per item** — a "done when" an agent can test, not "owner call".
4. **Current file paths** — `apps/services/...` tree; anchors re-verified at
   dispatch, not trusted from authoring time.
5. **Verification commands** — the exact pytest/tsc/build/feature_check calls.
6. **Single owner** — one owning spec; every other doc that mentions the work
   links here and adds nothing.
7. **Gate labels** — every item marked **AGENT-SAFE** or **OWNER-GATE**
   (see §6). An agent must refuse OWNER-GATE work and say so.

**Standing rules** (bind all specs from today):
- **R1 — no absolute future migration numbers.** Write "next free number at
  build time". The audit found ~12 wrong citations (117/118/119/120/122/123/
  128/131/133/134/135 all point at unrelated shipped migrations).
- **R2 — no phase-ID reuse across docs.** "Phase 2" currently means three
  different things. New work uses the WS-n IDs below.
- **R3 — nomenclature per `department_centers.md` §1.** "Agent Workshop" not
  "Agent Creator" (5 spec sites violate this); "Agent Registry" for `/agents`;
  Center/module/group as defined there.
- **R4 — status changes propagate.** A PR that ships spec'd work updates the
  owning spec's status header in the same PR.
- **R5 — tenant-ready by construction** *(owner-directed 2026-08-09, D18; binds
  every PR while WS-29 is in flight)*. App work continues in parallel with the
  tenancy retrofit on these terms, each enforced by an existing test, not by
  prose: **(a)** every new persisted table is tenant-scoped — it must satisfy
  `tests/unit/test_tenant_coverage.py`'s source gate (covered by the generated
  RLS migration, or in `gen_tenant_migration.EXEMPT` with a reason a reviewer is
  expected to challenge); **(b)** no new database connection sites outside the
  seam — additions to `_SYNC_ENGINE_ALLOWED` / `_PSYCOPG_ALLOWED` need a cited
  reason in the PR; **(c)** new Redis keys go through the tenant-prefix wrapper
  (allow-list additions likewise); **(d)** session acquisition uses the current
  seam idiom only, so H2's conversion stays mechanical — do not invent new
  acquisition idioms; **(e)** never trust a tenant (or identity) from request
  input — `user_management_contract.md` R11/R3. The ratchet tests **bind `main`** (PR #404 merged 2026-08-09).
- **R6 — expand/contract migrations** *(D28, 2026-08-10; owning spec
  `specs/engineering_practice.md` §3).* **The deploy applies migrations BEFORE
  restarting services**, so old code always runs against new schema for a window.
  A migration must therefore be compatible with the code currently running: new
  columns **nullable with a default**; **no rename in place** (add, backfill,
  switch readers, drop in a LATER release); constraints over existing data land
  `NOT VALID` and validate in a guarded block (migration 148 is the reference).
  The tightening half is always a second, later migration. ⚠️ **We cannot roll
  back** — forward-only ladder, no blue/green — so recovery is roll-forward or
  restore, which is why §8's off-box backup and SHA-in-`/health` items are
  business risks rather than tech debt.
- **R7 — a rule names its fence, or it is advisory** *(D28; the generalisation of
  R5's "enforced by an existing test, not by prose").* Any PR introducing an
  architectural rule must name the test that makes breaking it fail, or label the
  rule advisory. Prose binds nobody: the next agent has not read that paragraph.
  Prefer **structural fences** (assert the invariant over the whole tree) to
  example tests — they are what defend against future agents.
- **R8 — SQL is verified against a real database** *(D28; `engineering_practice.md`
  §4).* Hermetic fakes agree with whatever SQL they are handed, which is how five
  live bugs shipped green (an unencodable `CAST(:param AS timestamptz)`; a fake
  matching `lower(col) = :param` against NULL; a `LEFT JOIN` whose `ON` could not
  see its table). Any change whose subject is a query, a migration or a predicate
  is run against a real Postgres before it is believed. **Verified-red-first**
  applies to every fence and every bug fix; **mutation testing** applies to money,
  auth, tenancy and outward writes.
- **R9 — an app spec declares its shell manifest** *(D89, 2026-10-05. Owning
  spec: `specs/navigation_shell.md` §5.)* Every app joins the one shell, and no
  app builds a shell of its own. So the spec of an app carries a section named
  **"Shell manifest"**. It names the app's team, a one-line purpose and its jobs,
  each with a verb, its fields and its form route. It also names the app's search
  provider, its "needs you" items, its Home cards with their altitudes, and its
  agent. It names no palette, bell, assistant rail or ⌘K listener of the app's
  own. R9 binds a NEW app spec, and the spec of an app the owner promotes. An
  older app spec gains the section when its app is next worked on.
  **Fences.** The code half is a test: `src/lib/shell/seams.test.ts` fails on a
  new palette, bell, assistant rail or ⌘K listener in any app. The spec half is
  advisory: the spec-auditor refuses an app spec with no "Shell manifest"
  section, and nothing in CI reads specs for it.

---

## 2. The dispatch board

This section is an index of the workstreams, not their state (D118). Metorite holds the status, the priority, the owner and the order of each item. `scripts/board.py` reads the rows of §2.1 and §2.2.

### 2.0 Roadmap

The core function brief sets the phases (D119). Customer zero comes first inside Phase 1.

| Milestone | Phase | Date | Meaning |
|---|---|---|---|
| **M0** | Phase 1, weeks 1 to 3 | about 17 Nov 2026 | Customer zero uses Metorite every day: Fracktal and Shaurrya Tele. The backups are safe. |
| **M1** | Phase 1, the rest | December 2026 | Pilot-ready. Brief tasks 1 to 7 are done, the isolation tests pass, and a second organization is safe. |
| **M2** | Phase 2 | 31 March 2027 | The first paid customer. Brief tasks 8 to 15 and the money path. |
| **M3** | Phase 2 operations | 31 March 2027, if the brief needs it | Restore, compliance and observability. An item that is not on the brief moves to Phase 3. |
| **M4** | Phase 3 | after Phase 2 | Studio, the UX System, Writing Help, MCP data parity, Notes and Workflows. |

⚠️ **Timing.** Ten six-week pilots must complete in December, so they start by mid-November. Phase 1 has about three weeks of build time before the first pilot. **Ten pilots are ten organizations.** Tenant isolation (M1) lands before the first pilot outside customer zero (D119.5).

**The critical path** to a safe, then paying, second organization:

1. **Customer zero safe and working** (M0, P0): H-259, BO-23b, M0.4, the chat flag, H-163, H-373, H-120, H-326 and CZ-DEWIN-1.
2. **The shared job queue**: BR-1a. Every brief task runs on it, so build it first and small.
3. **Issue reporting and the Get started checklist** before the pilots: IR-0 to IR-3 and OB-1. Help follows in M1: HLP-1 to HLP-3.
4. **The cheap isolation fixes** (M1, P0): H-249, H-312, H-334, H-201 and AX-0. AX-0 withholds the host shell tools from personal agents, and the owner confirms it.
5. **The isolation spine**: H2, then H3.2 (D120), then H3 in a maintenance window. H4, H5 and H6 run beside it.
6. **The owner answers**: WS-40-Q (D121) and H3.2 (D120). Both gate P0 work.
7. **Branch protection**: WS-38a, then WS-5a and WS-38b.
8. **Secrets**: H-97, H-207 and H-3, then WS-24b.
9. **Enforce the designed controls** (brief tasks 6 and 7): WS-1e, WS-3, BH-3, BH-4, BR-6a to BR-6c, BR-7a and WS-40. Agent code runs in a sandbox off the box: SBX-0 (the owner picks the vendor), then SBX-1.
10. **The daily promise** (brief tasks 2, 4 and 5): BR-4, BR-2a to BR-2d, BR-5 and WS-58. The brief reads across every app, so fast retrieval comes with it: KG-1 to KG-3 (D128). The brief runs unattended, so AUTO-2 makes each run happen at most once.
11. **Email and calendar out of preview** (brief task 3): E-05, E-09, E-11, E-07, WS-21-P4 and CE-0.
12. **A real second organization**: MT-1j-exec provisions it and runs the smoke. E-58 and BR-14 follow.
13. **Pay** (Phase 2): H-14, the rate card and the spend gate (D123), SC-5, H-292 and H-287.
14. **The Phase 2 brief tasks**: BR-10, BR-11, BR-12, BR-9, BR-8, BR-13 and BR-15.

**The full item list is in Metorite**, in the HathiLabs project Metorite, under the epics that §2.1 names.

### 2.1 Workstreams

One row for each workstream. State: 🟢 active, 🟡 gated or ⏸ after Phase 2, then the milestone. The last column names the next P0 or P1 plan ID. A workstream with no P0 or P1 item names its next item.

| WS | Workstream | State | Owning spec | Gates · next |
|---|---|---|---|---|
| WS-CZ | **Customer Zero**: Fracktal and Shaurrya Tele (DEWiN) | 🟢 active · M0 | none yet | Metorite: epic 519 (Metorite Application) · next H-259, BO-23b, CP-8-act, H-326 |
| WS-TS | **Tenancy and Security**: a second organization is safe | 🟢 active · M1 | `specs/saas_multitenancy.md` · `specs/saas_multitenancy_handover.md` | Metorite: epic 520 · gates H3.2 (D120) and WS-40-Q (D121) · next AX-0, H-249, H-312, H2 |
| WS-CM | **Console and Money**: seats, credits, invoices | 🟡 gated · M2 | `specs/customer_console.md` | Metorite: epic 478 (Billing & Accounts) · gates H-14 and the rate card (D123) · next H-170, CP-10 |
| WS-TO | **Trust and Operations**: enforced controls, restore, compliance | 🟢 active · M1 | `specs/box_hardening.md` | Metorite: epic 521 (Platform & CI/CD) · gate SBX-0 (the sandbox vendor) · next WS-1e, WS-3, BH-3 with SBX-1, the WS-37 spec (D119.4) |
| WS-PL | **Platform and CI**: delivery, staging, branch protection | 🟢 active · M1 | `specs/development_and_delivery_framework.md` | Metorite: project Platform & CI/CD · next WS-38a, WS-38e, WS-5a |
| WS-PT | **Projects and Tasks** | 🟢 active · M1 | `specs/project_management_app.md` · `specs/one_projects_app.md` | Metorite: project Projects/Tasks App · no P0 or P1 · next H-257, H-387 |
| WS-CAL | **Calendar**: events, reminders, external sync | 🟢 active · M1 | `specs/calendar_focus_os.md` · `specs/calendar_events_reminders.md` | Metorite: project Calendar App · next CE-0, WS-21-P4 (read-only first, D119.6) |
| WS-PP | **People and Profile** | 🟢 active · M1 | `specs/people_center_app.md` | Metorite: project My Profile · next E-58 |
| WS-EM | **Email** | 🟢 active · M0 | `specs/email_app_master_plan.md` · `specs/shared_mailbox.md` | Metorite: project My Email · Gmail in test mode (D122.4) · next E-05, E-09, E-11 |
| WS-CRM | **CRM**: native mode, the Zoho mirror, and the cutover from one to the other | 🟢 active · M1 | `specs/crm_platform.md` | Metorite: epic 477, cutover sub-epic CRM-Z10 · gate CRM-Z10f (labels) · next CRM-Z10a, CRM-Z10c, CRM-Z10d, H-366 |
| WS-WA | **WhatsApp** | 🟡 gated · M2 | `specs/whatsapp_message_manager.md` · `specs/whatsapp_assistant_channel.md` | Metorite: epic 476 · D115 freeze, lifted for brief task 8 only (D122.3) · next BR-8a, E-20 |
| WS-KB | **Knowledge** | 🟢 active · M2 | `specs/knowledge_base.md` · `specs/knowledge_curation.md` | Metorite: project Knowledge · no P0 or P1 · next H-401, KC-1 |
| WS-KG | **Knowledge graph and retrieval**: one graph over every app, for Mira and the MCP | 🟢 active · M1 | `specs/knowledge_base.md` §20 · `specs/company_context.md` (KG-0 writes `specs/knowledge_graph.md`) | Metorite: epic 783 (Knowledge) · D128 · gate MEM-3 (the memory shape) · next KG-0, KG-1, KG-9 |
| WS-MCP | **MCP and external agents** | 🟢 active · M2 | `specs/external_agent_access.md` | Metorite: epics 354 and 495 (MCP & External Agents) · parity after Phase 2 (D124) · next WS-55-follow, MCP-Ga |
| WS-STU | **Studio**: tool packs, workflows, AI apps | ⏸ after Phase 2 · M4 | none yet | Metorite: epic 491 (Agent Tooling) · D127 · next STU-0, then SKL-1 and SKL-2 after SBX-1 |
| WS-UX | **UX System** | 🟢 active · M1 | none yet | Metorite: epic 492 · next UI-G1, UX-3 (D119.3) |
| WS-WH | **Writing Help** | ⏸ after Phase 2 · M4 | none yet | Metorite: epic 493 · next WA-1 |
| WS-CHAT | **AI chat**: Mira, the one assistant | 🟢 active · M1 | `specs/one_assistant_context.md` · `specs/chat_first_surface.md` | Metorite: project AI Chat Interface, epic 470 · a parallel session owns it · next #469 with BR-5, #471, WS-58, SUB-1 (the sub-agent policy for OA-9) |
| WS-PRE | **Preview apps**: Notes, Meetings, Dashboard | ⏸ after Phase 2 · M4 | `specs/note_taker_app.md` · `specs/meeting_bot_platform_plan.md` | Metorite: epic 494 · next E-28, E-25 |
| WS-DOC | **Docs and repo cleanup** | 🟢 active · M1 | none yet | Metorite: epic 522 (Developer Workflow) · next DOC-1, DOC-2 |
| WS-BRIEF | **Core brief**: brief tasks with no earlier plan line | 🟢 active · M1 | none yet | Metorite: epics 479 (Proactive Intelligence) and 480 (Secrets & Security), connectors in epic 481, automations in sub-epic AUTO of epic 479 · gate AUTO-3 (the trust ladder) · next BR-1a, BR-4, BR-2a, AUTO-2 |
| WS-IR | **Issue Reporting** | 🟢 active · M0 | none yet | Metorite: epic 475 (Observability & Alerts) · D126 · next IR-0, IR-1, IR-2 |
| WS-OB | **Onboarding and Help** | 🟢 active · M1 | none yet | Metorite: Onboarding project · next OB-1, HLP-1, ADM-1 |

### 2.2 Earlier rows (WS-0 to WS-67)

These are the rows of the board before D118. Each row keeps its ID, so a spec that cites `WS-31` still resolves. State: ✅ done, 🟢 open, ⏸ parked or ⛔ withdrawn. The last column names the workstream that holds the work now.

| WS | Workstream | State | Owning spec | Gates · next |
|---|---|---|---|---|
| WS-0 | Documentation remediation (the old §5) | ✅ done | none | now in WS-DOC |
| WS-1 | Action Broker truth and completion (BO-1) | 🟢 open | none, the BO checklist is in git | now in WS-TO (WS-1e) |
| WS-2 | Secrets (BO-8): rotate, purge, fail closed | 🟢 open | none, the BO checklist is in git | now in WS-TS (WS-2d) |
| WS-3 | Isolation ladder (BO-7, HH-6, T0 to T2) | 🟢 open | `specs/permissions_sandbox_b6.md` §P5 | now in WS-TO (WS-3a, WS-3b) |
| WS-4 | Event-bus consumer and durable queue (BO-20) | 🟢 open | none, the BO checklist is in git | now in WS-BRIEF (BR-1a) |
| WS-5 | CI gates real (BO-17, BO-18) | 🟢 open | none, the BO checklist is in git | now in WS-PL (WS-5a) |
| WS-6 | Observability wiring and attribution (BO-5) | 🟢 open | `specs/observability_e2.md` §7 | now in WS-TO |
| WS-7 | Memory activation and search (BO-21, BO-22) | 🟢 open | `specs/llm_caching_memory.md` | now in WS-CHAT |
| WS-8 | Agent architecture A0 to C | 🟢 open | `specs/agent_architecture.md` §12.2 | now in WS-CHAT |
| WS-9 | Memory tiers 3b, 3c and 4 | 🟢 open | `specs/memory_architecture.md` §9 | now in WS-CHAT |
| WS-10 | Multiplayer remainder: S1 compartments | 🟢 open | `docs/multiplayer/memory-clearance.md` §7 | now in WS-CHAT |
| WS-11 | Workflows slice 3: gallery, fan-in, loops | ⏸ parked | `specs/workflows_app.md` §8.3 | after Phase 2 (D119.3) · now in WS-STU (STU-2) |
| WS-12 | Framework uplift, Phase 4 only (D6) | 🟢 open | `specs/multi_agent_orchestration.md` | now in WS-CHAT |
| WS-13 | Centers B: groups become real | ⏸ parked | `specs/department_centers.md` | parked by D49 · now in WS-TS |
| WS-14 | Centers C: scoping deepens | ⏸ parked | `specs/department_centers.md` §3 | parked by D49 · now in WS-TS |
| WS-14a | Tenancy TV-1: the org_group slug-only joins | ✅ done | `specs/tenancy_and_visibility.md` §2 | absorbed by MT-1i · now in WS-TS |
| WS-15 | Centers D: dashboards and the Company Center | ⏸ parked | `specs/department_centers.md` | parked by D49 · now in WS-PRE |
| WS-16 | Centers E: AI budgets | ⏸ parked | `specs/department_centers.md` | parked by D49 · now in WS-CM |
| WS-17 | Email completion | 🟢 open | `specs/email_app_master_plan.md` | now in WS-EM |
| WS-18 | Tasks phase 3: weekly review, waiting-for | 🟢 open | `specs/task_manager_app.md` §13 | re-based by D53 · now in WS-PT |
| WS-19 | Notes and the meeting bot | ⏸ parked | `specs/note_taker_app.md` · `specs/meeting_bot_platform_plan.md` | after Phase 2 (D119.3) · now in WS-PRE |
| WS-20 | WhatsApp activation and remainder | ⏸ parked | `specs/whatsapp_message_manager.md` §11, §12 | D115 · now in WS-WA |
| WS-21 | Calendar F2 and F3 | 🟢 open | `specs/calendar_focus_os.md` §9 · `specs/calendar_timeboxing.md` §13 | re-homed by D54 · now in WS-CAL |
| WS-22 | draw.io | ⏸ parked | `specs/drawio_integration.md` | parked by the owner (D25.7) · no workstream |
| WS-23 | Skills registry and per-agent toggles | 🟢 open | `specs/skills_registry.md` | now in WS-STU |
| WS-24 | Colleague onboarding readiness | 🟢 open | `specs/colleague_onboarding.md` | now in WS-TS (WS-24b, WS-24c) |
| WS-25 | Deploy delivery path | 🟢 open | `specs/deploy_delivery_path.md` | now in WS-PL · WS-25d is in WS-CZ |
| WS-26 | CRM app: native CRM and Zoho retirement | ✅ done | `specs/crm_app.md` (the as-built record) | new work went to WS-53 · now in WS-CRM |
| WS-27 | Projects app: native PM | 🟢 open | `specs/project_management_app.md` §9.7 | now in WS-PT |
| WS-28 | People Center: directory, org chart, assignment | ✅ done | `specs/people_center_app.md` | now in WS-PP |
| WS-29 | Multi-tenancy: Metorite sold to other companies | 🟢 open | `specs/saas_multitenancy.md` · `specs/saas_multitenancy_handover.md` | now in WS-TS |
| WS-30 | Subscription Console | 🟢 open | `specs/subscription_console.md` | now in WS-CM |
| WS-31 | Customer Console: subscription, seats, AI metering | 🟢 open | `specs/customer_console.md` | now in WS-CM |
| WS-32 | Organization identity | 🟢 open | `specs/organization_identity.md` | now in WS-PP |
| WS-33 | Marketing site on the apex | 🟢 open | `specs/marketing_site.md` | now in WS-OB |
| WS-34 | Launch surface | 🟢 open | `specs/launch_surface.md` | now in WS-CM |
| WS-35 | Workspace identity | 🟢 open | `specs/saas_multitenancy.md` MT-1k | now in WS-TS |
| WS-36 | Per-tenant restore | 🟢 open | none yet | now in WS-TO |
| WS-37 | Trust and compliance | 🟢 open | none yet, written in Phase 1 (D119.4) | now in WS-TO |
| WS-38 | Development and delivery framework | 🟢 open | `specs/development_and_delivery_framework.md` | now in WS-PL |
| WS-39 | Projects, Tasks and Calendar: the re-plumbing | 🟢 open | `specs/project_management_app.md` §12 · `specs/my_tasks_cutover.md` | now in WS-PT |
| WS-40 | Content-app write authority | 🟢 open | `specs/org_access_control.md` §8d | D121 · now in WS-TS |
| WS-41 | Project import by file | ✅ done | `specs/project_import.md` | now in WS-PT |
| WS-42 | Projects settings | 🟢 open | `specs/projects_settings.md` | now in WS-PT |
| WS-43 | MAF coding engine and the sandbox terminal | 🟢 open | `specs/maf_coding_engine.md` | now in WS-CHAT |
| WS-44 | The shell | 🟢 open | `specs/navigation_shell.md` | now in WS-UX |
| WS-45 | AI tier routing | 🟢 open | `specs/ai_tier_routing.md` | now in WS-CHAT |
| WS-46 | Projects agent parity | 🟢 open | `specs/projects_agent_parity.md` | now in WS-PT |
| WS-47 | WhatsApp assistant channel | ⏸ parked | `specs/whatsapp_assistant_channel.md` | D115 · now in WS-WA |
| WS-48 | Data narrowing pipeline | 🟢 open | `specs/data_narrowing_pipeline.md` | now in WS-CHAT |
| WS-49 | Box hardening | 🟢 open | `specs/box_hardening.md` | now in WS-TO |
| WS-50 | Operator Console: money and the UI rebuild | 🟢 open | `specs/operator_console_money.md` | now in WS-CM |
| WS-51 | Chat run continuity | 🟢 open | `specs/chat_run_continuity.md` | now in WS-CHAT |
| WS-52 | How Metorite writes: one voice | 🟢 open | `specs/agent_writing_voice.md` | now in WS-CHAT |
| WS-53 | CRM: two modes and the Zoho mirror | 🟢 open | `specs/crm_platform.md` | now in WS-CRM |
| WS-54 | Integrations: one registry and a console | 🟢 open | `specs/integrations_console.md` | now in WS-BRIEF (connectors, epic 481) |
| WS-55 | External agent access over MCP | ✅ done | `specs/external_agent_access.md` | the follow-ups are in WS-MCP |
| WS-56 | External agent dispatch | 🟢 open | `specs/external_agent_dispatch.md` | no decision yet · now in WS-MCP |
| WS-57 | Task references | ✅ done | `specs/task_references.md` | now in WS-PT |
| WS-58 | My Day as a live feed | 🟢 open | `specs/my_day_feed.md` | now in WS-CHAT |
| WS-59 | Reach: Metorite speaks first | 🟢 open | `specs/proactive_reach.md` | now in WS-CHAT |
| WS-60 | Artifacts: one library | 🟢 open | `specs/artifact_management.md` | now in WS-CHAT |
| WS-61 | Knowledge | 🟢 open | `specs/knowledge_base.md` · `specs/knowledge_curation.md` | now in WS-KB |
| WS-62 | Chat first | 🟢 open | `specs/chat_first_surface.md` | now in WS-CHAT |
| WS-63 | Personalization | 🟢 open | `specs/personalization.md` | now in WS-CHAT |
| WS-64 | One Projects app | 🟢 open | `specs/one_projects_app.md` | now in WS-PT |
| WS-65 | Shared mailbox | 🟢 open | `specs/shared_mailbox.md` | now in WS-EM |
| WS-66 | Reminders and web push | 🟢 open | `specs/calendar_events_reminders.md` §5 | now in WS-CAL |
| WS-67 | One assistant: context, OCR, tool packs | 🟢 open | `specs/one_assistant_context.md` | now in WS-CHAT |

---

## 3. Decisions recorded

Resolutions for the cross-doc conflicts the audit surfaced. D1–D8, **D13**, **D14**,
**D16**, **D17** and **D43** are **proposed defaults, adopted unless the owner objects**
(`agent-proposed, owner may overrule`); D9, D10, D11, D12, D15, **D18** and **D32**
are owner calls, taken and dated; **D34 and D35** are owner calls of 2026-08-12 (D35
agent-recommended, owner-accepted). ⚠️ Two entries below are superseded and kept
as records: **D11** (re-taken by D15) and **D10 part 1's planning premise** (re-scoped
by D15/D16) — read their banners before citing either.

- **D70 — Google Workspace, not Entra. The `hd` hosted-domain claim replaces
  the Entra `tid` as directory check 1.** *(owner call, 2026-09-01. Owning spec:
  **`specs/operator_identity_and_access.md`**. Board: **WS-31**, ticket series
  **CP-12**. This decision **amends D64.1** and restates **D35.3**'s mechanism.)*

  **What forced it.** The owner answered a direct question on 2026-09-01.
  We do not have Entra, and staff can sign in with email or Google *"like we do
  in the app itself."* Asked whether `hathilabs.com` is a Google Workspace
  domain with an admin console, the owner answered **yes**. So the directory
  D35.3 and D64.1 named does not exist. A different one does, and we already
  pay for it.

  1. **Supabase Auth, with the Google Workspace provider, authenticates staff.**
     The `hd` hosted-domain claim replaces the Entra `tid` as check 1. The
     Console asserts that `hd` equals a new `OPERATOR_GOOGLE_HD` value.
     **D35.3's intent stands** — one directory, ours, admin-managed. Do not
     "fix" this to multi-directory. Only the mechanism moves, and D64.1's
     provider name moves with it.
  2. ⚠️ **Email OTP is refused for this console**, although the tenant app
     offers it. The tenant app carries a Resend 6-digit OTP, and its blast
     radius is one organization. The Operator Console reaches EVERY customer
     organization. Inbox control would then become staff access, with no
     directory, no offboarding, and nobody who can revoke.
  3. ⚠️ **The `hd` claim is load-bearing, and a domain match alone is not
     enough.** Google lets a person create an account on a non-Gmail address.
     Google verifies that address by mail. It then returns `email_verified:
     true` and NO `hd` claim. So a domain match alone admits anybody who
     receives mail at a staff domain. That includes a former employee's alias,
     a forward, a catch-all address, and a compromised mailbox. The `hd` claim
     appears only for an account the Workspace admin manages.
  4. **Checks 2 and 3 of D64.2 do not move.** The email domain stays in
     `OPERATOR_STAFF_DOMAINS`. The `operator` registry row stays the answer to
     *may you*. D64.3 to D64.6 are untouched.
  5. **The strength that replaces `tid` is the admin-managed account, not the
     domain string.** An Entra `tid` names a directory nobody outside it can
     present. An `hd` claim names a Workspace account an admin created and an
     admin can delete. Both give the same property, which is offboarding that
     works.

- **D64 — The Operator Console gets real staff identity: one directory, three
  roles, and no standing destructive privilege.** *(owner call, 2026-08-26,
  answering three questions put by an agent in session. Owning spec:
  **`specs/operator_identity_and_access.md`**. Board: **WS-31**, ticket series
  **CP-12**.)*

  **What forced it.** The console was deployed on 2026-08-22 and is gated by
  **one shared passphrase** (`OPERATOR_CONSOLE_STAFF_SECRET`). The cookie holds
  that passphrase itself, there are no roles, removing one person means changing
  the secret for everybody, and `control_audit.actor` records the literal string
  `operator` for every write. The measured findings are F1–F7 in the spec's §2.
  The posture was correct while the app was dark. It stopped being correct the
  day the app was deployed.

  1. ⛔ **AMENDED 2026-09-01 by D70. The provider is GOOGLE WORKSPACE, not
     Microsoft, and the `hd` claim replaces the Entra `tid` as check 1.** Read
     D70 before you cite this clause. Was: **Supabase Auth, with the Microsoft
     provider, authenticates staff.** This
     reconciles **D35.3** with **D34**, which nobody had done for four months.
     D35.3's *intent* is kept — one directory, ours, and never multi-directory.
     D35.3's *mechanism* is dropped: we do not stand up a second identity
     integration to say what the one we already pay for says.
  2. **Three checks admit an operator, and all three must pass** — the directory
     claim *(the `hd` hosted domain since D70, and the Entra tenant id before
     it)*, the email domain, and a row in a new `operator` registry. The
     directory answers *who are you*. The registry answers *may you*. This is
     **D34.4 applied to staff** rather than a new idea. ⚠️ Without check 3, every
     person our directory ever admits becomes a platform operator on first
     sign-in.
  3. **Three roles: `viewer`, `editor`, `admin`**, bound in a matrix to the
     Console routes that exist today (spec §5). `admin` is the only role that
     adds, re-roles or deactivates another operator.
  4. **No standing destructive privilege.** An `admin` holds the *right to
     elevate*, not the privilege. Elevation is time-boxed, needs a stated reason,
     and is logged. The shared operator token is kept as the **break-glass**
     path — alerted on every use, and rotated after each one. It is the only
     thing that bypasses the role system, and using it is an owner act.
  5. ⚠️ **Operators reach the commercial record only.** Plan, seats, credits,
     lifecycle, invoices. **No tenant content, and no impersonation, ever.** This
     is the single largest cut to the blast radius, and it is why the 2020
     Twitter failure mode is not reachable here. Widening it is a **new owner
     decision**, not a code change an agent may take.
  6. **Four-eyes approval and per-action step-up are DEFERRED, with triggers
     written down** (spec §9). Four-eyes needs a second admin to mean anything,
     and today it would only lock the owner out. ⚠️ The trigger is *"a second
     admin exists, or the first SOC 2 engagement starts"*, and it must not pass
     unnoticed — four-eyes is the control that still holds when every other one
     has failed.

- **D61 — The eight open AI-access questions, ANSWERED. Seven are agent calls taken under
  the `agent-proposed, owner may overrule` convention; one (G-1) was mis-classified as an
  owner call and is corrected here. §6A.10's register closes with this decision.**
  *(owner directive, 2026-08-26: "make all the obvious decisions for me and clearly explain
  the decisions and their consequences which I have to make so that we can close this
  documentation and start building." Owning spec: `specs/customer_console.md` §6A.9/§6A.10.
  Board: **WS-31**.)*

  **The point of this decision is to leave the owner a SHORT list.** Every question below
  had one defensible answer that follows from a decision already taken; taking them here
  removes them from the owner's queue rather than adding to it. The genuinely owner-owed
  items are collected in `work_plan.md` **§6.0**, and none of them blocks building.

  - **D61.1 — G-1: per-task, OpenAI-shaped Router endpoints, added one task at a time.
    ⚠️ And this was NOT an owner call — I mis-classified it.**
    ⚠️ **AN AMENDMENT IS PROPOSED, 2026-09-23, by D75, and the owner has not acknowledged it.**
    The `decide` task has no OpenAI shape to copy, so `POST /v1/decide` takes a Metorite
    shape. Until the owner acknowledges it (H-166), the text above stands unchanged. The Router is an
    **internal seam** (our gateway → our Console, on a `cc_depl_`/`cc_live_` credential we
    issue), not a public API, so its shape is not a commitment to anyone outside. The
    choice is also **lower-regret in both directions**: OpenAI's shapes are what litellm
    already implements *on both sides*, so following them costs nothing now and leaves the
    option of exposing the Router publicly later without a redesign. A generic
    `/v1/invoke` would have us inventing a protocol nothing speaks and making file upload
    awkward for audio.
    **Order: `transcribe` first, and only when a caller needs it.** It is the only
    non-chat task that already ships. **An endpoint nobody calls is CP-4's mistake
    repeated** — that is the whole lesson of the first-caller ticket (D57.3).
  - **D61.2 — G-2: widen `acb_stt`'s provider pattern; do not author a second dispatch
    abstraction.** `SttProvider` + `resolve_stt_provider` already implement D60's
    `(model, task) → invocation` for one task, including the case that broke the naive
    design (AssemblyAI's submit-then-poll API, which *"can't be expressed as a LiteLLM
    `atranscription` call"*). The package generalises to `ProviderHandler` +
    `resolve_handler(model, task)`; **litellm verbs become one family of handler, native
    providers another.** A second dispatch seam beside this one is the CLAUDE.md §5 defect.
  - **D61.3 — G-3: the CALLER declares the task. The Router never sniffs the payload.**
    D32.7 already refuses inference in this exact area — *"a bare model id is rejected
    400, not coerced; silent coercion hides a misconfigured agent behind a bill."* Payload
    sniffing is that same inference wearing a helpful face, and it would make the model a
    call is routed to depend on the *content* of the message, which is unpredictable for
    the caller and unexplainable in a bill.
    **Consequence, stated plainly: a chat call carrying an image and NOT declaring
    `vision` is routed by `(chat, tier)` and may be refused by the provider.** That is the
    intended behaviour — a loud failure the caller can fix, rather than a silent
    reroute. ⚠️ The tier *alias* carries the task (D60.4), so **existing call sites do not
    change**; only a caller that wants a non-default task states one.
  - **D61.4 — G-4: the rate card gains `pricing_mode` ∈ {`unpriced`, `absorbed`,
    `priced`}, and `absorbed` is what D19.2 means.** A zero cannot carry three meanings.
    Today `rate_call` raises `UnpricedModel` on a zero card **by design** (*"refusing to
    bill it as free"*), which is right for *not priced yet* and **wrong for embeddings**,
    which D19.2 deliberately absorbs into the package price — an absorbed task hitting
    that path would **refuse the customer's call**.
    So: **`unpriced`** raises (today's behaviour, and the seeded ladder stays here, so
    `test_the_rate_card_ships_unpriced` is unaffected); **`absorbed`** rates to zero
    *deliberately* and still writes the `usage_event`, because we want the volume even
    when we do not charge for it; **`priced`** uses the rates. **The distinction is
    explicit rather than encoded in a magic zero**, which is the whole point.
  - **D61.5 — G-5: tasks are an ALLOWLIST the Router publishes; tiers stay free text.**
    Asymmetric on purpose. A **tier** is a label with no behaviour, so a new one is an
    INSERT (D59.1) — that flexibility is the design working. A **task** binds to a handler
    the Router must already know (D61.2), so free-text tasks let an operator create a
    binding that looks configured and 500s. The allowlist rides the tier-capabilities
    document **CP-5 already owes**, so the operator UI can only offer what can be served.
  - **D61.6 — G-6: BYOK is per PROVIDER, therefore per task-as-served, and a
    partially-BYOK organization is a NORMAL state.** `provider_credential` already prefers
    an org's own row per provider (`router.py:165`), so the mechanism exists; this fixes
    the billing reading. An org is **not charged for tokens on any call served by its own
    credential**, and is **charged normally on calls served by ours** — so an org that
    brings an OpenAI key but takes images from our account is billed for the images and
    not for the chat. **No new table, no new flag**: what is billed follows from which
    credential served the call, which `usage_event` can already record.
  - **D61.7 — G-7: `model_capability` gains `deprecated_at`, surfaced in the operator
    catalog.** Vendors retire models; `effective_from` re-points forward cleanly but
    nothing tells an operator that the model behind `(chat, balanced)` is on its way out.
    A nullable timestamp plus a badge is the whole change. It **warns, never refuses** — a
    deprecated model that still answers is still serving customers, and failing their
    calls because of a date in our catalog would be us causing the outage.
  - **D61.8 — G-8: per-organization rate limiting is DEFERRED, with a named trigger.**
    The credit balance bounds *spend*, not *rate*, so one organization can still saturate
    a vendor quota. This is deliberately not designed now: **the right ceiling is
    unknowable before real traffic**, and a guessed one either throttles a paying customer
    or does nothing.
    **Trigger, so this is a decision and not an omission:** the first time a single
    organization's burn threatens a quota recorded in `provider_subscription` (§6A.3), or
    the first vendor 429 we serve onward to a customer. `provider_subscription` is what
    makes that observable — which is why it is in CP-10 at all.

- **D60 — The AI access model, final: a call names a `(task, tier)` pair; the operator
  binds that pair to a model; and HOW a model is invoked for a task is a property of the
  MODEL, not of the task. ⚠️ This SUPERSEDES D59.2 and D59.3 — "modality on the tier" does
  not survive a multimodal model.** *(owner directive, 2026-08-26: "in the future we might
  have a model that is actually multimodal, in the case in which we might be using one
  model to do multiple things like text-to-speech, image generation, as well as the raw
  performance. I want to ensure that the system we are building for AI access is
  future-proof and robust." Owning spec: **`specs/customer_console.md`** — **CP-10**,
  design in **§6A.9**. Board: **WS-31**.)*

  **The correction first, because it is mine and it matters.** D59.3 put a `modality`
  column on the **tier** and argued for two axes. That is right about there being two
  axes and **wrong about where modality lives.** If `tier-balanced` binds to a model that
  does text *and* vision *and* image generation, a single `modality` value on that tier
  cannot express it — and the design would need a special case for exactly the future the
  owner is asking about. **A design that needs a special case for "one model, many jobs"
  is the wrong design**, and multimodal is not an edge case any more; it is where the
  vendors are going.

  - **D60.1 — three concepts, separated once, and never conflated again.**

    | Concept | The question it answers | Belongs to |
    |---|---|---|
    | **Task** | *what is being asked* — `chat` · `vision` · `transcribe` · `speak` · `image` · `embed` | the **call** |
    | **Tier** | *which performance/cost rung* — `fast` · `balanced` · `powerful` | the **choice** |
    | **Capability** | *what a model can do, and how it is invoked for that* | the **model** |

    Today all three are tangled in one flat string: `tier-fast` is a tier, `tier-stt` is a
    task, and the invocation verb is a Python `frozenset` (`client.py:95`).
  - **D60.2 — resolution is TWO steps, and the second is the future-proofing.**
    **(1) `(task, tier) → model`** — `tier_binding`, effective-dated, gains a `task`
    column. **(2) `(model, task) → invocation`** — a new `model_capability` row carrying
    the litellm verb.
    ⚠️ **The invocation belongs to `(model, task)` and NOT to the task alone, and this is
    the clause that survives contact with the future.** A multimodal model may expose
    image generation through its **chat** endpoint while another provider exposes the same
    task through a dedicated images endpoint. Same task, different verb, **depending on
    the model.** Any design that derives the verb from the task alone breaks the first
    time two providers disagree — which they already do.
  - **D60.3 — multimodal then needs NO special case, and that is the test of the design.**
    One model appears in several capability rows and several bindings:
    `('openai/gpt-4o','chat','acompletion')`, `('openai/gpt-4o','image','aimage_generation')`,
    `('openai/gpt-4o','speak','aspeech')` — and `('chat','balanced','openai/gpt-4o')`,
    `('image','balanced','openai/gpt-4o')`. **Nothing in the schema knows or cares that it
    is the same model.** If a future change needs a "multimodal" flag, the design has
    already failed; there is no such flag here on purpose.
  - **D60.4 — the flat slugs SURVIVE as aliases, which is what makes this affordable.**
    `tier-fast` → `(chat, fast)`; `tier-stt` → `(transcribe, ·)`. **The 80+ hardcoded call
    sites do not change** (D59.4). ✅ And the alias table already exists — `_TIER_ALIAS_MAP`
    (`acb_llm/client.py:86`) is exactly this mapping, in Python; it moves to data and
    stops being a mirror that *"must stay in sync with `v1_compat.py._TIER_NAME_TO_ID`"*,
    which is a synchronisation hazard the file already documents against itself.
  - **D60.5 — 🔴 PRICING IS THE REAL GAP, IT IS LIVE TODAY, AND MULTIMODAL ONLY MAKES IT
    ACUTE.** Measured 2026-08-26: `credits.rate_call` is **tokens-only** — it divides
    `fresh_prompt_tokens` / `cached_tokens` / `completion_tokens` by 1000 against per-1k
    rates and knows no other unit. `model_rate_card` has only those three columns, and
    `usage_event` records only those three counters.
    **So `tier-stt` — which ships in production seed today — cannot be priced**, even
    though D19.2 says *"LLM + **per-minute STT** metered"*. Image generation (per image)
    and TTS (per character or per second) are equally unpriceable. **This is a
    pre-existing hole that predates the multimodal question and would have surfaced the
    day anyone priced the card.**
    **The fix, and note where the key goes:** `model_rate_card` keys on
    **`(model, task, effective_from)`** — because the same multimodal model costs a
    different amount for chat than for images, so pricing cannot key on the model alone —
    and carries a **`unit`** ∈ `{token_1k, second, character, image}`. `usage_event` gains
    `task`, `quantity` and `unit` (nullable, R6-safe); the token columns stay, because
    chat is the common case and they are indexed and load-bearing.
  - **D60.6 — degradation stays WITHIN a task.** D32.8's default on credit exhaustion is
    *"degrade to `tier-fast`"*. Under `(task, tier)` that must mean
    `(image, powerful) → (image, fast)` — **never** `(image, powerful) → (chat, fast)`,
    which would answer an image request with a paragraph. If a task has no cheaper rung,
    the call is **refused**, not silently re-tasked.
  - **D60.7 — an unbound task is a 400, never a coercion.** D32.7 already rules that a
    bare model id is *"rejected 400, not coerced — silent coercion hides a misconfigured
    agent behind a bill"*. The same rule extends: asking for a task with no capable
    binding at that tier is a **refusal**. Quietly serving an image request from a text
    model is that defect wearing different clothes.
  - **D60.8 — capability is NOT availability, and keeping them apart is the operator's
    whole value.** `model_capability` says what a model **can** do; `tier_binding` says
    what we **use it for**. Two tables, because the interesting operator question is
    exactly the gap between them — *"gpt-4o can generate images and we have not bound it
    to anything"* is a sentence only a schema that separates them can produce.
  - **D60.9 — streaming is a property of `(model, task)` too**, and belongs on the
    capability row rather than being assumed. Only `chat` and `speak` stream; the Router's
    pass-through (**CP-4b**, minted and unbuilt) is what consumes this, and §6B.5 already
    warns that silently de-streaming a chat UI is a behaviour change nobody will attribute
    to the flag.
  - **D60.11 — ⚠️ TWO CORRECTIONS TO THIS DECISION, found by auditing it against the tree
    the same day it was taken** (owner asked *"is anything still undecided?"*; the honest
    register is `customer_console.md` **§6A.10**).
    **(a) `invocation` is NOT merely a litellm verb (G-2).** `packages/acb_stt/` exists
    because **AssemblyAI's batch API is submit-then-poll and, in its own words, "can't be
    expressed as a LiteLLM `atranscription` call"** — it is reached by a native provider
    chosen by model prefix. So the column names a **handler**, of which litellm verbs are
    one family. ✅ **And this is a reuse finding: `acb_stt`'s `SttProvider` +
    `resolve_stt_provider` already implements D60's step 2 for one task.** Widen that
    package's pattern; do not author a second dispatch abstraction beside it.
    **(b) The Router serves ONE task (G-1).** `POST /v1/chat/completions` is its entire
    `/v1` surface — no transcription, image or speech endpoint exists. So this decision's
    catalog can *describe* six tasks while the Router can *serve* one. **It bounds how
    much of the multimodal promise is reachable; it blocks neither CP-10 slice 1 nor
    CP-11**, because chat is 96 of the 110 measured call sites. The endpoint-shape fork
    (per-task OpenAI-shaped routes vs one generic `/v1/invoke`) is in §6A.10 with a
    recommendation, and is an owner call.
    📌 Three smaller open items go with them: **`vision` needs a declarer** (caller or
    payload sniff — G-3), **`embed` must be rated *free by decision* rather than
    *unpriced*** or it contradicts D19.2's absorption (G-4), and **tasks must be an
    allowlist** the Router publishes, unlike tiers which stay free text (G-5).
  - **D60.10 — what does NOT change, stated so the blast radius is honest.** The wire
    vocabulary (callers still name a tier slug) · D32.7 (customers never see a **model**)
    · D58 (litellm SDK, credentials per call, no Proxy — the verb table sits behind
    `set_provider_call()`, so this stays one seam) · the INSERT-only write contract
    (§6A.5) · `test_the_rate_card_ships_unpriced` (this decision builds the mechanism to
    price correctly and prices nothing) · and D59.4/D59.5/D59.6 — the feature→tier
    registry, customer-visible tiers and the build order all stand, re-expressed over
    `(task, tier)` instead of over a flat slug.

- **D59 — The tier catalog gets TWO axes: modality × performance. Modality becomes DATA,
  not a Python frozenset** ⚠️ **PARTLY SUPERSEDED THE SAME DAY BY D60 — read D60 first.**
  *D59.2's and D59.3's placement of `modality` on the TIER does not survive a multimodal
  model and is replaced by D60's `(task, tier) → model` + `(model, task) → invocation`.
  **D59.1, D59.4, D59.5 and D59.6 all stand** — the indirection needs no schema change,
  feature→tier binding is hardcoded at 80+ sites, customer-visible tiers are allowed, and
  the build order holds — re-expressed over `(task, tier)` instead of a flat slug. Kept
  unedited below because the reasoning that got to "two axes" is the reusable part; only
  the placement was wrong.* — original text:* — and that one column is what makes image generation, TTS and
  vision possible without a code change per modality.** *(owner directive, 2026-08-26:
  "we still should be able to configure tiers and models and have different models for
  different kinds of functionality, like image generation, text-to-speech, speech-to-text,
  and different levels of performance… different features could use the different model
  tiers… customers' chat app might be able to see these tiers depending on the kind of
  performance that they are looking at." Owning spec: **`specs/customer_console.md`** —
  **CP-10**, scope extended; detail in **§6A.9**. Board: **WS-31**.)*

  **Short answer: yes, and D32.7 predicted it** — *"adding `tier-vision` / `tier-embed` /
  `tier-voice` later is a data change, so this decision deliberately does not decide
  them."* This decision decides them. Measured 2026-08-26, two of the three pieces already
  exist and the third is the real work.

  - **D59.1 — the indirection already supports it, with no schema change.**
    `tier_binding.tier` is plain `TEXT` with **no `CHECK`, no enum and no foreign key**
    (`001_customer_console.sql:231`). Adding `tier-image` or `tier-tts` is an **INSERT**.
    ✅ And one modality tier is *already in production seed*: `tier-stt` →
    `groq/whisper-large-v3-turbo` (`002_seed_catalog.sql:76`).
  - **D59.2 — 🔴 but the MODALITY is hardcoded in Python, and that is the actual blocker.**
    `acb_llm/client.py:95` holds `_STT_TIER_IDS: frozenset({"stt"})` — *"Kept separate so
    chat-only call sites can ignore them"* — and the litellm **verb** is chosen from it:
    `atranscription` for STT, `acompletion` for everything else. Measured verb usage
    across the tree: **`acompletion` ×96, `atranscription` ×11, `aembedding` ×3, and no
    image or speech verb at all.** So today, adding `tier-image` inserts a row that
    **every caller would send to `acompletion`** and the provider would reject — a data
    change that silently does not work.
    **The fix is one column on the tier catalog: `modality`** ∈ `{text, vision, stt, tts,
    image, embed}`, from which the Router picks the verb. That is what turns *"add a
    modality"* from a code change into the data change D32.7 promised.
  - **D59.3 — TWO AXES, TWO COLUMNS — not a naming convention.** The namespace is flat
    today and already mixes them: `tier-fast` / `tier-balanced` / `tier-powerful` are a
    **performance** ladder, `tier-stt` is a **modality**. Left flat, the fourth modality
    forces names like `tier-image-fast`, which is a **convention that has to be parsed**,
    and parsers drift. So the catalog carries `modality` **and** `performance`
    (`fast|balanced|powerful`, nullable — an STT tier has no meaningful performance rung
    until we offer two). The tier *slug* stays the wire vocabulary and stays opaque to
    callers; the two columns are how the Router and the operator reason about it.
  - **D59.4 — 🔴 "different features use different tiers" is TRUE TODAY, and it is
    hardcoded.** Measured: tier strings are string literals at **80+ call sites across 12+
    files**, frontend included — `assistant.py` ×21, `settings.py` ×8, `_common.py` ×8,
    `drafting.py` ×7, `tasks/ai.py` ×6, `notes/summaries.py` ×5, and four in
    `taskStore.ts`. **So changing which tier the email digest uses is a code change and a
    deploy, not an operator action.** If the operator is to bind features to tiers, that
    needs a **feature→tier registry**, and it is a large change — deliberately phased
    behind D59.2 (see D59.6).
    📌 **There is already a precedent to reuse rather than invent:** the Apps feature
    declares its tier in its **manifest** (`ai:tier-1` scope → `_SCOPE_TIER_MAP` →
    `tier-fast`, `apps/_common.py:66-79`) with a documented fallback to the cheapest
    alias. That is a feature→tier binding that is already declarative. **Generalise it;
    do not author a second vocabulary.**
  - **D59.5 — ✅ customers MAY see tiers, and this does not contradict D32.7.** D32.7 says
    *"customers never see a **model**"* and makes **tiers the only vocabulary outside the
    Router** — so a customer-facing *performance* choice (Fast / Balanced / Powerful) is
    not an exception to that rule, it is the rule working as designed. The tier is a
    stable promise; the model behind it is ours to change.
    ⚠️ **Two consequences to design in from the start, not retrofit.**
    **(a) Commercial:** a customer who can choose `tier-powerful` can choose our most
    expensive model, so tier choice must draw credits at that tier's rate — which the
    existing `model_rate_card` already does per **model**, so nothing new is needed
    except *not* flat-rating tiers. **(b) Entitlement:** whether a plan may reach
    `tier-powerful` is an **entitlement** question, and the `intersect()` seam plus the
    402-vs-403 partition already answer that shape (`saas_multitenancy.md` §2.3). D49
    made the module intersect trivially total for a seated member; **tier access is where
    a flat plan may still legitimately differentiate later**, and keeping the seam is why
    that stays possible.
  - **D59.6 — order, because these are very different sizes.**
    **(1) `modality` column + Router verb selection** — small, unblocks image/TTS/vision
    as data, and is the only part CP-10 needs. **(2) `performance` column + the operator
    tier-catalog UI** — small, and it is what the owner asked to *configure*.
    **(3) The feature→tier registry** — large (80+ sites), generalises the Apps manifest
    precedent, and is **its own ticket, not a CP-10 slice.** **(4) Customer-visible tier
    choice in chat** — last, because it is meaningless until (1)–(3) make the tiers real
    and until entitlement decides who may pick what.
    ⚠️ **Do not let (4) lead.** A picker over tiers whose modality is a frozenset and
    whose feature bindings are literals would look finished and change nothing.

- **D58 — The AI calling architecture: the litellm PYTHON SDK, credentials passed PER
  CALL, and no LiteLLM Proxy. The Console Router is our proxy.** *(owner question
  2026-08-26 — "do we use full-blown LiteLLM, or stick to the Python bindings? What
  exactly is the issue?"; agent-recommended, owner-accepted. Owning spec:
  `specs/customer_console.md` §4.)*

  **The question decomposed into a different one on measurement.** We have never run a
  LiteLLM Proxy — `infra/docker-compose.yml:92` says *"LLM routing — handled by litellm
  Python SDK directly (no proxy container)"*. And we already built our own proxy: the
  Console Router (D32.1). So the real finding is that **the same SDK is called two
  different ways, and only one of them is tenant-safe.**

  - **D58.1 — the measured difference, which is the whole issue.** The **Console Router**
    passes credentials **per call** (`main.py`'s `call_kwargs["api_key"] = secret`) —
    no shared state, tenant-safe by construction. The **tenant gateway** goes through
    `_ensure_keys_loaded()` → `key_store.configure_litellm()`, a **once-per-process latch**
    assigning `litellm.<provider>_api_key` module attributes **and `os.environ[...]`**.
    That is the §6 (f) blocker, and `client.py:262`'s own docstring states the
    consequence: *"Calling it per organization would not scope anything; it would make the
    LAST organization's key the one every caller sends."*
    **This is a property of how we call the SDK, not of the SDK.** A proxy would fix it by
    accident — by moving the call out of our process — which is an expensive way to buy a
    keyword argument.
  - **D58.2 — standardise on the Router's convention: credentials per call, never
    global.** The fix for §6 (f) is the shape `router.py` already uses. No new
    infrastructure.
  - **D58.3 — do NOT adopt LiteLLM Proxy as the billing or entitlement plane.** Its
    headline features — virtual keys, team budgets, spend logs, rate limits — **duplicate
    `llm_api_key`, `credit_ledger`, `usage_event`, `model_rate_card` and
    `_spend_refusal`**, and ours are the *product*: ₹10 credits, provider cost × 2, GST
    invoices, an append-only ledger a customer can dispute. Adopting it means running two
    billing systems or migrating our margin logic onto someone else's schema. It would
    also add a third hop (agent → gateway → Router → Proxy → vendor) and a second home for
    keys and models — the CLAUDE.md §5 second-implementation defect at infrastructure
    scale. **What the SDK genuinely earns and we keep buying:** normalising ~100
    providers' request, response and usage shapes.
  - **D58.4 — three sources of tier→model truth collapse to one.** `tier_binding`
    (Console) is the store of record. ⚠️ `infra/litellm/config.yaml` is **proxy-format
    config that no proxy reads** — our own code parses it at import time
    (`acb_llm/client.py:144`, `routes/settings.py:70`) — and `model_config.tier_overrides`
    is the third. The file becomes a seed or is deleted; `tier_overrides` retires with
    CP-10 slice 6.
  - **D58.5 — the vocabulary bleed is evidence, and it is worth naming once.**
    `LITELLM_MASTER_KEY` is a proxy-ism that leaked into our auth and is now
    **byte-identical to `GATEWAY_INTERNAL_TOKEN`** on the box (§6, measured 2026-08-05).
    Half-adopting a system's naming without its architecture is how that happens; the
    split is already an owner gate.
  - **D58.6 — the named trigger that would REVERSE this, so it is not re-argued
    from scratch.** LiteLLM Proxy earns its place if we want **cross-provider fallbacks,
    load-balancing across multiple keys, or semantic caching** and do not want to write
    them. At that point it goes **BELOW the Router** — `Router → Proxy → vendor`, as a
    supplier — **never above it, and never holding the budget.**
    📌 **The swap is already cheap by design and that is the main reason to leave this
    alone:** `router.py` has `set_provider_call()` (*"Tests use this; production never
    does"*), so re-pointing `_litellm_call` at a proxy endpoint is a one-function change.
    The current architecture is positioned for that decision without pre-committing to it.

- **D57 — "AI features work" means the tenant app SERVES them through the Console's
  configured models. The serving hop is the missing link, and it gets the ticket that
  `work_plan.md` §6 (d) has been waiting for since 2026-08-18.** *(owner directive,
  2026-08-26: "The next main task is assigning AI credits and managing AI API keys
  through the Operator Console. Ensure all documentation is ready so the app can sync
  with the console and access AI features via the configured models." Owning spec:
  **`specs/customer_console.md`** — **CP-11**, minted with this decision; detail in its
  **§6B**. Board: **WS-31**.)*

  **The measurement that shapes this decision, taken before it was written.** Of the three
  things named, **one is already built, one is ticketed, and the third had no ticket at
  all** — and the third is the one that makes the other two matter.

  - **D57.1 — ✅ Assigning AI credits through the Operator Console ALREADY WORKS, end to
    end. Do not re-build it.** Measured 2026-08-26: `Actions.tsx::CreditsPanel` (`:354`)
    → `POST /api/operator/credits` → `lib/console.ts::grantCredits` → Console
    `POST /credits/grant` (`main.py:2608`) → an append-only `credit_ledger` row. The
    customer page already renders `org.credit_balance` (`page.tsx:229`), and
    `ActivatePanel` can include an opening credit grant at activation. `GET
    /credits/balance` exists. **An agent asked to "build credit assignment" must read
    this clause and stop.** What is genuinely missing around credits is narrower and is
    named in D57.4.
  - **D57.2 — 🔴 "AI API keys" is TWO credentials, and conflating them is the trap this
    clause exists to stop.** They live in different tables, have different blast radii,
    and only one of them is CP-10's.
    **(a) Provider API keys — ours.** Our DeepSeek / Anthropic / Groq account secrets,
    Console table `provider_credential`. **Zero writers today** (D56.2), which is why
    the Router cannot call a provider at all. Ticketed: **CP-10 slice 1**.
    **(b) Organization LLM keys — theirs.** The `cc_live_…` token a deployment presents
    to call our Router, Console table `llm_api_key`, minted by `POST /keys`
    (`main.py:2645`, `Operator`-gated, **returned exactly once**). The route exists;
    ⚠️ **the Operator Console has no UI for it** — `lib/console.ts` has no `issueKey`
    caller and there is no keys page. Ticketed here: **CP-11 slice 1**.
    ⚠️ **The naming precedent is already recorded and must be kept:**
    `packages/acb_common/acb_common/settings.py:163-171` argues at length why
    `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` (`cc_depl_`, capability `{resolve}`) and
    `CUSTOMER_CONSOLE_ORG_KEY` (`cc_live_`, org-scoped, read-only) are deliberately
    **different names for different credentials** — *"reusing one name for two
    credentials is how a box presents the wrong one and gets a 401 nobody can explain."*
    A third credential enters that same file and follows that same rule.
  - **D57.3 — 🔴 THE FINDING: the tenant app does not call the Console Router, and nothing
    was ever ticketed to make it.** `apps/services/gateway/gateway/routes/v1_compat.py`
    serves `/v1/chat/completions` and its own header says it *"routes through the litellm
    SDK directly (no proxy)"*. Repo-wide, `CUSTOMER_CONSOLE_URL` is read only by
    `console_resolve.py` (sign-in), `seats.py` and `signin.py` — **never for an LLM call**.
    So **every model, tier binding, rate card and provider key configured in the Operator
    Console is inert as far as the product is concerned.** `work_plan.md` §6 (d) recorded
    the shape of this on 2026-08-18 — *"the flag named in CP-4's done-when does not exist
    in code; CP-4 ships dark by having no caller, and this gate binds the first caller
    ticket, which is where the flag arrives"* — and **the first-caller ticket was never
    minted.** It is **CP-11**, and the flag arrives with it.
  - **D57.4 — what "AI features work" means, stated as acceptance so it cannot be
    declared done early.** All five, in this order:
    1. An operator installs a provider key through a route (**CP-10 s1**).
    2. An operator issues an organization key through the Operator Console (**CP-11 s1**).
    3. A tenant deployment holding that key serves a real completion **through the Router**,
       against the tier binding the operator set — verified by the call appearing in
       `usage_event` with that organization's id (**CP-11 s2/s3**).
    4. Re-pointing `tier-balanced` in the Console changes what that deployment gets on its
       next call, **with no tenant deploy** (this is CP-5's clause, and it is the whole
       value of the indirection).
    5. Granted credits are **drawn down** by that traffic and a zero-balance org is refused
       402 — which needs the rate card **priced** and `CUSTOMER_CONSOLE_SPEND_GATE` **on**,
       both 🔴 owner acts (D19.2, §6). **Until then the ledger records grants and nothing
       consumes them, and that is the honest state to report.**
  - **D57.5 — ordering: CP-10 slice 1 → CP-11 → the rest of CP-10.** CP-11 cannot be
    proven without a provider key to call with, so slice 1 of CP-10 comes first; but the
    remaining CP-10 slices (the UI move, the tenant-side deletions) are **not** on
    CP-11's critical path and must not delay it. ⚠️ **Do not delete
    `v1_compat.py`'s direct path when CP-11 lands** — it is the BYOK and
    Console-unreachable fallback, and R6 forbids removing the old path in the release
    that adds the new one.
  - **D57.6 — an unexpected dividend, recorded because it changes an owner-gated item.**
    §6 (f) records that `/v1/chat/completions` **cannot be tenant-threaded**:
    `require_llm_api_auth` is a pure token check binding no identity, so
    `current_tenant()` is `None` by construction. **Routing outward through the Console
    inverts that**: the `cc_live_` key IS the organization identity, resolved
    Console-side by `KeyCaller`, so a Router-served call carries a tenant where the local
    path cannot. This does **not** close §6 (f) — the local path and
    `configure_litellm`'s process-global injection both remain — but it means the
    *served* path stops being the one that cannot name its tenant. Recorded so nobody
    reads the two facts as contradictory.
  - **D57.7 — failure semantics are decided here, not at 2 a.m.** The Console is a
    **supplier**, and a supplier outage must not become a silent quality change.
    A Router call that fails because the Console is unreachable **fails the call** with a
    clear error; it does **not** fall back to the local litellm path, because a silent
    fallback would serve traffic on tenant-local keys at tenant-local models, unmetered,
    and nobody would learn it happened. ⚠️ **The one exception is BYOK and it is not a
    fallback** — a BYOK org is *routed* locally by configuration, deliberately, not by
    a failure. `saas_multitenancy.md` §3.3 already says failure semantics get decided in
    advance; this is that clause applied to the hop CP-11 adds.

- **D56 — AI model management is an OPERATOR capability. The customer never chooses a
  model, and the operator console is where models, provider accounts, provider API keys
  and their subscriptions are managed.** *(owner call, 2026-08-26. Verbatim intent: "we
  need to move the management of the AI models to the operator console. The end user does
  not manage the models that they use and just gets the one that we give them. On the
  operator console, we are supposed to be managing the models, the subscriptions, and the
  API keys of all the models that we are using for our application… This should be the
  next thing that we are working on so that we can deliver the AI-related features."
  Owning spec: **`specs/customer_console.md`** — **CP-5** (the removal half, already
  specced and unbuilt) + **CP-10** (the operator management plane, minted with this
  decision). Board: **WS-31**.)*

  **This does not re-open D32.7 — it executes it and finishes it.** D32.7 already ruled
  *"customers never see a model"* and *"the customer-facing provider/model/tier picker is
  removed from the product"* on 2026-08-12. Fourteen days later the picker is still in
  the product and the operator has no surface at all. D56 says where the capability
  lands, and makes it the next thing built.

  - **D56.1 — the surface already exists, on the WRONG SIDE of the tenancy boundary.
    Move it; do not build a second one.** Measured 2026-08-26:
    `workbench/control_plane/src/app/settings/models/page.tsx` is a **complete three-tab
    model-operations console** — *Providers* (with API-key entry), *Models* (enable /
    disable per provider), *Tiers* (edit the model bound to each tier) — sitting inside
    the **customer's** application, hidden from navigation only by D49's `preview` status.
    The **operator** console (`workbench/operator_console/`) has `activate · catalog ·
    credits · lifecycle · orgs · provision · purge · seats · session` and **no model
    surface whatever**. So the work is a **relocation across the boundary**, not a build:
    re-point that UI at the Console's own tables and delete the tenant-side copy per CP-5.
    Authoring a fresh operator UI while this one exists is the CLAUDE.md §5
    second-implementation defect.
  - **D56.2 — 🔴 THE FINDING THAT MAKES THIS URGENT: `provider_credential` has ZERO
    writers, so the Router cannot call any provider on the platform's account.**
    `customer_console/router.py:165 provider_credential()` SELECTs from a table that
    **no migration seeds, no route writes and no script populates** (measured repo-wide,
    2026-08-26). On a fresh Console database it returns `None` every time. The AI product
    therefore cannot serve a customer on our account **at all** — not because metering is
    unpriced, but because there is no way to put a key in. This is why the owner's
    sequencing is right: **CP-10 is not a convenience surface, it is the missing half of
    CP-4's Router.**
  - **D56.3 — the Console's tables are the store of record, and there are four objects,
    not three.** `tier_binding` (tier → model, effective-dated), `model_rate_card`
    (per-model price, effective-dated) and `provider_credential` (our accounts; a non-NULL
    `organization_id` means BYOK) already exist in `infra/customer_console/001` and `004`.
    ⚠️ **The fourth has no table and is new work: the provider SUBSCRIPTION** — which
    vendor plan we hold, its quota and rate limits, its spend to date and its renewal.
    The owner asked for *"the subscriptions… of all the models we are using"* and nothing
    in the corpus models it. Named here so it is not silently dropped into "keys".
  - **D56.4 — effective-dating is the write contract, and it is already the shape.**
    `resolve_tier` and `resolve_rate_card` both take *the newest row whose `effective_from`
    has passed*. So **every operator write is an INSERT with a future or present
    `effective_from`, never an UPDATE** — re-pointing a tier and re-pricing a model are
    both appends, and a past invoice is never recomputed. CP-10 must not add an UPDATE
    path; that would make billing history mutable, which is the same defect as a balance
    column (CP-6).
  - **D56.5 — what retires on the tenant side, under R6.** `model_config` (migration 35)
    and the `/api/settings/llm/*` write routes stop being written, then are dropped a
    release later.
    ⚠️ **Get the reason right, because the obvious one is out of date.**
    `saas_operations_doctrine.md` §4 finding **7** called these *deployment-singleton*
    config, and **MT-0d fixed that** — migration **158** re-keyed `provider_keys`,
    `model_config`, `mcp_servers` and `plugins` onto `(organization_id, …)` on
    2026-08-08. (`schema.generated.sql` still shows the pre-158 shape; it is a snapshot,
    not the ladder.) So the defect is **not** a singleton any more. It is the opposite
    and it is worse: **per-organization state holding what is a single platform decision
    that is the same for every tenant.** Two consequences follow, and both are the
    argument for this refactor — the operator would have to configure models **N times,
    once per tenant**, and each tenant **can** configure them, which is exactly what
    D32.7 forbids. The fix is not to re-scope the tables; it is that these rows should
    not exist on the tenant side at all. **BYOK survives** as the one case where an organization legitimately holds a
    provider key (§3.4, *"BYOK is a tier, not an exception"*), and it is served by
    `provider_credential` with a non-NULL `organization_id` — **on our side of the
    boundary, not on the customer's box.**
  - **D56.6 — this closes four recorded items at once**, which is the argument for doing
    it now rather than later: **CP-5** (specced 2026-08-12, never built); ops-doctrine
    finding **7**'s *successor* (per-tenant copies of one platform decision — D56.5);
    finding **10** (*"a
    customer-facing model picker fixes your provider cost to the customer's choice and
    gives away the margin lever"* — marked *already overridden by D32.7* and never
    executed); and it shrinks the blast radius of **§6 (f)**, because a tenant deployment
    that no longer stores platform provider keys is a deployment where
    `configure_litellm`'s process-global injection is no longer a cross-tenant credential
    write for platform-served orgs.
  - **D56.7 — priority: NEXT, ahead of the remaining WS-39 tail.** The owner named it.
    §2.0 M2.9 is re-ordered accordingly. ⚠️ **It does not jump M1** — CP-10 writes the
    *Console* plane, which is cross-tenant by design and unaffected by the tenant RLS
    promotion, so the two proceed in parallel and neither blocks the other.
  - **D56.8 — the gates do not move.** Issuing or installing a **real** provider API key
    against the live Console remains the credential class of §6 **(e)/(f)**; **pricing the
    rate card on a live system remains D19.2 / §6**, and `test_the_rate_card_ships_unpriced`
    still refuses a priced ladder. CP-10 builds the *mechanism* and is AGENT-SAFE against
    fixtures and scratch databases; putting our real DeepSeek/Anthropic/Groq keys in, and
    setting a real price, stay the owner's acts.

- **D55 — The environment ladder and the promotion model: direct-to-production until
  customer #2, then development → staging → production, with features promoted by FLAG
  and code promoted by SHA.** *(owner call, 2026-08-26. Verbatim intent: "Initially,
  before the first customers, we will work directly in production. After launch, follow
  this workflow: stage changes in development, develop and test there, promote features
  to production individually… If needed, use three environments: development, staging,
  production, where new features flow from development → staging → production." Owning
  spec: **`specs/development_and_delivery_framework.md`**, which moves from 🟠 PROPOSED
  to **ADOPTED** with this decision. Board: **WS-38**, minted here.)*

  This decision **answers D-A, D-B, D-C, D-E and D-G** of that spec's §9. **D-D (where
  staging runs and what it costs) and D-F (the CODEOWNERS map) remain owner-owed** —
  the first is money plus an external account, the second cannot be written before the
  second person exists.

  - **D55.1 — the framework is ADOPTED, and `engineering_practice.md` §1 is amended on a
    premise change** (D-A). That section says *"We do not run a classic dev → staging →
    prod triple, and should not"*, and its stated premise is **one box, single-digit
    customers, one developer**. The owner is changing all three. The reasoning was sound
    for the world it described and is not being overruled on taste; it is being retired
    with its premise. §1 gains a banner pointing here.
  - **D55.2 — ⏳ PHASE 0: until customer #2 exists, we work directly in production —
    and this phase has a NAMED END, because a bridge with no trigger is a destination.**
    That is the owner's stated intent and it is correct today: there is one customer, and
    it is us (D36), so Ring 0 *is* production and a staging box would be a second thing to
    keep truthful for no reader. **Phase 0 ends at whichever of these comes first:**
    **(a)** a second organization is provisioned on a real deployment; **(b)** a second
    human contributor gets commit access; or **(c)** the tenant RLS promotion (H3) is
    executed — after which a mistake in production is no longer recoverable by one person
    who knows what they did. ⚠️ **Phase 0 is a statement about ENVIRONMENTS, not about
    discipline.** Everything else on this board still binds while it lasts: ship dark
    (`engineering_practice.md` §2), expand/contract migrations (R6), the owner-gate
    registry (§6), and R5. Working directly in production is precisely the condition
    under which *"every feature lands behind a flag, defaulting OFF"* stops being a
    nicety.
  - **D55.3 — trunk plus two fast-forward-only promotion refs; no long-lived `develop`**
    (D-B). `feature → PR → main → (auto, on green) staging → (owner promotion of a named
    SHA) release`. **Nothing ever merges into `staging` or `release` and nothing is ever
    cherry-picked onto them** — they are pointers at commits that already exist on `main`.
    A long-lived `develop` branch is rejected on measurement, not taste: it
    institutionalises the drift already charged here as three migration-number collisions,
    two green-alone/red-together PRs in one day, and a duplicated multi-tenancy design.
    The thing a `develop` branch is wanted for — *somewhere green that is not customers
    yet* — is an **environment**, and D55.5 supplies it.
  - **D55.4 — ⚠️ THE LOAD-BEARING CLAUSE. "Promote features to production individually"
    means FEATURE FLAGS. It does not, and must not, mean promoting one feature's commits.**
    A `release` ref is a pointer at a SHA, so promoting a SHA promotes **everything merged
    before it** — the unit of *code* promotion is the whole trunk up to that commit, and
    no branching scheme changes that. The unit of *feature* promotion is **the flag**.
    So the owner's requirement is met as: merge continuously to `main`, promote SHAs to
    `release` in batches, and turn each feature on **for production independently, when it
    is ready, by flipping its flag** — which is already this repository's habit and is why
    merges here rarely become incidents.
    🔴 **Cherry-picking one feature's commits onto `release` is FORBIDDEN BY NAME**, and
    the reason is R6 rather than tidiness: migrations are numbered, forward-only and
    applied before services restart, so a `release` that contains migration 191 but not
    190 is a database no ladder describes and **we cannot roll back**. It also
    re-creates the "this fix has to be applied twice" divergence that fast-forward-only
    refs exist to prevent. An agent asked to cherry-pick onto a promotion ref must refuse
    and say so.
  - **D55.5 — three environments; staging is a nightly RE-DERIVATION of production, not a
    maintained copy.** Local/CI → **staging** → production. Staging is rebuilt each night
    from production's own backup, **anonymised on the box before the dump leaves it**
    (DPDP — this is an acceptance clause, not a best-effort step), with the pending
    migration ladder replayed on top. This answers the doctrine's objection instead of
    overruling it: an environment that is *derived* cannot drift from what it is derived
    from. It is the same object as `engineering_practice.md` §1's practice item **P-1**,
    already ranked item 3 of its "fix before customer #1" list — build it once, get both
    the migration rehearsal and the soak.
    ⚠️ **Staging is a real Metorite deployment and R5 binds it identically** —
    `organization_id` populated, RLS on, pooled schema. *"It's only staging"* does not
    create a phase in which single-tenant code is acceptable (D15).
  - **D55.6 — migration numbers keep their scheme and gain a coordinator** (D-C, option
    (a)). The collision fence runs on every PR **including conflicted ones**, plus a
    re-check against `origin/main` at merge. Timestamp/ULID prefixes (option (b)) are
    held in reserve for *if (a) still collides* — they touch the ledger, the replay job,
    the `[0-9]*` globs and the promotion set, and that is a schema-adjacent change for a
    quieter quarter.
  - **D55.7 — promotion cadence: on demand, with a soak floor of one working day for the
    tenant plane and one successful lifecycle rehearsal for the Console plane** (D-E,
    `agent-proposed, owner may overrule`). The ring ORDER is not optional; the clock is.
    The Console plane earns the stricter gate because its blast radius is different in
    kind: a tenant-plane regression annoys one deployment's users, a Console regression
    stops sign-in for **every customer at once** and can mis-bill.
  - **D55.8 — one repository** (D-G): `site/` and `workbench/operator_console/` stay.
    Their deploy, dependency and design boundaries already exist; a repo boundary would
    add only an **access** boundary. **Named reversal triggers:** an outside agency or
    marketing hire who must not see product source (for `site/`), or a separate ops team
    owning the operator console. ⚠️ **Before any operator-console split, two things must
    exist first:** a contract test between the Console's operator routes and the BFF that
    consumes them (there is none today in either topology), and a decision about how
    `.claude/`'s owner-gates reach the new repository — otherwise the surface that assigns
    seats and grants AI credits becomes the one surface with no gate enforcement (D29).
  - **D55.9 — what is still owed from the owner, so it is not mistaken for decided:**
    **D-D** — where staging runs and what it costs (a second VPS, a second Supabase pair,
    or both; external accounts are owner-side); **D-F** — the CODEOWNERS map, which cannot
    be written before there is a second person to name. Both are registered in `HANDOFF.md`.

- **D52 — ClickUp is retired outright, not cut over. Projects IS the
  project-management system of record.** *(owner call, 2026-08-24; owning spec
  `specs/project_management_app.md`, board **WS-39**. Verbatim intent: "remove all
  the ClickUp integrations. The projects app becomes the ClickUp clone itself, and
  we don't have to have an external integration. We have a full-blown project
  management app within Metorite itself.")*
  - **D52.1 — the staged retirement is cancelled, and its gates die with it.**
    WS-27 **c** (two-way sync) is **unbuilt by decision**, not deferred — it was
    blocked on BO-1a/BO-1b and is now cancelled. WS-27 **g** loses its cutover
    half: there is no parity sign-off against an external system because there is
    no external system. What survives of **g** is the retirement half, and it ships
    now instead of behind a migration that will never be scheduled. In §6, WS-27
    gate **(a)** (running either import endpoint against the production workspace)
    and gate **(b)** (enabling the outbound push) are **dead** — an agent cannot be
    asked to refuse an endpoint that does not exist. Gate **(c)** narrows to the two
    acts that are still the owner's: **revoking the ClickUp tokens** wherever they
    are stored, and any **drop of ClickUp-sourced columns on a live database**.
  - **D52.2 — both importers go.** `POST /projects/import/clickup` (live API) and
    `POST /projects/import/from-tasks` (the local `gtd_*` mirror) are deleted. The
    owner was offered keeping the second as a one-shot and declined. This does not
    strand D53's data: the `gtd_*` → `pm_*` move is a **backfill migration** run
    once under R6, not an HTTP endpoint. An importer kept "just in case" is a live
    credential dependency plus a second write path into `pm_tasks`, which is the
    CLAUDE.md §5 second-implementation defect wearing a migration's clothes.
  - **D52.3 — what is NOT deleted, and why.** The `clickup_id` / `clickup_kind` /
    `clickup_snapshot` / `clickup_synced_at` columns on `pm_projects`/`pm_tasks`,
    and the `clickup_*` columns on the `gtd_*` and `acb_graph` tables, **stay**.
    R6 forbids dropping in the same release that stops writing them — the deploy
    applies migrations before restarting services, so old code would meet a dropped
    column. They stop being written and stop being read here; a **later, separate**
    migration drops them. ⚠️ `pm_projects.clickup_id` and `pm_tasks.clickup_id` are
    still GLOBALLY UNIQUE (migration 161 §424) and that is now pointless rather than
    wrong — fold the widening into whichever release drops them.
  - **D52.4 — root `AGENTS.md` constraint 8 is amended in THIS PR.** "Source systems
    are authoritative — Metorite is a read-mostly mirror" was slated to be amended in
    the WS-27g PR, which will now never exist. It stays true of Zoho and Gmail. It is
    **false of project management from today**, and a constraint that is false in a
    file every agent loads is worse than no constraint.
  - **D52.5 — why outright rather than staged.** The staged plan existed to keep an
    operating company running on ClickUp while Metorite caught up. §11.2's parity
    backlog CLOSED on 2026-08-09, and u–z, aa–ag, S1–S6 and ah–bj have landed since.
    The app is not catching up any more. Past that point the connector buys nothing
    and costs a token, a webhook receiver, a poll scheduler, a rate limiter and a
    three-way merge — five things that can break in a product nobody is using them for.

- **D53 — One task store. Tasks is the personal lens over Projects; `gtd_*` is
  retired.** *(owner call, 2026-08-24; owning specs `specs/task_manager_app.md` +
  `specs/project_management_app.md`, board **WS-39**. Verbatim intent: "The projects
  app and the tasks app are tightly coupled to each other. The tasks app is just the
  personal view of the company-wide projects. And in addition, the tasks app has
  personal tasks that are not configured in the projects app because they are specific
  only to the person.")*
  - **D53.1 — `pm_*` is the store of record for every task**, personal or shared.
    `gtd_items` and its satellites are retired.
  - **D53.2 — this is not new architecture; it is finishing WS-27e.**
    `routes/projects/personal.py` has implemented exactly this shape since D-PM-6:
    `/projects/my/inbox`, `/projects/my/project`, `/projects/my/tasks`,
    `PATCH /projects/tasks/{id}/personal`, with the per-member **overlay** on
    `pm_task_personal`. Measured 2026-08-24: that table already carries
    `disposition`, `next_action`, `context`, `energy`, `time_estimate_mins`,
    `is_two_minute`, `defer_until` and `clarified_at` — the GTD overlay, column for
    column. What never happened is the second half: the `/tasks` UI still calls
    `/api/tasks/*` and reads `gtd_items`. D53 says finish it.
  - **D53.3 — a personal task is a PRIVATE task in the member's personal project**,
    not a row in a second table. "I don't want this on the company board" is answered
    by `visibility = private` on a task in the member's own `pm_project` — a grant
    question D12's visibility model already answers — and never by a separate store.
    A second store would re-create the coupling this decision exists to remove.
  - **D53.4 — promotion is a MOVE, not a copy.** Publishing a personal task to a real
    project is an update of `project_id` + `visibility`. No sync, no id mapping, no
    reconciliation, because there was never a second row. Completing it in Tasks
    completes it in Projects because it *is* the same row — that property is the
    entire point, and it is the one a two-store design cannot buy at any price.
  - **D53.5 — retirement is expand/contract (R6), in three releases.**
    (1) the `/tasks` UI moves onto `/projects/my/*` and `gtd_*` writes stop;
    (2) a backfill migration moves surviving `gtd_items` rows into their owners'
    personal projects; (3) a later migration drops the `gtd_*` tables.
    **(2) and (3) against a live database are OWNER-GATE** — the same data-move class
    as WS-29's pooled cutover, registered in §6 by this decision.
  - **D53.6 — what `gtd_*` KEEPS past (3).** `user_settings`, `calendar_day_state` and
    `calendar_rollover_log` are per-member state, not task rows. They are NOT part of
    this retirement. Named here so a later sweep does not take them with the rest.
    ⚠️ **Renamed 2026-09-22**, from `gtd_settings`, `gtd_day_state` and
    `gtd_rollover_log`. The last two belong to D54's app. `gtd_settings` did NOT
    become `calendar_settings`, because it also carries the Tasks app's model tiers
    and capture toggles. It is the member-level sibling of `org_settings`, and the
    owner took that call on 2026-09-22.
  - **D53.7 — the SCHEDULED BLOCK is per-member; the DEADLINE is per-task.**
    *(Taken 2026-08-24 while building S3a; migration **187**.)* `scheduled_start`,
    `scheduled_end`, `flexible`, `is_hard_date`, `actual_start`, `actual_end` land
    on **`pm_task_personal`**, not on `pm_tasks`. This is D53.5's overlay argument
    one axis over, and it is the same fact rather than an echo: two people assigned
    one task hold different *dispositions* because they are doing different things
    with it, and for exactly that reason they hold different *calendars* — each
    blocks their own Tuesday. A `scheduled_start` column on the task would let
    whoever writes last delete the other's afternoon, silently, with neither told.
    **`due_at` is the opposite case and stays on the task**: one date, shared by
    everyone assigned, and a fact about the work rather than about anyone's week.
    ⚠️ `flexible` is **nullable, not `NOT NULL DEFAULT true`** — NULL means "never
    stated", which the reader resolves as flexible but which is deliberately not the
    same as a member pinning it. The Ideal Week packer (WS-21) needs that
    difference: "never stated" is what it is allowed to fill in.
    Verified two-member on real Postgres — `tests/live/live_ws39_s3a.sql`, 8 checks.
  - **D53.8 — the MATRIX, the RANK and WAITING-FOR are per-member too.**
    *(Taken 2026-08-25 building S3a-server-2; migration **188**.)* `important`,
    `leveraged`, `deep_work`, `kept_mine`, `sort_key`, `waiting_on`,
    `delegated_at`, `expected_by` and `last_nudged_at` land on
    **`pm_task_personal`**. Third application of D53.5's argument, unchanged:
    each is a fact about how ONE PERSON is holding the task. "I am waiting on
    Priya" is true for the delegator and false for the doer of the very same
    row; `sort_key` is my drag order in my own list. This closes H-33's
    prerequisite — **every `GtdItem` field now has a `pm_*` home**, so
    S3a-client can be written without silent data loss.
    ⚠️ **`important` is NOT `pm_tasks.importance`, and the names invite the
    mistake.** `importance` is an INTEGER the Projects UI labels **"Priority"** —
    shared, per-task, seen by everyone assigned. `GtdItem.important` is the
    BOOLEAN axis of the Eisenhower matrix, paired with an `urgent` that is
    DERIVED from `due_at` and never stored. Mapping one onto the other (say
    `important = importance >= 3`) would invent a threshold no decision records,
    publish one member's private triage to the whole task, and change what the
    Projects Priority column means. Two facts, two homes; `importance` is left
    alone.
    ⚠️ **AMENDED by D77 (2026-09-23), by owner directive.** The owner asked for
    one field per work fact. The overlay's `time_estimate_mins` is retired, and
    `pm_tasks.estimate_mins` is the one estimate. `important` stays on the
    overlay: D76 lets the shared Priority seed it and never writes it. The rest
    of D53.8 stands.
    ⚠️ **Waiting-For collapses to four columns rather than porting
    `gtd_waiting`.** That table allows many rows per item with a `resolved`
    flag — a delegation HISTORY — but **every reader in the tree filters
    `resolved = false`** (measured: `routes/tasks/core.py:479` and
    `routes/tasks/ai.py:1645`, the only two), and `GtdItem` exposes one of each
    field rather than a list. So nothing displayed is lost. Resolved history is
    deliberately **not** carried; "how many times have I chased this" would be a
    new feature with a new home, not a restoration.
    ⚠️ `clarified_at` is **not** in 188 — it has existed since 147. It was
    WRITTEN on every triage and projected by nothing, so it had been collecting
    a real value no caller could read. A wiring gap, fixed without a migration.
    Verified on real Postgres — `tests/live/live_ws39_s3a_server2.sql`, 9 checks,
    including the partial Waiting index being CHOSEN rather than merely present.

- **D54 — Calendar is its own app under Personal Center.** *(owner call, 2026-08-24;
  owning spec `specs/calendar_focus_os.md`, board **WS-39**. Verbatim intent: "it
  might make sense to also remove calendar from the tasks app and make it into an app
  by itself under personal center.")*
  - **D54.1 — the surface moves.** The calendar becomes **`/calendar`**, a
    top-level pane in the **Personal Center** section of `src/lib/nav.ts`.
    ⚠️ **AMENDED 2026-08-24 while building S2, on two measurements:**
    **(a)** there was never a `/tasks/calendar` route — the calendar was a
    **view mode** (`selectedView === "calendar"`, a `ViewKey` in the shared task
    store), so this is a genuine extraction and there is nothing to redirect;
    **(b)** the gate stays **`feature:tasks`**, not a new `feature:calendar`.
    A new slug is a grant nobody holds: it would ship the app **dark to every
    existing member**, and un-darkening it is an owner-gated role write (§6, the
    WS-24 (d) class) on top of a migration that has to reach a box first — and
    ~~H-1 records `main` as many migrations ahead of every box.~~ ⚠️ **Corrected 2026-08-26 — H-1 no longer exists in `HANDOFF.md` (its Check passed and the entry was deleted), and its premise is now false**: the 2026-08-25 deploy run reported *"Migrations complete (0 applied, 186 already recorded)"*, i.e. the box is current with `main`. The argument for riding `feature:tasks` is unaffected and stands on its own — a new slug is still a grant nobody holds. The calendar lived
    *inside* Tasks, so every `feature:tasks` holder already had it; riding that
    grant keeps reachability exactly as it is. Minting `feature:calendar` is a
    later act that must ship **with** its grant migration, never before it.
  - **D54.2 — it ships `live`, not `preview`, and the fence moves with it.** The
    calendar is reachable today *inside* a live app, so classifying the extracted
    pane `preview` would withdraw a shipped capability. `launch_surface.md` §2's live
    set therefore goes from **eight rows to nine**, and `nav.test.ts`'s count fence is
    updated in the same PR that adds the pane. That fence exists precisely so adding
    a pane without deciding its launch status fails — so this is a deliberate edit,
    not a test that needed fixing.
  - **D54.3 — "Personal Center" is D49's section label, not a Center projection.**
    D49 withdrew the Centers *surface*; `lib/centers.ts`, the `center.*` features and
    the `group:<slug>` grant vocabulary are untouched by this decision.
  - **D54.4 — scope is personal.** My time blocks, my scheduled work, my connected
    external calendars. Center-wide and company calendars are **explicitly out of
    scope**: they need a model for whose blocks are legible to whom, and that is a
    separate owner decision, not an extension of this one.
  - **D54.5 — ⚠️ the calendar has no store of its own, and that changes the ordering.**
    Measured 2026-08-24: `routes/tasks/calendar.py` reads and writes **`gtd_items`
    directly** (scheduling is fields on the task row) plus `user_settings`,
    `calendar_day_state` and `calendar_rollover_log`. There is no `gtd_time_blocks` table and no
    `calendar_accounts` table — WS-21's S1–S4 and the OAuth work are unbuilt, and two
    specs cite those tables as if they exist. So Calendar is a **third lens on the
    same rows**, not a separate system: Projects is the company board, Tasks is my
    list, Calendar is my time. Its own three tables survive D53; its **task reads
    re-point from `gtd_items` to `pm_tasks` when D53's release (1) lands**, which is
    why D54 is sequenced with D53 rather than before it.
  - **D54.6 — WS-21 keeps ownership of calendar BEHAVIOUR** (F2/F3, Focus Shield,
    timeboxing, and Horizons per §4). D54 changes where the surface lives and who
    renders it, not who owns the semantics. External-sync OAuth stays OWNER-GATE.

- **D49 — Flat pricing, one seat, and a launch surface: Centers leave the product,
  ₹500/user/month + AI credits replaces the package ladder, and every app not
  finished is held back rather than shipped half-built.** *(owner call, 2026-08-24;
  owning spec `specs/launch_surface.md`, WS-34.)*
  Four parts, taken together because they are one decision about what we sell on
  day one.
  **(1) Pricing is flat.** ₹500 per user per month, carrying everything that is
  live, plus AI credits metered as before. This **supersedes D23 and D24** —
  `saas_multitenancy.md` §2.4b's Center packages, the two org-wide add-ons, the
  ₹1,800 all-Centers seat and ₹3,000 Complete are retired as customer objects;
  §2.4b keeps its banner as the decision record and prices nothing. **`core`
  stays the one seat slug** (membership IS the seat, D19.3) and is repriced
  rather than replaced: a second slug would immediately raise "which seat does
  this member really hold", which is the CLAUDE.md §5 second-implementation
  defect. **Explicitly NOT superseded:** D19.3's hard cap (assignment still 409s
  with `buy_more` and never auto-upgrades), D32.5's three counts, the entitlement
  seam and the 402-vs-403 partition, and the credit ledger with its
  unpriced-until-measured fence. A flat plan makes the module intersect
  trivially total for a seated member; it does not delete the seam, because
  deleting it is a one-way door the first time we sell a tier again.
  **(2) Centers leave the surface, not the tree.** The "Centers" nav section is
  deleted and the Center landing pages are unlinked; `lib/centers.ts`, the
  `/centers/<slug>` routes, the `center.*` feature rows (migration 140) and the
  `group:<slug>` slice grants (D12) are **all kept, unchanged**. The concept
  stops being a *destination* and stays the *scoping primitive* — deleting the
  registry would take the group vocabulary with it and break the live Projects
  grant model. **D22's Center roster is not repealed**; WS-13/14/15/16 are
  **parked, not cancelled**, and `department_centers.md` stays ACTIVE as the
  design record with a banner. Personal Center survives **as a nav section
  label** — a category of apps mapped to the signed-in person, which was never a
  departmental projection anyway.
  **(3) The launch surface is an allowlist, and it is written down.** Eight panes
  go live (`launch_surface.md` §2); every other pane is marked `preview` and
  disappears from navigation while its **routes, API, tests and board row stay
  exactly as they are**. `preview` is NOT a permission — a member holding
  `feature:email` still does not see Email, because access answers *may you* and
  launch status answers *are we selling it yet*; conflating them would mean
  revoking grants to hide an app and re-granting them to promote it, i.e. a
  migration per launch decision. One escape hatch, `NEXT_PUBLIC_SHOW_PREVIEW_APPS`,
  default OFF, which restores the nav entries and changes no authorization.
  **(4) Seats are a thing a customer admin does, from the app.** Invite → assign →
  release → **Unassigned** → reassign, all four in Organisation, with the same
  four available to an operator per customer. *Unassigned* is a first-class state:
  a released member stays on the roster, stays `active`, and keeps an enabled
  Assign control — which is why the seat surface reads the **gateway's** member
  roster (everybody) rather than only the Console's seat rows (only those who
  ever held one). The invite is **never** rolled back by a failed assign: two
  systems, two transactions, and a member who exists without a seat is precisely
  the state the surface exists to show.
  **Rejected alternatives.** (a) *Delete the Center code.* It is the group
  vocabulary D12's grants are built on; the directive says remove the concept
  from the application, not delete the work. (b) *Delete or 404 the unfinished
  apps' routes.* The directive says they remain in the application, and a route
  that 404s is a deleted app with extra steps. (c) *Hide apps by revoking
  features.* See (3) — it makes a product decision into a data migration and
  makes `/access` lie about why a pane is missing. (d) *A new `flat` plan slug
  beside `core`.* See (1).
  **Owner-gated consequence, refused by name:** anything touching an organization
  that already holds seats on a plan this decision retires — repricing,
  converting, refunding, proration (`launch_surface.md` LS-11, §6 money-on-a-live-
  system).
- **D50 — Member invites: a NOTIFICATION email, a Console membership written at invite
  time, and auto-activation at first resolve.** *(owner-ratified, 2026-08-24. Three
  clauses, one design; specced as `customer_console.md` CP-2f + `subscription_console.md`
  SC-2c, board WS-30/WS-31.)*
  - **D50.1 — no accept-token, ever.** The invite email is a **notification**
    (*"you've been added to &lt;org&gt; on Metorite — sign in with this address at
    `https://app.metorite.com/signin`"*). Identity is proven at sign-in by the IdP
    (Google / email OTP, CP-2d), so there is no token, no query parameter and no secret
    in the mail. A token would be a **second identity system** beside the one
    `user_management_contract.md` already binds, and a mail-borne bearer is a credential
    in a mailbox we do not control. Consequence, stated rather than implied: the email is
    pure copy, so losing it costs the recipient nothing but a nudge — the membership is
    already written.
  - **D50.2 — the invite writes the Console-plane membership IMMEDIATELY,
    `status='invited'`.** `POST /admin/members` mirrors the member into the Customer
    Console's `org_membership` at invite time, so the seats grid sees the person and a
    seat is assignable **before** their first sign-in. **Core-seat burn is unchanged and
    still happens at first resolve (D19.3 / D32.5)** — a membership row is not a seat.
    This is the load-bearing clause: without it an invited member is invisible to
    `GET /me/members`, 404s at `_seat_admin_target`, and — with resolve ARMED — resolves
    `console-empty` and is funnelled into creating **their own** organization instead of
    joining their employer's.
  - **D50.3 — first sign-in auto-activates an `invited` member, guarded to `invited`
    ONLY.** On a successful deployment-arm resolve, a Console membership whose status is
    exactly `invited` is promoted to `active` (and `joined_at` stamped
    `COALESCE(joined_at, now())`). **Never** `suspended`, `removed`, or any other status
    — the guard is the `AND status = 'invited'` in the UPDATE's own `WHERE`, not an `if`
    a later edit can widen. Scope, named honestly: this closes the **registry** half of
    `colleague_onboarding.md` N6b. The **tenant** plane's `app_user.status` is untouched
    by this decision — activation there is still §2 Step 1b (`PATCH /admin/members/
    {email}`), and folding it in is a change to the live auth path with its own ticket.

- **D51 — Subdomain workspaces are WITHDRAWN; the organization is explicit in the UI;
  the multi-org workspace choice is a session claim (MT-1k).** *(owner directive,
  2026-08-24: "Drop subdomains entirely; they can cause future complications. Ensure
  the UI clearly indicates which organization a user is signing into. Since a user's
  email may belong to multiple organizations, provide a way to select and switch
  organizations during login." D50 is the member-invites decision, in flight on
  `ws-30-invites`.)*
  - **D51.1 — one door.** `app.<domain>` is the only product hostname. MT-1f's
    slice-1 code (host parser, proxy branch, flag) is REMOVED, not left dark —
    dark code nobody will flip is future complications by another name. What
    survives is the slug **vocabulary**: `RESERVED_LABELS`/`SLUG_RE` in
    `lib/subdomain.ts` + the gateway slug gate (CP-2c 4a) — a slug is a public
    identifier regardless of DNS. The browser tier now reads NO request hostname
    at all (`subdomain.test.ts`'s zero-reader sweep — the old sole-reader fence,
    inverted).
  - **D51.2 — the organization is explicit in the UI.** A logo-less org's NAME is
    the sidebar caption (`lockup(branding, fallback, orgName)`); the org-less
    signed-in state is an explicit JOIN-vs-CREATE chooser (AccessGate's org-less
    arm — it used to show the suspended-member copy and route nowhere); `/signup`
    says loudly it creates a NEW organization and points invited people back to
    sign-in; `/signin` carries the fork line. Built as WS-35 slice A.
  - **D51.3 — multi-org is a SESSION-CLAIM choice, later.** One email in several
    organizations, selectable at sign-in and switchable in-session — **MT-1k**,
    slices B (session claim + gateway per-request bind from the claim; the claim
    only ever selects among the identity's own memberships, R11) and C (the
    tenant-plane enabler: per-org `app_user` uniqueness — today `app_user.email`
    is globally UNIQUE — plus the H6 re-key's completion). Owner-noted for later
    development; not launch-blocking (first customers' employees are single-org).

- **D48 — H6 identity cutover re-keys RBAC onto `user_identity`, resolved in TWO phases,
  with status mirrored FORWARD.** *(owner-ratified, 2026-08-22. ⚠️ AMENDED by D51,
  2026-08-24: D48's "multi-org active-org = per-request bound org (host = workspace
  choice)" loses its selector — subdomains are withdrawn, so the workspace choice is
  a SESSION CLAIM chosen in UI at sign-in (MT-1k). The claim may only SELECT among
  the identity's own memberships, never grant — the rule survives its mechanism.)*
  H6 re-keys RBAC (`user_role`, `user_permission_override`, `org_group_member`) from
  `app_user.id` → `user_identity.id`, resolved in TWO phases: identity+org+status from the
  RLS-EXEMPT `user_identity` ⋈ `org_membership` on the UNBOUND session (feeds
  `bind_tenant`), then roles/permissions from the RLS-forced RBAC tables on the BOUND
  session within the active org. **`app_user`-as-view REJECTED** (can't preserve the id
  linkage without re-keying anyway; can't express multi-org role-sets; unfences the ~18
  bound RBAC reads; needs `INSTEAD OF` triggers). **Status is mirrored FORWARD into
  `org_membership.status`** (reconcile-from-`app_user` is impossible post-RLS — the
  identity leg reads unbound while `app_user` is RLS-forced). Multi-org active-org = the
  per-request BOUND org (host = workspace-choice). Ships dark behind `IDENTITY_CUTOVER`
  (default OFF); H6 must land dark BEFORE H3 phase-4 promotion. R6 expand/contract.
  *(Slice 3a — this change, WS-29 — records this decision and builds the forward status
  mirroring the read-cutover (slice 3b) depends on: `acb_auth.access.mirror_membership_status`
  on `members.py`'s suspend/remove/reactivate + `provision_member`, and reconcile migration
  183. Still dark: no reader consumes `org_membership.status` yet.)*
  *(Superseded-in-part 2026-08-22 by the H6 ORPHAN-CLOSURE slice, WS-29: the invite/approve
  mirror moved OUT of `provision_member` to its callers POST-COMMIT — `members.invite_member`
  and `access_requests.approve_access_request` — so a rolled-back caller txn commits no shadow
  orphan; and purge now deletes the shadow via `acb_auth.access.purge_identity_shadow`. Decision
  unchanged; only the call site moved. See `saas_multitenancy_handover.md` §H6.)*

- **D47 — The Customer Console SERVICE runs on the production VPS, for now.** *(owner
  call, 2026-08-19: "ok, let's just use the same VPS for the customer console as well."
  Answers `customer_console_infrastructure.md` §5 item 4 AGAINST that document's own
  default — which explicitly invited the disagreement.)*
  1. Own systemd unit + own env file beside `acb-gateway`/`acb-workbench`; the data
     plane is unchanged (D34's Supabase Console project) — a box loss loses no Console
     data, and CP-2b's fail-soft cache covers sign-in through a Console outage ≤24h
     while purchases fail closed.
  2. **Public surface: the Razorpay webhook path ONLY.** Gateway and BFF calls target
     `127.0.0.1` and never leave the box.
  3. **Obligation on the way in:** the deploy pipeline gains the
     `infra/customer_console/` ladder-apply step — closing the recorded "platform_api
     is on the box but inert" gap. Until then the unit's schema changes never land.
  4. **Named move trigger:** the day a second deployment exists, the service leaves
     the box; relocation is re-pointing `CUSTOMER_CONSOLE_URL` (data unmoved).
  5. Deploying the unit, its env file, and its Caddy block remain §6(a) — owner act or
     a dated D45 grant. Artifacts travel as ready-made blocks (plan-guard forbids
     agent writes under `deploy/`).

- **D46 — Self-serve signup is the next build: the apex site, the signup flow, then the
  in-app growth MVP; 2FA/email-OTP documented-deferred.** *(owner directive, 2026-08-19:
  "let us first work on the entire sign-up flow and build that out … Build a basic website
  on metorite.com where users can sign up … They have to set up an organisation, and the
  first user becomes the admin and can then log into the application. Eventually … two-factor
  authentication and email OTP … Not made it by MVP, but documented … Purchasing extra
  seats and then inviting users to fill those seats, buying AI credits are all then done in
  their app, which I want to see an MVP for after building the signup workflow." The
  ticket-mapping below is agent-proposed, owner may overrule.)*
  1. **The apex marketing site is WS-33** (`specs/marketing_site.md`, minted with this
     decision) — D41's "apex reserved for a future marketing site" clause, now exercised.
     One static page, CTA into the signup flow. Apex DNS record + Caddy vhost + deploy stay
     owner acts.
  2. **The signup flow is CP-2c** (`customer_console.md`, minted with this decision) — the
     form over CP-2a's ✅ API, Google-attested identity, GST fields at signup (doctrine
     §3.1), both provisioning planes driven through ONE orchestration (Console
     `/orgs/provision` + migration 179's `provision_organization`), first user = owner on
     both. Dark behind `SELF_SERVE_SIGNUP_ENABLED` (minted there; existed nowhere).
     **Hard dependency: MT-1j slice 4** — CP-2c's step (b) IS the Console↔tenant seam,
     `org_placement` hole included. D36.1 stands: Fracktal onboards through this same flow,
     no first-party bypass.
  3. **2FA + email OTP are CP-2d, DOCUMENTED-DEFERRED** — not MVP by the owner's words;
     the stub records the eventual shape (IdP-attested identity means no password system
     exists to add a factor to; OTP arrives only with a non-OAuth identity ticket) so
     nothing builds against a guess. No board row until minted.
  4. **The in-app growth MVP (seats · invites · AI credits) runs AFTER the signup
     workflow** and is mostly existing tickets: SC-4a's ₹0 checkout is code-complete dark,
     SC-4h is the paid hand-off (needs H-14's Razorpay account), member invites are live
     (`POST /admin/members`), and **credit packs remain blocked on §9 input 4** (the pack
     ladder is an owner price call) — the owner's ask re-sequences these, it does not
     re-litigate D19.2/D23/D24 pricing.
  5. **Sequencing consequence:** CP-2c's chain (MT-1j slice 4 → CP-2c → WS-33 deploy)
     runs ahead of MT-1f (subdomains) and the remaining MT-1j slice-5 ratchet rounds.
     The owner's §6 Fracktal go-live sequence (H3 → provision → Console deploy → flips)
     is unchanged and stays the gatekeeper of anything LIVE.
  6. **The deployment-identity adjudication** *(added 2026-08-19, same day, after the
     slice-4 dispatch audit returned NO-GO on exactly this: nothing said where
     `org_placement.deployment_id` comes from, and CP-2c proposed the auth answer slice
     4 forbade its implementer from taking — one seam, two owners, three answers.
     `agent-proposed, owner may overrule`.)* `POST /orgs/provision` gets **two arms,
     one per ticket**: **MT-1j slice 4** ships the **operator arm** — a required
     `deployment_label` names the deployment explicitly (breaking API change, free
     while the Console is deployed nowhere); provisioning **never moves** a placement
     (same label no-ops, different label 409 — moves are a future operator ticket);
     the sole-deployment `count(*)=1` heuristic is **forbidden by name** (it would be
     a fourth copy of the sole-org guess MT-1j retires). **CP-2c** builds the
     **deployment-key arm** — the key IS the deployment identity, carries no label
     (400 if presented, the R11 rule), and the `{resolve, provision}` capability
     growth stays the owner's §6 (f)/(h) act at issuance. Slice 4 is thereby decoupled
     from D43-2's open sub-question; slice 4 also owns the tenant-side seam function
     (`provision_local_organization`, the one wrapper over migration 179) and CP-2c
     may not invoke the SQL directly.
     ⚠️ **Amended 2026-08-19 (CP-2c's dispatch audit, blocker B1 — same class, same
     day):** the two-arm route cannot keep `deployment_label` required AT THE MODEL,
     so when CP-2c slice 1 lands the model becomes `str | None = None` on the
     `ResolveRequest.org_slug` precedent, both arms' rules move to the handler, and
     the operator arm's missing-label answer moves **422 → 400** — slice 4's
     mutation-proved no-inference fence is amended in that same PR, red-first. Ruled
     in writing in both owning specs; an implementer changes neither silently.

- **D43 — Tenant-side organization provisioning gets its own ticket (MT-1j), and the three
  defaults that shape it.** *(`agent-proposed, owner may overrule`, 2026-08-19 — the D16/D17
  class. Owning spec: `specs/saas_multitenancy.md` §11 **MT-1j**, minted the same day.)*
  **The finding first:** four specs and one migration disclaimed per-org role seeding,
  owner bootstrap and local `organization`-row creation to *"the org-provisioning ticket"*
  (`saas_multitenancy_implementation.md` §7.1/§8 · `customer_console.md` CP-2b §6(k) ·
  `subscription_console.md` `:196`/`:987` · `user_management_contract.md` §3 ·
  `178_billing_purchase_permission.sql:39-48`) — **and that ticket did not exist**, while
  **D36.1** already asserts customer zero onboards through `/orgs/provision`. Each
  individual refusal was correct; the chain had no terminus.
  1. **Seed per-org roles with ONE SQL function, not a role-template table.**
     `provision_org_roles(org_id)` replays migrations 130/131/133/178's grants as data.
     One seeding doctrine — 178's own comment refuses *"a second seeding doctrine"* and
     assumes the successor parameterises the existing one. A template table is more
     general and buys nothing until customers edit system roles, which no ticket asks for.
  2. **Provisioning runs PULL, not push** — the box asks the Console which organizations it
     should host, symmetric with CP-2b's fail-closed resolve, which already carries the
     deployment key. Push would give the Console a write credential into every tenant
     database. ⚠️ **Open sub-question, deliberately not answered by an agent:** if pull
     needs more than the deployment key's `{resolve}` capability, that is a
     credential-scope change and belongs to §6 gate (f).
  3. **H3 (the RLS promotion) is a hard prerequisite for EXECUTING MT-1j against a real
     second organization — and blocks neither building nor R8-testing it.** Provisioning
     org #2 before promotion yields a tenant with **no database-level isolation**:
     `infra/postgres/generated/04_policies.sql` has never been replayed and
     `tests/unit/test_tenancy_boundary.py:126`'s `BASELINE_UNSCOPED` names **114** tables
     carrying no `organization_id` (measured 2026-08-19). A scratch database applies the
     generated phases in-fixture, so the build is unblocked today. **Registered as an
     execution gate in §6.**
  4. **What this decision does NOT do.** It does not move MT-2's one-assignment act
     (D23.2), does not build CP-2a's signup form, does not re-point
     `_BOOTSTRAP_ORG_SLUG` (**D36.3** keeps it as the fresh-box path), and does not
     weaken `key_store._resolve_org` — item 3 of the D33 findings below records that
     convenience as one that **must NOT be "fixed"**, and MT-1j slice 5 threads the
     tenant through the call sites instead.

- **D42 — Customer zero goes through the REAL purchase rails at ₹0: discount codes are a launch feature of the checkout.**
  *(owner directive, 2026-08-18: "ensure that we have some ability to put some
  discount coupon so that [Fracktal] does not have to pay anything, but still
  goes through the payment onboarding process … so that we actually can test it
  out like the production.")*
  1. The checkout (WS-30 SC-4a / **CP-9**) ships WITH operator-issued discount
     codes. The contract is **`subscription_console.md` SC-4g** — minted with
     this decision; do not build a purchase slice that treats codes as a
     later add-on. *(Ticket re-pointed 2026-08-18: this read "CP-8", which is
     the Operator Console. The payment seam had no ticket body anywhere and is
     now `specs/customer_console.md` **CP-9**. SC-4g was **completed** the same
     day — code storage via `keys.py`'s split-key pattern, the two tables,
     percent-in-basis-points against the **pre-GST** base, the named
     `credit_ledger` reason/ref vocabulary, and the honest note that clause 2's
     test-mode rehearsal is not in an agent's reach.)*
  2. The two-leg reality is recorded there and binds: Razorpay cannot capture
     ₹0, so the 100% coupon path completes **without a provider order** while
     writing the identical records (referenced to the redemption id), and the
     capture leg is rehearsed with **test-mode keys**. Together they cover the
     whole flow; neither alone does. A coupon that fakes a capture, or a test
     that skips the invoice, defeats the owner's stated purpose.
  3. Gates unchanged: issuing a code against a live org is owner-gated (the
     SC-4e adjustment gate class); live Razorpay keys stay owner-gated (§6).

- **D41 — “Customer Console” is the name; both IdPs at launch; slug subdomains ride MT-1f; the box is the wiped VPS.**
  *(owner calls, 2026-08-18: rename direction given — "something more
  descriptive about the job that system is actually doing" — concrete name
  recommended and accepted; IdPs and VPS stated directly.)*
  1. **Rename.** The WS-31 engine (D32) is now the **Customer Console** — the
     operator-side system that registers customer companies and manages their
     subscriptions, seats, keys, credits and provisioning. Mapping, applied
     2026-08-18: package `platform_api` → `customer_console`
     (`apps/services/customer_console/`) · ladder `infra/platform/` →
     `infra/customer_console/` (`001_control_plane.sql` →
     `001_customer_console.sql`) · env `CONTROL_PLANE_*` →
     `CUSTOMER_CONSOLE_*` (`_DATABASE_URL` · `_OPERATOR_TOKEN` ·
     `_INTERNAL_TOKEN` · `_ENCRYPTION_KEY` · the workbench billing proxy's
     `_URL`/`_ORG_KEY`) · specs `platform_control_plane.md` →
     `customer_console.md`, `control_plane_infrastructure.md` →
     `customer_console_infrastructure.md` · tests `test_platform_*` →
     `test_customer_console_*` (`_platform_ladder` →
     `_customer_console_ladder`). Pre-rename records below (D32, D33, D34,
     D40, board history) keep the old names — read them through this mapping;
     they are records, not anchors. Two deliberate non-renames: ⚠️
     **`workbench/control_plane/` is NOT this system** — it is the workbench
     UI's historical directory name; renaming it is pure churn across CI and
     design-system anchors and would be its own mechanical PR, if ever. And
     **WS-30 keeps the name Subscription Console** (the *customer-facing*
     billing surface); "Customer Console" always means the operator-side
     platform system.
  2. **Sign-in launches with BOTH Google and Microsoft** (extends D40.4's
     "Google first"). The code is already provider-driven
     (`workbench/control_plane/src/auth.ts`; issuer defaults to
     `organizations`, i.e. any customer's directory). The Microsoft half is
     an owner act: a **multi-tenant** Entra app registration with redirect
     `https://app.metorite.com/api/auth/callback/microsoft-entra-id`, then
     `AUTH_MICROSOFT_ENTRA_ID_ID`/`_SECRET` in the box `.env` with
     `_TENANT` left **unset** (setting a tenant GUID pins sign-in to one
     directory — the exact defect CP-0 removed).
  3. **Per-tenant hostnames (`<slug>.metorite.com`) are MT-1f, unchanged**
     (`saas_multitenancy.md` §MT-1f): the subdomain is a *lookup*; the tenant
     claim rides the authenticated session; sign-in stays on the fixed host
     `app.metorite.com` because OAuth redirect URIs cannot be wildcards. DNS
     gets a **wildcard `*.metorite.com` A record now** so MT-1f needs no DNS
     act later. Wildcard TLS is a Caddy change under `deploy/` (owner-gated)
     when MT-1f ships: on-demand TLS gated by an `ask` endpoint backed by the
     org registry, or a DNS-01 wildcard cert — decided then, not now.
  4. **The production box is the existing VPS, wiped.** The old CommandCenter
     deployment on it is torn down, not migrated; Fracktal re-onboards as a
     customer through the product path (D36). Wipe and SSH-credential
     rotation (H-3) are one owner session.

- **D40 — Production identity & topology for the Metorite fork.**
  *(owner calls, 2026-08-17: control-plane placement picked directly; domain
  layout and IdP explicitly delegated — "help me make these decisions for me" —
  recommended and accepted in-session.)*
  1. **Hostnames:** `app.metorite.com` (workbench) + `api.metorite.com`
     (gateway). The apex is reserved for a future marketing site and should
     redirect to `app.` until one exists. Rationale: the app's hostname is
     effectively immovable once OAuth redirect URIs, `NEXTAUTH_URL` and
     bookmarks pin it, so the apex stays free from day one, and per-tenant
     slug subdomains (MT-1f) coexist under the same apex when built.
  2. **Tenant database:** the owner's Supabase project, reached through the
     session-mode pooler. The complete ops delta and its traps live in
     `docs/EXTERNAL_POSTGRES.md`.
  3. **Control plane:** a SECOND Supabase project — never the tenant DB. Both
     planes define an `organization` table with different shapes
     (`infra/customer_console/001_customer_console.sql` — pre-D41:
     `infra/platform/001_control_plane.sql` — vs
     `infra/postgres/130_org_access_control.sql`); they cannot share a schema.
  4. **Sign-in:** Google first (Fracktal is a Google Workspace shop; identity
     is keyed by email, not provider, so the provider switch is transparent
     to `app_user` rows). The signin surface renders whichever providers are
     configured, so Microsoft Entra returns by configuration alone. An Entra
     app registration, when used, must itself be multi-tenant — Azure
     configuration, owner act (`saas_operations_doctrine.md` §8).

- **D39 — Pending work crosses sessions in the REPO, not in the owner's head.**
  *(owner-directed 2026-08-14: "I don't want to remember anything… create a
  system so old sessions can put information into and remove information from
  the system prompt as needed.")* `project-docs/HANDOFF.md` is a **queue of
  actions**, injected at session start by `.claude/hooks/session-handoff.mjs`
  and maintained by `/handoff`. Sessions append when they create an obligation
  and **delete** when its Check passes.
  ⚠️ **The design constraint that shapes everything else: this must not become a
  second board.** §2 is the only current-state authority, and a mutable queue
  that also described state would be the CLAUDE.md §5 defect in its purest
  form — two descriptions of one truth, and the *stale* one trusted precisely
  because it is the one loaded into every prompt. So an entry may never restate
  state; it carries a **Check**, a cheap read-only command whose output says
  whether the entry is still real. A stale entry then costs one command and gets
  deleted, and it can never be quietly believed, because believing it requires
  running something that would have contradicted it. Two Checks were wrong in
  the first draft and both were caught by running them — which is the mechanism
  working, not an argument against it.
  **Why a hook rather than a line in CLAUDE.md:** CLAUDE.md is the router, edited
  when the architecture changes; a queue changes every session, and a stale entry
  inside a stable document is indistinguishable from doctrine. Separate file,
  separate lifetime, separate trust level. The hook does **not** execute Checks —
  running commands out of a markdown file at session start would be an
  arbitrary-execution seam pointed at the one file every session loads — and it
  fails open and silent, so a missing or malformed queue can never stop a session
  starting. **R7 fence:** `.claude/hooks/session-handoff.test.mjs`, blocking in
  CI beside `plan-guard`'s, covering the silent failures (no file, garbage file,
  content below `# DONE` resurfacing as live work, `[OWNER]` surviving into the
  injected text).

- **D38 — Billing is a surface we own, and the tax invoice is OURS to issue.**
  *(owner-directed 2026-08-13: admins must be able to download bills for AI usage
  AND subscriptions, monthly, see all of them, and manage their billing. Owning
  spec `subscription_console.md` **SC-5**.)*
  1. ⚠️ **Corrects a wrong assumption carried since WS-30 was written.** Its
     non-goals called invoicing and tax *"the processor's job"*. **That does not
     survive Indian GST:** Razorpay collects money and issues a *payment
     receipt*; a **tax invoice** is the obligation of the **supplier of record**,
     which is us. It must carry OUR GSTIN, our serial, the SAC code, the place of
     supply and the CGST+SGST vs IGST split — none of which a processor can
     assert on our behalf. Left as-is, a year of invoices turns out
     non-compliant all at once.
  2. **One billing home, two document types.** The admin sees one chronological
     list; underneath, a seat subscription (recurring, monthly) and a credit pack
     (discrete prepaid sale) are **separate documents**, because they are
     separate taxable events. Merging them would mean delaying the credit invoice
     to the cycle boundary or back-dating the subscription — both wrong.
  3. **Three rules that are law, not preference, and all three constrain the
     schema:** invoice serials are **gapless and sequential per financial year**
     (April–March) and allocated at *issue* not at *attempt*, so a failed payment
     consumes no number; **an issued invoice is never edited**; corrections are
     **credit notes** referencing the original. SC-4e's ledger adjustment and a
     credit note are two halves of one act.
  4. **Download is the deliverable, not a list view.** Per-invoice PDF plus a
     period export, all history, forever, with **no processor round-trip** —
     which is also what keeps it working if we ever change processor.
  5. **The billing profile is admin-editable**: legal name, GSTIN, billing
     address and **state** (it decides the tax split, so it is not cosmetic),
     a billing contact distinct from the login, and a PO/cost-centre reference
     that mid-size finance teams require. A GSTIN edit is a tax-relevant act and
     lands an audit row; a state change affects the NEXT invoice, never a past one.
  6. **E-invoicing (IRN) is designed for, not built.** Mandatory above ₹5 crore
     turnover; SC-5b's field set is deliberately the IRN field set, so compliance
     later is registration plus a QR code rather than re-modelling invoices.

- **D37 — The AI-credit product: how customers buy, and how they find out.**
  *(owner-answered 2026-08-13 in session; owning spec `subscription_console.md`
  SC-4, which was previously a one-sentence placeholder. The credit ENGINE was
  well specced — ledger, rating, rollover, gate, member caps; the credit
  PRODUCT was not, and this closes that.)*
  1. **Fixed packs, self-serve** via Razorpay — not a free-text amount. Every
     pack is priced **under ₹15,000** so a repeat purchase never trips the RBI
     e-mandate AFA threshold, which would otherwise demand an OTP from a
     customer who is already dry mid-workflow (D33.4b). **Auto-top-up ships OFF**
     and opt-in, reversing §3.3's "default for paid plans": an auto-charge that
     silently fails at the cap is worse than one that never existed.
  2. **Alerts are delivered, not merely rendered.** Email to billing admins at
     80% / zero / failed top-up, an in-app banner while the condition holds, and
     **the affected member is told why their own call was refused and who to
     ask** — without figures. The 80% state was previously specced only as
     something the console draws, which requires the admin to already be
     looking. Once per cycle, not per call. *Operator-side push was considered
     and declined: CP-8's console already shows burn across all customers.*
  3. **Runway, not just balance.** "About N days left at your current rate" is
     the number that prompts a purchase; a balance alone is not.
  4. **Adjustments are ledger rows, distinguishable forever.** Goodwill credits
     after a runaway agent get `reason='adjustment'` plus a mandatory note —
     never an edit, and never indistinguishable from a purchase. 🔴 OWNER-GATE
     against a live org.
  5. **A monthly usage statement** the customer can export, read from
     `usage_rollup` so it survives the 90-day raw retention.
  6. **GST on prepaid credits — working assumption: TAXED AT PURCHASE**
     *(owner, 2026-08-13: "GST will probably apply".)* Credits are read as a
     **single-purpose voucher**: the supply is identifiable at issue — one
     supplier, one service, one SAC, one rate — which is the condition that puts
     the tax point at issue rather than redemption. **This unblocks SC-4a**,
     which may now issue a tax invoice.
     Two consequences built in rather than discovered: **(a) consumption is not
     a taxable event**, so SC-4f's usage statement is *informational* — no
     serial, no tax line, and it says so on its face, or the same supply is
     taxed twice; **(b) expiring or unused credits need no adjustment**, since
     tax was discharged at purchase and D32.6 already makes credits
     non-refundable in cash. That the two rules agree is a point in favour of
     the reading.
     ⚠️ **Still confirm with a CA before the first invoice goes out.** The design
     is robust either way: `invoice.tax_point` (`purchase | redemption`) is
     recorded **per document** rather than assumed in code, so a reversal is a
     policy flip plus credit notes — not a re-model. **"Probably" is not
     settled, and never appears in customer-facing copy.**

- **D36 — Fracktal Works is customer zero, and gets no special path.**
  *(owner-directed 2026-08-13: "Fracktal Works itself becomes another
  organization that is signed up as a tenant.")*
  1. **Fracktal signs up through the same provisioning flow as any customer** —
     same `/orgs/provision`, same trial, same seat cap, same credit ledger, same
     lifecycle. It is an `organization` row, not a hard-coded identity.
  2. ⚠️ **Therefore: no first-party bypass, anywhere.** No `if org == 'fracktal'`,
     no "internal" flag that skips the seat cap or the balance gate, no
     `EXECUTIVE_EMAILS` shortcut into a customer-shaped surface. Every such
     branch is a code path that only WE exercise, which means the path our
     customers use is the one nobody tests. Recorded as a rule because the
     temptation arrives disguised as convenience, usually at 6pm before a demo.
  3. **The `default` bootstrap org stays** (`acb_auth/access.py`
     `_BOOTSTRAP_ORG_SLUG`) as the first-run path for a **fresh box**, and is
     not Fracktal's tenancy. D33.5's finding is unchanged: provisioning a
     *customer* organization never routes through that constant.
  4. **This is the dogfooding argument, and it is the point.** If seat
     exhaustion, a suspended-state lockout or a credit soft-block is unpleasant,
     we feel it first and on our own operations — which is the only reliable way
     these get fixed before a customer meets them.
  5. **Consequence for the test deployment:** the acceptance bar for "the
     platform works" is *two* organizations provisioned through the same API,
     with data isolation demonstrated between them — not one org that happens
     to be us. MT-1i's two-org fixture is the tenant-side half of the same
     proof.

- **D35 — Two consoles, two deployables: the customer's inside the product, ours
  outside it.** *(owner-accepted 2026-08-12 — agent-recommended in session, owner
  replied "everything looks good"; sub-parts 3 and 4 are the ones a future agent
  would otherwise "fix". Owning specs: `subscription_console.md` (WS-30) and
  `saas_multitenancy.md` §4.1a.)*
  1. **Subscription Console — inside Metorite**, at `/settings/billing` in
     the workbench, admin-gated, one org, tenant-scoped. Unchanged from
     `subscription_console.md`; restated here so both halves sit in one place.
  2. **Operator Console — a SEPARATE Next.js app** in this monorepo, deployed at
     its own hostname. Chosen over a gated route tree inside the existing
     workbench because *"they share tables and must never share routes"* is then
     enforced **structurally** — the surfaces are different applications — rather
     than by a guard that is one misconfiguration away from serving cross-customer
     data into a tenant's UI. **R7: the fence is the deployment boundary itself,
     not a test.**
  3. ⚠️ **The Operator Console SHOULD pin one directory — ours.**
     This is the exact assumption D33.1 recorded as fatal, and it is **correct
     here**: the console is staff-only, and every user of it *is* in our
     directory. **Do not "fix" this to multi-directory** — that would widen a
     cross-customer surface to identities we do not control. D33.1 binds the
     *customer* product; this is its inverse and the distinction is the point.
     ⛔ **The MECHANISM moved on 2026-09-01. D70 makes the directory GOOGLE
     WORKSPACE, and `hd` the claim.** This clause read *"one Microsoft Entra
     directory"* until then. The intent above is unchanged, and it still
     binds.
  4. ⚠️ **The Operator Console is EXEMPT from the theming engine**
     (`workbench/control_plane/AGENTS.md`'s eight rules and its conformance
     suite). "One product, one look" exists for surfaces **customers** see; this
     is an internal tool for a handful of staff. Exempt structurally — it is a
     different app, so the conformance suite does not scan it — which is why the
     exemption must be *written down* rather than merely observed.
  5. **Neither console is on the critical path to customer #1.** Provisioning,
     seat assignment, credit grants and balance reads all work through the
     Control Plane API today (CP-1). The console makes that pleasant; it does not
     make it possible — which is why the **Operator** console is CP-8 and
     deliberately last. **Revenue order is enforcement, then checkout:** CP-3 →
     CP-4 → CP-6 makes credits real and sellable against a hand-issued invoice
     (D19.4's manage-only launch posture), and **CP-9's** self-serve checkout
     follows once manual invoicing is the bottleneck rather than the plan.
     Shipping checkout before enforcement would mean taking money for something
     we cannot yet limit.
     > ⚠️ **Corrected 2026-08-18.** This clause said *"CP-8's self-serve
     > checkout"*. CP-8 is the Operator Console and reconciliation and always
     > was; the checkout's payment seam had **no ticket anywhere** until it was
     > minted as **CP-9** (`specs/customer_console.md` §6). Two consequences of
     > this clause that a dispatching agent must read together: the ordering
     > binds the **flip to live checkout**, not the dark build — CP-6's
     > mechanism is already BUILT with its refusals OFF behind
     > `CUSTOMER_CONSOLE_SPEND_GATE` — so CP-9 and WS-30 SC-4a/SC-4g are
     > dispatchable now, and the four-item flip set is in `subscription_console.md`
     > SC-4a.

- **D34 — Supabase is the Control Plane's database AND its authenticator.**
  *(owner call, 2026-08-12: "Let's use Supabase for auth configuration." Owning
  spec: **`specs/customer_console_infrastructure.md`** *(pre-D41:
  `control_plane_infrastructure.md`)*, whose §4 recommended exactly
  this and whose §3 disqualified Firebase on technical grounds.)*
  1. **Managed Postgres, Mumbai (`ap-south-1`)** for DPDP residency (§2 R-f).
     CP-1's migrations apply **unchanged** — Supabase *is* Postgres, which is
     why this decision was safe to take late and stays reversible by `pg_dump`.
  2. **Supabase Auth is the authenticator** — Google, Microsoft and email/OTP for
     customers on no directory at all. This is the half of CP-0 that was never
     code: the providers are wired, but *operating* per-customer SSO is work we
     now buy instead of build.
  3. **NextAuth keeps issuing the Metorite session; Supabase Auth becomes
     one upstream provider inside it** — *not* a replacement. Chosen over
     swapping NextAuth out because it (a) preserves **D32.4 exactly** (the
     Control Plane owns the registry, CC issues sessions), (b) keeps CP-0's
     conditional-provider wiring and its 16 fences rather than discarding them,
     and (c) does not cut the **live auth path** of a running system in the same
     quarter as everything else here.
  4. **Supabase Auth authenticates; it never decides entitlement.** Seats,
     membership and credits stay the Control Plane's authority. A signed-in
     person with no seat is still refused at `/registry/resolve` — sign-in and
     *admission* are two questions and Supabase answers only the first.
  5. ⚠️ **Do NOT adopt Supabase's client-direct + RLS idiom.** That is the
     *tenant* plane's model and explicitly wrong for this plane, which is
     cross-tenant by design (§0.9.2). Our FastAPI service stays in front; no
     browser talks to Supabase directly.
  6. 🔴 **OWNER-GATE:** creating the project, configuring providers, and holding
     the keys. Everything above is buildable against fixtures without them.

- **D33 — The personal-brain assumptions that do not survive, and the two nobody had
  found.** *(owner-directed 2026-08-12 — "we might have made a lot of decisions
  initially that are not scalable anymore… create documentation to overwrite some of
  the initial architecture decisions"; audit `agent-taken` and measured against the
  working tree the same day. Owning spec: **`specs/saas_operations_doctrine.md`** —
  §2 the eight capability domains, §3 the Indian commercial/legal layer, §4 the
  twelve-finding audit, §5 the gap table.)*
  1. **Two findings are NEW, in one file, and both block customer #1 —
     not customer #2.** `workbench/control_plane/src/auth.ts`: **(a)** sign-in is
     pinned to **one Microsoft Entra directory** (*"the tenant-level app registration
     ensures only users in the Fracktal Microsoft 365 directory can sign in"*) — a
     paying customer's staff are not in our directory, so **today the product can
     onboard exactly one customer: us**; **(b)** auth **fails OPEN** when unconfigured
     (*"if no AUTH_MICROSOFT_ENTRA_ID_ID is set, middleware allows all traffic"*) —
     a mis-provisioned production box is wide open and reads as working. **No existing
     ticket covered either.** Now **CP-0**, ordered ahead of the whole WS-31 sequence.
  2. **There is no signup.** No `signup`/`register`/`onboard` route exists in the app
     tree; the only way in is `ensure_owner_bootstrap()` promoting an
     `EXECUTIVE_EMAILS` address (`acb_auth/access.py:581`). Invite-only is a product
     decision; *no signup at all* is a missing product. Now **CP-2a**, with
     provisioning required to be **idempotent and resumable**.
  3. **Not every old convenience is a defect, and one must NOT be "fixed".**
     `ProviderKeyStore._resolve_org` resolving "the sole organization" and **raising
     once a second exists** (`acb_llm/key_store.py:124`) is the correct pattern — it
     **fails closed and names its successor**. Recorded so a future agent does not
     helpfully replace it with a silent default. `colleague_onboarding.md` likewise
     stays as-is: it is the **internal staff** path and must not be merged with the
     customer path, because a customer is not a colleague — no shared directory, no
     shared threat model.
  4. **Three build-order changes with legal, not architectural, reasons**
     (`saas_operations_doctrine.md` §3, sources dated in §9 — re-check before relying):
     **(a)** capture **GSTIN + registered state at signup**, in the org-creating slice
     — SaaS is an 18% service supply and B2B invoices need both; **(b)** the **RBI
     E-mandate Framework 2026** clears recurring debits without OTP only to
     **₹15,000/transaction**, which makes ~25 seats at ₹600 a real product boundary,
     makes annual billing a friction escape rather than just a discount, and means
     **auto-top-up above the cap will fail exactly when a customer is out of credits
     mid-workflow** — so cap the default top-up below it and treat a failed mandate as
     the §3.3 soft-block path; **(c)** **model the DPDP consent record now**, while the
     tables are empty — *model training / product improvement / benchmarking are named
     secondary purposes needing their own consent*, which for an AI product is the
     sharp edge (Consent Manager obligations Nov 2026, full compliance May 2027).
  5. **Two capability domains have NO owner** and need one before customer #1:
     **compliance** (§3.3, and it carries a date) and **per-tenant restore** (D31 /
     `saas_multitenancy.md` §6.6 — a whole-cluster restore rolls every other customer
     back). Naming them here is not the same as ticketing them; both still need a
     seven-point contract before dispatch.
  6. **The audit is the record; do not re-run it from the same starting point.**
     Findings 6, 7, 9, 10 and 12 were already tracked (MT-1i, §6.3, D31, D32.7, D10) and
     are listed in §4 only so one table answers "what did we assume as a personal app".

- **D32 — The platform engine: eight calls taken 2026-08-12** *(owner-directed in
  session — "Metorite is evolving to a SaaS platform… I want to rework the way
  we set up model access"; the sub-calls marked `agent-taken` were delegated
  explicitly: "you can take the decisions accordingly". Owning spec:
  **`specs/customer_console.md`** *(pre-D41: `platform_control_plane.md`)* (WS-31).)*
  1. **⚠️ `saas_multitenancy.md` §3.1 is REVERSED.** Its blockquote —
     *"Do not reintroduce a separate proxy. The gateway's /v1 already IS the
     proxy"* — no longer binds. **The reason it gave still binds**: Metorite
     must not grow a second tenancy boundary, and it does not, because the Router
     sits OUTSIDE CC as a supplier and never resolves a CC session. What §3.1 did
     not weigh is **§5.1's silo rollout**: with one deployment per customer,
     "meter inside Metorite" puts the rate card, the margin and the credit
     balance on the customer's own box, N times over. A meter the metered party
     hosts is a suggestion. §3.1 carries a banner pointing here.
  2. **The Control Plane is ONE CENTRAL SERVICE, built at full scope now** —
     org registry, subscriptions, seats, AI metering and routing together, not AI
     alone. This is `§0.9.2`'s Control plane extracted from each deployment rather
     than a new concept, and it pulls MT-2/MT-4 forward of their board position.
     *(Owner chose full scope over the narrower agent recommendation; the size
     was flagged and accepted.)*
  3. **D15 is untouched.** Tenancy stays a ROW (`organization_id` + FORCE RLS at
     the seam); the deployment stays a PLACEMENT. Silo-vs-pooled remains a
     `tenant_placement` row. The Control Plane is what makes that indirection
     worth having — it can move a customer between placements; N sovereign
     deployments cannot.
  4. **Identity: the Control Plane owns the registry; Metorite keeps issuing
     sessions** (`agent-taken`). Global `user_identity`/`org_membership` authority
     moves central; NextAuth Google SSO and the session cookie stay exactly where
     they are, resolving against the registry on sign-in and caching the answer
     into migration 159's (currently inert) tables as a projection. **No
     authentication server is built and nothing on the live auth path is cut
     over.** Cost accepted: deprovisioning is cache-TTL fast, not instant. The
     full-IdP option is not foreclosed — the registry table is the same either way.
  5. **Seat semantics, defined once** (`agent-taken`): purchased =
     `SUM(seat_grant)`, assigned = live `COUNT(seat_assignment)`, available =
     the difference. **Membership is the Core seat (D19.3, unchanged)** and a seat
     is consumed at **first successful sign-in resolution**, not at invitation —
     an invited person who never signs in costs nothing. D19.3's hard cap is
     carried verbatim: over-assignment is a **409 with a buy-more payload**, never
     an auto-upgrade.
  6. **Credits roll over indefinitely while the subscription is active**
     (`agent-taken`, owner-stated intent "credits that roll over monthly").
     Balance is a ledger sum, so rollover is the default and expiry is what would
     need machinery. Not refundable in cash; expire only at `cancelled` + export
     window. No purchase-lot tracking until a grant with an expiry exists.
     **D19.2 is carried unchanged** — ₹10 credit, fractional draw at provider
     cost × 2, INR-native rate card, LLM + per-minute STT metered, embeddings and
     WhatsApp per-number fees absorbed into package prices.
  7. **Model access is reworked: customers never see a model** (`agent-taken`).
     Tiers are the only vocabulary outside the Router; `_TIER_ALIAS_MAP`
     (`acb_llm/client.py:86`) becomes a cache of a Router-published **tier
     capabilities document**, not truth. The customer-facing provider/model/tier
     picker is **removed from the product** (it was correct for a personal
     application where the operator was the user). A bare model id is **rejected
     400, not coerced** — silent coercion (`_model_resolution.py:35`) hides a
     misconfigured agent behind a bill. Consequence: adding `tier-vision` /
     `tier-embed` / `tier-voice` later is a data change, so **this decision
     deliberately does not decide them**.
  8. **Per-member metering and WS-16 Phase E are ONE mechanism**, as the board
     already required ("design once, serve both"). Credits are an **org pool**;
     a per-member cap is a policy against the pool, never a sub-wallet, so
     unallocated headroom is never stranded. Default on exhaustion is **degrade to
     `tier-fast`**, not block — which is itself an argument for keeping tiers.
  > 🔴 **Unchanged prerequisite:** per-org keys are meaningless until
  > `GATEWAY_INTERNAL_TOKEN` is split from `LITELLM_MASTER_KEY` (§6, measured
  > 2026-08-05 as byte-identical). Owner's act, via redeploy.

- **D31 — D15 re-tested against Odoo, Salesforce and SAP; it stands. One gap found.**
  *(owner-raised 2026-08-11 — "Odoo is also a large complex app with multiple sub-apps
  for one company; is shared-database still ideal?"; agent-answered,
  `agent-proposed, owner may overrule`.)* Full record in **`saas_multitenancy.md`
  §0.9.9**; do not re-run the comparison from the same starting point. **The rule it
  yields: what forces a database per customer is not customization, it is
  customization implemented as DDL.** Odoo adds a custom field by issuing real DDL and
  keeps modules/views/rules as rows in the tenant database, so its tenants cannot share
  one — a fact about Odoo's implementation, not about RLS (Odoo's own `ir.rule` is
  row-level filtering, trusted to separate legal entities inside one database).
  Salesforce/ServiceNow/NetSuite run the deepest per-tenant extensibility in the
  industry **pooled**; SAP's answer to the same pressure was to constrain *how*
  extension is expressed (key-user + side-by-side on stable APIs), not to silo. We are
  already on the metadata side and shipped it: `155_projects_custom_fields.sql` —
  definitions as rows, values as JSONB + GIN — and apps as rows (migration 114).
  **Recorded against ourselves:** SAP CAP runs §7 item 1c's schema-per-tenant in
  production, and it survives rejection here only because *we* ship continuously
  against a 160+ file ladder applied before restart with no rollback — so §7 item 1c
  now carries that as the first rejection to re-open if our cadence changes.
  **The one genuine finding: there is no per-tenant restore — only a whole-cluster one
  (§6.6).** Due before customer #2, not before customer #1 (with one tenant they are the
  same operation). **Caution carried:** keep custom fields typed and narrow; a general
  EAV/metadata platform is a priced feature with its own spec, not a pattern to spread.
- **D30 — `CLAUDE.md` at the repo root is the always-loaded briefing.**
  *(owner-directed 2026-08-10 — "any cloud instance must understand the
  development philosophy, the work plan and the architecture at all times".)*
  Claude Code auto-loads `CLAUDE.md` into **every** session including cloud and
  headless ones; the repo had none, and the 16 KB root `AGENTS.md` is not
  guaranteed to be in context at session start — so a cloud instance began
  blind. `CLAUDE.md` is a **router plus the minimum that must be known before
  the first tool call**, and deliberately duplicates nothing: where truth lives
  and in what order (INDEX → §1 rules → §2 board → §6 gates → §3 decisions →
  engineering practice → owning spec); the architecture in one screen (Centers
  are projections · apps have two access paths and one permission model ·
  modules are billing atoms and Center packages are what customers buy · tenancy
  is a row and we are multi-tenant from customer #1 · visibility is
  private→Center→org); the non-negotiables (never commit on main, refuse
  owner-gated work by name, R5/R6/R7/R8/R1, verify delivery by evidence); the
  development method (one narrowed slice, audit → implement → verify → review →
  PR → stop, verifier ≠ implementer, adversarial reviewer, re-verify anchors,
  extend seams never fork them, short branches); what NOT to do (re-litigate
  D1–D29, refactor to conform, build a second way to do an existing thing, mark
  done from the write-up); and the environment traps that waste the most time.
  **Rule: `CLAUDE.md` stays a router.** Anything that grows into content belongs
  in an owning spec with a pointer here — a briefing nobody finishes reading is
  a briefing nobody follows.
- **D29 — The agent harness is tracked in git; derived indexes are not.**
  *(owner-raised 2026-08-10 — "cloud instances should use the full development
  philosophy"; `agent-proposed, owner may overrule`.)* `.claude/` was a **blanket
  gitignore**, so every checkout that was not the owner's laptop — a cloud Claude
  instance, a fresh clone, a headless run, an agent's own worktree — ran with
  **no `plan-guard`** (i.e. no §6 owner-gate enforcement at all), no
  supervisor-worker agent definitions and no `/next-ticket`. A safety control was
  travelling by accident. **Now tracked:** `.claude/AGENTS.md`, `agents/`,
  `commands/`, `hooks/` (`plan-guard.mjs` + test, `rtk-bash.sh`),
  `settings.json` — scanned, no secrets. **Still ignored:**
  `settings.local.json` (per-machine), `worktrees/` (ephemeral checkouts),
  `scheduled_tasks.lock`, `skill-observations/`, `skills/` (third-party bundles —
  tooling, not doctrine). **Derived artifacts stay ignored and are rebuilt:** the
  CodeGraph index (`.codegraph/`) would be large, churn every commit and conflict
  every merge; its config (`.mcp.json`, `codegraph.json`) is already tracked, so a
  fresh environment installs the tool and builds its own. General rule: **anything
  derivable from source is rebuilt on the new machine; only sources of truth are
  tracked.** Fence (R7): `node .claude/hooks/plan-guard.test.mjs` now runs
  **blocking** in `pr-check`. Recorded in `engineering_practice.md` §5.1.
- **D28 — The development doctrine is written down and three of its rules
  bind.** *(owner-requested 2026-08-10 — "document all of this for our
  development process"; the doctrine itself is `agent-proposed, owner may
  overrule`.)* Owning spec: **`specs/engineering_practice.md`** (CONTRACTS &
  DOCTRINE in `INDEX.md`). Two premises: **(i)** our failures are *delivery and
  integration* failures, not coding failures — the evidence table in §0.1 is
  seven measured incidents from one fortnight, none of which a unit test could
  see; **(ii)** *a rule binds an agent only when a test refuses to break it*,
  which R5 already said and R7 now generalises. Recorded as binding: **R6**
  expand/contract migrations (the deploy applies migrations before restarting
  services, so old code always meets new schema), **R7** name-the-fence, **R8**
  real-database verification for SQL plus verified-red-first and mutation
  testing. Recorded as doctrine, not enforced: deploy≠release with **ring order
  us → one friendly customer → all** (the silo phase makes rings free), seam-based
  work partition with short branches and a **3–4 in-flight cap** (long branches
  are the single root cause behind the renumber trap, the cross-PR fences and the
  duplicated tenancy design), the adversarial-reviewer rule, and the
  customer-grade definition of done (delivery verified by evidence, never by a
  green job). §8 lists five items to close before customer #1, each pointed at an
  existing board row — SHA-in-`/health` (WS-25), off-box backup (BO-23,
  owner-deferred and **due for revisit**), migration rehearsal against a
  prod-shaped restore (WS-5, new), RLS promotion (MT-1b), lifecycle-sweep tenant
  binding (MT-1d).
- **D27 — The WS-27 × WS-29 alignment audit.** *(agent-run 2026-08-10 at the
  owner's request; findings fixed or recorded the same day.)* **Verdict:
  substantially aligned** — the Projects app's 19 `pm_*` tables are all
  tenant-keyed, `routes/projects/` uses the one shared seam with no Redis and
  no route reading a tenant from request input (R5 a–e hold), and no `pm_*`
  column repeats the CRM `organization_id` homonym. **Three gaps, all closed or
  ticketed:** (1) **the generated RLS set was stale and contained a phantom** —
  `pm_intake`/`pm_task_watchers` had no policy in any phase file (they would
  have promoted tenant-keyed but cross-tenant readable), and
  `04_policies.sql` carried `ALTER TABLE if ENABLE ROW LEVEL SECURITY`, a
  statement that cannot run, in a script promoted **by hand against production**;
  root cause was the generator regexing `CREATE TABLE` out of comment prose.
  Fixed: comments stripped, phantom names now RAISE, set regenerated (135
  tables), and a new CI fence compares the committed artifact against
  `discover_tables()` — nothing did before. (2) ~~**`run_lifecycle_sweep` sweeps
  every tenant's projects with no predicate on a scheduled path H2 never
  reaches**~~ ✅ **CLOSED 2026-08-10 by WS-27aa** — the sweep takes a required
  `organization_id` (blank → `TenantUnbound`, before any statement) and filters
  roots on it; `_pm_lifecycle_sweeper` resolves that tenant from the workflow
  **owner** through `app_user` (the `workflows` table has no `organization_id`
  until H3 phase 1) and binds `tenant_session(org)`. MT-1d's own "it needs a
  per-tenant loop" is struck: the loop is over *workflows*, one per tenant —
  a loop inside the sweep would be the unbounded-job shape H4 forbids. Proven
  two-org against a real Postgres (`tests/live/live_ws27aa.py`, 23 checks). (3) **`resolve_organization_id` resolves the tenant from
  inside the session and encodes one-person-one-org** — recorded on **H2/H6**,
  which also gained the measurement that 21 `app_user`-derived org reads across
  6 modules sit behind H6's one-line "app_user reads are gone" criterion. Also
  swept: migration 161's header now covers the generated **phase 3** (duplicate
  FK + the 17 indexes it argues against), `intake.py`'s recursive descent
  repeats the tenant predicate like `core.py`'s, and
  `project_management_app.md` — which had cited **no tenancy decision at all** —
  now cites D15 and records D23's placement of Projects inside Center packages.
- **D26 — The docs consolidation: `ai-company-brain/` → `project-docs/`, and an
  ACTIVE/DEFERRED classification of record.** *(owner-directed 2026-08-10 —
  "retire what is not in the immediate work plan, make active vs deferred
  clear, simplify so agents focus on required code".)* The folder renamed
  (261 tracked files swept, CI path filters included); **`project-docs/INDEX.md`
  is the classification of record** — ACTIVE (dispatch) · CONTRACTS & DOCTRINE ·
  REFERENCE · DEFERRED/HISTORICAL (19 files banner-marked, dispatching
  nothing). Standing rule: a new spec enters INDEX in its creating PR, in
  exactly one section; leaving ACTIVE takes a banner in the same PR. The
  `docs/` root stays engineering reference (one ACTIVE owning spec lives
  there: `docs/multiplayer/memory-clearance.md`). #399's docs classified:
  `multi_tenancy.md` superseded-measured-record, leak audit = MT-1i reference,
  branch HANDOVER historical, plane research reference-only.
- **D25 — Seven standing gates answered in one round (owner, 2026-08-10).**
  1. **Identity/IdP (MT-1a):** homegrown auth through the silo phase; an
     external IdP (WorkOS/Clerk/Keycloak) becomes a precondition of the §5.1
     pooled cutover **or** the first enterprise SSO ask, whichever first.
  2. **Version skew (§1.4b): meaning B only** — one codebase, one schema,
     always latest; orgs differ by per-org feature flags + release channel
     (`org_feature_flag`). A/C/D are not supported and no spec may assume them.
  3. **WS-14 C4 approvals routing:** **Center leads approve their Center's
     actions** — approver role on the `org_group`, proposals carry a
     requesting-Center column, fallback to org admins when unset. The
     "answer Q2, then add a column" ticket is now dispatchable.
  4. **WS-10 floor control: CUT.** Steer suffices; the five floor modes, turn
     queue, observer lane, handoff-with-note and HITL floor-holder routing are
     retired unbuilt. WS-10's remaining mandate is the S1 `subject:` slice
     (+ the still-gated prefs/user backfill APPLY).
  5. **WS-24 N5:** the nine unscoped `routes/notes` modules do **not** block
     colleague #1; the D12 grant-table treatment is owed **before colleague #2
     or any external member**. · **D14(ii) closed:** `manager` keeping the
     `/admin` read surface is acceptable — department privacy protects work
     content, not the org chart.
  6. **WS-12 Phase 4.0: minimal bump** (SDK 0.1.32 → 1.0.2, breaking-fixes
     only). Phase 4.1 evidence → 4.x now dispatchable; the 4.6 human soak
     remains owner-gated. · **WS-20 OCR: fast/cheap vision tier** via LiteLLM;
     per-call escalation later; Meta env/app review remains its own gate.
  7. **WS-22 draw.io: ⏸ PARKED by owner** (officially; re-enters when a real
     need pulls it). · **Guest access to Centers: deferred** until the first
     real guest; the standing rule holds — no external sharing before that
     design exists.
- **D24 — The customer-framing round: §8 item 5 CLOSED (owner, 2026-08-10).**
  1. **The ₹600 headline stays** — Core is the advertised base; the agent's
     "Workspace ₹1,200 default" framing was considered and REJECTED.
  2. **Slices-only Centers stay ₹300**, pitched as "a full team workspace"
     (projects, knowledge, dashboards for that department), never as a filter.
  3. **An all-Centers seat exists: ₹1,800/user** — every Center package, NO
     add-ons (Builder/Workflows stack on top). The multi-hat relief.
  4. **Complete reprices ₹3,600 → ₹3,000** — the all-Centers seat made ₹3,600
     dominated (full stack = ₹3,200); Complete = Core + all Centers + both
     add-ons at ₹3,000, keeping D20.5's all-GA + price-protection promise at
     the new number. Ladder of record: 600 · 1,200 · 1,800 · 2,400
     (Core + all-Centers) · 3,000 (Complete).
  5. **Role presets ship in SC-2's launch scope** ("Sales rep", "Field staff",
     "Founder" generate the users × Centers grid). Also standing: a
     typical-month credit anchor on the pricing page; internal vocabulary
     (atoms/slices/modules) never appears customer-facing.
- **D23 — Pricing is CENTER-SHAPED: Center packages are the sales object,
  modules stay the billing atoms.** *(owner-directed 2026-08-10, four knobs
  answered in session; supersedes D19's module-first customer framing and
  **D20's Team/Business tiers** — Complete survives, recast below. D19's credit
  model (₹10 fractional unit), seat rules (hard cap · member = Core seat ·
  processor proration) and Complete's price-protection promise (D20.5) all
  carry over unchanged.)*
  1. **Three layers.** **Core ₹600/member, mandatory** (basic AI chat — the
     front door stays lit and burns credits — personal tasks, calendar, people
     directory, personal dashboard, approvals, admin plane). **Center packages,
     per user per Center**: each bundles the Center's own apps **plus that
     Center's slice of the base cross-cutting set (Projects, Knowledge Base,
     Dashboards)** — app-bearing Centers **₹600** (Personal = Email + WhatsApp +
     Meetings, **optional per user, never mandatory**; Sales = CRM incl. CPQ;
     Marketing; Finance; Support), slices-only Centers **₹300** (R&D,
     Operations, People until its HR apps ship). **Org-wide add-ons, per user,
     light up in all the user's Centers**: Builder (App + Agent, incl. custom
     agents) **₹500** · Workflows **₹300** (deliberately an upsell — the
     highest-leverage credit driver). **Company Center is free for leadership**
     — a projection of data the org already bought.
  2. **The unification that kills the admin complexity: one assignment act.**
     Granting a user a Center package = the billing seat + the `org_group`
     membership + the module entitlements + the D12 slice grants, one row;
     unassignment reverses all of it. The console's seat surface becomes a
     **users × Centers grid** (+ an add-ons column) — the customer's org chart
     IS the invoice. WS-30's SC-2 inherits this shape.
  3. **Union semantics.** A multi-Center user holds each shared module once
     (never billed twice for a module) but pays per Center, because each
     package buys a new *slice of team data*, not a re-purchase. Enforcement is
     untouched: MT-2 modules at the seam + D12 grants; schema delta is one
     `center_package(center_slug, module_slugs[], price)` catalog beside
     `plan_catalog`, and seat `source` gains `'center'`.
  4. **Bundles.** Team and Business (D20) are retired — Center packages replace
     them. **Complete recast** *(price agent-proposed, owner may overrule)*:
     all Centers + both add-ons + Core, ~~₹3,600~~ **₹3,000/user/month (amended
     by D24.4 — the all-Centers seat made ₹3,600 dominated)**, keeping D20.5
     verbatim — every GA Center and
     add-on, always, price-protected per contract term, wildcard row.
  5. **Worked seats (as amended by D24):** shop-floor member ₹600 · desk worker
     (Core + Personal) ₹1,200 · sales rep (+ Sales) ₹1,800 · Core + all-Centers
     seat ₹2,400 · everything (Complete) ₹3,000.
- **D22 — The Center roster of record, and four architecture calls.**
  *(owner statement + question round, 2026-08-10; owning spec
  `department_centers.md` §5.)* The full shape: **Personal Center** (per-user
  private workspace — Email/WhatsApp/Meetings/Tasks/Calendar — **NOT a
  department**, no `org_group`, exists for every member automatically; the §2
  registration checklist needs a caller-scoped variant, never a fake group) ·
  **seven department Centers** — Sales, Marketing, Finance, **R&D (new)**,
  People, Operations, **Support (new)** — answering that spec's open questions
  2 and 3; **R&D and Operations launch slices-only** (cross-cutting apps
  scoped to their teams, unique apps deferred) · **Company Center kept** as the
  leadership surface (WS-15; multiple configurable rollups per D21, in-app
  all-slices filters as explicit org-tier grants). **Sales' products, price
  books, brochures and proposal generator are CRM-module features — sold only
  inside the Sales Center package (₹600, D23), never separate SKUs** —
  `crm_app.md` gains them as future phases. The admin/IT
  plane (Appearance, Membership/roles, Live activity, Integrations, Approvals,
  Agent Registry, AI credits) is Core capability surface, not a Center.
  **Amended 2026-08-10 (owner): dual access paths** — every cross-cutting app is
  also a standalone top-level app whose primary grouping is **by Center**: the
  caller sees the Centers they hold access to, each containing its slice (e.g.
  Projects opens to Center groups, each with its projects and tasks). Both paths
  resolve through the same D12 grants — the app view is the union of the
  caller's Center slices, never a second permission model
  (`department_centers.md` §5).
- **D21 — The future module roster is named.** *(owner-directed 2026-08-09;
  roadmap `agent-drafted, owner may amend`.)* Five directions beyond the D19
  catalog, recorded in **`specs/future_modules_roadmap.md`** so future specs
  start from what exists: **Knowledge Base** (graph-based org memory surface —
  new; builds on Mem0/D17, the OFF-by-design Graphiti, notes, acb_graph) ·
  **Marketing** (social + sites + ads insights — new; the Center exists, the
  module does not) · **Customer Support & Success** (tickets + customer pages +
  AI resolution — new; declares a real dependency on Knowledge Base) ·
  **Department Dashboards** (⚠️ NOT new — this is WS-15's mandate; the owner's
  statement adds scope colour: per-department *configurable* dashboards and
  *multiple* leadership rollups — carry into WS-15 acceptance) · **Builder +
  Workflows as cross-department substrate** (⚠️ NOT new — the slicing IS the D12
  doctrine; the one addition is a standing review rule: Builder apps and workflow
  definitions declare a visibility tier at creation). SKU posture *(rewritten
  2026-08-10 for D23 — no a-la-carte list, no D20 tiers)*: a future module joins
  a Center package (Marketing → Marketing Center, Support → Support Center, KB →
  the base slices in every package) or, exceptionally, becomes a new org-wide
  add-on; the spec-time owner call is placement, not a price. Sequencing:
  nothing here precedes WS-29/WS-30 or in-flight
  apps; expected order Dashboards → KB → Support → Marketing. Nothing in the
  roadmap is dispatchable until it earns an owning spec per §1 and a WS row.
- **D20 — Plan tiers: four bundles beside the a-la-carte list.** *(⚠️ **Team and
  Business SUPERSEDED by D23** (2026-08-10, Center packages); Complete survives
  recast at ₹3,600 all-in — cite D23 for the pricing shape, this record for
  Complete's all-GA + price-protection promise (part 5), which D23 keeps
  verbatim. Original record: owner-directed
  2026-08-09 — "bundle modules into three to four plan tiers"; the specific
  packaging is `agent-proposed, owner may overrule`.)* Tiers are **per-user
  assignable plans** (Microsoft-365 style: a user holds one plan, a-la-carte
  module seats stack on top), so they reuse D19.3's seat machinery — a plan seat
  expands to its module seats; no org-level plan concept exists. The ladder,
  chosen for clean ₹600 steps and standard bundle discounts:
  **Core ₹600** (the D19 base alone) · **Team ₹1,200** = Core + Projects +
  Meetings + Workflows (₹1,500 a-la-carte, 20% off — "run your team's work") ·
  **Business ₹1,800** = Team + CRM + Email + WhatsApp (₹2,400 a-la-carte, 25%
  off — the customer-facing layer, the expected default landing tier) ·
  **Complete ₹2,400** = Business + Finance + Builder (₹3,200 a-la-carte, 25%
  off — the Zoho-One-style everything play). Schema consequence for MT-2:
  `plan_catalog(slug, module_slugs[], price_per_seat_month)` beside
  `module_catalog`; `user_module_seat` gains a `source ∈ (plan, alacarte)` so
  unbundling is computable. **Included-monthly-credits per tier is deliberately
  NOT decided** — launch default is none (credits sold separately); bundling
  credits into tiers is an owner knob left open. **Amended 2026-08-09 (owner,
  final review round): Complete = every GA module, always, with price protection
  for existing subscribers within their contract term** — encoded as a wildcard
  `plan_catalog` row, never a hand-maintained list (§2.4a rule 5). Recorded in
  `saas_multitenancy.md` §2.4a; console impact in `subscription_console.md`.
- **D19 — Twelve owner calls taken 2026-08-09** *(the subscription/pricing
  clarification round, answered question-by-question in session; supersedes D18's
  SKU list and §2.4's module table wherever they disagreed — cite D19 for the SKU
  model, D18 for the parallel-plus-ratchet and board-format calls).*
  1. **SKU list of record.** **Core ₹600/user/month** = chat, memory, dashboard,
     artifacts, settings, **tasks (personal lens)**, **calendar**, **people
     directory**, **approvals**, **observability**. **Add-ons ₹300/user/month
     each**: CRM, Projects, Email, Meetings (was `notes`), WhatsApp, Workflows
     (was `automation`), **Finance**. **Builder ₹500/user/month.** Customer-facing
     SKU names are Meetings and Workflows; internal slugs may stay. Consequence
     for the one-store merge (D-PM-6/WS-27h): the Core/Projects boundary is drawn
     on FEATURES over the single `pm_tasks` store — personal task management is
     Core; portfolios, project boards, dependencies, ClickUp import/sync and
     org-wide views are the paid Projects module. A `calendar` feature slug must
     be minted (none exists today). The old paid `people` module row folds into
     Core; approvals + observability leave the `automation` bundle for Core.
  2. **The credit unit.** A **credit is the ₹10 purchase/display unit**; each
     model call draws credits **fractionally** at provider cost × 2 via the rate
     card (a cheap call ~0.2 credits, a large agent run ~15). Customers see a
     credit balance, never tokens; "AI action" is marketing shorthand for the
     credit, not a flat per-call price. **STT (Meetings) is metered as credits**
     on the same rate card; **embeddings and WhatsApp per-number fees are
     absorbed into module prices**. The rate card is **denominated natively in
     INR** — hand-maintained per-model prices, no FX machinery.
  3. **Seats.** **Hard cap**: assigning beyond `seats_purchased` is blocked with
     a buy-more prompt — purchase is always an explicit act. **Every member
     consumes a Core seat** (membership IS the Core billing event; no zero-seat
     members). Mid-cycle changes use the **processor's standard proration**.
  4. **The customer subscription console ships manage-only first.** Launch scope:
     view modules/seats/credit balance and per-module burn, assign/unassign seats
     within purchased caps, request module or seat changes (fulfilled manually
     while invoicing is by hand, per §5's Phase-2 posture). Online
     checkout/top-up arrives with MT-4. Owning spec:
     `specs/subscription_console.md` (WS-30).
  5. **Payments (§8 item 3 — closed): Razorpay only at launch**, behind the
     `payment_provider` seam; Stripe lands as a second implementation of the same
     seam when the first international customer appears.
  6. **Data residency (§8 item 4 — closed): promise India-only at launch.** All
     customer data on India-region infrastructure until a second residency tier
     is deliberately priced. Amended for email triage by D-EM-9 (2026-10-02). Mail content may go to TypeSafe and AI/ML API for a triage decision.
- **D18 — Three owner calls taken 2026-08-09** *(via the consolidation session's
  question round; recorded here so none is re-litigated. ⚠️ Part 3's SKU list is
  superseded by **D19**, and D19's module-first customer framing in turn by
  **D23** — cite D23 for anything a customer buys; module boundaries survive as
  internal atoms).*
  1. **Priority of record: parallel + ratchet.** App workstreams continue at full
     speed alongside WS-29; the price is **R5** (§1) — tenant-ready by
     construction, enforced by the shipped ratchet tests, so H2's 561-site
     conversion surface stops growing in unconvertible ways. Neither an MT-first
     freeze nor unruled parallelism was chosen.
  2. **Board format: compact rows.** §2 rows carry state + gates + pointers only;
     narrative lives in each owning spec's "Board record (2026-08-09)" section.
     Rationale: rows had reached 29.5k characters and §2 ~77k tokens — unreadable
     in one pass by the dispatch loop's supervisor, whose own contract says to
     read §2 only.
  3. **MT-2/MT-3 business inputs answered** (were the blockers in
     `saas_multitenancy.md` §8 items 1–2): **modules sell as Core ₹600/user/month**
     (Tasks, Calendar, Chat, People directory) **+ ₹300/user/month per add-on
     module** (CRM, Projects, Email, Meetings, WhatsApp, Workflows); **AI resells
     as a ₹10 "AI action" credit unit at ~50% gross margin** (rate card prices
     each model call at provider cost × 2, denominated in credits; credits sold
     via the rate card, never provider tokens). Recorded in `saas_multitenancy.md`
     §8; MT-4's payment-provider split (§8 item 3) remains the one open input.
- **D17 — Mem0 binds the tenant via connection options (Option A).**
  *(`agent-proposed, owner may overrule` — 2026-08-09; owning spec
  `saas_multitenancy.md` §0.1 path 8, shapes in `_implementation.md` §2.4.)*
  MT-1c's done-when 4 required this decision taken and written down — "leaving it
  undecided fails the ticket". The call: Mem0's pgvector conninfo gains
  `options=-c app.tenant_id=<org>` so the same RLS policies govern memory rows as
  every other table; per-tenant roles (B) add operational surface for no isolation
  gain, and scope-string-only (C) is an accepted-risk fallback nobody has accepted.
  Consequence: `org:global` memory scope becomes tenant-global, not
  deployment-global — coordinate with WS-10 S1 before adding any scope shape
  (`saas_multitenancy.md` §1.9).

- **D16 — The agent sandbox splits; the raw-SQL tool goes now, the container
  tier waits for the pooled cutover.** *(`agent-proposed, owner may overrule` —
  2026-08-08, owner delegated the call.)* MT-0c bundled four clauses of wildly
  different cost and urgency, so the cheapest and most valuable waited behind the
  most expensive. **MT-0c-1 (built):** `query_history` took a *model-generated SQL
  string* and ran it through `acb_graph` — and its keyword guard was wrong both
  ways, rejecting its own documented example (`CREATED_AT` contains `CREATE`)
  while letting `SELECT * FROM provider_keys` straight through. That is a live
  within-org read primitive **today**, so it is fixed now: search criteria, bound
  parameters, two tables, plus a build-failing ratchet against the shape
  returning. **MT-0c-2 (still parked, still OWNER-GATE):** the container/microVM
  tier. **D10's reasoning survives for the silo phase** — one tenant per box means
  an escaped agent reaches only the data it already had — so T2 becomes a
  precondition of the **§5.1 pooled cutover** (customer 8–12), not of Phase 0.
  Building Firecracker-grade isolation before customer #1 is speculative
  infrastructure paid for out of the runway that should be buying customers.
  Owner: **WS-29**, spec `specs/saas_multitenancy.md` MT-0c.
  *[Amended 2026-10-03 by **D83**, owner decision: T2 is un-parked for the
  WS-43 sandbox terminal only. The rest of MT-0c-2 stays parked, and so does
  T2 as a precondition of the pooled cutover.]*
- **D15 — The tenant boundary is a ROW, not a deployment.** *(owner-requested
  2026-08-08; re-takes **D11**.)* Tenant = `organization_id`, enforced by Postgres
  **FORCE ROW LEVEL SECURITY** bound at the `get_db()` seam with `SET LOCAL
  app.tenant_id`; the deployment becomes a *placement* (region/tier), and a dedicated
  database or stack survives as a **priced tier**, not the architecture. D11's cost
  objection — *"a `WHERE organization_id = ?` on 111 tables and every query"* — does not
  hold: connection sites are a bounded set of **eight** (`saas_multitenancy.md` §0.1) and
  **zero existing `SELECT`/`INSERT` statements are rewritten**. **D11's §2–§5 survive
  untouched** — this changes tenancy only, never visibility. Consequences: row-level
  tenancy, an org switcher, multi-org users and per-org credentials all move **into**
  scope (D11 §6 listed all four as out); leak sites 1–10 stop being moot and become
  MT-1i; and **MT-0c requires un-parking D10's T2**, because "trusted colleagues, not
  hostile users" is exactly the threat model that selling externally retires. Owner:
  **WS-29**, spec `specs/saas_multitenancy.md`.
- **D1 — Cost attribution is one workstream.** Stamp every LLM call at the
  gateway choke points with (run_id, member_email, agent, instance). Per-room
  (multiplayer §5.3), per-instance (agent-kinds §9.4), per-member and
  per-Center views are all rollups of that one record. Owner: WS-6.
- **D2 — Budget subject: member first.** Per-member monthly caps ship first
  (WS-16); per-room `token_budget` + degrade-to-read-only (multiplayer Phase 4)
  builds later on the same records. Per-group rollups after.
- **D3 — Instancing storage: columns now, manifest later.** Add the `sharing`
  columns to `dynamic_agents` now (agent-kinds §3 shape; next free migration
  number) to unblock WS-14. When WS-8 Phase A lands, those columns become
  *derived from* `agent_defs` manifests — one store, not two. The
  agent_architecture manifest is the long-term source of truth.
  **Amended 2026-08-03:** WS-14's unblock does **not** wait on WS-8 Phase A.
  `config.json`-based instancing already ships via `AgentManifest.instance_key()`
  and is live on the blob store and the workspace file manager with no schema
  change (`agent_architecture.md` §12.1/§12.5). The `dynamic_agents` columns are
  WS-14's own migration; Phase A only changes where they are *derived from*.
- **D4 — Orchestrator org-memory: patch now, unify later.** The missing
  org/agent-scope read on the orchestrator path (`agent_architecture.md`
  §11.1.2) is fixed as a small standalone defect in WS-15. WS-8's A1 runtime
  unification remains the structural fix and deletes the duplicate path.
- **D5 — Shared mailboxes:** `email_app_master_plan.md` owns implementation;
  Centers C sequences it; research §16.7 is design reference only.
- **D6 — The Workflows app won.** `workflows_app.md` + `docs/workflow-editor/`
  are authoritative for graphs, compiler, editor, and workflow-as-tool.
  `multi_agent_orchestration.md` Phases 2–3 and §5.3 are superseded; its
  **Phase 4 alone remains live as WS-12; Phase 1 was struck to WS-23 and
  Phase 5.1 to WS-11 on 2026-08-03** (Phase 5.2 shipped as multiplayer rooms
  under WS-10).
- **D7 — MCP registry exists, with a MAF-side gap.** `13_mcp_servers.sql` +
  gateway CRUD + per-run injection are live (the coherence audit missed it by
  searching for the spec's planned name — R1's disease exactly).
  `mcp_plugin_integration.md` Phase A = shipped; Phases B/C remain research.
  **Verified 2026-08-01:** `_inject_mcp_servers` runs for every agent but
  writes `agent._mcp_servers`, which only the Copilot runtime reads — for
  native-MAF agents MCP injection is a **silent no-op** (no
  `MCPStdioTool`/`MCPStreamableHTTPTool` wiring exists). Any manifest
  `capabilities.mcp_servers` promise (agent_architecture §6) is unimplemented
  on MAF until WS-8 closes this. **Retargeted 2026-08-03:** that instruction is
  now carried in the owning spec as the ticket **WS-8c**
  (`agent_architecture.md` §12.2, AGENT-SAFE) — dispatch it from there, not from
  this decision record.
- **D8 — Budgets/caps enforcement lives at the gateway choke points**, never
  per-app. (Same principle as prompt caching and model tiers: one seam.)
- **D9 — "Pomad Centre" — RESOLVED 2026-08-01.** Owner confirmed it is not a
  real venture (a stray name that should have read Metorite). All 12
  sites across 8 files rewritten as "a second tenant deployment" — the
  phrasing that preserves each sentence's meaning, including the two
  security-requirement sites (`agent_platform_hardening` §64's T2 gate now
  reads "Before multi-tenant (a second org on this platform)"). The name no
  longer appears anywhere outside this decision record. *(2026-08-09: the
  replacement phrase itself — "a second tenant deployment" — embodied D11 and
  was re-swept to organization/placement language after D15;
  `department_centers.md` §4 Q1 keeps the twelve-site inventory as history.)*
- **D10 — Two owner calls taken 2026-08-03.** Recorded here so neither is
  re-litigated by a later dispatch.
  1. **Metorite is an internal Fracktal tool.** *(⚠️ Premise re-scoped
     2026-08-08 by D15/D16: still true as a fact today — no external tenant
     exists yet — but no longer the planning posture; WS-29 exists to retire it.
     The T2 parking below survives in narrowed form: un-parking is a
     precondition of the §5.1 pooled cutover (D16), not "a second org on this
     platform, or agent authorship from outside Fracktal". Every doc decision
     that rests on this premise was annotated with its expiry trigger in the
     2026-08-09 sweep — `agent_platform_hardening_2026-07.md` §1.5,
     `permissions_sandbox_b6.md` §P5-c/d, `workflows_app.md` §1.4,
     `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-1's enforce posture,
     `meeting_bot_platform_plan.md`'s ELv2 argument.)* The team uses it; there
     are no external tenants and no third-party agent authors. **Consequence,
     already applied in the specs:** WS-3's T2 / full run sandboxing
     (`permissions_sandbox_b6.md` §P5-c) is **parked** under a
     trusted-colleague threat model — the ladder must hold against colleagues,
     not hostile users, and P5-a's credential scoping plus P5-b's ceilings plus
     WS-3a/WS-3b address the concrete standing exposures. **Un-parking is
     OWNER-GATE and has an explicit condition: a second org on this platform,
     or agent authorship from outside Fracktal.** Until then P5-c carries no
     acceptance criteria and none should be written; P5-d is blocked behind it.
     *[Amended 2026-10-03 by **D83**: the owner un-parked T2 for the WS-43
     sandbox terminal only. P5-c, P5-d and T2 for the pooled cutover stay
     parked.]*
     The same threat model is what makes `ACTION_BROKER_ENFORCE` OFF an
     acceptable posture (audit-and-chokepoint rather than per-click approval)
     and what bounds the Agent Workshop's value in `agent_architecture.md` §12.
  2. **Loops in the workflow engine are approved**, against `workflows_app.md`
     §11's standing anti-n8n rule R1. Real automations iterate; an engine that
     cannot iterate pushes makers back to the toil the app exists to remove.
     The engine-complexity cost was stated and accepted. **R1 keeps its original
     meaning unchanged — it governs the node *catalog* (a node exists only if
     the Integration Registry has the integration), not the control-flow
     *vocabulary*** — and must not be cited as a blocker on WS-11's 8.3c.
     Recorded in `workflows_app.md` §8.3c and §11 R1.
- **D11 — ⛔ SUPERSEDED: the tenant boundary is THE DEPLOYMENT.** *(owner call,
  2026-08-03 — **re-taken 2026-08-08 by D15**. Retained verbatim below as the
  decision record; do not build against it and do not cite it for tenancy — cite
  D15. Its §2–§5 visibility content was never touched; its TV-1 carve-out is
  absorbed as WS-29 MT-1i, where the three joins are re-classed from
  "wrong within one org" to "leak across tenants".)*
  One deployment per tenant: a second organization gets its own box, its own
  database, its own credential set. **Row-level organization isolation is
  explicitly NOT being built.** Consequences, each verified against code and
  recorded in `specs/tenancy_and_visibility.md` §1: `organization_id` stays a
  **label, not a mechanism** — it is on **3 of 111** own tables (`app_user`,
  `org_role`, `org_group`) and is read by **zero** authorization decisions
  (`UserContext.organization_id` is populated by an extra `SELECT` at
  `acb_auth/deps.py:155-157` and never consulted; every `WHERE organization_id`
  in the gateway binds from `get_org_id()`'s hardcoded `slug='default'`, not
  from the caller). Nine of the ten enumerated leak classes are **moot by
  definition** rather than by fix; deployment-singleton credentials
  (`provider_keys.provider` is the PK; integration secrets go into the
  process-global `os.environ`) become **correct** rather than a gap. The cost of
  a second tenant — new box, new DB migrated from zero, new credential set, DNS
  + TLS + systemd units — is written down in §1.2 so the choice stays honest.
  **Do not** "fix" this by threading `user.organization_id` into queries: that
  is the first 5% of row-level multi-tenancy and creates a second scoping
  doctrine alongside D12's. The one carve-out is **TV-1** (§2 of that spec): the
  three `org_group` joins that match on **slug alone** are wrong *within* one org
  too, two of them inside the session-authority intersection —
  `gateway/rooms.py:181-199` (the `SELECT g.slug` at `:192`), `:368-403`
  (`SESSION_VISIBLE_SQL` from `:368`, slug join `:377`), `acb_auth/access.py:330-336`.
  *(The first two were published as `:170-179` and `:332-340`; both were wrong at
  `520476ab` and were corrected 2026-08-03 — see `tenancy_and_visibility.md` §2. The
  third was and is correct.)* AGENT-SAFE, one small PR, with a two-org fixture that
  must be verified red first — **but see the board row for the skip-green trap that
  made "verified red" unsatisfiable as originally written.** Owner: the new spec;
  **board row: WS-14a**, minted 2026-08-03 (it had none, which is why it never
  dispatched).
- **D12 — Visibility is private → Center → org, plus ad-hoc groups by invite;
  and a project belongs to a team by an explicit `group:` grant.** *(owner call,
  2026-08-03; owning spec `specs/tenancy_and_visibility.md` §3–§4.)* The owner's
  words were *"department-wise privacy so that the sales team cannot see what the
  finance team is doing… at the same time organizational-level sharing… and
  projects and groups where information can be shared between select users of
  different departments, depending on invite."* **department = Center =
  an `org_group` row** (R3; `department_centers.md` §1) — write "Center".
  **The primitive exists and must be generalised, not reinvented:**
  `routes/rooms.py::_valid_subject` (`:100-111`) already accepts
  `email | group:<slug> | org` and `chat_session.visibility` is already
  `private|people|org`, with group membership expanded at read time
  (`gateway/rooms.py:181-199` — corrected 2026-08-03 from the stale `:163-179`).
  **Correction to the claim that reached this
  board:** `app_grants` does **not** share that vocabulary — `routes/apps/
  grants.py::is_valid_subject` (`:68-85`) is `email | agent:<name> | agents:*`
  and **rejects the literal `org`** (`:77`), with no `group:` case at all; the
  docstring at `rooms.py:103` claiming the two are "identical" was **false** and
  was corrected 2026-08-03 (a docstring-only edit — the false claim was actively
  misdirecting implementers of this very decision).
  Rooms is the only surface honouring `group:` today; the gap table in §5 of the
  spec is the app-by-app map. **"A project belongs to a team" = an explicit grant
  row carrying a `group:<slug>` subject** — *not* derived from assignees (access
  would become a side effect of task assignment) and *not* an owning column
  (single-valued, so it cannot express the cross-Center project the owner asked
  for). This is the semantic that has blocked **WS-14** for weeks; it is
  answered. **Standing review rule:** a new persisted user-facing surface
  declares its tier — it does not inherit one by accident. Two doctrines in one
  codebase is what produced the Notes hole — **PR #346 merged as `d2ef7fa0` on
  2026-08-03**, so the Notes owner filter has landed
  (`routes/notes/core.OWNED_MEETING_PREDICATE`) and a grant table is the remaining
  work there; the spec's §3.3 and §5 rows were updated to match.
- **D13 — The project grant table is `gtd_*`-local, and it has no `role` column.**
  *(`agent-proposed, owner may overrule` — 2026-08-03; owning spec
  `specs/tenancy_and_visibility.md` §4.1.)* Registered here 2026-08-03 because it was
  discoverable only through a parenthetical in the WS-14 row, while being the first
  decision the tasks team slice makes. **The call:** `gtd_project_grant (project_id,
  subject, granted_by, created_at)` — a `gtd_*`-local table with a real FK onto
  `gtd_projects`. **Three alternatives, all rejected in §4.1:** a polymorphic
  `object_grants` (no FK, an index per `object_type`, and a platform migration
  decision taken inside an app ticket — if the owner takes it, it is its own ticket
  that *also* migrates `app_grants`); reusing `app_grants` itself (impossible without
  dropping its `app_id … REFERENCES apps(id)` key, `114_custom_apps.sql:58-67` — i.e.
  the `object_grants` option reached by mutating a live table four Custom-Apps code
  paths read); and an owning `group_id` column on the project row (single-valued, so
  it cannot express the cross-Center project D12 requires). **The `role` column is
  cut, not deferred:** every clause of the slice's acceptance is a read-path clause,
  so `role` would ship with one legal value and no reader — write-through-grant is
  unanswered and arrives later as an additive `ALTER TABLE` at the next free number
  (R1). **What must not fork is the subject grammar:** one validator for
  `email | group:<slug> | org`, and §4.1 now names its home —
  `packages/acb_auth/acb_auth/permissions.py` (pure, already owns the permission
  vocabulary, no new import edge), because "the shared validator" previously named no
  module and no shared home existed. Acceptance: `department_centers.md` C1.
  *(⚠️ 2026-08-09: re-audit C1 against WS-27e's owner-directed one-store revision
  (D-PM-6 — `pm_tasks` is THE task table, WS-27h retires `gtd_items`) before
  dispatching — the `gtd_*`-local grant table may be building on a floor that is
  scheduled for demolition. The subject grammar rule above is unconditional either
  way.)*

- **D14 — `manager`'s "org-wide visibility" is not `data:org:read`, and
  `data:org:read` should not be relied on by anything.** *(`agent-proposed,
  owner may overrule` — 2026-08-04; owning spec `specs/colleague_onboarding.md`
  §3.0(b).)* Minted because WS-24's capability matrix had to answer "does
  `manager` contradict D12's department privacy?" and the received answer named
  the wrong permission. **The measurement:** `data:org:read` is declared
  (`packages/acb_auth/acb_auth/permissions.py:132`), granted to `admin`,
  `manager` and `agent_service` (`130_org_access_control.sql:205, 221`;
  `:258`), and listed in the legacy-fallback set (`acb_auth/access.py:148`) —
  and **no route, query, predicate or frontend check in the repository ever
  reads it.** A repo-wide search outside the vocabulary, the seed migrations and
  the specs returns nothing. `org_access_control.md:81` described `manager` as
  "sees org-wide data" on the strength of it; that sentence was aspirational
  and was corrected on 2026-08-04 (the same edit replaced that row's *"all
  `feature:*` except `build.*`"* grants cell, which was wrong in five slugs —
  `manager` also lacks workflows, integrations, models, agents and every
  `center.*`).
  **The proposed call, in two parts.** (i) **No spec, ticket or acceptance
  criterion may rest on `data:org:read` until it has a consumer.** Writing one
  is its own ticket: either give it a meaning (which is an org-wide read path,
  i.e. the exact thing D12 constrains) or strike it from `CAPABILITIES` and the
  three seed grants. Leaving it as-is is also acceptable — it grants nothing —
  provided nobody *cites* it. (ii) **The department-privacy question the owner
  actually has to answer is about `admin:members:read`**, which is the floor for
  the **entire** `/admin` package (`routes/admin/_common.py:77-91`), not just
  the member list: a `manager` reads the full member directory, the role
  catalogue and the group list, and `/auth/me` returns `is_admin: true` for them
  (`routes/admin/me.py:96`). Combined with `feature:approvals`,
  `feature:observability` and `memory:write_org` (`131:62`), that is the real
  breadth of the role. **Rejected alternative:** quietly narrowing `manager` in
  a migration. It is a policy call about who may see the shape of the
  organisation, it is exactly the shape of D12, and no acceptance should be
  written for it until the owner decides. **Consequence if the owner does
  nothing:** `manager` stays as seeded and WS-24's matrix labels it accurately;
  nothing breaks, and the only standing rule is (i). *(2026-08-09: the
  zero-consumer measurement is retired — WS-27d's full-portfolio view is
  deliberately `data:org:read`'s first consumer, and granting it to a real
  member is owner-gated in §6 WS-27 (d). Part (i) still binds for every other
  spec: name the consumer or don't cite the permission.)*

- **D62 — A task may only move INTO a personal project it already lives in.**
  *(2026-08-26, owner-directed.)* Five combinations, one rule: team→team,
  personal→team (**promotion**, D53.4) and moves **within one member's own tree**
  are allowed; **team→personal** and **personal→somebody else's personal** are
  refused. ⚠️ The reason is not access. Moving a task into a personal tree is not
  an organising act, it is a **taking** act — `tree.py`'s list filters
  `personal_owner IS NULL`, so the task leaves the company board and everyone
  whose access came from a project grant loses it, **with no timeline entry and
  no way to undo it**. Every other route off a board (archive, delete, move to
  another team project) is recorded and reversible. The organising need it
  *resembles* is already met by the per-member overlay (D53.7/D53.8): a member
  can hold a team task with their own disposition, context, energy and schedule,
  none of which anybody else sees — so refusing costs nothing that was
  achievable. Deliberately phrased on the two PROJECTS, never on the caller: an
  unrestricted (`data:org:read`) admin is refused too, because the act is
  incoherent regardless of permission. Fence: `assert_move_keeps_privacy`,
  `test_personal_boundary.py`, `live_ws39_personal_boundary.sql`.

- **D63 — A departed member's personal tree is SEALED, not inherited.**
  *(2026-08-26, owner-directed. ⚠️ There is no member-deactivation flow yet —
  `app_user.status` only ever holds `'active'` — so this is settled BEFORE the
  thing exists rather than retrofitted onto it. Whoever builds deactivation
  implements this. They do not get to pick a default under time pressure.)*
  ✅ **The seal is BUILT (H-49 slice 1, 2026-09-23).** Migration **215** adds
  `pm_projects.sealed_at`, both visibility clauses filter it on both arms, and
  both off-boarding doors stamp the tree inside the status transaction.
  `tests/live/live_member_seal.sql` carries 14 checks on real Postgres.
  ✅ **H-304 (2026-10-13):** the `data:org:read` task clause now hides a sealed
  tree too, and `tests/unit/test_sealed_tree_visibility_r8.py` proves it.
  ✅ **H-305 (owner, 2026-10-13: "Stop counting them."):** a sealed tree no
  longer counts in its owner's load, and a handed-over task still counts for
  its new assignee.
  ⚠️ **The hand-over needed NO code, and that is a finding rather than a
  shortcut.** `task_visibility_clause` has a second arm over
  `pm_task_assignees` that never consults the project, so a task the leaver
  assigned outward stays visible to its assignee while the tree seals. No row
  moves. ✅ The dialog that states the split in numbers is built. It calls
  `seal_counts` through `GET /admin/members/{email}/seal-preview`
  (`admin/members.py:1123`). 📌 Still owed: the owner-only LOGGED door that
  lets somebody else open a sealed tree. `unseal_personal_tree` is the
  mechanism for it. Its one caller today is reactivation
  (`admin/members.py:362`), which gives the tree back to its owner.
  The principle: **the company is entitled to the work, not to the workspace.**
  So the tree is split by what was *already shared*, not treated as one object:
  * tasks in their tree **assigned to somebody else** → **handed over**. They
    were never private; that person can already see them (WS-27j's assignee arm).
  * tasks only ever theirs → **sealed**. Retained, invisible, **never deleted** —
    the same reasoning R6 applies everywhere else: you cannot undo a delete.
  * their `pm_task_personal` rows on TEAM tasks → nothing to do. The task is the
    team's and needs reassigning; the overlay is theirs and simply stops being read.
  * the Areas themselves → sealed with the tree.
  🔴 **One door, and it is watched:** an **owner-only, logged** action may open or
  export a sealed tree. A door that exists and is recorded beats no door — with
  no door, business-continuity pressure gets resolved by somebody sharing a
  password instead. "An admin read a departed colleague's private tasks" must
  never be an invisible act. Member writes are already §6 owner-gate, so this
  sits where it belongs by construction.
  ⚠️ **The deactivation dialog must state the split in numbers before the click**
  ("14 tasks — 3 handed over, 11 sealed, not deleted; later access is recorded").
  A policy nobody is told about at the moment it applies is one they discover by
  being surprised.

- **D65 — Horizons of Focus leaves the UI. The per-member grouping of personal
  projects is called AREAS.** *(owner call, 2026-08-26, in session. Owning spec:
  **`task_manager_app.md`**. Board: **WS-39**, and it releases **WS-21**'s hold
  on the surface only, not on the store.)*

  **What forced it.** The owner asked what a member's private projects should be
  called, and how they should be grouped. Two vocabularies were live at once.
  `task_manager_app.md` §F7 offered **Horizons of Focus** — six review altitudes
  from Purpose down to Next Actions. The WS-39 backfill built something else: a
  flat grouping from each member's old `gtd_projects`. Six altitudes is GTD's own
  model and it is correct. It is also a **reflection** feature, and nothing in
  the live product does reflection yet.

  **The decision, in three parts.**
  1. **Horizons comes off the surface, and stays in the code.** The same shape as
     D49 took with Centers: the nav entry and the altitude ladder go, the data
     and the routes stay. It is revisited when Weekly Review exists to give it a
     job. ⚠️ This is **not** a deletion, and **not** a permission change.
  2. **AREAS is the word**, for the one grouping members actually get: a flat set
     of buckets over their own personal projects, visible **only in the Tasks
     app**. Not "categories", not "lists" — `ListsSidebar.tsx` already spends
     "Lists" on the disposition views, and a second meaning for a live word is
     the CLAUDE.md §5 defect.
     ⚠️ **One collision survives, and it is named rather than hidden.**
     `task_manager_app.md` **F7** uses **“Areas of Focus”** for the H2
     altitude. That is a different thing, and D65 defers it. The bare word
     **“Areas”** belongs to this decision. Whoever revives F7 settles the
     naming at that point, and may not quietly reuse the bare word.
  3. **Areas are per member and never leave the Tasks app.** A personal project
     is private at any depth (migration **191**), so its grouping is private too.
     The Projects app must not render an Area, ever.

  ⚠️ **THIS DECISION WAS TAKEN ON 2026-08-26 AND WENT UNRECORDED UNTIL THE END OF
  THAT SESSION.** Two places contradicted it, and the same commit corrects both.
  `work_plan.md` §2 **WS-18** and **H-33** each say
  *"Horizons: WS-21 owns it — DO-NOT-DISPATCH stands."* That sentence is still
  true **of the store**. It is no longer true of the surface, and a reader who
  met only that sentence would defend a feature the owner asked to withdraw.

  📌 **The backfill depends on this.** `gtd_backfill_to_pm()` creates Areas from a
  member's old `gtd_projects`, so **H-29 must not run until the Tasks app can
  rename and delete one.** Otherwise members hold structure in their own data
  that they cannot edit. That constraint already sits in H-29 and this is why.


- **D66 — The customer never brings a model, and never sees one. The AI surface
  the customer gets is spend, not configuration.** *(owner call, 2026-08-27,
  taken in two parts in one session. Owning spec:
  **`specs/customer_console.md`** §5.1 + §4.5. Board: **WS-31**, ticket series
  **CP-7** and **CP-11**.)*

  **What forced it.** `customer_console.md` §5.1 already said the provider,
  model and tier tabs leave the customer product. Nothing enforced it. Two
  server doors still wrote a provider API key. The first door's failure branch
  opened the second one. The spec named the replacement surface *"credits, burn
  and per-member caps"* and then gave it no shape.

  1. **BYOK is OFF for the customer.** ✅ **BUILT — CP-11 slice 4.** The
     `byok_enabled` setting defaults to `False`, and both write doors refuse
     with 403. Reading an installed key is untouched, because the product runs
     on those keys while `ROUTER_SERVING_ENABLED` is off. ⚠️ *"For the time
     being"* is the owner's own framing. BYOK stays a **tier**, not an
     exception (§3.4), and an operator can turn it on for one deployment.

  2. **A failure must never turn it back on.** This is **D57.7 one layer
     down**. A Router outage that re-opened the key doors would move a paying
     customer onto their own keys and stop metering them. The fence is
     `tests/unit/test_byok_disabled.py`, which fails if any service names the
     environment variable at all.

  3. **The customer's AI view shows ACTIVITY and COST, never a model.** This
     executes **D32.7**, and it corrects an assumption. The Live Activity app
     is **not** that view: it reads a tenant-local Redis rollup, reports **USD**
     and breaks down **by model**. The product sells **₹10 credits** (D19.2) and
     the Console holds the number we bill on. Two sources cannot both be the
     bill.

  4. **An admin sees per-member usage and cost, and sets a monthly budget.**
     The engine exists and is unwired: `member_ai_cap.monthly_credits`,
     `credits.decide_member_cap`, degrade-to-`tier-fast` by default, and the
     80% warning. **CP-7 owns it.** ⚠️ A cap stays a **policy against the org
     pool, never a sub-wallet** (D32.8). Raising one member's budget mid-month
     is a cap edit, and it is not a transfer of credits.

  4a. 🆕 **BUILT 2026-08-28 — the READ half only** (`ws31-cp7s1-spend-reads`).
     `GET /my/usage/activity` and `GET /my/usage/members` answer items 3 and 4
     above. Neither read names a model, and two fences hold that.

  4b. 🔴 **The cap ENGINE stays unwired, and H-73 says why.** A cap decides
     whether a member may spend. So it must rest on an identity the member
     cannot choose. `Caller.member` comes from the `X-CC-Member` header, and
     `v1_compat.py:490` forwards that header from the request without change.
     A member who omits it has no cap row, and no cap row means no limit.
     **Attribution is good enough to report. It is not good enough to
     enforce.** Migration 005 states the same rule for the invoice.

  5. **This is a spec, not a queue item.** The owner asked for the shape to be
     recorded and not built. CP-7's done-whens carry the READ half and the
     top-up. Nothing here dispatches before CP-4b closes the streaming hole
     (**H-68**), because a usage view that omits most traffic is worse than
     none.

- **D67 — The customer's price is keyed on the TIER, and the tier slate is
  the whole product surface.** *(owner call, 2026-08-30, made in the
  operator-console session. Owning spec: **`specs/customer_console.md`**
  §6A.12. Board: **WS-31**. Migration: `015_tier_pricing.sql`.)*

  **What forced it.** `model_rate_card` priced `(model, task)`. So the
  customer paid whatever the SERVING model cost. A failover changed their
  price mid-day. Two tiers on one model could not differ in price, so a
  premium tier needed a premium model. The owner asked for per-tier prices
  and for tiers that cover every capability we intend to sell.

  1. **`tier_rate_card` is the card billing reads.** Keyed
     `(tier, task, effective_from)`, INSERT-only, same G-4 modes. The meter
     rates the tier the customer picked. A failover moves our cost, never
     their price. `test_customer_console_tier_pricing.py` proves both.
  2. **`model_rate_card` is retired as a billing input.** The table stays
     (R6). `POST /catalog/rates` answers 410 and names the successor.
  3. **`tier_catalog` is the registry.** Eleven tiers ship, and an empty
     tier now EXISTS on the board. `video` and `music` join `task_catalog`,
     priced in seconds. No Router verb serves them yet, on purpose - the
     tier, the task and the price can exist first.
  4. **Pricing moved to `/tiers` in the console.** `/models` keeps the
     supply side only: what a model is, and what WE pay for it.
     *(Amended 2026-08-30, the owner's IA pass: the cockpit now owns its
     own `/pricing` page, beside the new `credit_price` frame. The key
     stays `(tier, task)`.)*

- **D68 — A tier serves ONE kind of job, and the registry says which.**
  *(owner call, 2026-08-30, on sight of the per-tier job dropdown. Owning
  spec: **`specs/customer_console.md`** §6A.12. Migration:
  `016_tier_task.sql`.)*

  **What forced it.** The slate (D67) already dedicates a tier to each
  capability. The board still asked "which job?" on every tier, so an
  operator could bind speech-to-text onto "Fast" by mis-click, and the
  question re-asked what the tier's own name had answered.

  1. **`tier_catalog.task` is the tier's category.** The chat bands say
     `chat`. Each capability tier says its capability. NULL means
     uncategorised, and the checks below do not fire for it.
  2. **The binding and tier-rate writes refuse a mismatch** with a 400
     that names the tier's own kind. Ghost tiers keep the old freedom.
  3. **The board asks nothing.** An empty categorised tier shows its one
     job's chain editor directly, and the page groups by kind: chat
     bands, one tier per capability, then whatever the registry cannot
     place. A pre-D68 job of the wrong kind renders flagged, never
     hidden.
  4. **The call grammar (D60) is untouched.** This narrows what an
     operator can CONFIGURE, never what a call can name.


- **D69 — One look. The theming engine is retired.** *(owner call,
  2026-08-31. Owning contract: **`workbench/control_plane/DESIGN_SYSTEM.md`**
  §0 and the `AGENTS.md` beside it. Branch: `ws-27-projects-uiux`.)*

  **What forced it.** The owner asked for it directly: *"remove the theme
  switch and have just one theme that is proper… keep the original theme that
  we had when we started the command center project and have all of the apps
  and sub apps follow that theme only."*

  ⚠️ **This OVERTURNS the owner directive of 2026-08-10**, which said every app
  renders through the theming engine. That directive is not wrong about its
  point — one product, one look, no app-local palette — and that half stands.
  What changes is the mechanism. The engine that delivered it is gone, and
  `globals.css` now holds the look on its own.

  1. **There is one theme, and it is the Control Plane's original.** Fluent,
     Material and Graphite are deleted, with the `data-theme` attribute, the
     generated stylesheet, the three icon packs and the four Iconify packages.
     `src/app/globals.css` is the SOURCE — edit it to change the look.
  2. **`lib/theme/themes.ts` survives as a MIRROR, not a registry.** Three
     consumers need the tokens as data rather than as CSS: the generated-app
     sandbox, whose `--cc-*` block is a published interface baked into apps
     already shipped; Monaco and Shiki, which take their own theme names; and
     the contrast gate. `themes.test.ts` fails if the mirror drifts.
  3. **Three knobs stay, because they ADJUST the look instead of replacing
     it:** colour mode (dark and light), density, accent. The org-default tab
     keeps all three and its override lock. There is no font control — the
     owner considered one and declined it.
  4. **The icon rule outlives its own reason.** `<Icon>` no longer abstracts
     over packs. It stays mandatory because it owns the default size, the
     class contract and the unknown-name fallback across ~1,400 call sites,
     and `lib/icons.tsx` is now the ONLY file that may name `lucide-react` —
     `Icon.tsx` left the allowlist, so the rule tightened.

  📌 **What this cost, recorded because it is easy to forget.** The theme
  switch was the strongest review tool in `DESIGN_SYSTEM.md`: flipping to
  Material made every hardcoded value announce itself for free. Nothing
  replaces it at equal strength. The substitutes are light mode, compact
  density and a changed accent, and §8's checklist now names them. A hardcoded
  colour is exactly as wrong as it was on 2026-08-30 and nothing will show it
  to you.

- **D71 — admission has TWO modes, and the operator ROW is the gate in the
  new one.** *(owner call, 2026-09-02. Owning spec:
  **`specs/operator_identity_and_access.md`** §4.1b. Board: **WS-31**, ticket
  **CP-12i**. This decision **amends D70.2** and narrows it.)*

  **What forced it.** The owner said the console must admit operators who hold
  Gmail and outside addresses. `hathilabs.com` describes some staff and never
  all of them. So directory check 1 refuses a real operator, and domain check 2
  refuses the same person again. The owner chose the email fallback after the
  agent set out the cost.

  1. **`OPERATOR_ADMISSION_MODE` picks the mode.** `directory` keeps all three
     checks and stays the default. `registry` skips checks 1 and 2, and check 3
     becomes the whole gate. A box nobody changes behaves as it does today.
  2. ⛔ **This AMENDS D70.2. An email code may admit an operator.** Three
     things must hold together: the mode is `registry`, `OPERATOR_ALLOW_EMAIL_OTP`
     is on, and the operator's own row permits the method. D70.2's reasoning is
     not withdrawn, and clause 3 is what answers it.
  3. **`operator.allowed_methods` pins a person to their own methods.** A
     global flag weakens the `admin` most, because the `admin` adds operators.
     So a person who reads the admin mailbox adds themselves. The owner keeps
     `{google}` on their row, and the contractor carries `{email}`. NULL means
     no restriction, so every row written before migration 022 keeps working.
  4. 🔴 **The one-time bootstrap pins to `OPERATOR_BOOTSTRAP_EMAIL` in
     `registry` mode.** The old gate compares the directory claim, which is
     always false in the new mode. Deleting that clause hands `admin` to the
     first stranger who signs in. This is the most dangerous line in D71.
  5. 🔴 **The security boundary MOVED, and this clause names where.** In
     `directory` mode a mistaken operator row still admits nobody outside our
     Workspace. In `registry` mode that row is the only wall. So *"who may
     write an operator row?"* is now the whole boundary of this console, and
     spec §5 reserves that write to `admin`.

  **What it does not do.** The Operator Console page offers no code form yet.
  The backend admits an email code, and no browser sends one. HANDOFF **H-100**
  holds that half.


### D72 — Operator elevation is OFF by default. Role alone decides (2026-09-18)

⛔ **This AMENDS D64.4.** Owner decision, taken after a 403 blocked the
activation of a customer account.

> *"Really basic. Simple role-based, and since I only have admins, I want to be
> able to do whatever."*

**What D64.4 said.** *No standing destructive privilege.* An `admin` held the
right to elevate, not the privilege. Nine sharp routes demanded an open,
time-boxed window with a stated reason, on top of the rank.

**Why it is amended, and it is the company that changed rather than the
threat.** D64.4 is written for a staff directory with mixed roles, where a
hijacked admin tab is a real and separate risk. There are TWO operators today,
both `admin`, and both owners of the business. The window was not standing
between an attacker and the data. It stood between the owner and a routine,
reversible act — activating a customer's account — and the owner did not
recognise the refusal as their own control.

⚠️ **What this does NOT change, and the tests say so.** The §5 rank matrix is
untouched. A `viewer` and an `editor` are refused `/orgs/lifecycle` exactly as
before, and an `editor` is still refused a credit grant above the threshold.
What is removed is the SECOND factor on top of a rank the person already holds.

⚠️ **The `elevated=True` markers stay in the matrix.** They record which nine
routes are the sharp ones, and that judgement is still right. `D72` decides
whether a window is DEMANDED, not which routes would demand one. Deleting the
markers is a different and larger change.

**The mechanism is kept whole and tested.** `test_operator_elevation.py` pins
the flag ON for the window tests, so re-enabling cannot quietly ship broken.
Four of those tests failed the moment the default flipped, which is the fence
working.

🔁 **The trigger to revisit, named now so it is not left to memory: the first
operator who is NOT an owner of the company.** At that point D64.4's threat is
real again, and turning it back on is `OPERATOR_ELEVATION_REQUIRED=true` on the
Console — one environment variable, no code change.

**Fences:** `TestElevationIsOffByDefault`, `TestTheRankMatrixIsUNTOUCHED`,
`TestTheOwnerCanJustDoIt` and `TestTheSwitchIsReadAtBOTHEnforcementPoints` in
`tests/unit/test_operator_elevation.py`.

---

### D73 — The Tasks app is **My Tasks**, and the `gtd_*` store leaves the schema by name (2026-09-23)

Owner directive, 2026-09-23, given three times across four days. Owning spec:
**`specs/my_tasks_cutover.md`**. Board: **WS-39**, phase 2. This decision
closes the question H-132 asked, and it closes it the other way.

> *"Rename the tasks app to My Tasks so as to remove ambiguity."*

> *"Update all of the database naming entries also so that there is no
> confusion at all for the future."*

1. **The name is My Tasks.** Every member-visible string says so. The route
   `/tasks` and the slug `feature:tasks` do not change. A route rename breaks
   bookmarks for no member-visible gain. A slug rename ships the app dark.
2. **The table map.** `pm_` is the one task store. `my_tasks_` is a table only
   the My Tasks app reads. A shared registry gets a bare name. `gtd_items`,
   `gtd_waiting`, `gtd_projects`, `gtd_spaces`, `gtd_folders`, `gtd_contexts`
   and `gtd_retirement_arm` are dropped. `gtd_attachments` becomes
   `attachments`. `gtd_horizons` and `gtd_reviews` become `my_tasks_horizons`
   and `my_tasks_reviews`. `wa_commitments.gtd_item_id` becomes `task_id`.
   `pm_task_personal` keeps its name, because three lenses share it.
3. **`origin` lives on `pm_tasks`, as JSONB.** Provenance is a fact about the
   work. This closes H-62 (2).
4. **`workflow_stage` writes resolve a status name against the task's own
   project.** This closes H-62 (1).
5. **Contexts are derived from the overlay.** `gtd_contexts` goes.
6. **Index, constraint and policy names keep their old spelling.** Replay of an
   old migration re-creates the old name, and then two exist. A named,
   reversible exception.
7. **The code names follow the schema in a slice of their own** (S9). One
   `tsc` run and one pytest run then verify the sweep.
8. **Continuity with Projects is a property to verify, not a sync to build**
   *(added 2026-09-23, same day, second directive)*. A task assigned to me in
   Projects is in my inbox, my day and my calendar. It is one row.
   S6e adds three things. A project where I am the lead shows in My Tasks. An
   assigned task I have not triaged sits in a "From Projects" group. Both apps
   draw one task panel composition. `specs/my_tasks_cutover.md` §4.8.
9. **Stages come from Projects, grouped by category** *(added 2026-09-23,
   third directive)*. The owner, verbatim:

   > "if we do not have any ClickUp connection, then the status mapping also
   > needs to be removed from the settings of My Tasks. Make sure we are
   > properly mapping stages with the Projects app."

   The ClickUp status mapping and the Kanban stages editor leave the settings.
   Next Actions groups a task by its lane CATEGORY: To do, In progress, Done.
   A card keeps its own lane name. A drag into a group moves the task to the
   first lane of that category in its own project. Done completes the task.

   A stated NEXT is never hidden by a `backlog` or `triage` lane. It sits
   under To do. The `user_settings` columns stay until a later contract (R6).
   `specs/my_tasks_cutover.md` §4.9.

**The mechanism does not change.** The guarded rename prologue in the creating
migration (`people_center_app.md` §7.0), registered in
`test_gtd_rename_upgrade.py`. Sweep the tree first, add the prologue second.

⚠️ **Migration 190 is in the production ledger as applied and inert.** The
runner skips an unchanged file. So the drop needs a new migration that calls
the armed guard again. `docs/TASKS_LENS.md` carried the old assumption and now
carries the corrected order.

**Fences:** `nav.test.ts`, `naming.test.ts`, `lens.test.ts`,
`test_client_route_contract.py`, `test_gtd_rename_upgrade.py`,
`test_gtd_backfill.py`. `specs/my_tasks_cutover.md` §7 is the full table.

---

### D75 — `decide` is a new AI TASK, and TypeSafe's Jev serves it first (2026-09-23)

Owner decision, 2026-09-23. Owning spec: **`specs/customer_console.md` §6A.14**.
Board: **WS-31**, ticket **CP-13**. D74 is skipped on purpose, because
`specs/credit_pricing.md` §7 reserves it for credit expiry.

> *"Let's go with Jev. Build out the operator console for it first, and then we
> will figure out how to make changes in the app to [use] this in the best way
> possible."*

**What the owner decided.** The vendor is TypeSafe, and the model is Jev. The
Operator Console comes first. The app layer comes second, and it is still open.
The owner compared Jev with Convai's Laya, an open-weight model we would host
ourselves. The owner chose the hosted API.

**What the agent derived from it, each one an agent default the owner may
overrule:**

1. **`decide` is a TASK** (D60.1), because its output is a typed decision and
   not text. So D60.6 keeps degradation inside the task, and D60.7 makes an
   unbound call a 400. The Router never answers a decision with a paragraph.
2. **One hidden tier, `tier-decide`** (D68, `customer_visible = FALSE`). The app
   picks the task, and a member never picks a decision model.
3. **⚠️ This PROPOSES an amendment to D61.1, and the owner must acknowledge it.**
   D61.1 says each Router door copies an OpenAI shape. No OpenAI shape exists
   for a decision. So `POST /v1/decide` takes a Metorite shape, modelled on the
   only one on the market. An agent does not reopen a decision (CLAUDE.md §5),
   so D61.1's text changes only on the owner's word (H-166). Our wire says `boolean` where
   the vendor says `noul`, so a second vendor costs one handler and no caller
   change.
4. **Jev is the first caller of the handler seam** (§6A.10b, H-47). litellm
   reaches TypeSafe only through its Proxy, and D58 rejects the Proxy. So the
   Router needs a native handler, and `native_typesafe` is its first value. D58
   still holds. This is a direct call with the credential per call, and it is
   not a proxy.
5. **Pin `typesafe/jev-1.13.0`.** The vendor's aliases move, and a confidence
   threshold is tuned against one version. The reseller allows only `typesafe/jev` (CP-13h), so the live binding cannot pin a version. CP-13i records the dated id.
6. **The main chat gets a `decide` TOOL, not a sub-agent.** A sub-agent turn
   costs seconds and one of two depth levels. A platform tool costs one Router
   call. `agent_architecture.md` §8 records it.
7. **An app adopts it in shadow first, then with a confidence gate.** It
   switches only on measured agreement with its current LLM on our own data.
   Below its threshold, the feature asks its current LLM. When `decide` is
   unavailable, the feature uses its own older method. D57.7 does not forbid
   that, because D57.7 is about a routed chat call. Amended for email by D-EM-7 and D-EM-8 (2026-10-02). In `on`, email triage asks no LLM, and a missing answer leaves the email undecided.
8. **2026-09-24 — TypeSafe paused signups.** The owner chose to reach Jev
   through the reseller AI/ML API. `native_aimlapi` serves it. The operator
   binds `aimlapi/typesafe/jev`. AI/ML API is a second sub-processor. The
   residency answer below must cover it too. Detail: `customer_console.md`
   §6A.14 CP-13h.

**⚠️ What this decision does NOT decide — three owner answers, all in
`customer_console.md` §9 item 8:**

- **Residency.** D19.6 promises India-only data at launch, and TypeSafe states
  no region. Every call on a real tenant waits for this answer: the chat tool,
  every app slice, and shadow mode, because shadow mode also sends the content.
  `DECIDE_ENABLED` is the switch, and it is owner-only (§6.1 WS-31 (i)).
- **The price** (H-42). The vendor charges USD 0.042 for each million input
  tokens, and output is free. The margin floor sets almost all of the charge.
- **TypeSafe as a sub-processor.** No sub-processor list exists yet (WS-37,
  H-36). TypeSafe is the first AI vendor that receives tenant content through
  the Router, and it must be on that list.

**What does NOT change.** D32.7 and D66: the customer never sees a model. D58:
no Proxy, and credentials travel per call. D60: the two-step resolution, which
`decide` uses as it stands. D67: the price keys on the tier. The rule that a
migration prices nothing.

**Fences:** `customer_console.md` §6A.14 "Done when" names 13, one per
artefact. `test_the_rate_card_ships_unpriced` and
`test_the_slate_ships_unpriced` keep the seed unpriced.

---

### D76 — One task, two priorities, and one rule that joins them (2026-09-23)

⚠️ **Amended by D78 (2026-09-24).** The seed and the private Important are
gone. Important and `Leveraged` are one shared answer on the task, and
Projects shows the matrix level. Point 1 no longer applies, because the
Low-to-Highest scale is retired.

Owner decision, 2026-09-23. Owning specs: **`specs/task_manager_app.md`
§13.4b** and **`specs/project_management_app.md` §12.5b**. Board: **WS-39**.

> *"go ahead with your suggestions. What do you think would be best to do to
> reconcile this?"* The owner said this in reply to three named choices.

**The problem.** One task sits in Projects and in My Tasks (D53). Projects
ranks it with a shared Priority. My Tasks ranks it with a private matrix of
`important`, `urgent` and `leveraged`. The two apps used the word "urgent" for two
different facts. Projects labelled level 3 "Urgent", and My Tasks derives
`urgent` from the due date. Also, the shared Priority never reached the
matrix. So a task that a manager marked High arrived in the assignee's Low
Priority.

**What the owner decided.**

1. **Level 3 is "Highest".** The Projects Priority says how much a task
   matters to the company. The due date says when. Only My Tasks says "urgent".
2. **High and Highest seed a suggestion.** While a member has not stated
   `important`, a shared Priority of 2 or more counts as important. The member
   confirms or dismisses it with one click. Either act writes an answer. After
   that, the answer wins over every later change to the shared Priority.
3. **The Projects task panel shows a private "Your focus" row.** Only the
   member sees it. It uses the same controls as My Tasks.

**What does not change.** D53.8 holds. `important` stays on
`pm_task_personal`, and `pm_tasks.importance` stays shared. The seed writes
nothing. `leveraged` stays personal, and it has no company value. `urgent`
stays derived.

**The rule to teach.** The company sets what matters to the company, and when
it is due. The member sets what it costs them. Nobody sets urgency by hand,
because the shared due date sets it.

---

### D77 — One home for every work fact: My Tasks reads the Projects fields (2026-09-23)

Owner directive, 2026-09-23. Owning spec: **`specs/my_tasks_cutover.md` §4.10**.
Board: **WS-39**, slice **S6f**.

> *"I don't want to unnecessarily duplicate fields or have related fields in the
> Projects app and My Tasks app."*

> *"The My Tasks app might have specific fields for related context, from a more
> task-management perspective (personal and individual)"*

> *"but it should derive all the data or reuse all the field data from the
> Projects app itself."*

> *"For example, deadlines, priority"* … *"should be derived from the Projects
> app."*

> *"If you think that the Projects app fields are actually lacking and the My
> Tasks app has certain fields that are richer in nature,"*

> *"they should be included in the Projects app itself."*

**The rule.** A fact about the WORK has one home: `pm_tasks`, or a side table
of it. Both apps read and write that home. The overlay `pm_task_personal` holds
only how one member holds the work. It never holds a second copy of a work fact.

**⚠️ This AMENDS D53.8, by owner directive, for one field.** D53.8 kept the
overlay's own `time_estimate_mins` beside `pm_tasks.estimate_mins`. The
owner's words above overrule that. Every other part of D53.7 and D53.8 stands.

⚠️ **D78 (2026-09-24) replaces the priority rows below.** There is one
Priority now, the matrix level, and its inputs are shared.

**⚠️ D77 does NOT change priority.** D76 decided priority the day before D77
merged, and D77 follows it. The member's `important` stays on the overlay.
The shared `importance` only seeds it. An earlier draft of D77 derived
`important` from `importance` and was withdrawn for that reason.

**The audit (measured on `main` at `74653868`).** Six pairs held one fact twice,
and each pair could disagree:

1. `importance` vs the matrix cell. A task Projects called Urgent read
   `Low Priority · Eliminate?` in My Tasks. D76 answers this pair, not D77.
2. `estimate_mins` vs the overlay estimate. A My Tasks estimate never reached
   People capacity.
3. The status vs the stated disposition. A reopened task stayed DONE in my list.
4. The assignees vs the stored `waiting_on`. A reassigned task still named the
   old person.
5. `start_date` vs `defer_until`. My Tasks did not show the start date.
6. `tags` vs `context`. My Tasks lists did not show the tags.

**What each field is now.**

| Field | Home | Rule |
|---|---|---|
| Priority | shared `importance` | D76's vocabulary. Projects edits it. My Tasks shows it in a Priority column, a card chip, and a Priority sort, group and filter |
| Important | mine | The member's own answer. D76's rule seeds it. D77 does not change it |
| The word "Priority" | one meaning | In My Tasks it names only the shared `importance`. Every control over the member's matrix says "Your focus", D76's name for the private row. The default sort is still the matrix rank |
| Estimate | shared `estimate_mins` | Migration 216 copies the overlay values once |
| Deadline | shared `due_at` | A delegation never replaces a deadline the task has |
| Start date | shared `start_date` | My inbox hides the task until the later of it and my defer. The server and the client both judge "today" in the member's own timezone |
| Completion | derived from the lane | A closed lane reads DONE. A stated DONE on an open lane reads NEXT. Only INBOX, NEXT or WAITING on a closed task reopens it. Someday, Reference, Project and a defer file it and leave the lane closed |
| Waiting on | derived from the assignees | The assignees minus me. The stored record is a label, or the answer when no other person holds the task |
| Tags / context | both kept | Tags say what the work IS. A context says how I batch my own time |
| Description / next action | both kept | Both apps share the description. The next action is mine |
| Watching | shared | The toggle is in the shared body, so both apps have it |
| Energy, two-minute, defer, block, flexible, hard date, actuals, rank, clarified, kept mine | mine | Unchanged, each with its D53 reason |

**Promoted into Projects.** The Estimate editor, the description editor, the
start date, the watch toggle in the body, and **Time spent**. Time spent is a
read-only sum of every member's actuals, beside the estimate.

**Retired, not dropped (R6).** The overlay's `time_estimate_mins` stays as a
column. Nothing reads it. `validate_overlay` refuses a write with a 422, and
`_upsert_personal` refuses one with a ValueError. A later contract drops the
column.

**Fences:** `tests/unit/test_projects_personal_s6f.py`,
`tests/live/live_ws39_s6f.py`, `sharedFields.test.ts`,
`lens.test.ts`, and `itemDetail.test.ts`. `itemDetail.test.ts` refuses a work
fact in the strip.

---

### D78 — One priority system, the matrix, with one shared answer per task (2026-09-24)

Owner decision, 2026-09-24. It AMENDS D76 and the priority rows of D77.
Board: **WS-39**. Owning specs: **`specs/task_manager_app.md` §13.4b** and
**`specs/project_management_app.md` §12.5b**.

> *"Currently, I see both: the system for prioritization is there in the task
> app, while we can also set a priority in that projects app."*

> *"Probably, have only one. Maybe I am leading towards the prioritization
> system that the my task app has, and we could reuse the same thing in the
> projects app."*

The owner then chose two named options. The first was "Yes, the matrix". The
second was "Shared, one answer".

> *"Why is there a separate priority lane for the My Tasks app when it is
> being calculated similarly … Isn't this a duplication and creates
> confusion?"*

**The problem.** After D76 and D77, one task showed two priority systems on
one panel. The Projects field said Low, Normal, High or Highest. The My Tasks
matrix said Critical, Urgent, Important and four other levels. D76 joined
them with a seed, and D77 put both on screen in My Tasks. Two answers to one
question confused the owner, and they will confuse a customer.

**What the owner decided.**

1. **One system, the matrix, in both apps.** A task has one of seven levels.
   The level comes from three inputs: Important, `Leveraged` and Urgent.
2. **Important and `Leveraged` are one shared answer on the task.** Every
   member sees the same level. A member's private flag is retired.
3. **Urgent stays derived from the due date.** Nobody sets it.
4. **Deep work stays personal.** It says how I do the work, not what the
   work is. It does not change the level.

**Where the answer is stored.**

- Important is `pm_tasks.importance >= 2`. The column keeps its 0-3 values,
  so every filter, merge, recurrence and automation that reads it still
  works. A write of Important stores 2, and a write of not-important stores
  0. NULL means that nobody has judged the task yet.
- `Leveraged` is `pm_tasks.leveraged`, added by migration 218.
- Migration 218 copies each member's `important = true` and
  `leveraged = true` onto the task, once. It only raises a value.
- `pm_task_personal.important` and `.leveraged` stay on disk (R6). The
  gateway refuses a write to either one with a 422 that names the shared
  field (`RETIRED_OVERLAY_KEYS`).

**What each surface shows.**

| Surface | Now |
|---|---|
| Task body, both apps | "Priority": the level badge, and the Important and `Leveraged` toggles |
| Projects table | the level's label. The cell editor sets the two flags |
| Projects card | the level chip. Low Priority earns no chip |
| Projects bulk bar | the two flags |
| Projects group and lanes by priority | the seven levels, in rank order. A drop onto a level is refused, because the due date is part of the level |
| Projects sort by priority | the level's rank. The key `importance` is unchanged |
| My Tasks list, sort, group, filter and view | "Priority" is the level. D77's second Priority column and its "Your focus" labels are gone |
| My Tasks detail | a private "Your focus" card: the action nudge and Deep work |
| Projects panel | a private "Your focus" row: Deep work |

**Still to do.** The Projects chat tools accept `importance` 0 to 4 (H-173),
and the chat cards and the CSV export print the raw number. A second PR moves
them to the two flags and the level.

**Fences:** `projects/lib/priorityVocabulary.test.ts`,
`projects/lib/matrix.test.ts`, `tasks/lib/focusWording.test.ts`,
`tasks/lib/lens.test.ts`, and `tests/unit/test_priority_shared.py`, which
holds the client's and the server's `IMPORTANT_AT` equal.

### D79 — Stages group, statuses write (2026-09-25)

Owner decision, 2026-09-25. It AMENDS D73.9 (`specs/my_tasks_cutover.md`
§4.9). Board: **WS-39**. Owning spec: **`specs/my_tasks_cutover.md` §4.9**.

**The problem.** My Tasks groups work by stage: To do, In progress and Done.
A stage is a status category. A task is always in one exact status, such as
"In review".

When a member moved a task by stage, the app wrote the FIRST status of that
stage, and it said nothing. Five doors did this: the status
pill, the right-click "Change status", a drag on the board, a drag in the
list, and a quick-add. So a drag to In progress put a task in "Doing" when
the member meant "In review", and the screen did not say so. The undo of
Mark done also sent a task to the first To do status, not back to the
status it left.

**What the owner decided.**

1. **A status write is always to an exact status id.** A stage groups work.
   It never writes by itself. The client resolves a stage through ONE
   resolver, `landingLane` in `lib/statusCategory.ts`.
2. **The ask rule.** When a drag lands on a stage that holds two or more
   statuses, My Tasks asks which one. The card lands with no
   write. The status menu opens on the card, with the first status in
   focus. Enter picks it. Escape or a click outside puts the card back and
   writes nothing. A stage with one status writes at once. A quick-add
   takes the first status and does not ask.
3. **Projects keeps its own board.** The Projects board grouped by stage
   writes the first status and does not ask.
4. **Mark done never asks.** It moves the task into the project's first
   Done status by position. The receipt names that status when the set
   holds more than one, for example "Done · Shipped in Website relaunch".
   With one Done status it says "Marked done". Undo is always offered.
5. **Every status set keeps at least one Done status.** The gateway refuses
   (409) a delete of the last Done status. It also refuses a change that
   moves the last Done status to another category. A status-set switch and
   a promotion to a space refuse a set with no Done status. Every seed that
   creates a set holds a Done status. The Projects status editor says why
   in plain words, before the write, and never shows a raw error.
6. **One reopen rule.** A reopen moves a task into the first To do status,
   else into the first status that is not triage. When that status closes
   too, the task stays where it is. The gateway (`reopen_if_closed`) and the
   Projects Reopen tick (`reopenLane`) apply the same rule.

**Every status write says what it did.** The receipt is the undo toast's
title. A board task reads "Moved to In review · Website relaunch". A task in
my own tree reads "Moved to Doing". An Undo puts back the status id the task
had, and then my list.

**No database fence.** The guard is in the gateway, and no migration was
added. Production held 3 status sets on 2026-09-25, and all 3 had a Done
status. So no backfill is necessary. Every write to a status set goes
through `admin.py` or `tree.py`. A constraint trigger would also fire when
somebody deletes a project and its statuses together, and it would then need
its own exception.

**The assistant follows the same rule (2026-09-26).** The task tools in
`apps/skills/skill-my-tasks` write one exact status. They read the task's
statuses from `GET /projects/my/tasks/{id}/lanes`, which checks membership.
The old read was behind the project grant, so it failed on a board task that
the member gets by assignment only. A stage word writes only when its stage
holds one status. With two or more, the tool writes nothing and lists them,
and the model asks the member. An undo of Done writes NEXT only, and the
gateway reopens the task.

**Fences:**

- `tests/unit/test_skill_task_lens.py`: the assistant's status tools, the
  ask rule, and the undo that writes NEXT only.
- `tests/unit/test_projects_done_guard.py`: the Done guard, the seeds, and
  the SQL on a real Postgres. A status-set switch locks every set it
  touches before its first write.
- `lib/statusCategory.test.ts`: the resolver, and the reopen rule against
  the gateway source.
- `app/tasks/lib/statusCategory.test.ts`: one client resolver, the ask rule
  and the undo order.
- `app/tasks/lib/statusReceipt.test.ts`: a status write names the status
  and the project.
- `components/ui/StatusMenu.test.ts`: the menu's grouping and keyboard,
  and a height that follows the member's density.

### D80 — A file importer is allowed. D52.2 is amended (2026-09-26)

Owner directive, 2026-09-26. It AMENDS **D52.2**. Board: **WS-41**. Owning
spec: **`specs/project_import.md`** §2.

**What the owner asked for.** Import "tasks, projects, and spaces from
Clickup, Asana, Jira, or other popular applications". ClickUp comes first.
The owner then narrowed it: "I don't need integration of the API." The owner
wants to upload "some standard exports of those apps".

**What changes.** D52.2 deleted both ClickUp importers for two reasons: a
live credential, and a second write path into `pm_tasks`. D80 allows an
importer that answers both reasons by construction:

1. **It reads an uploaded file and nothing else.** It opens no network
   connection, holds no token, and never fetches a URL from the file.
2. **It writes through the helpers the Projects routes use.** It adds one
   caller, not one write path.

**What stays.** D52.1 stands: no sync in either direction, and an import is
one shot. D52.3 stands: the `clickup_*` columns stay unwritten, and
provenance goes into `pm_tasks.origin`. D52.4 stands: Metorite is the system
of record for project work.

**Fences:**

- `tests/unit/test_import_no_network.py` (new): no importer module imports a
  network client.
- `tests/unit/test_pm_task_insert_sites.py` (new): `INSERT INTO pm_tasks`
  appears only in the allow-listed helper files.
- `tests/unit/test_no_task_provider_connectors.py` stays green, unchanged.

### D81 — Projects has one settings pane, in two scopes (2026-09-29)

Owner directive, 2026-09-29. Board: **WS-42**. Owning spec:
**`specs/projects_settings.md`** §2.

**What the owner asked for.** "A settings button somewhere that opens up
settings for the projects app." It rolls up statuses, roll-ups, fields,
colours and the import and export. The right-click menus stay.

**What it decides.**

1. The settings are one Projects app pane, beside Analytics. It is not a nav
   entry, so the `launch_surface.md` §2 allowlist does not change.
2. Each setting is in one of two named scopes: the organization, or one
   space.
3. One body per setting. The row-menu dialog and the pane render the same
   manager through `ManagerFrame`.
4. The ClickUp import leaves the sidebar and moves into the pane.

**Fences:** `projectsSettings.test.ts` (new), `nav.test.ts`,
`conformance.test.ts`.

### D82 — `code_task` runs on a MAF harness session, not on the Copilot SDK. It amends `agent_architecture.md` §11 (2026-10-03)

Owner decision, 2026-10-03, in chat. The owner approved this recommendation:
"build the MAF coding agent with a container per org, no network by default,
network opened only on approval". Board: **WS-43**. Owning spec:
**`specs/maf_coding_engine.md`** §2.

**What changes.** §11 made MAF the one runtime, and it kept the Copilot SDK as
the coding engine behind `code_task`. D82 replaces that engine with a MAF
harness session (`create_harness_agent`). The session keeps the model loop and
the key in the gateway process. Its shell commands run in the sandbox
container of D83.

**What stays.** §11's decision stands: one runtime, and coding is a tool.
`code_task` keeps its name, its signature and its script contract. §11 keeps
its text, with a dated amendment box.

**How it ships.** Behind `MAF_CODING_SCOPE`, default empty. Each entry of the
scope names one organization, so the rollout goes one organization at a time.
The Copilot path stays until WS-43i shows parity through the Router. The
removal of the Copilot path (WS-43j) is an owner gate.

**Fences:** `tests/unit/test_maf_code_session.py`,
`tests/unit/test_maf_harness_contract.py`,
`tests/unit/test_run_command_tool.py` and
`tests/unit/test_no_host_git_on_sandbox_dir.py`, all new.

### D83 — The T2 container tier is un-parked for the sandbox terminal only. It amends D16 and D10.1 (2026-10-03)

Owner decision, 2026-10-03, the same approval as D82. Board: **WS-43**.
Owning spec: **`specs/maf_coding_engine.md`** §3.

**What changes.** D10.1 parked T2, and D16 made it a precondition of the
pooled cutover. D83 un-parks T2 for one kind of container: the one that runs
the shell commands of the coding engine. Three users of the sandbox broker
get it: the `code_task` session, the app-builder agent, and `run_script` and
`install_dependency` for an agent that the broker covers. One container
serves one organization, one agent and one thread. It has no network by
default, and only an approved request opens an allowlist.

**What stays parked.** P5-c (a whole agent run in a container), P5-d, P5-b.3,
and T2 as a precondition of the pooled cutover (MT-0c-2). An agent asked to
build any of them still refuses it by name.

**Why the un-park is narrow.** The agent loop, the tools and every key stay in
the gateway. Only the text of a command crosses into the container. So the
container needs no network. The broker gives it no key, and it refuses any
mount that could hold one, such as a clone whose `.git/config` holds the
GitHub token.

**Fences:** `tests/unit/test_sandbox_broker_seam.py`,
`tests/unit/test_sandbox_broker_argv.py`,
`tests/unit/test_sandbox_broker_tenant.py`,
`tests/unit/test_sandbox_egress_proxy.py`,
`tests/unit/test_sandbox_network_grant.py`,
`tests/unit/test_app_builder_engine.py` and
`tests/unit/test_sandbox_safe_open.py`, all new.

### D84 — The GitHub Copilot SDK leaves the platform. It extends D82 from `code_task` to every use (2026-10-03)

Owner decision, 2026-10-03, in chat: "go ahead and let's start the migration
to MAF, and we can remove GitHub Copilot SDK completely." Board: **WS-43**.
Owning spec: **`specs/maf_coding_engine.md`** §15.

**What changes.** Every use of the Copilot SDK moves to MAF: `code_task`,
app-builder, self-mutation, the root `metorite` agent, the agents that a repo
registers, the model list and the session store. Then the packages
`github-copilot-sdk` and `agent-framework-github-copilot` leave
`pyproject.toml` and `uv.lock`. An agent that a repo registers must build a
MAF `Agent`. There is no adapter. Two new targets of `MAF_CODING_SCOPE`,
`mutation` and `metorite`, take `*` only, because they change platform code
and not the data of one organization.

**What it supersedes.** `chat_agent_framework_review_2026-07.md` §1 item 1
and §2.3 item 2, which kept the Copilot engine for the mutation sandbox and
the `metorite` agent. That doc is a historical record, so it gets a pointer
only.

**What stays.** D83 does not change: a new engine inside the existing mutation
container un-parks nothing. `GITHUB_TOKEN` stays, for repo clones only. MAF
MCP stays with D7 and WS-8c.

**How it ships.** The new paths ship first, dark. Native session persistence
(WS-43t1 and WS-43t2) comes earliest. WS-8i (the `agent-task-manager` move),
the confirm-turn scopes and WS-43q wait on its production soak, and not on its
merge. The removal slices (WS-43j and WS-43p to WS-43s) come after the parity
eval of WS-43i, and each one is an owner merge.

**The MCP cost, under D7.** After WS-43q, registry MCP servers reach no agent
until WS-8c wires MCP on MAF, because only the Copilot runtime reads them
today.

**Fences:** `tests/unit/test_no_copilot_sdk.py` (its allowlist shrinks to zero
at WS-43r), `tests/unit/test_mutation_runner_maf.py`,
`tests/unit/test_root_agent_maf.py`, `tests/unit/test_agent_runtime_default.py`,
`tests/unit/test_router_model_list.py` and
`tests/unit/test_native_session_persistence.py`, all new.

### D85 — A shared agent runs no code on the host until the sandbox covers it. It reverses O1 of S7e (2026-10-03)

Owner decisions, 2026-10-03, in chat. Board: **WS-43**. Owning spec:
**`specs/maf_coding_engine.md`** §7.9. The S7e record is
`specs/projects_ai_chat.md` §13.7.

**Decision 1 — code over project and task data, in the sandbox only.** The
owner asked "Should code the Projects agent writes be allowed to work on
project and task data?" The answer was "Yes, in the sandbox". A script may
use the project and task data that the asking member can already see. It runs
in the sandbox container of that organization, with no network. The HR-only
fields stay gated as they are today. This reverses O1 of S7e, which forbade
code over member data.

**Decision 2 — the interim block.** The owner asked "Until the sandbox is
live, block code execution on the shared server for the Projects agent and the
other shared agents?" The answer was "Block until sandbox". So a shared
agent runs no shell on the host. It gets no `SHELL_TOOLS` member, which are
`code_task`, `run_script` and `install_dependency`, until `covers(agent, org)`
is true for it. And the Copilot CLI refuses each of its own shell commands.

**What is built.** Two halves, each with its own function and its own flag
(fix round 1 of PR #598 split them):

- **The tool half**, `_tool_injection._withheld_shell_tools` and the flag
  `shell_tools_withheld`. The withheld names leave the scope, so the addendum
  and the skill bodies do not name them. A cover lifts this half, because the
  broker then runs those tools in the sandbox.
- **The host half**, `_tool_injection._host_shell_refused` and the flag
  `host_shell_refused`. `permission_policy.guard_shared_agent_shell` refuses
  each Copilot CLI shell request. The CLI runs on the host, so a cover never
  lifts this half. Only a CLI inside a broker sandbox could, and none exists.
- `_install_copilot_permission_handler` wraps every Copilot handler in the
  guard, also a handler the agent's factory set. The guard holds in every
  `AGENT_PERMISSION_MODE`. Production runs `enforce` (the box's `.env` sets
  no mode, read on 2026-10-03), and a later switch must not waive D85.
- Only a `personal` agent is out of scope, and so is the root `metorite` dev
  agent, by name.

**What it removes today.** `code_task` and `run_script` leave
projects-assistant, crm-assistant, apis-config, orchestrator, task-manager and
app-builder. They also leave every Workshop agent with a shared config.
apis-config loses `install_dependency` too. task-manager and app-builder also
lose the Copilot CLI shell. app-builder loses its build shell
(`node build/build_t2.mjs`) in the `preview` App Workshop. WS-43h gives it
back in the sandbox, and D86 parks WS-43h.

⚠️ **One live flow stops (found in review, 2026-10-04).** On 2026-10-02 and
2026-10-03, the projects-assistant of a customer org used `code_task` to read
an uploaded `.docx`. D85 takes that tool, so the agent can no longer read a
Word file. H-229 gives chat attachments platform-side text extraction, and
the supervisor starts it next.

**What stays.**

- email-assistant and whatsapp-assistant are personal, so they keep their
  tools. The owner left personal agents out of scope. H-225 asks for the same
  rule there, once the sandbox covers them.
- The Projects instructions still forbid code over the rows. WS-43u changes
  them under D86, once the sandbox covers projects-assistant (H-226).
  **Built 2026-10-04 by WS-43u, dark.** A run that holds `run_command` reads
  the sandbox rules, and a run without it keeps the ban.
- The root `metorite` dev agent keeps its tools and its CLI shell. **Owner
  decision, 2026-10-03:** "Admins of our own org only". Only an admin of the
  first-party organization may run it, through any path, and a customer
  organization is refused. `executor._assert_may_run_agent` enforces that at
  every run boundary (`maf_coding_engine.md` §15.4, fence
  `tests/unit/test_root_agent_first_party.py`).
- `agent-sales-assistant` keeps its `approve_all` (H-211), inside the guard.
  It is shared, so it loses the CLI shell. Its own tools run their scripts
  through Python `subprocess`, and they still work.

**Fence:** `tests/unit/test_shared_agent_shell_tools.py` (WS43-F23), new.

### D86 — Projects first, then Email. The work to port the older agents is parked (2026-10-03)

Owner decision, 2026-10-03, in chat: "let's leave the other legacy agents
that we had built earlier alone for the time being, and just work out the
agent assistant for the projects app to start with". The owner added that the
email assistant comes next, and said "Drop all the work needed to port the old
AI assistants for now." Board: **WS-43**. Owning spec:
**`specs/maf_coding_engine.md`** §16.

**What changes.** The WS-43 order is the Projects track first, then the Email
track. projects-assistant is already a native MAF agent, so it gets the
sandbox tools directly in its own loop, with no nested `code_task` session.
The scope gets a new target, `projects:<org>`. The track is WS-43d (narrowed),
WS-43u, WS-43v and WS-43w.

**What is parked.** WS-8i (task-manager), WS-43h (app-builder), WS-43j,
WS-43l to WS-43s, and WS-43t2, which is paused mid-build with its branch kept.
Each one keeps its text, and its heading says "parked by D86". They are not
deleted.

**What stays.** D84 stays decided, and it is deferred. WS-43b, WS-43c (PR
#591), WS-43k, WS-43t1 and D85 (PR #598) are kept.

**Owner decision, 2026-10-03, in chat: delegation stays in a covered run.**
The question was "During a sandboxed coding turn, should the Projects agent
be able to call other agents?" The answer was "Keep delegation".

- A covered Projects run keeps `call_agent`, `call_agents_parallel` and
  `call_agent_background`. An agent that it calls runs outside the sandbox,
  with its own tools and the network of the host.
- So the spec drops the promise that no data leaves the platform. The true
  claim is narrower: the container has no network, and the run holds no host
  web tool (`specs/maf_coding_engine.md` §16.3).
- WS-43w gets a new prerequisite: a network control on the agents that a
  covered run can call, or the owner's named acceptance of the residual at
  the flip (§6.1). H-236 built the control on 2026-10-04, and it fails
  closed: a covered run and each run under it hold only the delegation
  tools, the sandbox tools and tools that say `open_world=False`
  (`specs/maf_coding_engine.md` §16.3, fence WS43-F24). The prerequisite
  is met for that control. At the flip, the owner accepts the residual
  that §16.3 names, by name.

This answer refines D86 and takes no D-number of its own, as the owner's
answer "Admins of our own org only" did inside D85.

**Fences:** `tests/unit/test_projects_sandbox_tools.py` and
`tests/unit/test_run_data_hygiene.py`, both new.

### D87 — Home is one page at three altitudes. A team view is a lens, never a Center (2026-10-05)

Owner decision, 2026-10-05, in chat. The question was Q1 of the navigation
design: is a team view of Home a Center by another name? The owner gave the
call to the design: "take the best decision on what you think is right".
Board: **WS-44**. Owning spec: **`specs/navigation_shell.md`** §4.

**What is decided.** Home has three altitudes: Personal, a team, and all my
teams. One scope chip moves between them, and the team view stays.

**Why.** D22 named three surfaces: Personal Center, Department Centers and
Company Center. D49 removed them as destinations and kept the group as the
scoping key. Three altitudes of one Home keep D22's shape in D49's terms.
Without a team view, a manager opens four apps and filters each one.

**Five rules are part of the decision** (spec §4.2):

1. No route and no nav entry per team. The scope rides on `/` as a query value.
2. No page built for one team. A team view renders only the cards apps declare.
3. No rule of its own. It reads `reader_scope`, the rule of the Reports app
   (`projects_reports.md` §7.1). It never checks a `center.*` feature.
4. Nothing the member could not already open in the owning app.
5. `/centers/<slug>` stays unlinked.

**What it does not change.** D49 stands. The top altitude is the union of the
teams that `reader_scope` allows, so it needs no new grant. D14 still says
that nothing may rely on `data:org:read`, and this decision does not use it.

**Fences:** `src/lib/nav.test.ts` and a new `src/lib/shell/home.test.ts`
(NS-5).

### D88 — The command bar's AI tier is metered, and its other two tiers are free (2026-10-05)

Owner decision, 2026-10-05, in chat: "For Q3, we go ahead with your
recommendations." Board: **WS-44**. Owning spec:
**`specs/navigation_shell.md`** §6.5.

- Tier 0 (the registry, in the browser) and tier 1 (record search) call no
  model, and they are free.
- Tier 2 (AI intent) is metered at the cheapest chat tier that the operator
  sets, `tier-fast` today. The customer never picks it (D32.7).
- The bar shows no price and no counter.
- When the balance runs out, the CP-6 balance gate, under `CUSTOMER_CONSOLE_SPEND_GATE` stops tier 2. Tiers 0 and 1
  keep working.
- One member's repeat of one query at one scope within 5 minutes comes from a
  cache, and it is free.

**What it waits on.** H-171 (the product's own AI is not metered) and H-69
(`ROUTER_SERVING_ENABLED`). Tier 2 ships dark behind `COMMAND_BAR_AI` until both
are done. The flip spends credits, so it is owner-only (§6.1).

**Fence:** `tests/unit/test_shell_intent.py` (NS-4b), new.

### D89 — Every app joins the shell through one manifest, and no app builds a shell of its own (2026-10-05)

Owner directive, 2026-10-05, in chat: "Future applications that we will start
building into Metorite should also follow the same UI/UX rules." Board:
**WS-44**. Owning spec: **`specs/navigation_shell.md`** §5.

**What is decided.** Every app, built or future, declares a manifest on its
`NavPane`. The manifest names its team, a one-line purpose, its jobs, its
search, its "needs you" items, its Home cards and its agent. The sidebar, the
launcher, the command bar, Home, the bell and the dock read it.

**What an app may not do.** It may not mount a ⌘K handler, a palette, a bell
or an assistant rail of its own. It may not draw its own top bar. A job
opens a form, and a person saves it.

**Why.** Each gap in today's shell is a place where one app solved a shell
problem alone (spec §2). Projects built a palette and a bell. Email built a
second palette. Three apps built three assistant rails. A second
implementation of a seam is a defect (CLAUDE.md §4).

**Where it binds.** `workbench/control_plane/AGENTS.md` rule 10 carries it to
every UI change.

**Fences:** `src/lib/nav.test.ts` (every live pane has a team and a purpose)
and a new `src/lib/shell/seams.test.ts`, whose baseline only goes down (NS-1,
NS-2). *Pointer, 2026-10-05: `seams.test.ts` landed ahead of NS-1, keyed by
app. R9 in §1 carries the spec half.*

### D90 — The platform picks the tier for each step, by the kind of work. The chat's model picker goes, and the effort selector stays (2026-10-06)

Owner direction, 2026-10-05, in chat. **Decided: owner, 2026-10-06.** The
owner approved the text below as written, and the spec's default as the answer
to each open question. Board: **WS-45**. Owning spec:
**`specs/ai_tier_routing.md`** §3.

1. **The model picker leaves the chat.** No member picks a tier in a chat
   composer. The effort selector (Auto, Thinking, Max) stays.
2. **The executor picks the tier for each model request, by the kind of
   work.** A decision goes to `tier-fast`. Ordinary chat and tool use go to
   `tier-balanced`. Code, a plan of many steps and hard analysis go to
   `tier-powerful`.
3. **Every AI call goes through our Router tiers.** No agent and no tool calls
   an outside AI vendor directly.
4. **An agent calls System 1 as a tool.** It is an MAF agent wrapped as a
   function tool, on `tier-fast`, with no tools and one fixed answer: a choice,
   a confidence and a short reason. Low confidence hands the decision back to
   the main model. It takes a batch. It says `open_world=False`, so a covered
   run keeps it (H-236).
5. **It binds every agent**, through one flag, `AI_TIER_ROUTING`, default OFF.
6. **Modality tiers come later the same way.** A tool declares the task.

**What it amends, each one cited and none reopened:**

- **D75.6.** The chat's `decide` tool keeps its name. Its engine moves from
  the `decide` task (Jev, `tier-decide`) to the System-1 agent on
  `tier-fast`. D75's other clauses keep governing the email features.
- **D59.5 and D59.6 (4).** A customer may still see a tier label. A customer
  may not pick a chat tier, and step (4) will not come.
- **D67.1.** The meter rates the tier that served the call, which the
  platform picked. The price key does not change.
- **D-AI-3** (`ai_metering_and_analytics.md` §3.3). `customer_visible` TRUE
  now means that the label may show. Nobody picks it.

**What does NOT change.** D32.7 and D66 (a customer never sees a model). D61.3
(the caller declares the tier, and the Router reads no payload). D57.7 (a
routed call that fails, fails). D-AI-4 (an app declares its default tier, and
that default is the Balanced rung).

**Fences:** the spec's §10 names 16. The new ones are
`tests/unit/test_tier_policy.py`, `tests/unit/test_system_one_tool.py`,
`tests/unit/test_no_direct_ai_vendor_calls.py` and
`src/components/AgentChat.picker.test.tsx`.

**The open questions, answered by the owner on 2026-10-06 with each default
(spec §13.2):**

- **Q1.** The email triage features stay on `tier-decide` (Jev), under D-EM-7
  and D-EM-8.
- **Q2.** Code goes to `tier-powerful`, not to `tier-code`.
- **Q3.** The effort selector keeps three labels. It gets no "Fast" label.
- **Q4.** Each answer shows its tier label in its details menu, for every
  member.
- **Q5.** The rule that `tier-fast` serves only an existing sub-processor is an
  operator warning on the binding page, added with WS-37.
- **Q6.** S2 reads the live bindings on the box and reports. The policy does not
  change.
- **Q7.** The thresholds are 0.70, 0.80 and 0.90, until S1's measured agreement
  says otherwise.
- **Q8.** The turn-kind question stays. S2 reports the median time it adds.

### D91 — The Projects assistant reaches the app through native tools, held to the API and the UI by fences. MCP is a later facade for external clients (2026-10-06)

Owner direction, 2026-10-05, in chat. **Decided by the owner on 2026-10-06:
the text below, as written, with the defaults of spec §14 as the answers to
Q1 to Q6.** Board: **WS-46**. Owning spec:
**`specs/projects_agent_parity.md`** §13.

1. **Native tools.** The tools of the Projects assistant live in
   `skill_projects`. They say `open_world=False`, and they call the gateway
   as the member. No MCP server stands between the Projects agent and the
   Projects API.
2. **Parity is a rule with fences.** Each route, request field and UI
   client method has a chat tool or a recorded exemption.
3. **A refusal reaches the model as text.** An unknown argument is refused
   by name, never dropped.
4. **Context from another module comes through that module's own agent,**
   by delegation. A personal store, for example a mailbox, never gets a
   direct read tool in a shared agent.
5. **An MCP facade for external clients is a later, separate item.** It
   gets its own spec and an owner gate.

**Why native, and not MCP.** H-236 treats every MCP tool as egress, so a
covered Projects run holds none. D7 records that MCP injection does not
reach a MAF agent. The native seam already carries the member's identity,
the route's authority and the card.

**What it amends.** D-PM-37 (`specs/projects_ai_chat.md` §7.1), from the
route to the request field and the UI action. It reads the owner's word
"MCP" as the capability, delivered natively.

**What it does not change.** D7 and WS-8c. D-PM-35 and D-PM-40. D85, and
D86 with "Keep delegation". The H-236 rule that every MCP tool is egress.

**Fences:** `tests/unit/test_projects_field_parity.py` (F2),
`tests/unit/test_projects_ui_actions.py` (F3) and
`tests/unit/test_projects_agent_refusals.py` (F4 and F5), all new.

### D92 — Every agent runs on MAF. The Copilot SDK agents port, and the SDK path goes. It amends D84 and D86 (2026-10-07)

Owner decision, 2026-10-07, in chat. **Decided: owner, 2026-10-07.** Board:
**WS-43**, with a pointer row in **WS-48**. Owning spec:
**`specs/maf_coding_engine.md`** §17.

**The decision.** Metorite stops using Copilot SDK agents. Every agent runs on
Microsoft Agent Framework.

**What D84 said, quoted.** "Every use of the Copilot SDK moves to MAF". It
then named the uses: "`code_task`, app-builder, self-mutation, the root
`metorite` agent, the agents that a repo registers, the model list and the
session store." D86 then parked
the slices that port the older agents, and said "D84 stays decided, and it is
deferred."

**What D92 changes.**

1. **D84 is no longer deferred.** D92 restarts the port and removal slices
   that D86 parked: WS-8i, WS-43h, WS-43j, WS-43l to WS-43s and WS-43t2.
2. **D84 gains a reason.** An SDK agent cannot change its tier inside a run
   (`specs/ai_tier_routing.md` §4.5). So it cannot follow the D90 policy, and
   two runtimes are two seams (CLAUDE.md §4).
3. **D86's order of the Projects and Email tracks does not change.**

**The agents, measured at `main` `82d09830b`.** Two agents in
`apps/agents/` build a `GitHubCopilotAgent`: `agent-task-manager/agents.py:37`
and `agent-app-builder/agents.py:18`. ⚠️ Both `config.json` files say
`"runtime": "maf"`. The gateway's `_AGENT_REGISTRY` labels both
`"github-copilot"` (`gateway/routes/agent.py:439` and `:496`). The executor
finds a Copilot agent by the label or by the object, through
`_default_options` (`executor.py:3821-3824`). Two more
agents are Copilot agents: the root `metorite` agent (root `config.json`,
`"runtime": "github-copilot"`) and the repo agent `agent-sales-assistant`
(`apps/services/gateway/agents.json:11`).

**What retires with the SDK.**

- The `copilot_chat_model` setting (`acb_common/settings.py:735`) and its
  route `POST /settings/llm/copilot-model` (`gateway/routes/settings.py:933`).
  ⚠️ A native agent that `AI_TIER_ROUTING` does not cover reads it too
  (`executor.py:3871-3874`). So WS-43q item 5 holds until the flag covers
  every agent: it becomes `default_chat_model` for one release.
- The `.agent.md` model resolution for an SDK agent
  (`_model_resolution.py:69-85`, and `_agent_default_tier` at
  `executor.py:6222-6235`).
- The BYOK provider injection: `_byok_default_model`
  (`_model_resolution.py:35`, called at `executor.py:1177` and `:3896`) and
  the block that logs `executor.copilot_maf_byok_early` (`executor.py:3911-3938`).
- The Copilot CLI fetch in `scripts/vps_apply.sh:743-755`.

**How it ships (R6, expand and contract).** Port each agent to MAF behind a
flag. Prove parity, and then flip the flag. Then delete the SDK path. The removal
slices stay owner merges (WS43-G7, WS43-G9). The dev-phase window does not
open them.

**Fences:** `tests/unit/test_no_copilot_sdk.py` (WS43-F15) is the fence of
record. Its allowlist shrinks to zero at WS-43r. The label fence,
`tests/unit/test_agent_config_runtime.py` (WS43-F31, new), fails when an
agent's runtime label names `github-copilot`. ⚠️ It must read four places:
each agent `config.json`, the root `config.json`, `gateway/agents.json` and
`_AGENT_REGISTRY`. The label of task-manager and app-builder is not in their
`config.json`. Its allowlist holds the four labels of today, and it only
shrinks.

### D93 — A typed System-1 question goes to the decision model, `tier-decide`. It amends D90 (2026-10-07)

Owner decision, 2026-10-07, in chat. **Decided: owner, 2026-10-07.** Board:
**WS-48**, slice N3. Owning spec: **`specs/data_narrowing_pipeline.md`** §9
N3, with the amendment boxes in **`specs/ai_tier_routing.md`** §5 and §6.1.

**The decision.**

1. **A typed question goes to `tier-decide`,** through the existing door
   `POST /v1/decide` and its one facade, `acb_llm.decide`. A typed question is
   a `choice` or a `score`, and a yes-or-no relevance check is one too.
2. **A free-form question, and the turn-kind question, stay on `tier-fast`.**
3. **A failure falls back to `tier-fast`, and the tool logs the fallback.** A
   failure is an unbound tier, a 4xx or 5xx answer, or a timeout.

**What it amends.** D90 clause 4 put System 1 on `tier-fast`. The sentence
that changes is in `ai_tier_routing.md` §5: "**A decision stays on
`tier-fast` in every mode.** The owner put System 1 on the fast tier." After
D93, a typed decision goes to `tier-decide`, and `tier-fast` is its fallback.

D93 also takes back, for typed questions, D90's amendment of D75.6. Under D90
the engine of the `decide` tool moved from Jev to `tier-fast`.

**What does NOT change.** D57.7: both engines are Router tiers, and nothing
falls back to a direct vendor. D75.7's adoption rule: N3 reports the
agreement of the two engines before the owner turns it on. D90 Q1: the email
triage features stay on Jev. The tool name `decide`, and its schema.

**Three code facts that bound it, measured at `main` `82d09830b`.**

- The System-1 schema has no free-form kind today. Its kinds are `yes_no`,
  `choice` and `score` (`decide_tools.py:76`). So clause 2 binds the turn-kind
  question and any later free-form use.
- `acb_llm.decide` calls a 400 or a 422 a caller bug that must surface
  (`acb_llm/decide.py:103-107`). The fallback still answers, and it logs that
  case at `error`.
- The decide vendor is a separate sub-processor (D75.8). So in a `no_egress`
  run every question stays on `tier-fast`, and the egress class of
  `ai_tier_routing.md` §6.6 does not change. This is an agent default, and
  `data_narrowing_pipeline.md` §12 Q4 asks the owner.

**Fences:** `tests/unit/test_system_one_tool.py`, extended with WS48-F6.

---

### D94 — The Operator Console states money in one vocabulary, in rupees, and a free credit is not revenue (2026-10-09)

Owner decision, 2026-10-09, in chat. **Decided: owner, 2026-10-09.** Board:
**WS-50**. Owning spec: **`specs/operator_console_money.md`** §3.

**The decision.** The owner accepted four recommendations.

1. **A free credit is not revenue.** A credit from a trial, promotion, grant or
   refund lot earns nothing. Its AI cost shows as "Given away".
2. **Rupees everywhere.** A dollar cost converts at the planning rate saved on
   `/pricing`. The tooltip shows the dollar figure.
3. **"We charged" is the value of what the customer used in the period.** It is
   seats plus paid credits spent. Cash received is a separate figure.
4. **One PR per slice**, in order, each merged and deployed before the next.

**What it retires.** The ratio of credits to dollars ("× cost") as a margin.
It dates from before a credit had a price (H-42), and the owner read it as a
markup.

**Fences:** `tests/unit/test_customer_console_credit_draw.py`, and the slice
fences that the spec names in §7.

### D95 — The CRM has two modes, Native and Mirror, over one store. A mirror is a product (2026-10-11)

Owner directive, 2026-10-11, in chat. **Decided: owner, 2026-10-11.** Board:
**WS-53**. Owning spec: **`specs/crm_platform.md`** §3 and §16.

**The decision.** The owner read the plan and told the agent to build it. The
owner took five recommendations.

1. **Two modes for each org.** Native makes Metorite the CRM. Mirror copies an
   external CRM, Zoho first. "Zoho is retired" is no longer the end state of
   the product. Fracktal can still retire its Zoho, as CRM-Z10.
2. **The rename.** `crm_organizations` becomes `crm_companies`, and the
   business column becomes `company_id`. Then `organization_id` means the
   tenant only. The rename lives inside the migration, behind a guard, as the
   `gtd_*` rename did. The tables are empty on prod, and the app is `preview`.
3. **Mirror v1 reads only.** This narrows D-CRM-7. The write path stays in the
   code, and it is off for every connection until CRM-Z9.
4. **One store and one connector seam.** Both modes use the `crm_*` tables, so
   every reader and every AI agent is blind to the mode. A provider is an
   adapter, and its credential lives in `integration_connections` only. *(Amended 2026-10-11: the store is general, not CRM-only, so the Integrations app, WS-54, reads one registry.)*
5. **CRM follow-ups live in `pm_tasks`.** This applies D52 and D53 to the CRM.

*Amended 2026-10-15 (owner, in chat): in Mirror mode, the CRM shows the
source's own labels, for example Contacts as "Existing Sites". A Native CRM
keeps "Companies". This narrows D95.2 for Mirror mode only
(`crm_platform.md` §18.9.1).*

**What it does not change.** D52 still retires ClickUp. D52.4 already allows a
read-mostly mirror of Zoho, and D95.3 stays inside it. The flat ₹500 seat
holds, and the mirror is inside it.

### D96 — Integrations: connect in the app, manage in one console, over one registry (2026-10-11)

Orchestrator decision, 2026-10-11, at the owner's request. **The owner was told the
same day and may overrule it.** Board: **WS-54**. Owning spec:
**`specs/integrations_console.md`** §3 and §10.

1. **A connect flow lives in the app that needs it.** The console links to it
   and never repeats it. This is D-EM-1 made general.
2. **The Integrations app is an admin console over one registry,**
   `integration_connections` (D95.4). It also holds the connections that have
   no app, such as MCP servers.
3. **A customer credential never enters `os.environ` or `.env`.** It reaches a
   run as an argument. This is D58.2 for integrations.
4. **An MCP server is a connection, and MAF agents get an MCP client** (WS-8c).

---

### D97 — Metorite is an MCP server for outside agents, and the member's live access is the ceiling (2026-10-10)

Owner decision, 2026-10-10, in chat. **Decided: owner, 2026-10-10.** The owner
accepted every recommendation of the spec. Board: **WS-55**. Owning spec:
**`specs/external_agent_access.md`** §6.

1. **Door.** One MCP edge on its own host, `mcp.metorite.com`, in its own
   unit. The consent page stays on `app.metorite.com`.
2. **Authorization.** OAuth 2.1 from a vetted library inside our code. No
   second identity system.
3. **Authority.** An agent acts as one member. Its power is the member's live
   access, cut down by the scopes and project pins the member chose.
4. **Personal projects** stay out of reach unless the member ticks a box on the
   consent page. The box is off.
5. **Version 1 has no personal access token.** Interactive OAuth only.
6. **Company context** is the name, domain and branding. It also holds the
   shared memory that the member may read. This lasts until a company profile
   exists (H-15).
7. **No charge.** The agent uses the member's seat.
8. **Who may connect.** An organization admin switches the feature on, and it
   is off by default. Then any member may connect, unless the admin sets an
   allow list.

**What it extends.** D91.5, which asked for this spec and an owner gate.

**What it does not change.** D91.1 keeps native tools for the in-product
assistant. D-PM-35 and D-PM-40 keep the ban on delete and on grant writes.
D63 keeps sealed trees. The H-236 rule stays: every MCP tool is egress.

**Still an owner gate.** EA-10: the DNS record, the Caddy block, the unit and
the flag. No grant covers them until the owner names them.

**Fences:** `tests/unit/test_mcp_edge_coverage.py` and the slice fences of
spec §15. All are new and none exists yet.

---

### D98 — An org MCP connection over OAuth acts as the admin who connected it, and says so (2026-10-12)

Owner decision, 2026-10-12, in chat. **Decided: owner, 2026-10-12.** Board:
**WS-54**. Owning spec: **`specs/integrations_console.md`** §3.6 (IN-8).

**The question, verbatim:** "Notion, Linear and Atlassian issue tokens per user,
so an org-wide connection acts as one admin. How should org-wide OAuth
connections work?"

**The answer, verbatim:** "Allow, clearly labelled."

1. **The owner allows org connections over OAuth.** An admin connects an MCP server
   that needs an OAuth consent, and the connection serves the agents of every
   member of the org.
2. **The label.** The console and each confirmation card show "acts as <admin>
   at <provider>". The admin is the member who connected it (`connected_by`).
3. **What a member can reach.** A read-only tool runs with no card (H-299). So
   a member can read, through an agent, what the admin can read at that
   provider. The Connect dialog says this before the consent.
4. **It ships dark,** behind `MCP_OAUTH_ENABLED`, default off. The flip is gate
   `enforcement-flip`. It waits for the owner's real consent test against a
   third-party account (CLAUDE.md §3a rule 3).
5. **Org scope only, for now.** The owner said the same day: "get the
   organization-wide connectors done, and later on we can start working on the
   personal connectors." A member connection over OAuth is out of scope.

**Recorded the same day, not a decision of its own.** H-300 asked: "should
admins get a per-server 'always confirm' switch, now that MCP is on for all
orgs?" The owner answered: "Build it now, keep MCP on." `MAF_MCP_ORGS=*` stays
on production. The switch is `provider_meta.always_confirm` on the registry row,
read by `McpPermissionGate`. It came right after IN-4, and it is built as
`specs/agent_architecture.md` §12.2 WS-8c, item 31.

**What it extends.** D96.4: an MCP server is a connection. D98 adds the
connections that need an OAuth consent.

**What it does not change.** D56 keeps operator keys in the operator plane, and
a pre-registered OAuth client is one of them. H-236 stays: every MCP tool is
egress. H-299 stays: a tool that can change data waits for the member's card.

**Fences:** the rows of `specs/integrations_console.md` §3.6.11. All are new and
none exists yet.

### D99 — A member talks to one assistant, and keeps sessions, not agents (2026-10-12)

Owner decision, 2026-10-12, in chat. **Decided: owner, 2026-10-12.** Board:
**WS-44**. Owning spec: **`specs/navigation_shell.md`** §7.1 (NS-6 slice 6b).

> **Amended by D117 (2026-10-26) for conversational work.** Item 2 changes.
> The one assistant loads each app's tools as a tool pack and does the work
> itself. A dynamic sub-agent does the parallel work. A specialist agent stays
> for a scheduled job, a workflow and an external agent. Items 1, 3 and 4
> stand.

**The question:** does the member talk to one assistant with specialists
behind it, or to each specialist directly?

**The answer, verbatim:** "We can basically need to keep track of multiple
chat sessions rather than multiple agents. We can specialize sub-agents, but
the user chats with one general agent always."

1. **One assistant.** Every new chat goes to the general agent,
   `orchestrator`, which the member sees as "Metorite". The member picks no
   agent before the first answer.
2. **Specialists are tools.** The general agent calls a specialist as a tool,
   and control comes back to it (`multi_agent_orchestration.md` §5.1). A
   specialist never takes over the chat. D25.4 stands: no handoff.
3. **The member keeps sessions.** A member names, finds and reopens a chat.
   A chat records its agent, so an old chat with a specialist still opens.
4. **The chat leaves the sidebar.** A chat bar at the foot of every page
   opens the assistant as a side panel or as a full view. `/chat` stays as
   the address of the full view. Chat stays a live pane
   (`launch_surface.md` §2). This decision changes where it opens, not
   whether it is live.
5. **The assistant knows where the member is.** Each turn carries the app
   and what the app shows.
6. **The activity control moves into the chat bar.** This amends the place
   that WS-51 S3 gave it.
7. **It ships dark,** behind `NEXT_PUBLIC_SHELL_CHAT`. Only the owner turns
   it on in production (`navigation_shell.md` §13.1).

**Why.** In a controlled study, users rated one agent above a team of
agents for usability, with answers of almost equal quality (arXiv
2401.07123). Custom GPTs took about 1.5% of ChatGPT visits in February 2024.
People stay in the default box. ChatGPT, Claude, Gemini and Microsoft 365
Copilot each give one box, and an orchestrator routes behind it.

**The friction we accept.** The member sees less of which specialist
answered. A misroute can occur. A routed turn costs one more model call.
The chat shows each specialist step, and a routing test set is a later
slice.

**What it amends.** `navigation_shell.md` §7.1 ("the agent follows the
app") and §13.2 Q7 (the dock toggle in the shell bar).
`multi_agent_orchestration.md` §5.3 ("Users chat directly with
specialists"). `chat_run_continuity.md` S3 (the place of the control).

**Fences:** `src/lib/shell/seams.test.ts` (one dock),
`src/lib/shell/assistant.test.ts` and `src/lib/shell/shellNav.test.ts`.

---

### D100 — `decide` may serve every app, not only email triage (2026-10-13)

Owner decision, 2026-10-13, in chat. **Decided: owner, 2026-10-13.** Board:
**WS-58**, and every later adopter of `decide`. Owning spec for its first
non-email use: **`specs/my_day_feed.md`** §10.

**The question:** D-EM-9 approves `decide` residency for email triage "and for
no other app". My Day would send task titles, deal names, goal names and mail
subjects as clipped facts. May it?

**The answer:** the owner approved the use of `decide` across the whole
product, in chat on 2026-10-13.

1. **Residency for `decide` covers every app.** Tenant content may go to
   TypeSafe and to AI/ML API for a `decide` call from any app. This widens
   D-EM-9, which stays the record for email.
2. **The adoption rule still binds each feature.** Every feature starts
   `off`, then runs `shadow`, then `on` behind a gate (D75 item 7,
   `customer_console.md` §6A.14). D100 answers residency, and nothing else.
3. **The customer pays in AI credits** (`my_day_feed.md` §9.3, owner,
   2026-10-13).

**What it does not change.** `DECIDE_ENABLED` stays an owner act (H-166). A
text model is not `decide`, so D100 says nothing about a text call.

---

### D101 — A My Day card may act in place, unless the act needs more context (2026-10-13)

Owner decision, 2026-10-13, in chat. **Decided: owner, 2026-10-13.** Board:
**WS-58**. Owning spec: **`specs/my_day_feed.md`** §11.

**The answer, in the owner's words, in three parts:** "actions can happen
inside cards, inside my day itself, unless the action needs more context".
Then "the user can prefer to open it in the app itself". Then the owner asked
the design to work out when to do which.

1. **An act runs in the card.** An act on the member's own data is one tap. An
   act that reaches another person expands in the card, shows its whole
   effect, and waits 10 seconds with Undo.
2. **"Needs more context" is a test in code** (`my_day_feed.md` §11.2). When
   it is true, the act opens the owning app.
3. **The member can always open the app instead,** and can make that the
   default for a kind.
4. **Class C never runs in a card:** delete, bulk acts, money, and the
   approval of an agent's act.

**What it amends.** `navigation_shell.md` §4.3 allowed My Day three one-click
acts. D101 widens that for the feed of WS-58.

### D102 — One bar asks, searches and opens, at the foot of the page. It amends D88's place (2026-10-13)

> ⚠️ **Amended 2026-10-14 by the owner: the one field is in the shell bar.**
> The owner wrote: "remove the bar at the bottom and have it at the title
> bar … because we have space in the top bar in any case. The functionality
> should be as expected by the user." So rules 1 to 4 below stand, with two
> changes of place:
>
> - **The field is `AskField`, in the middle of the shell bar.** No bar sits
>   at the foot of the page. The results drop BELOW the field, and ↓ moves
>   into them. The ask row is the top row, nearest the field.
> - **The modal opens only on a phone.** The shell bar sits above the
>   assistant, so the field shows in every form of it. Words typed while the
>   assistant is open go into the chat on screen.
>
> The same day the owner asked for a predictable panel: "I'm not sure when
> to expect the sidebar or the full screen". Four rules now hold:
>
> - **Every open from closed is the side panel.** The full view is a moment
>   of focus that the member asks for, and it is not kept for the next open.
> - **A desktop narrower than 1280 px has the side panel too.** It floats
>   over the right of the page. The width never picks the form.
> - **A page change in the full view shrinks it to the side panel,** on
>   every desktop.
> - **The activity control has one job.** It shows the running count and
>   opens the list of runs. A panel button beside it opens and closes the
>   assistant. ⌘J opens it, and closes it when the cursor is in it.
> - **A finished-run toast opens its chat in place,** as the side panel. A
>   new tab or a link to `/chat` still opens the full view, because that is
>   a page of its own.
>
> Fences: `oneBar.test.ts`, `assistant.test.ts`, `appBar.test.ts` and
> `activityControl.test.ts`.

Owner direction, 2026-10-13, in chat: "there is an overlap between the top
search bar and the bottom agent bar. I think we should just have one and
integrate the functionality of that top search bar into the bottom as well."
Board: **WS-44**. Owning spec: **`specs/navigation_shell.md`** §6.1 and §7.1.

1. **One field.** With `NEXT_PUBLIC_SHELL_CHAT` on, the chat bar at the foot
   of the page is the only field. The shell bar at the top holds no search
   field. It keeps the logo, the bell and the account.
2. **Typing shows the results above the field.** They are the groups of the
   command bar: Do, Go to, Find and In this page. One engine answers both
   the bar and the modal (`useBarResults` in `CommandBar.tsx`).
3. **Enter asks Metorite.** The bar is a chat box first. Enter opens a result
   only when the member moved the highlight onto it, with the arrow keys or
   the mouse. The bottom row of the results says what Enter does.
4. **⌘K, `/` and "search everywhere for …" reach the bar,** with the results
   open. ⌘J reaches it too.
5. **The modal stays for two cases.** The phone opens it from the Menu
   drawer, and a desktop opens it while the assistant is open, because the
   bar is then hidden.

**Why.** Two fields that each "search or ask" make the member choose before
they type. A chat box at the foot of the page is where people type to an
assistant. A results list rising above a field is the shape of a phone's
keyboard suggestions and of Slack's composer.

**What it does not change.** D88 stands: tiers 0 and 1 are free, and tier 2
is metered and dark. Only the place of the field changes.

**Fences:** `src/lib/shell/oneBar.test.ts` (new) and
`src/lib/shell/appBar.test.ts`.

### D103 — Reach is the one way Metorite contacts a member first (2026-10-14)

Owner direction, 2026-10-14, in chat. The WhatsApp bot should send messages
first, about "what it thinks I should know". Then, on the proposal: "I am fine with your
suggestions, go ahead with your recommendations." Board: **WS-59**. Owning
spec: **`specs/proactive_reach.md`**.

1. **One outbound seam.** An app reaches a member outside the app only
   through a My Day card with a reach class. No app sends to a member
   channel itself (D-RE-1).
2. **My Day judges, Reach delivers.** The judge is `my_day.nudge`, packed
   into the member pass. Reach adds no model call of its own, except one
   brief sentence each day (D-RE-2).
3. **The budget.** 3 interrupts and 1 urgent each day, inside working
   hours. A member may raise the cap to 6. Only the member's own rule may
   break quiet hours (D-RE-3, D-RE-4).
4. **The outbox in Postgres is the delivery truth** (D-RE-5). Every
   WhatsApp message is utility or free text in an open window, never
   marketing (D-RE-6).
5. **The owner's answers.** Metorite absorbs Meta's template charge inside
   the seat. Opt-in is off until the member says yes. The brief follows
   `work_schedule`. An org admin has one switch for the org.

**What it does not change.** `my_day_feed.md` keeps the signals, the cards
and the judge. `whatsapp_assistant_channel.md` keeps the bot. CLAUDE.md §3a
rule 3 still binds: a flag that sends to a real person is the owner's flip.

### D104 — Knowledge is the company brain, authored and cited, with one index (2026-10-15)

Owner direction, 2026-10-15, in chat. The owner asked for a knowledge base
in the shape of Notion or Confluence, linked to Artifacts, queryable by the
chat and every agent, fed by Dropbox, OneDrive and Google Drive, and able to
give insights in chat and My Day. On the proposal: "Go with defaults."
Board: **WS-61**. Owning spec: **`specs/knowledge_base.md`**.

1. **Knowledge is an authored, cited store with one index.** It is not a
   surface over Mem0. It replaces the framing of `future_modules_roadmap.md`
   §1. Memory stays WS-9 (D-KB-1).
2. **One retrieval seam, `acb_search`, over one table, `kb_chunks`.** The
   lexical half and the vector half live in one row. It is BO-22 built. No
   app adds a second vector table for documents (D-KB-2).
3. **Visibility is the space's grants in the one subject grammar.** A page
   inherits its space. A draft is the author's. There is no page-level ACL
   and no provider ACL mirror in the first release (D-KB-3).
4. **An agent never publishes.** Every agent write to knowledge is a draft
   or a card that a person answers (D-KB-4).
5. **A connected folder is linked, not copied.** Metadata at connect, text
   on inclusion, bytes on a member's "Keep a copy". Metorite never writes
   to a provider (D-KB-5).
6. **"Save to Knowledge" is a copy with provenance.** The artifact stays in
   its chat with the chat's visibility (D-KB-6).
7. **Files is a view of the Knowledge app, not a pane** (D-KB-7).
8. **`kb:read` joins the `/mcp` scopes**, as an amendment to
   `specs/external_agent_access.md` §8 (D-KB-8).
9. **The owner's answers, by default.** Dropbox first, then OneDrive, then
   Google Drive (Q1). Google's restricted-scope verification is filed when
   the first paying customer asks for Drive, and Drive serves test users
   until then (Q2). An org source lands in one space at connect, and a
   member maps folders one at a time after (Q3). No per-file ACL mirror in
   v1 (Q4). The index is lexical only until the owner flips
   `KNOWLEDGE_EMBED` for an org (Q5). The `me` space ships in the first
   release (Q6). The editor is TipTap in KB-2b, over a Markdown textarea
   from KB-2a (Q7).

**What it does not change.** `memory_architecture.md` keeps the agent's
memory. `integrations_console.md` keeps the registry and the credential
rule. `my_day_feed.md` keeps the cards. CLAUDE.md §3a rule 3 still binds:
a flag that spends credits or reaches a provider for an org is the owner's
flip.

### D105 — Company retrieval asks each app, and copies none of them (2026-10-15)

Owner directive, 2026-10-15, in chat. The owner asked for retrieval over
the knowledge base and over the data stores of every app, the CRM and email
included, so the AI gathers what it needs and gives insights without being
asked. Board: **WS-61**. Owning spec: **`specs/knowledge_base.md` §20**.

1. **One seam.** `gateway/retrieval/` holds one registry of sources. Each
   source calls its app's own authorized search with the member's
   `UserContext`. The command bar, the assistant, `narrow_and_read` and My
   Day read the same registry. No consumer adds a source list of its own.
2. **Federate, never copy.** No table holds a copy of another app's rows
   for search. An app that needs semantic recall adds it in its own table,
   behind its own read. Knowledge's `kb_chunks` is a store, not a copy.
3. **No model in retrieval.** The seam finds and ranks with code. A model
   reads what it returns.
4. **The assistant's legacy retrievers retire.** `retrieve_entity_context`
   and `retrieve_sales_context` read five tables that hold 0 rows in
   production. `search_company` replaces them behind
   `COMPANY_SEARCH_ENABLED`, and a later release deletes them.
5. **Insight is a join, not a new judge.** My Day asks the seam for the
   records related to a card, and `decide` judges the card with them. Reach
   delivers it. No app gets a second feed.

**The owner may overrule it.** It is an engineering shape under the
owner's directive, as D96 was. It changes no price and no product surface.

### D106 — Context stays current: a stamp, recency, a digest and healthy connectors (2026-10-18)

Owner directive, 2026-10-18, in chat. The owner pointed at a talk on AI
knowledge bases, and gave its build prompt as the template, made general.
The owner asked that its ideas guide how all of Metorite feeds the AI. Board: **WS-61**. Owning spec: **`specs/company_context.md`**. It
extends D105 and changes neither D104 nor D105.

1. **Every hit carries a stamp**, the date its record last changed in
   substance, beside `when`.
2. **The seam ranks newer above older inside each app**, by a decay per app
   with a floor. A Knowledge page decays by its review state, not by its
   age. A replaced record scores at half. The model reads the stamp, and
   the newer record wins a conflict.
3. **A cheap model writes a digest per long record**, in the record's own
   words. It holds segments (a topic, its question, a short summary) and
   atoms. Code checks each atom against the record, and masks secrets
   first. The digest lives in the owning app's table, under the record's
   visibility.
4. **Lexical first.** The query asks for any of its words. A digest comes
   before a vector. A fixed-question eval is the fence for every ranking
   change.
5. **Every connector holds a cursor, reads only, and reports
   `last_new_at`.** A quiet connector makes a card for an admin.
6. **Past AI sessions are a source**, for their participants only.

**The owner may overrule it.** It is an engineering shape under the owner's
directive. Each flag that spends credits stays the owner's flip.

### D107 — Docmost core code may be ported, and the product will release under AGPL (2026-10-19)

Owner directive, 2026-10-19, in chat: "We will also eventually release
under AGPL, so you can take code from Docmost if needed. Do what is needed
get the UI/UX systems as similar to Docmost as soon as possible." Board:
**WS-61**. Owning spec: **`specs/knowledge_base.md` §21.1 and §21.7**. It
replaces the rule of §21.1 of 2026-10-18, "ideas only, never code".

1. **Core only.** A builder may port code from Docmost core (AGPL-3.0).
   Nothing comes from `apps/server/src/ee`, `apps/client/src/ee`,
   `packages/ee` or `packages/base-formula`. Those carry a separate licence.
2. **A header and a notice line.** Each ported file names Docmost in its
   first lines, and `THIRD_PARTY_NOTICES.md` lists it. A test fails when
   they disagree.
3. **Our look stays.** Parity means the features, the layout and the
   interactions. It does not mean Mantine, and it does not mean a second
   palette. `DESIGN_SYSTEM.md` binds.
4. **The repo licence is not changed here.** The owner sets its date
   (HANDOFF H-370). AGPL-3.0 section 13 binds from the first release that
   serves ported code.
5. **Staging first, then production on the owner's word.** The owner
   answered on 2026-10-19: "Merge to staging first. Get everything working
   on staging first. I'll test it out and then we'll do it on production."
   A pull request with a ported file merges after its review, and staging
   carries it. Production does not move until the owner has tested staging
   and says so. The agent held the nightly release with `RELEASE_HOLD=1`
   meanwhile. On the owner's word, the agent sent wave 1 to production on
   2026-10-22 (#1210, #1217), and the owner cleared the hold. ⚠️ That release
   carries ported Docmost files, and the licence date of H-370 is not set.

**The owner may overrule it.** It changes no price. It changes the licence
position of the repo, which is why part 4 names the owner.

### D108 — File bytes go to Supabase Storage through one seam, and an artifact owner gets 5 GB for each seat (2026-10-20)

Owner decision, 2026-10-20, in chat: "Go ahead with your recommendations."
The recommendations were the store of `saas_multitenancy.md` MT-1g and the
quota of `artifact_management.md` §13 Q3. Board: **WS-29** (MT-1g) and
**WS-60** (AM-5). Owning specs: **`specs/saas_multitenancy.md` MT-1g** and
**`specs/artifact_management.md` §7.5**.

1. **One store for file bytes: Supabase Storage, through its S3 endpoint.**
   One private bucket for each environment: `metorite-files` for production
   and `metorite-files-staging` for staging. The backup bucket
   `metorite-backups` stays separate.
2. **One seam.** `acb_common.object_store` is the only code that talks to
   the store. Keys are `<organization_id>/<app>/<object id>`. The tenant
   comes from the bound context, never from request input (R5). The seam
   refuses a key of another organization.
3. **No project-wide S3 key.** A Supabase S3 access key opens every bucket
   of the project, the backups included (H-271). So the app signs in as its
   own Supabase Auth user and sends the short-lived JWT as the S3 session
   token, as BH-4 does for the backup. Storage policies limit that user to
   the files bucket of its environment.
4. **The order of the moves is MT-1g's.** Files with one copy on the box's
   disk move first, then `agent_blob`, then large text, one table at a time.
   New upload paths write to the store from the day the seam is on.
   **The files bucket gets its own backup before `agent_blob` moves.** Those
   bytes then leave the nightly `pg_dump`, and the files user can delete. So
   MT-1g-2 adds a nightly copy of the files bucket to the off-box backup,
   written by the insert-only backup writer of BH-4.
5. **Artifact quota: 5 GB for each member, and 5 GB × `seats_purchased`
   for each organization, pooled.** A membership row is not a seat (D32.5). A write past either limit is refused with a
   reason, and a delete always works. This answers §13 Q3. The organization
   number becomes a plan fact when WS-31 holds one.

**The owner may overrule it.** It adds a storage cost of about $0.02 per GB
each month on Supabase, and it moves no price.

---

### D109 — Chat first: the seven decisions of WS-62, with one amendment (2026-10-22)

Owner decision, 2026-10-22, in chat: "go ahead with your recommendation",
after the owner asked for the recommendation of an interaction designer.
Board: **WS-62**. Owning spec: **`specs/chat_first_surface.md`** §5.

1. **No peek strip on a desktop** (D-CF-1). The side panel is the desktop's
   way to keep the chat beside the page.
2. **No auto-open on a desktop** (D-CF-2). The desktop gets one nudge under
   the field.
3. **A phone launch starts in the chat, once per browser session, and never
   on a deep link** (D-CF-3). A choice made in the session stands. A member
   who closes the sheet at once on three launches in a row stops the open,
   and gets a "Chat first" switch. That refinement is CF-9, not built.
4. **The chat moves the page only while the member watches that chat**
   (D-CF-4).
5. **A write that leaves the company or cannot be undone asks once, on one
   card. A reversible write on the member's own work acts at once and offers
   Undo** (D-CF-5, amended). This amends the owner's rule of 2026-10-20 for
   the reversible case: mark done, snooze, move to Monday. Send, delete,
   money and membership still ask.
6. **The chat's brief is My Day's composition, read as is** (D-CF-6).
7. **The nudge is one, held on the server** (D-CF-7).

**Why.** Conversation first, screens on demand, and the member's last
choice wins over the default. People habituate to a confirm on a low-stakes
act within days, while Undo keeps the flow and the safety.

**What it amends.** The owner's rule of 2026-10-20 ("a write asks once, on
one card"), for the reversible case only. `projects_ai_chat.md` §29 reads
with this decision.

---

### D110 — Personalization: one bounded profile per member, a learned voice, and a setup coach (2026-10-24)

Owner decision, 2026-10-24, in chat: "Send me the questions for decisions, or go ahead with your recommendations."
Board: **WS-63**. Owning spec: **`specs/personalization.md`** §8 and §12.8. The owner accepted every default.

1. **The profile lives in `user_settings.assistant_profile`** (D-PZ-1). It is not in Mem0, and it is not a new table.
2. **No learning in silence** (D-PZ-2). The assistant writes only on the member's words or yes, and offers Undo.
3. **Only the member reads the profile** (D-PZ-3). No admin read, and no block in a shared room.
4. **One profile per organization** (D-PZ-4), because tenancy is a row (D15).
5. **The form of address** is the profile, then People's preferred name, then the first word of the display name (D-PZ-5).
6. **The greeting is brief**, in the first reply of a chat only (D-PZ-6).
7. **The learning switch is on** by default (D-PZ-7).
8. **The assistant stays "Metorite"** (D-PZ-8).
9. **Custom tools wait** for their own spec (D-PZ-9).
10. **A consent notice comes before the app reads sent mail** (D-PZ-10). PZ-12 drafts the words, and the owner reads them before PZ-12 merges.
11. **WhatsApp drafts use the email voice**, through one member voice in `acb_llm.voice` (D-PZ-11).
12. **The member's own WhatsApp messages and chat turns feed the voice**, as derived features only (D-PZ-12).
13. **The setup coach asks at most once a day**, and waits 14 days after a dismiss (D-PZ-13).
14. **The coach asks on WhatsApp only when the owner turns it on**, through Reach (D-PZ-14).

**Also recommended and accepted:** the Assistant entry joins the Settings navigation in PZ-11, when the flags go on. Until then, the link row on Appearance is the way in. The directory lookup of PZ-5 needs People access (owner, 2026-10-23).

**Why.** `memory_architecture.md` §2: a fact whose absence makes the agent do the wrong thing goes in a bounded block that is always present. A wrong name in each reply is that kind of fact. A learned item asks first, because a guess about a person is a wrong act when it is wrong.

**Fences:** named per slice in `specs/personalization.md` §10.

---

### D111 — Personalization, the research decisions: a private chat, a second yes, an org switch, app scopes and review dates (2026-10-24)

Owner decision, 2026-10-24, in chat, by answer to two questions: "Accept all defaults" for D-PZ-15 to D-PZ-19, and "Yes/No buttons" for the second yes on WhatsApp. Board: **WS-63**. Owning spec: **`specs/personalization.md`** §13.4.

1. **A member may turn the profile off for one chat** (D-PZ-15, PZ-21). On WhatsApp, the word "private" does it for one message.
2. **A sensitive fact needs a second yes** (D-PZ-16): health, religion, caste, politics, sexuality or finance. Opinions are never saved.
3. **An admin may turn the profile off for the whole organization, and never read it** (D-PZ-17, PZ-24).
4. **A fact may apply to one app only.** All apps is the default (D-PZ-18, PZ-23).
5. **A fact gets a review date only when the member or the fact gives one.** Nothing expires by itself (D-PZ-19, PZ-22).
6. **On WhatsApp, the second yes is a pair of Yes and No buttons** (D-PZ-20, PZ-6b), as the confirm-before-send acts are.

**Why.** A web survey of eight assistants on 2026-10-24 (§13.1). Each major assistant has a way to answer without memory, an admin switch and a readable memory. A WWW '26 audit found 28% of saved memories held sensitive data, so a sensitive fact asks twice.

**Fences:** named per slice in `specs/personalization.md` §10 and §13.3.

### D112 — Knowledge curation: company files in, provider readers kept, facts with time, and a brief beside the body (2026-10-25)

Owner decision, 2026-10-25, in chat: take each decision as the spec recommends. It accepts D-KC-1 to D-KC-9 and the default of each question Q1 to Q7. Board: **WS-61**. Owning spec: **`specs/knowledge_curation.md`** §20 and §21.

1. **Four layers: source, document, fact, page** (D-KC-1). Facts are Postgres rows with provenance and four times. No graph database.
2. **An entity points at the record that owns it** (D-KC-2). Knowledge owns only products, written workflows and topics.
3. **A connected document keeps its provider readers** (D-KC-3). It sits in a space only when every reader of the space is a proven reader of the document. This amends D104's D-KB-3 and §16 Q4 for connected documents only.
4. **An entity page has a brief and a body** (D-KC-4). The brief is a per-reader view over facts, and a learned fact shows in it, marked, above a confidence of 0.5. The body keeps D-KB-4 in full. This amends D-KB-4 for the brief only.
5. **Filing is virtual** (D-KC-5). Nothing moves at the provider. D-KB-5 stands.
6. **Google Drive and OneDrive first, then Dropbox** (D-KC-6). This amends the default of `knowledge_base.md` §16 Q1.
7. **A fact has the audience of its episodes, and never merges across audiences** (D-KC-7).
8. **Knowledge is the org memory of every agent** (D-KC-8). Mem0 `org:global` stops being written for an org on `KC_FACTS`.
9. **Every paid pass has a flag, a per-org cap and a usage row** (D-KC-9).

**The answers.** Q1: file Google's `drive.readonly` verification now. Q2: member and org sources in v1, the Workspace domain install later. Q3: yes. Q4: shared rooms, meetings with two members, done tasks in org-visible projects, and documents. Private chats and email stay off until the member turns them on. Q5: no drive write-back. Q6: no cap means no model call. Q7: work facts about a member, which the member sees and can hide.

**Why.** Production on 2026-10-25 held no published Knowledge page in any org. The research record of `knowledge_curation.md` §4 (Onyx, Graphiti, mem0, Letta, STORM and the LLM-wiki pattern) gives the shape. The amendments of D-KB-3 and D-KB-4 are narrow on purpose.

**Fences:** `test_kc_sources_read_only_scopes.py` (KC-1), `test_kc_document_visibility.py` (KC-4), `test_kc_fact_audience.py` (KC-9) and the leak gate of KC-15.

### D113 — Personalization, the first release: rollout by org, and an interview that learns about the member (2026-10-25)

Owner decision, 2026-10-25, in chat: "Go ahead with your recommendation". Board: **WS-63**. Owning spec: **`specs/personalization.md`** §14.

1. **The profile turns on for named organizations first** (PZ-25). A new setting, `ASSISTANT_PROFILE_ORGS`, limits the master switch to a list. The first release is Hathi Labs only.
2. **Settings gains an "Assistant" entry.** It is a Settings entry, not a pane, so the twelve live panes stay twelve.
3. **The assistant interviews the member** (PZ-26, PZ-27). Ten areas: role, goals, a good day, decisions, detail, rhythm, channels, energy, peeves and people. The first sitting asks up to three questions, one at a time. After it, one question a day at most.
4. **The limits of D-PZ-13 apply.** A skip waits 14 days. "Don't ask again" stops an area. No question goes to a shared room, and none goes out on WhatsApp.
5. **Each answer is an explicit write with Undo** (D-PZ-2). The second yes of D-PZ-16 stays for a sensitive answer.

**Why.** The profile of §4 holds how to talk to the member, and nothing about what the member works toward or enjoys. The owner wants to use the feature now, on one organization, before PZ-10 and phase 2.

**Fences:** named per slice in `specs/personalization.md` §14.5.

### D114 — A reminder is its own private record, it stays open until the member closes it, and a tap opens the chat (2026-10-25)

Owner direction, 2026-10-25, in chat. "From the chat application, I want to tell the AI to remind me of things that are not necessarily tasks or events." Then: "If I did not confirm that I got the haircut, nudge me, and if not, reschedule the reminder." Then: "Build web app notifications for iOS and Android. When I open the reminder on mobile, it opens the chat, and I talk to the AI about what to do next." The owner chose each recommended option in two question rounds. Board: **WS-66**. Owning spec: **`specs/calendar_events_reminders.md`** §5.

1. **A reminder has its own store, `reminders`.** It is a private nudge to one member, at a point in time, with an optional link to one object (a task, an email thread, a CRM contact, a WhatsApp chat or a project). It is not a task. This replaces form 3 of that spec's §2 (a standalone reminder as a private task) and its `pm_task_personal.remind_at` column, before either was built. "Make it a task" promotes a reminder into `pm_tasks`, so D52's one task store stands. A lead time before a block or an event stays a column (CE-1, CE-6), not a reminder.
2. **A reminder does not close when it fires.** An `action` reminder stays open until the member confirms it, drops it, or promotes it, or until evidence closes it. If the member does not answer, Metorite checks back once and then reschedules it to a smart slot. It stops after two reschedule cycles or 72 hours, and parks the reminder as **Unconfirmed**. It never deletes it silently. A `notice` reminder closes when the member sees it.
3. **The member's own reminder is outside the Reach budget.** A reminder at the time that the member set has reach class `urgent`. It is outside D103.3's daily cap, up to 10 a day, and it may break quiet hours. A check-back or a reschedule that Metorite makes has class `interrupt`. It counts against the cap and never lands in quiet hours. This amends D103.3 and D-RE-3 for the two reminder kinds only.
4. **A reminder push shows the reminder's text.** The push payload is encrypted end to end (RFC 8291). It holds the title and the time, unless the member turns on "Hide details on the lock screen". This amends `chat_run_continuity.md` §S7 item 5 for reminder pushes only. Every other push keeps S7's rule of no content.
5. **A tap opens the one assistant (D99) with the reminder in context.** The first message is built by code, with no model call. Its chips are Done, Not yet, Later, Drop and Make it a task. A chip acts at once, with Undo (D-CF-5). A typed answer goes to the model with the reminder and its linked object in context.
6. **Web push is the platform for every notification, in the installed web app on iOS and Android and in desktop browsers.** It is S7's transport, and it is opt-in on each device. Reach delivers through it as RE-9. D103's one outbound seam stands: a reminder reaches the member only as a My Day card with a reach class.
7. **The first channels are the bell, the chat and web push.** WhatsApp delivery of reminders waits for a later ticket (RM-9).
8. **The owner approved both flags ON for every org in production.** The owner said, 2026-10-25: "Feel free to turn on the flags for all the accounts. I only have friendly customers at the moment." So an agent turns `WEB_PUSH_ENABLED` and `CALENDAR_REMINDERS` on for every org after each slice passes on dev.metorite.com. The agent reports the act, the box and the evidence (CLAUDE.md §3a rule 2). Web push stays opt-in on each device.

**Why.** The research record is in the spec's §5.0. No assistant on the market checks back and reschedules: ChatGPT, Gemini, Siri and Google fire once and forget. Single-purpose nag apps repeat on a fixed timer. Google folded reminders into Google Tasks and lost the time of day and the location triggers, so a reminder that is not work stays out of the task store.

**Fences:** `test_reminders_r8.py` (RM-1, two orgs and two members), `test_reminder_loop.py` (RM-2), `test_push_subscriptions.py` (PN-1, S7's named fence) and `src/lib/push.test.ts`.

### D115 — WhatsApp is deprioritized for now (2026-10-25)

Owner decision, 2026-10-25, in chat: "For now, we're deprioritizing the WhatsApp interface." The owner can revisit it later. Boards: **WS-20**, **WS-47** and the WhatsApp parts of **WS-48**.

1. **Do not start new WhatsApp work.** This covers the assistant channel (WS-47), the WhatsApp inbox (WS-20) and the WhatsApp narrowing run (WS-48 N4).
2. **Keep what is built.** Do not delete WhatsApp code, routes, tables or tests. Do not turn a WhatsApp flag off that is on now.
3. **Do not turn a WhatsApp flag on.** `WHATSAPP_ASSISTANT_WRITES` stays off, and H-391 waits.
4. **WhatsApp HANDOFF entries wait.** An agent skips them when it picks work. Examples are H-391, H-275, H-251 and H-192.
5. **A fix stays allowed.** When a shared change breaks a WhatsApp test, fix the test or the code. Do not leave the suite red.
6. **D99 and the channel rule stand.** A tool that the chat gains still works on every channel. Nobody builds a WhatsApp-only surface for it.

**Why.** The owner wants the email, Projects and Knowledge work finished first. WhatsApp needs Meta App Review and a company number, and each one waits on the owner.

**Revisit.** Only the owner reopens this. A new decision that reopens it names D115.

**Fence:** advisory. No test enforces a priority.


### D116 — One assistant keeps the member's words: fix the handoff now, add OCR, then measure tool packs before any rebuild (2026-10-25)

Owner decision, 2026-10-25, in chat: take the recommendations. Board: **WS-67**. Owning spec: **`specs/one_assistant_context.md`**.

1. **Fix the handoff now.** The orchestrator copies the member's words and corrections verbatim into each task (OA-1). A specialist marks each fact as quoted or inferred, and a file name is never content (OA-2).
2. **Add OCR** to `read_attachment` for scanned PDFs and images (OA-3). The owner chose a library, not an image model. A PDF with a text layer already reads.
3. **Store corrections in the chat** (OA-4).
4. **Measure before any rebuild** (OA-5). Run the email scenarios with the email tools in the one assistant, and delegated as now. Compare accuracy, latency and cost.
5. **The owner decides from the numbers** (OA-6). A yes amends D99's "specialists behind it" to "tool packs in it" for conversational work. A sub-agent stays for heavy jobs, with a structured handoff.

**Why.** On 2026-10-25 the assistant named a Navy order's parts from a sample file's name, and it kept the name after the owner said the files were references. The trace found four causes in the design: no OCR, prose replies with no provenance, member words that do not reach the specialist, and no stored correction (spec §1).

**What does not change.** D99 stands until OA-6: the member talks to one assistant. The held-chat guard stays in the email tools.

**Fences:** named per slice in `specs/one_assistant_context.md` §5.


### D117 — One chat for the whole app: tool packs in the one assistant, and dynamic sub-agents for parallel work (2026-10-26)

Owner decision, 2026-10-26, in chat. This is the OA-6 gate of D116. Board:
**WS-67**. Owning spec: **`specs/one_assistant_context.md`**. It amends
**D99** item 2 for conversational work.

**The question (OA-6):** do tool packs replace conversational delegation?

**The input.** The OA-5 report (`docs/one_assistant_measure_2026-10-25.md`,
PR #1348) ran 108 live email turns. The tool pack and delegation each passed
51 of 54 turns. The pack was 2.3 times faster to the first text (median
9.1 s against 21.6 s) and cost half as much. It was faster in each of the
13 chats, because it made fewer model calls.

**The answer, verbatim.** The owner chose "Yes, for all apps", then wrote
two more messages. The block keeps the owner's words as written.

```text
across the whole metorite application, might be better just to have one
chat instead of deligation, and load tools in a smart way dynamically
depending on the execution, possibly using tire decide, if you think that
will make things fatster.

but we should still have the ability to call subagents when needed, say
when we want to do work in parallel, like web search, multiple crud
operations, etc. dont loose the ability to spin up a dynamic sub agent,
even though we wanto to do majority of the work in a main agent
```

1. **One chat for every app.** The one assistant does the conversational
   work of every app itself. No conversational turn goes to a specialist
   agent.
2. **Tool packs, loaded per turn.** Each app's tools and a short rules block
   form a pack, in the WS-23 registry. A pack loads when a turn needs it.
3. **The loader is fast first.** It chooses packs with no model call where
   it can: from the app surface, the packs that the chat already loaded, and
   the working set. A tool lets the model load a missed pack during the
   turn. `tier-decide` helps only when the first choice is ambiguous, and
   only inside a model call that already runs. The reason: the turn-kind
   question costs 1.3 to 3.0 s in production, and a pack turn costs about
   10 s.
4. **The pack set stays steady in a chat.** About 90 percent of the input
   comes from the prompt cache. A tool list that changes on each turn loses
   that cache.
5. **Dynamic sub-agents stay.** The main assistant can start sub-agents for
   parallel work, such as web searches, batch writes over many records, or
   many reads. It builds each one for one job, with the contract of the
   spec §6. Their writes ask the member once, on one batch card.
6. **The guards move with the tools.** The held-chat guard, the cards and
   the egress floor hold in the one assistant as they did in the specialist.
7. **A specialist agent stays** for a scheduled job, a workflow and an
   external agent.
8. **Measure before retirement.** OA-10 retires conversational delegation
   only after a measure of turns for other apps with the packs loaded, and
   of a fan-out to sub-agents.

**Why.** OA-5 found the same accuracy at half the time and half the cost.
The time went into the extra model calls of each hop between agents.

**What does not change.** D99 items 1, 3 and 4 stand: the member talks to
one assistant and keeps sessions. D25.4 stands: no specialist takes over
the chat.

**Revisit.** Only the owner reopens this. A new decision that reopens it
names D117.

**OA-7g, shipped live (owner, 2026-10-26, in chat).** In OA-5, both shapes
made a draft after the member only corrected a fact. The owner chose to ship
the rule against that now, ahead of OA-7, and not behind `CHAT_CONSTRAINTS`.
The rule: "A correction is not a request." It is rule 12 of the
orchestrator persona, a line of `handoff._RULES`, and a bullet of the email
assistant's instructions. PR #1356. Fence: `tests/unit/test_no_unasked_draft.py`.

**Fences:** named per slice in `specs/one_assistant_context.md` §5.
---

### D118 — Metorite holds the state of the work. The repository holds the specs, the decisions and the rules (2026-10-27)

Owner decision, 2026-10-27, in chat. The owner chose "Metorite owns state, repo owns specs" for the plan reset.

1. **Metorite holds** the status, the priority, the owner and the order of every work item. The HathiLabs project **Metorite** is the one place where people and agents coordinate.
2. **The repository holds** the specs, these decisions, the rules R1 to R8 and the owner gates in §6.
3. **`work_plan.md` §2** keeps one short row per workstream. It names the Metorite project and the owning spec. It carries no slice history.
4. **`HANDOFF.md` retires** into Metorite tasks. The session hook keeps a short file until the sync is complete.
5. **Fence.** A test that fails when an ACTIVE spec slice has no `Metorite: #N` pointer. Until that test exists, this clause is advisory.

### D119 — The core function brief sets the roadmap: pilots in December 2026, paid customers by 31 March 2027 (2026-10-27)

Owner decision, 2026-10-27, in chat: "go ahead with your recommendations for the decisions owed." The input is the brief "Metorite Core Function Tasks" (2026-10-27).

1. **Phases.** M0 is customer zero (Fracktal and Shaurrya Tele), about three weeks. M1 is pilot-ready, December 2026. M2 is the first paid customer, 31 March 2027. M3 and M4 come after.
2. **Priority.** Brief tasks 1 to 7 are P0. Brief tasks 8 to 15 are P1. Customer zero comes first inside Phase 1.
3. **Moved after Phase 2.** Studio, Writing Help, the UX rules, MCP data parity, Notes, Workflows and the Dashboard. Fixes that pilots see stay early: CRM deal stages, My Profile and the Cc and Bcc roll-up. CRM deal stages (UX-3) move into M1, because DEWiN uses them daily.
4. **WS-37 compliance spec** is written in Phase 1. Brief task 12 needs it, and DPDP has a November 2026 date.
5. **Ten pilots are ten organizations.** Tenant isolation (M1) lands before the first pilot outside customer zero.
6. **Dates.** Calendar two-way sync cannot meet December: it ships read-only first. The Gmail review and the external pen test are filed in December and finish after March.

### D120 — H3.2: the identity cutover comes before RLS phase 4. A carve-out is a bridge only (2026-10-27)

Owner decision, 2026-10-27, taking the recommendation of `saas_multitenancy_handover.md` §H3.2.

1. **Option B.** H6 moves sign-in onto `user_identity` and `org_membership`, which are exempt, before a clean phase 4.
2. **Option A as a bridge only.** If phase 4 must ship before H6, the `app_user` carve-out ships with it. The cross-tenant read of `app_user` it opens is then a tracked P0 item that H6 closes.
3. **H3.3.** The live-catalog gate subtracts `HOMONYM_BLOCKED` as the spec proposes, with the CRM homonym tracked on its own.

### D121 — WS-40: who may rename, move and delete (2026-10-27)

**Agent-proposed, owner may overrule.** The owner accepted the recommendations in chat, and the four answers below are the recommendation for `org_access_control.md` §8d.4.

1. **Grain.** One permission per content app, `<app>:manage`, in the `workflows:publish` shape. It covers rename, move, delete and settings. Editing content stays with visibility and grants.
2. **Where authority sits.** On the node. A project grant gains a level: view, edit or manage. An org `admin` holds manage everywhere.
3. **Delete.** The creator may delete what they made. Anybody else needs manage on the node.
4. **Day one.** It ships dark behind a flag. Each existing member gets manage on the nodes they created, so nothing turns read-only. One week in log-only mode, then enforce. The rollout copies §7.

### D122 — Integrations for the brief: ERP order, WhatsApp, Gmail in test mode (2026-10-27)

Owner decision, 2026-10-27, in chat, taking the recommendations.

1. **ERP connectors (brief question 1).** Ask the ten pilots first. Without their data the order is Zoho Books, then Tally Prime, then QuickBooks Online.
2. **WhatsApp (brief question 2).** Use the official API for the business number, plus a forward-to-Metorite path for chosen messages. No linked-device reader, because of the terms-of-service and ban risk. This narrows brief task 8: the official API cannot read personal chats or most groups.
3. **D115 is lifted for brief task 8 only.** The rest of WhatsApp stays frozen.
4. **Gmail for the pilots** runs in Google test mode with named test users until the restricted-scope review passes. Check the current test-user limit and refresh-token expiry first, because a short expiry makes each pilot sign in again.

### D123 — Money before the first paid customer: rate card, Razorpay, seats, database size (2026-10-27)

Owner decision, 2026-10-27, in chat, taking the recommendations.

1. **Rate card.** Price it from measured burn before the first paid customer. Flip `CUSTOMER_CONSOLE_SPEND_GATE` after the price is set, never before.
2. **Razorpay.** The owner creates the TEST account now (H-14). The owner takes the GST position before SC-5 builds the invoice.
3. **Seats.** Pricing stays flat per user. Ten users per organization is a soft cap that matches `MAX_TEAM_SIZE`.
4. **Production database.** Move off the free tier before the pilots start. For the tenant plane, use the Supabase Pro plan at Small compute or larger (H-262). The Console database must not auto-pause (H-108). The owner approves the spend.

### D124 — External agents get full parity with the in-built assistant. Writes and deletes ask the member each time (2026-10-27)

Owner decision, 2026-10-27, in chat: "full parity, but the external agent can take permission for writes/deletes". It amends **D91.4** and the version-1 non-goals of `external_agent_access.md` §3.

1. **Reads.** One scope per source (CRM, email, WhatsApp, knowledge, meetings), each with its own line on the consent page. The member's visibility decides every row.
2. **Writes and deletes.** The agent asks. The member approves in the app, on a card that shows the exact payload. A delete, a send to a third party and a bulk change ask for each act.
3. **Safety.** Every returned body is marked untrusted text. Every call is audited. An admin can switch any scope off for the organization.
4. **When.** After Phase 2. The `use_metorite` prompt (MCP-Ga to MCP-Gd) ships earlier.

### D125 — The one assistant is named Mira (2026-10-27)

Owner decision, 2026-10-27, in chat. The one assistant of D99 is called **Mira** in the app, in the MCP prompt and in the docs. No vendor model name is shown to a member.

### D126 — An issue report may carry the member's chat, with consent per report. It amends D64.5 for that report only (2026-10-27)

Owner decision, 2026-10-27, in chat, taking the recommendation. Workstream: Issue Reporting, Metorite #475.

1. A member may attach a chat session to an issue report. The report card shows the exact payload, and the member consents on that card.
2. Secrets and tokens are scrubbed before the report leaves the organization.
3. Metorite staff may read that transcript, and only that one. Every read is audited.
4. A report is kept 90 days and deleted on request.

### D127 — Studio is one app with three object types. Sandbox code comes after the off-box sandbox (2026-10-27)

Owner decisions, 2026-10-27, in chat: "One app, three object types" for AI Studio, and the recommendations for O15.

1. **Studio** is one pane. A member builds tool packs, workflows and AI apps from one library. It replaces App Workshop, Agent Workshop and Workflows. It is scheduled after Phase 2.
2. **Sandbox code and reusable member tools (SBX)** belong to the one assistant. They ship in Phase 2, after BH-3 moves code execution off the box, and never before. A tool is private by default. Sharing it with the organization needs an admin check.

### D128 — One knowledge graph of pointers, in Postgres, behind the one retrieval seam (2026-10-27)

Owner directive, 2026-10-27, in chat: a knowledge graph like Graphify, so that Mira and the external agents find and read knowledge fast, across every app. The design is agent-proposed. Workstream: **WS-KG**. It extends D105 and D106 and changes neither.

1. **A graph of pointers.** `kb_entities` holds the entities, and `kb_links` holds the edges between any two records. An edge holds two record ids, their kinds, a relation and its provenance. It never holds a copy of a record's content, so D105.2 holds.
2. **One seam.** `search_company` and `gateway/retrieval/` stay the only retrieval path for Mira, the command bar and the MCP. The graph adds one step: after the ranked hits, it adds their neighbours within a token budget.
3. **Visibility on every read.** The seam checks each record that the graph returns again, through its owning app, with the member's `UserContext`. A link never shows a record that the member cannot open.
4. **Structure first, inference last.** The first edges come from foreign keys and exact identifiers, and they cost no inference. An exact identifier is an email address, a domain, a phone number or a task number. A model extracts edges only from Knowledge pages, at write time, on the cheap tier. Retrieval itself uses no model (D105.3).
5. **Provenance on every edge:** EXTRACTED, INFERRED or AMBIGUOUS, with a confidence. An AMBIGUOUS edge is never shown as a fact. An admin can remove a wrong edge.
6. **Postgres only.** The graph runs in the tenant database with FORCE ROW LEVEL SECURITY. No Neo4j. Graphiti and the empty `acb_graph` tables retire after KG-6. **The owner confirms that retirement**, because WS-7 gates Graphiti.
7. **Memory is tenant-scoped first.** No memory flag (`MEM0_ENABLED`, `GRAPHITI_ENABLED`) turns on while its store has no organization scope (KG-9). Today the Mem0 org key is the one value `org:global` for every tenant (`acb_memory/compartments.py:36`).

**The owner may overrule it.** It is an engineering shape under the owner's directive, as D105 was.

### D129 — An org that leaves Zoho keeps its Zoho labels, and an admin can edit them later. It closes CRM-Z10f and amends D95.2 for cutover orgs (2026-10-27)

Owner decision, 2026-10-27, in chat: "we keep the ZOHO labels, but we should be able to edit them later on. Possibly have the AI suggest improvements to the way in which the CRM is set up." Workstream: **WS-CRM**. Owning spec: `specs/crm_platform.md` §3.4.

1. **The cutover keeps the labels.** CRM-Z10b copies the labels of `GET /crm/mode` into the Native org, for example Contacts as "Existing Sites". The members see the same words before and after.
2. **An admin can edit each label later.** In Native mode the labels are Metorite values. An edit changes the label only, and never a key, a table or a route.
3. **The AI may suggest a better setup.** It proposes, and an admin approves each change, as the mapping suggestions do (CRM-Q20-2). It never applies a change by itself. This is a later slice, not part of CRM-Z10.
4. **Timing.** Fracktal and Shaurrya Tele do the cutover later (CRM-Z10-run). The owner puts the CRM bug fixes and the setup UX first.

### D130 — The Deals board shows the columns in the order of the settings list. Mirror mode defaults to the order of the Zoho pipeline. It amends the 2026-10-12 order rule and CRM-U6 item 1 (2026-10-27)

Owner report, 2026-10-27, in chat: the deal stages were "very complicated" to configure, and the Kanban columns do not show "like it is currently showing up in ZOHO". The owner asked for the Zoho order, or an easy setting. Workstream: **WS-CRM**. Owning spec: `specs/crm_platform.md` §20.15 (CRM-U11).

Measured on production, 2026-10-27, for Fracktal:

- The sync stores the order of the Zoho pipeline correctly. That order starts with Re-Evaluate, then Confirmation and Advance, then New Deal.
- The board never reads that order. With one pipeline, the page sends no `pipeline_id`, so no lane has a pipeline place. With two or more, the place only breaks a tie.
- A saved Metorite order sat on all 17 stages. It was a copy of the global Zoho picklist, not of the pipeline. So the board put Confirmation and Advance first.
- The board also regrouped the columns by type: open, then on hold, then won, then lost. So a drag in settings often moved no column.

The rule:

1. **The settings list is the board, left to right.** No type group moves a column. What the admin sees in the list is what the board shows.
2. **Mirror mode, with no saved order: the order of the Zoho pipeline.** That is the chosen pipeline, or the default pipeline when the board shows every deal. The stages that the pipeline does not list come after it, in the order of the Zoho picklist.
3. **A saved order wins until the admin picks "Same as Zoho".** That choice clears it.
4. **Native mode: the admin's order.** A new stage lands before the first won or lost stage.
5. **The win chance never moves a column.** CRM-U6 item 1 stands on this point.

## 4. Single-owner registry (who owns duplicated work)


| Work | Owner | Mirrors (link-only after §5) |
|---|---|---|
| **The user-management contract every app must follow** (identity chain, member lifecycle, permission vocabulary, the ten build rules) | **`specs/user_management_contract.md`** — created 2026-08-05, and it deliberately **owns RULES, not FACTS**: every fact is cited to the spec that owns it, so it can never become a fifth competing description of the access model | `org_access_control.md` (the model) · `colleague_onboarding.md` (the gate + the runbook + the matrix) · `tenancy_and_visibility.md` (D11/D12) · `department_centers.md` (Centers as projections). Surfaced to builders from root `AGENTS.md` constraint 10, `apps/services/gateway/AGENTS.md` and `workbench/AGENTS.md` |
| **Colleague onboarding readiness** (the pre-invite gate, the invite runbook, the role × app capability matrix) | **WS-24 — `specs/colleague_onboarding.md`** | `org_access_control.md` §7 (bootstrap) and its role table, lines 79-83 — **line 81's `manager` row was corrected 2026-08-04 per D14** (intent no longer claims org-wide data; the grants cell is now the literal seeded array, because *"all `feature:*` except `build.*`"* was wrong in five slugs) · `department_centers.md` §2 (the five-place Center registration checklist the runbook's step 3 depends on) · `tenancy_and_visibility.md` §5 (the app-by-app gap table — that doc owns the *doctrine*, this one owns the *measured current state per role*) |
| **The single member of record** | **`vjvarada@fracktal.in` is the only signed-in member** (⚠️ **owner-reported 2026-08-04, NOT measured** — it is a live-DB fact and §6 forbids an agent the tool that could measure it; re-check on the box before relying on it) | There is exactly one. `EXECUTIVE_EMAILS` is the bootstrap candidate list (`acb_auth/access.py:467-519`) and is **not** a role — a member's real access is resolved from `app_user` + `user_role` + `user_permission_override`. **No agent may add, promote or suspend a member**: `POST /admin/members`, `PUT /admin/members/{email}/roles` and `PATCH/DELETE /admin/members/{email}` are live-DB writes to the access model and are registered in §6. Onboarding runbook: `specs/colleague_onboarding.md` §2 |
| Groups admin UI + seeding | **WS-13 / Centers B** | groups_sessions_authority §6.5 · org_access §8 Ph2 · multiplayer §4.5 |
| Team-instanced agents | **WS-14 / Centers C3** (mechanism per D3) | `docs/multiplayer/agent-kinds.md` §6/§8 — **note the path: it is under `docs/multiplayer/`, not `project-docs/specs/`**, and its §6 roster is a **design proposal, not a work list** (seven of its twelve agents do not exist; it contradicts three shipped `config.json` files — both annotated there 2026-08-03) · agent_architecture §6/§12A · memory_architecture §6.1 · groups §6.2 |
| Shared mailboxes | ⚠️ **OWNERLESS IN FACT — do not dispatch** (measured 2026-08-03) | D5 assigns implementation to `email_app_master_plan.md`, sequenced by WS-14. That spec contains **zero** occurrences of "shared mailbox", and `email_account_member` — cited as Phase-2 content by `department_centers.md` and `org_access_control.md:311` — **exists nowhere in the repo** (0 hits in `*.sql` and `*.py`). D5's *sequencing* stands; its *ownership* is nominal. **Next action is a doc action, not a build:** either `email_app_master_plan.md` gains a section for it, or this row names a different owner. Recorded in `department_centers.md` C2. | org_access §8 Ph2 · groups §1 · research §16.7 |
| Per-Center approvals routing | **WS-14 C4 — 🔴 OWNER-DECISION, not dispatchable** | `org_access_control.md:405` Q2 is open verbatim (*"who is asked? … per-module approvers is a Phase 2 question"*), **and there is no column to route on**: `infra/postgres/66_pending_actions.sql:13-38` carries no requesting-member, group or Center column. ⚠️ **Corrected 2026-08-03** — this cell used to add "(`actor` is the proposing *agent*)", which is **false**: two of `actor`'s six writers put the requesting human in the string (`routes/apps/tools.py:393`, `routes/apps/actions.py:345`, both `actor=f"app:{slug}:{email}"`), so a group **is** derivable there. The verdict holds on the real evidence — `actor` is free text with five shapes (`app:<slug>:<email>` · `app:<slug>` · `workflow:<name>` · `tasks:<provider>` · `tasks:clickup:ws:<id>`) and a human in only **two of six** proposers, so a Center inbox filtered on it would be silently empty for every workflow-, publish- and provider-originated proposal. The new column must be written by every proposer, not parsed from an ad-hoc string. Evidence: `department_centers.md` C4. The ticket is "answer Q2, then add a column", never "add a filter" |
| Cost attribution | **WS-6** (D1) | multiplayer §5.3/Ph4 · agent-kinds §9 Q4 · Centers D |
| Budgets | **WS-16** (D2) | multiplayer §4.3/§5.3/Ph4 |
| Digest workflows | **WS-15** (also scores workflows G1) | workflows_app §1.2 |
| Orchestrator org-memory fix | **WS-15** (D4); structural fix WS-8 A1 | agent_architecture §11.1.2 |
| Workflow engine/editor | **workflows_app.md** (D6) | multi_agent_orchestration Ph2–3/§5.3 · Ph5.1 (Magentic/GroupChat as graph node types — reassigned to WS-11, 2026-08-03) |
| Isolation ladder (BO-7 / HH-6 / T0–T2) | **`permissions_sandbox_b6.md`** (the Phase-5 build order `P5-a…d`; WS-3) | `agent_platform_hardening_2026-07.md` §1.2 — the ladder *definition* only, and the single T0/T1/T2 table of record · `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-7 · `competitive_hardening_2026-07.md:119-141` (build log for the 2026-07-27 passes) |
| **The coding engine, the sandbox terminal, and the removal of the Copilot SDK** (`code_task`'s engine, the sandbox broker, the coding image, the egress proxy, and every other Copilot use until the SDK leaves the lock) | **WS-43 — `specs/maf_coding_engine.md`** (D82, D83, minted 2026-10-03, and D84 §15 the same day) | `agent_architecture.md` §11 (amendment boxes only) · `permissions_sandbox_b6.md` §P5-a.2 and §P5-c (pointer notes only) · `chat_agent_framework_review_2026-07.md` (a pointer only) · root `AGENTS.md` (dated D84 notes only) |
| **Child-process env seam, systemd unit hardening, off-box backup credential** (the env of every child process of the gateway, the systemd drop-ins of the units, the root-owned copies of the root units, the compose port binds, the write-only off-box credential and its retention) | **WS-49 — `specs/box_hardening.md`** (minted 2026-10-08) | `maf_coding_engine.md` WS43-G1 (a pointer to BH-3 only) · `backup_and_restore.md` §4.2 (a pointer to BH-4 only) |
| **Native MAF session persistence** — a MAF `AgentSession` kept per organization, thread and agent, so a turn sees the tool calls and results of the turns before it | **WS-43 — `specs/maf_coding_engine.md` §15.9** (D84, slices WS-43t1 and WS-43t2) | `agent_architecture.md` §11.3.1 and HANDOFF H-215 point here and add nothing |
| Context discipline / prompt budget | **WS-23** — `skills_registry.md` + `skills_scope_out.md` | multi_agent_orchestration Ph1 (struck 2026-08-03: 1.1 shipped, 1.2 moot, 1.3 delivered here) |
| Collaborative multi-agent chat (Shape C) | **WS-10** — shipped as multiplayer rooms (`docs/multiplayer/README.md`); the floor-control residue is OWNER-GATE | multi_agent_orchestration Ph5.2/§5.6 (struck 2026-08-03) |
| Calendar / Focus OS | **WS-21** — `calendar_focus_os.md` §5 canonical for `gtd_time_blocks`, §9 canonical for all F2/F3 acceptance; `calendar_timeboxing.md` §13 canonical for P4 external sync | The family has **four** docs, not two: `calendar_ai_review.md` and `calendar_ux_review.md` are **unregistered sub-docs**. `calendar_ai_review.md` is cited by three shipped migration headers (92 / 97 / 100) but by no board row and no spec index entry — *(focus_os §9.13 lists the third as 98; the file that cites it is 100)*. `calendar_ux_review.md` is the **sole** home of the block-reminders/notifications item (focus_os §9.13). **Horizons / Top-5 outcomes is DISPUTED between WS-21 (§4.7) and WS-18 — assigned here, to WS-21**; it is still DO-NOT-DISPATCH until it has acceptance (`calendar_focus_os.md` §9.10). |
| Chat HITL model | **generative_ui_2.md §2** (shipped) | chat_ux §12.3 (superseded) |
| Multiplayer prior art (`qm`, 2026-08-01) | **`multiplayer_prior_art_qm_2026-08.md` is reference-only** — it owns no work and no status; the specs it links stay authoritative | multiplayer README §4.6/§5.1/§6.4/§6.5 · memory-clearance §3.3/§7 · agent-kinds §9 Q1 · skills_scope_out §6 · WS-10 · WS-23 |
| Memory compartments + clearance (incl. `subject:`) | **`docs/multiplayer/memory-clearance.md` §7** (surface design §7.1); dispatched as **WS-10 S1** | memory_architecture §9 `3a′` (link-only since 2026-08-02) · multiplayer README §6.3/§8 Phase 3 (index only) · prior-art §QM-D1 (reference only) |
| Native CRM + the Zoho retirement path | **WS-26 — `specs/crm_app.md`** (minted 2026-08-05), the as-built record · **WS-53 — `specs/crm_platform.md`** (minted 2026-10-11) owns new CRM work: the tenant port, the two modes and the Zoho mirror | `department_centers.md` Sales Center "Pipeline" app (a projection of `/crm`, flipped live by WS-26c) · WS-1 interplay **settled 2026-08-05 (D-CRM-7/D-CRM-8) — the writer now EXISTS** (branch `ws-26b-zoho-sync`): `ingestion/sources/zoho/writer.py`, the sync engine's **single, broker-gated** writer with one grep-asserted caller (`routes/crm/sync_zoho.py::execute_push`) and three registered `crm.zoho_*` handlers that auto-apply while `ACTION_BROKER_ENFORCE` is off, retired at WS-26e. WS-1's "no Zoho write path anywhere in the repo" sentence was corrected in that same change (done-when 6) — this row and the WS-1 row now agree, and neither should be re-softened · WS-2 (the Zoho-token P0's endgame is WS-26e's **revoke**) · WS-20 §11's "Odoo/Zoho-bound items" (bind to `crm` `entity_ref` per WS-26d instead — ⚠️ as of 2026-08-06 WS-26d has made `"crm"` a KNOWN system so such a ref **parses**, but there is still no linker: nothing writes `wa_contacts.entity_ref` for any system, and the drawer's `crm` block is still `None`. Whoever binds these items owes both halves) · `orchestrator/sales_views.py` + `scripts/reconciler.py` + `skills/sales\|reconciler/*` keep reading the graph mirror until WS-26e repoints them |
| Native project management + the ClickUp retirement path | **WS-27 — `specs/project_management_app.md`** (minted 2026-08-05) | `task_manager_app.md` (the personal GTD lens — untouched as an app; its ClickUp provider **arm** retires at WS-27g while the provider *interface* stays, becoming the seam WS-27e's internal `metorite` provider uses) · `department_centers.md` C1/WS-13 (the tasks team slice and the People Center sub-app list; C1's `gtd_project_grant` = D13 stays C1's own — `pm_project_grants` is a sibling on the same subject vocabulary, never a replacement) · `task_manager_hr_planning_and_memory.md` (people/capability layer — WS-27 reads it, never rebuilds it) · `workflows_app.md` owns the automation engine WS-27f feeds (D6; the Paca-grade uplifts are recorded there as backlog, not here — **written up in full 2026-08-06 as `workflows_app.md` §13, items U1–U8**, where **U1** = the `pm.update_task` node and **U7** = agent dispatch, i.e. WS-27f's two halves, and U2–U6/U8 are engine work WS-27 does not wait on; §13 is backlog and changes neither Slice 3 nor Slice 4) · `paca_pm_research_2026-08.md` (reference-only, owns no work) · WS-1's BO-1a/BO-1b are **named prerequisites** of WS-27c, not discoveries |
| The People Center's surfaces (directory, org chart, capability search, seats) | **WS-28 — `specs/people_center_app.md`** (minted 2026-08-06) | It owns **surfaces, not facts**: `task_manager_hr_planning_and_memory.md` owns the HR data and the capability vectors · `org_access_control.md` owns identity, roles and overrides · `colleague_onboarding.md` owns the invite process and the role × app matrix · `department_centers.md` owns Centers and groups · `project_management_app.md` owns the work. WS-13's *People directory read view* is closed by WS-28b rather than staying open in Centers B |
| **AI serving — how a tenant call reaches a configured model** | **WS-31 — `specs/customer_console.md` CP-11** (D57, 2026-08-26). Detail in that spec's **§6B** | ⚠️ Until CP-11 lands the tenant serves its own calls (`routes/v1_compat.py`, litellm direct) and the Console Router has **no caller** — the two facts that make operator configuration inert. Link-only: `routes/v1_compat.py` · `customer_console/main.py:4538` (the Router endpoint — this cell read `main.py:2711` until 2026-08-30, and the route had moved) · `customer_console/auth.py::KeyCaller` |
| **AI model & provider administration** — which models exist, which model each tier resolves to, what a model costs in credits, our provider accounts and their API keys, and the AI subscriptions Metorite itself holds | **WS-31 — `specs/customer_console.md` CP-10** (D56, 2026-08-26); the artefact-by-artefact refactor inventory is that spec's **§6A** | ⚠️ Until CP-10 lands, the *implementation* lives in the tenant plane and the *decision* lives here — which is the split D56 exists to close. Link-only, after CP-10: `workbench/control_plane/src/app/settings/models/` (moving out) · gateway `routes/settings.py`'s 15 `/llm/*` routes (deleting) · `acb_llm/model_config.py` (retiring) · `infra/litellm/config.yaml` (already only a seed — migration 35's header says the DB is sole source of truth) · `acb_llm/key_store.py` (**stays** — it keeps integrations and BYOK; it stops being the platform key path) |
| **Platform staff identity and access** — who may sign in to the Operator Console, what each role may do, how an operator is added and removed, and what `control_audit` says afterwards | **WS-31 — `specs/operator_identity_and_access.md`** (D64, 2026-08-26), ticket series **CP-12** | ⚠️ **Staff, never customers.** `customer_console.md` CP-8 keeps owning the customer-management **surface**; this spec owns the **gate in front of it** and adds no route to it. The customer's own admin is `launch_surface.md` §6.2 + `subscription_console.md` SC-2 — two audiences, two surfaces, and merging them would widen a cross-customer console to identities we do not control (D33.1's inverse, per D35.3). Link-only: `workbench/operator_console/src/lib/staff.ts` (the interim shared passphrase this retires) · `customer_console/auth.py` (the four schemes it becomes a fifth of, and the reason it earns one) · `customer_console/keys.py` (the bearer-secret seam the session token reuses as a fourth **value**) · `infra/customer_console/001_customer_console.sql:341` (`control_audit`, whose `actor` stops being the literal `operator`) |
| **Tenancy boundary** (which company) | **`specs/saas_multitenancy.md`** (**D15** §1 · the three planes §0.9 · tickets §11) + its child **`specs/saas_multitenancy_implementation.md`** (SQL, seams, ratchets, runbooks — shapes only, no decisions) | ⚠️ **`tenancy_and_visibility.md` §1 + §6 are SUPERSEDED** (D11 re-taken 2026-08-08). Cite D15 for tenancy, never D11 |
| **The provider-handler seam** — what `model_capability.invocation` names, and which object serves a (model, task) pair | **WS-31 — `specs/customer_console.md` §6A.10b** (H-47, minted 2026-08-30) | The Console owns its own handler table (`customer_console/handlers.py`), and `packages/acb_stt` stays the TENANT package — the plane boundary forbids the shared import, and §6A.10b clause 1 records the rejected `acb_provider` alternative. Mirror: `note_taker_app.md` **D3** (AssemblyAI as the STT default, and the native-provider argument behind it). ⚠️ `resolve_invocation` (`customer_console/router.py:488`) is a DATA READ and is allowed. A second handler-OBJECT seam is the defect. 📌 **The first caller is `native_typesafe`** (D75, CP-13a, 2026-09-23) |
| **The `decide` task** — the door, the tenant facade `acb_llm.decide`, the chat tool, and each app's adoption mode | **WS-31 — `specs/customer_console.md` §6A.14** (CP-13, D75, minted 2026-09-23) | ONE tenant facade. A second decide client in an app is a defect. Each app's `off`, `shadow` or `on` mode lives in `gateway/decide_features.py` until H-44's feature-to-tier registry absorbs it. Mirrors: `email_app_master_plan.md` §2.1 (triage adoption), `agent_architecture.md` §8 (the chat tool), `ai_metering_and_analytics.md` §3.3 (`tier-decide` is hidden) |
| **The tier vocabulary against the tier schema** — who owns `tier_catalog` | **Split, and the split is the point.** `customer_console.md` **§6A** owns `tier_catalog`'s schema and the serving path. `ai_metering_and_analytics.md` owns the tier vocabulary, the customer-facing labels (D-AI-1) and the §8 slice contracts | Recorded 2026-08-30 after slice 3's re-audit found the table already shipped in `015_tier_pricing.sql` under D67. One table, two owners, two questions: `customer_console.md` answers "what columns and who writes them", `ai_metering_and_analytics.md` answers "which tiers exist and what a customer is told they are" |
| **The inbound MCP edge** — an outside agent that acts as a member: the OAuth server, the `/mcp` endpoint, the signed request to the gateway, the agent scope on `Visibility`, and the `MCP_ROUTES` allowlist | **WS-55 — `specs/external_agent_access.md`** (D97, minted 2026-10-10) | `external_agent_dispatch.md` (WS-56, the outbound twin, link-only) · `projects_agent_parity.md` D91 (it keeps the native tools of the in-product assistant. Its §5.3 is the parked item, and WS-55 answers it) · `tenancy_and_visibility.md` §3.2 (a pointer to the agent scope only) |
| **The artifact registry** — what an artifact is, its row, its scope and shares, its retention, and the library | **WS-60 — `specs/artifact_management.md`** (minted 2026-10-15) | `agent_file_and_memory_framework.md` and `agent_persistence_implementation.md` (the bytes, the three folders, the blob store: unchanged) · `projects_ai_chat.md` §21.16 (the run's artifact context, H-201/H-227) · `task_references.md` (the `artifact` kind of `pm_task_refs`) · `navigation_shell.md` NS-6 slice 6c (the chat door of the Artifacts app) · `agents-workspaces-artifacts.md` (REFERENCE, stale) |
| Visibility model (who inside that company) | **`specs/tenancy_and_visibility.md`** (D12 §3–§4 · the app-by-app gap table §5 · TV-1 §2 — **unchanged and still binding**) | `department_centers.md` (the "separate deployment is for a separate org, never a department" rule) · `org_access_control.md` §8 Ph2 · `multi_user_organization_research.md` §5/§7/§8/§9/§17 (**research only, and superseded for planning by the new spec**) · `groups_sessions_authority.md` §3 (the intersection rule it constrains) · D9 (the twelve "second tenant deployment" sites) |
| **The shell** — the bar, the sidebar shape, Home and its altitudes, the command bar, the dock, the bell, the avatar menu, role presets and the app manifest | **WS-44 — `specs/navigation_shell.md`** (D87, D88 and D89, minted 2026-10-05) | `launch_surface.md` keeps WHICH panes are live (§2) and the one nav filter (§8.4) · `DESIGN_SYSTEM.md` §6a and `workbench/control_plane/AGENTS.md` rule 10 (pointers only) · `department_centers.md` (D49 banner, a pointer only) · each app's own spec keeps what happens inside the app |
| **The My Day feed** — what goes into My Day: the signals, the cards, their order and life, the `decide` questions that judge them, and their cost | **WS-58 — `specs/my_day_feed.md`** (D100 and D101, 2026-10-13) | `specs/navigation_shell.md` keeps the page frame, the bell, the needs contract of §7.2 and the flags (pointers in its §4.3 and §7.2) |
| **The org-facts seam** — `gateway/org_facts.py`, its provider registry, `GET /org/facts` and the agent tool `org_facts`: the one read of the facts that an org answered about its own data | **WS-53 — `specs/crm_platform.md` §18.14.4** (CRM-Q32, 2026-10-17. CRM-Q32-5 built it, merged as #1130 on 2026-10-18), until a cross-app owner exists | Each app adds a provider and links here. It adds no second fact store. Mem0 `org:global` is not a store of org facts until D17 ships |
| **Company retrieval** — the one registry of sources that the command bar, the assistant's `search_company`, `narrow_and_read` and the My Day context join all read (D105) | **WS-61 — `specs/knowledge_base.md` §20** | `navigation_shell.md` §6.3 (the bar is a projection of it) · `data_narrowing_pipeline.md` (the `company` adapter, KR-4) · `my_day_feed.md` (the context join, KR-5) |
| **Context pipeline** — the stamp on each hit, the recency rule, the digest of each record, the connector contract and its freshness card, and the retrieval eval (D106) | **WS-61 — `specs/company_context.md`** | `knowledge_base.md` §20 (the read seam it feeds) · each app's own spec for its digest table |
| **The public legal pages** — `site/privacy.html`, `site/terms.html`, `site/refunds.html` and `site/contact.html`. A merge publishes them, because Caddy serves `/opt/acb/app/site` | **WS-33 — `specs/marketing_site.md` §5** (recorded 2026-10-12, PR-D0). The page files and their fence belong to WS-33. 🔴 The words of each change are OWNER-GATE: the owner approves the text before the PR that publishes it, because a legal page binds Metorite to third parties (CLAUDE.md §3a rule 3) | `email_app_master_plan.md` §12.4, after step 15, holds the Gmail draft, for approval only. `site/AGENTS.md` points to the owner spec and adds nothing |

## 6. Owner-gate registry (agents must refuse these)

> # ⚠️ DEV-PHASE WINDOW — MOST OF THIS SECTION IS OPEN UNTIL 2026-09-30
>
> **Owner directive, 2026-09-01. `CLAUDE.md` §3a is the contract. Read it before
> you refuse anything here.** On 2026-10-01 this whole banner goes, and every
> row below returns to full force with no further action.
>
> **Why.** The owner wrote 22 day-scoped grant lines in eight days. A permission
> the owner always gives is not a decision. It became a toll, and the toll
> showed up as stalled sessions instead of as safety.
>
> **What changed.** `plan-guard.mjs` now reads a second grant form,
> `ALLOW-UNTIL <date> <gate-id>`, which holds through that date instead of for
> one day. The window still closes by itself, on a date a human wrote.
> `.claude/OWNER_GRANTS.md` holds the live lines — **read it, then act**.
>
> **What this does NOT open.** Four acts, which no grant unlocks: force-push,
> history rewrite, committing on `main`, and an agent writing the grant file.
> Three more need the owner's word each time: charging money, mailing a real
> person, and writing a live organization's membership or credit balance.
> §6.0 rows **A1, A2, A4, B2** and **D3** stay closed for that reason.
>
> **What this does NOT change.** R6 — a production migration is one-way. Confirm
> the pre-migration backup completed, then apply it. And rule 8 of `CLAUDE.md`
> §3 — verify by the migration ledger, the deployed SHA and the log line.
> Faster delivery makes a silent no-op deploy more likely, not less.

### 6.0 Decisions the owner owes — the short list *(added 2026-08-26)*

> ⚠️ **This sub-section lists DECISIONS, not actions.** The rest of §6 is the register of
> **acts an agent must refuse**. These are **judgements only the owner can make** — and
> the distinction matters, because an agent meeting one of these must *ask*, not refuse.

**✅ None of these blocks building.** Every one sits at the *turn-it-on* boundary, not the
*build-it* boundary. CP-10 slice 1, CP-11 and the whole of WS-38's first four tickets are
AGENT-SAFE and unblocked today.

**The list is short on purpose.** D61 took the seven AI questions that had one defensible
answer each; what remains is money, credentials, external accounts, commercial judgement,
and one thing that cannot be written until a second person exists.

#### A · Commercial — these cost money or set a price

| # | Decision | Consequence of NOT taking it | Consequence of taking it wrong |
|---|---|---|---|
| **A1** | **Price the AI rate card** (D19.2 · **H-42**) | Credits are granted and **nothing draws them down** — we serve AI for free and learn nothing about margin | ⚠️ **Order matters more than the number.** Pricing before real traffic is a guess you then change on customers who have already seen it — which is why the card ships unpriced by fence and CP-11 exists to produce the traffic first |
| **A2** | **Then flip `CUSTOMER_CONSOLE_SPEND_GATE`** (§6, below) | No 402, no balance enforcement — a customer can burn without limit | 🔴 **Flipping it BEFORE A1 is the expensive mistake**: a zero-balance org (the state provisioning leaves *every* org in) is refused on all AI calls, while a funded org can never reach 402 because nothing bills. All of the cost, none of the benefit. **Decided (owner, 2026-10-17):** the gate also refuses the embedding door (`tier-embed`) for a zero-credit org, and the owner accepts that. A zero-credit org gets no embeddings. Its premium features are decided later (`customer_console.md` §6A.16) |
| **A3** | **D-D — where staging runs and what it costs** (**H-38**) | WS-38's **T-7/T-8/T-9** stay blocked; we keep working directly in production past the point where Phase 0's triggers fire | It is a recurring cost that scales with nothing — staging is a *nightly re-derivation*, so it can be small and torn down. Under-provisioning it is cheap to fix |
| **A4** | **LS-11 — seats held on plans D49 retired** (**H-20**) | Those customers keep working; what they should pay stays unanswered | Money on a live system — repricing, converting, refunding, proration. Expected to be empty or Fracktal-only today, which is why it is safe to leave open a little longer |

#### B · Credentials and external accounts — an agent cannot hold these

| # | Decision / act | Consequence of NOT doing it |
|---|---|---|
| **B1** | **Install our real provider API keys** through CP-10 slice 1's route (§6 (e)/(f)) | 🔴 **The Router cannot call any provider.** This is the single thing standing between a built AI path and a working one |
| **B2** | **Create the Razorpay TEST account** + three env vars (**H-14**) | Fracktal's ₹0 onboarding (D42) is blocked; the checkout answers 503 |
| **B3** | **Rotate the disclosed secrets** (**H-3**, **H-16**) and split `GATEWAY_INTERNAL_TOKEN` from `LITELLM_MASTER_KEY` (§6 item 1) | Every access control in this plan rests on an identity that a leaked key can forge |
| **B4** | **The `.env*` edits** — H-13, H-30, H-34 | A fresh box is still told to configure a retired integration, and the Tasks-lens pair appears for the first time on cutover day |
| **B5** | **Configure the Supabase Auth GOOGLE WORKSPACE provider for STAFF**, and set **`OPERATOR_SIGNIN_PROVIDER=google`**, `OPERATOR_GOOGLE_HD`, `OPERATOR_STAFF_DOMAINS` and `OPERATOR_BOOTSTRAP_EMAIL` (**D70**, which amends **D64.1**, CP-12a). ⚠️ **Corrected 2026-09-01.** This named the Microsoft provider and `OPERATOR_ENTRA_TENANT_ID`. We have no Entra directory. ⚠️ **Amended the same day**: the switch was missing, and without it the box stays on `azure` and answers 401 to every Google sign-in | 🔴 **The Operator Console stays on one shared passphrase.** It has been deployed and reachable since 2026-08-22 with no per-person identity, no roles, and an audit log that records the literal `operator` for every write. CP-12a…g are all AGENT-SAFE to build without this — but none of them can be turned on |

#### C · Governance — settings and people

| # | Decision | Consequence of NOT taking it |
|---|---|---|
| **C1** | **Restore branch protection on `main`** after T-1 lands (**H-23**) | Every CI gate stays **advisory at merge**. Survivable with one disciplined developer; not survivable with two — and delivery is automatic again, so an unreviewed commit reaches production by itself |
| **C2** | **D-F — the CODEOWNERS map** (**H-39**) | ⚠️ **Deliberately not actionable yet.** Cannot be written before a second person exists; a map naming one person enforces nothing |
| **C3** | **Promoting any `preview` app to `live`** (**H-21**) | The launch surface stays nine panes. This is the standing judgement *"is this finished enough to sell"* and never expires |
| **C4** | **Name the first operators and their roles** (**D64.3**, CP-12d). 🔴 **The four-eyes half has FIRED. Measured on production 2026-09-01: TWO `active` `admin` rows** — `nithin@hathilabs.com` and `vjvarada@hathilabs.com`, both created 2026-08-30 | Nobody can be added until the owner says who is a `viewer`, who is an `editor` and who is an `admin`. ⚠️ Naming a **second** `admin` is also the trigger that pulls four-eyes approval (**DEF-1**) out of deferral — the two decisions arrive together and must not be separated. 🔴 **That trigger is no longer hypothetical.** `specs/operator_identity_and_access.md` §9.1 records the firing and **H-93** carries the queue entry. An agent may not build four-eyes on this row alone. The owner takes the shape first |
| **C5** | **WS-44's four open questions**: Q2 (does `/` become My Day), Q4 (one shared bar row), Q5 (fixed presets or an editor) and Q6 (does Desk mode hide All apps). `specs/navigation_shell.md` §13.3 · **H-243** | Nothing is blocked. Each question has a default that an agent builds to, behind a flag. The owner checks the default before the flag goes on |

#### D · Live-data acts — build agent-safe, run owner-only

| # | Act | Note |
|---|---|---|
| **D1** | **H3 — the tenant RLS promotion**, and **§H3.2's sign-in brick decision first** | 🔴 **The gate for customer #2.** As rehearsed, phase 4 breaks sign-in for every user; the fix is written up and not enacted |
| **D2** | **WS-39 S3b/S3c** — the `gtd_*` backfill and drop (**H-29**) | Built on this branch, unrun |
| **D3** | **MT-1j** against a real second organization | Composes three existing gates; H3 is a hard prerequisite |
| **D4** | **Flipping any dark flag on a live box** | `ROUTER_SERVING_ENABLED`, `SELF_SERVE_SIGNUP_ENABLED`, `TASKS_LENS`, `MEMBER_INVITE_EMAIL_ENABLED`, `CUSTOMER_CONSOLE_RESOLVE_ENABLED` — each has its own entry below. Also `EMAIL_OUTLOOK_DELTA` with its account list `EMAIL_OUTLOOK_DELTA_ACCOUNTS` (WS-17 EM-T4d, gate `enforcement-flip`): any value other than `off`, and any account id in the list. `email_app_master_plan.md` §10.4.6 holds the rule. Also `EMAIL_GMAIL_CONNECT` (WS-17 EM-G7, D-EM-36, gate `enforcement-flip`): the value `true`. EM-G7b adds a member allowlist beside it, and `EMAIL_GMAIL_CONNECT_MEMBERS` is the planned name: any member in the list. `email_app_master_plan.md` §12.2 holds the rule. Also `EMAIL_INSIGHTS` with its organization list `EMAIL_INSIGHTS_ORGS` (WS-17 EM-T14a): the value `true`, and any organization id or `*` in the list. Both are owner-only, and the §3a window does not open them. The job sends unread mail to a model and costs money. `email_app_master_plan.md` §13.9 holds the rule. Also the four flags of WS-17 §14. `EMAIL_HTML_FROM_PROVIDER` (EM-S1) and `EMAIL_HTML_HOT_ONLY` (EM-S3): the value `true`, gate `enforcement-flip`, which the §3a window covers. `EMAIL_HTML_CLEAR` (EM-S4): the value `dry_run` is gate `enforcement-flip`. The value `on` is owner-only, and the §3a window does not open it, because it removes stored HTML in each organization and is one-way. `EMAIL_SYNC_WINDOW_CHOICE` (EM-S6): the value `true` is owner-only, and the §3a window does not open it, because a longer window costs AI compute. `email_app_master_plan.md` §14.6 holds the rule. Also `EMAIL_TRIAGE_ONCE_PER_CYCLE` (WS-17 EM-T16 PR-A) and `EMAIL_STATUS_SKIPS_RULE_MATCH` (WS-17 EM-T16 PR-B): the value `true`, gate `enforcement-flip`, which the §3a window covers. `email_app_master_plan.md` §10.4.17 holds the rule. Also `EMAIL_CONTEXT_SELECT` (WS-17 EM-T17 PR-B): the value `true`, and `email.context_select` in `DECIDE_FEATURE_MODES` (EM-T17 PR-C): the value `shadow` or `on`. Each is gate `enforcement-flip`, which the §3a window covers, while the `decide` state holds no body. A state with a body waits for the owner answer to D-EM-62 open (a). `email_app_master_plan.md` §17.4 holds the rule. Also `TOOL_PACKS` and `TOOL_PACKS_ORGS` (WS-67 OA-7), and `SPAWN_SUBAGENTS_ORGS` (WS-67 OA-9): any value other than empty. Each is owner-only, and the §3a window does not open it, because it moves the egress guards and spends model tokens. `specs/one_assistant_context.md` §4.5 holds the rule. The WS-44 shell flags and `COMMAND_BAR_AI` are NOT in this class. They are owner-only, and §6.1 holds the rule |

---

**Two things that are NOT on this list, and should not be re-raised as owner decisions:**

1. **The AI access model.** D58 (SDK, no proxy), D59, D60 (`task × tier`, capability
   dispatch) and **D61** (the eight open questions) close it. `customer_console.md`
   §6A.9/§6A.10 carry the design and its own register. **D75** adds the `decide`
   task inside that model and does not reopen it. Its vendor, residency and price
   are gated, in §6.1 WS-31 (i).
2. **The delivery framework.** D55 adopted it and minted WS-38. Only **A3 (D-D)** and
   **C2 (D-F)** remain of its nine open questions.

---

### 6.1 The acts themselves

> **WS-29 / MT-0c-2 — un-parking the WS-3 T2 container tier.** Still OWNER-GATE, and
> **still parked** — narrowed by **D16** (2026-08-08). `saas_multitenancy.md` §0.9.3's
> *conditions* (no raw-SQL tool; no agent-reachable `app.tenant_id` write) are satisfied
> without it: **MT-0c-1 shipped the first**, and the second cannot be violated before
> `app.tenant_id` exists (MT-1b). What remains is the container/microVM boundary, which
> D10 parked on the "trusted colleagues" threat model — a model that survives the silo
> phase and dies at the pooled cutover. **An agent must refuse to build T2 and say so**;
> it is a precondition of the §5.1 cutover, not of Phase 0.
>
> ⚠️ **Amended 2026-10-03 by D83.** The owner un-parked T2 for the WS-43 sandbox
> terminal only (`specs/maf_coding_engine.md` §3). An agent may build that sandbox.
> It still refuses P5-c, P5-d, P5-b.3 and T2 for the pooled cutover, by name.
>
> **WS-43 — the sandbox terminal on the box.** *(Registered 2026-10-03 with D82 and
> D83. Gate id `ws43-sandbox-flip`.)* To build WS-43a to WS-43i, WS-43k to WS-43o,
> WS-43t1 and WS-43t2 is AGENT-SAFE, and
> each slice ships dark. These acts are OWNER-GATE: Docker access for the gateway
> user and the host firewall rule on the box, the image on the box, and
> `MAF_CODING_SCOPE` or `SANDBOX_EGRESS_ENABLED` on production. So are the
> production allowlist, gVisor or rootless Docker, the eval on the production
> Router, and the merge of WS-43j. `specs/maf_coding_engine.md` §12 lists them as
> WS43-G1 to WS43-G8.
>
> ⚠️ **The dev-phase window of CLAUDE.md §3a does NOT open these gates.** No line of
> `.claude/OWNER_GRANTS.md` names `ws43-sandbox-flip`, and the grants for `deploy`,
> `deploy-write`, `env-write` and `enforcement-flip` do not cover it. Until H-214
> adds a plan-guard rule, this text is the only fence.
>
> **Added 2026-10-03 with D84.** The same gate id covers the Copilot removal: the
> merge of WS-43p, WS-43q, WS-43r or WS-43s (WS43-G9, and WS-43s is a one-way
> migration), the `mutation:*` and `metorite:*` targets on production (WS43-G10),
> a change in a repo outside this one, such as `agent-sales-assistant` (WS43-G11,
> moot since 2026-10-13: WS-53 CRM-C7 removed that agent),
> the edit of `scripts/vps_apply.sh` (WS43-G12), and `MAF_NATIVE_SESSIONS` on
> production (WS43-G13).
>
> **Added 2026-10-03 with D86.** The active track is Projects first
> (`specs/maf_coding_engine.md` §16). Its AGENT-SAFE slices are WS-43d, WS-43u
> and WS-43v. Its owner flip, WS-43w, is three acts under the same gate id:
> WS43-G1 (Docker access on the box), WS43-G2 (the image on the box) and WS43-G3
> (`MAF_CODING_SCOPE=projects:<Fracktal org>`). The slices that D86 parks need
> the owner to restart them before any work.
>
> **Added 2026-10-03, the owner decision "Keep delegation" (D86).** A covered
> Projects run keeps `call_agent`, `call_agents_parallel` and
> `call_agent_background`, and the agent that it calls runs outside the
> sandbox, with the network of the host. So WS43-G3 has a new prerequisite:
> a network control on the agents that a covered Projects run can call.
> **Met for the built control, 2026-10-04.** H-236 fails closed: a covered
> run and each run under it hold only the delegation tools, the sandbox
> tools and tools that say `open_world=False`, and the control refuses a
> call to any other tool (`specs/maf_coding_engine.md` §16.3, fence
> WS43-F24). At the flip, the owner accepts the NAMED residual of §16.3,
> by name. It has five open items: an agent that a member assigns by hand, a
> delayed send through a store, what an agent from another repo does on its
> own servers, a workflow on a task event, and the answer that an uncovered
> parent gets. PR #618 closed the sixth, a remote image in the answer text.
> An agent may not take
> that acceptance as given. The three acts of WS-43w stay
> owner acts.
>
> **Added 2026-10-04 with H-227.**
>
> WS43-G3 waits on one more act, the merge and the deploy of H-227. H-227
> makes the uploads and the S8 documents of a shared agent thread-scoped.
> A container then mounts only the uploads of its own thread
> (`specs/maf_coding_engine.md` §16.3). Without it, the container of one
> member's covered run mounts the uploads of every other member of the
> organization.
>
> **Added 2026-10-06 with WS-43y.** To build WS-43y1 to WS-43y4 and WS-43x1
> is AGENT-SAFE, and each slice ships dark. WS-43x2 is two acts under this
> gate id: WS43-G2, and WS43-G3 for `email:<org>`.
>
> **Projects — `PROJECTS_AGENT_DISPATCH`, a run for an `agent:` assignee.**
> *(Registered 2026-10-04 with PR #622.)* When a member assigns a task to
> `agent:<name>`, the dispatch sink can start a run, and the run spends the
> org's AI credits. The flag is OFF by default. With it OFF, the sink starts
> no run and writes one timeline row that says so.
>
> To build and test the run is AGENT-SAFE. **To set the flag on a box is an owner flip**, because it
> spends money (CLAUDE.md §3a rule 3). The grant `enforcement-flip` does not
> cover it. `specs/project_management_app.md` §6.4 and §9.12.10 are the
> authority, and H-240 is the open gap behind it.
>
> **WS-44 — the shell flags and `COMMAND_BAR_AI`.** *(Registered 2026-10-05
> with D87 to D89.)* To build NS-1 to NS-7 is AGENT-SAFE, and each slice ships
> dark. To turn on `NEXT_PUBLIC_SHELL_BAR`, `NEXT_PUBLIC_SHELL_NAV`,
> `NEXT_PUBLIC_SHELL_SCOPE`, `NEXT_PUBLIC_SHELL_DOCK` or `NEXT_PUBLIC_MY_DAY`
> in production is an owner flip. Each flag is build-time, so one flip
> changes what every organization sees on day one, and it answers Q2 for all
> of them. The dev-phase window does NOT open it, and `enforcement-flip` does
> not cover it.
>
> **`COMMAND_BAR_AI` on production is an owner flip**, because each tier-2
> call spends the org's credits (CLAUDE.md §3a rule 3). The grant
> `enforcement-flip` does not cover it. It also waits on H-171 and H-69.
> `specs/navigation_shell.md` §13.1 is the authority.
>
> ⚠️ **No plan-guard rule names these flags**, so this text is the only fence.
> A rule needs the `guard-write` grant.
>
> **WS-58 — the My Day feed flags.** *(Registered 2026-10-13 with D100 and
> D101.)* To build MD-1a to MD-12 is AGENT-SAFE, and each slice ships dark. To
> turn on `NEXT_PUBLIC_MY_DAY_FEED` in production is an owner flip, for the
> reason of the WS-44 flags above. `MY_DAY_COMPOSE`, and any `my_day.*` mode
> other than `off` in `DECIDE_FEATURE_MODES`, are owner flips because they
> spend the org's credits (CLAUDE.md §3a rule 3). A `my_day.*` mode spends only
> for an org in `MY_DAY_DECIDE_ORGS`, so a name in that list is half of the
> same flip. MD-10, a message outside the
> app, is owner-only because it reaches a real person. `specs/my_day_feed.md`
> §17.1 is the authority. No plan-guard rule names these flags either.
>
> **WS-53 — the CRM note labels.** *(Registered 2026-10-16 with CRM-Q26.)* To
> build CRM-Q26 is AGENT-SAFE, and it ships dark. A mode other than `off` for
> `crm.note_labels` in `DECIDE_FEATURE_MODES` is an owner flip, because each
> label request spends the org's credits (CLAUDE.md §3a rule 3). A `crm.` mode
> spends only for an org in `CRM_DECIDE_ORGS`, so a name in that list is half
> of the same flip. The grant `enforcement-flip` covers neither.
>
> The backlog of an org with many notes costs credits for days, so ask the
> owner first. `specs/crm_platform.md` §18.11 is the authority. No plan-guard
> rule names these flags either.
>
> **WS-53 — the CRM mapping pipeline.** *(Registered 2026-10-16 with
> CRM-Q20-1.)* To build CRM-Q20-1 is AGENT-SAFE, and it ships dark. A mode
> other than `off` for `crm.map_role`, `crm.map_target` or `crm.map_stage` in
> `DECIDE_FEATURE_MODES` is an owner flip, because each request spends the
> org's credits (CLAUDE.md §3a rule 3). A name in `CRM_DECIDE_ORGS` is half
> of the same flip. The grant `enforcement-flip` covers neither, until the
> owner answers the open question of `specs/crm_platform.md` §18.9.6.
> The probe after the flip spends credits too, so an agent prepares it and
> stops. No plan-guard rule names these flags either.
>
> **WS-53 — the CRM catalogue.** *(Registered 2026-10-17 with CRM-Q31-3.)* To
> build CRM-Q31-1 to CRM-Q31-3 is AGENT-SAFE, and each ships dark. Two flips
> differ. A name in `CRM_CATALOGUE_ORGS` spends nothing, so it is the grant
> `enforcement-flip`.
>
> A mode other than `off` for `crm.map_catalogue` in
> `DECIDE_FEATURE_MODES` is an owner flip, because each request spends the
> org's credits (CLAUDE.md §3a rule 3). A name in `CRM_DECIDE_ORGS` is half of
> that flip. The grant `enforcement-flip` does not cover it. The cost bound of
> one run is 24,000 estimated tokens and 12 requests for the catalogue step.
>
> `on` also writes catalogue items with no approval, which is owner call 1 of
> `specs/crm_platform.md` §18.13.8, so the owner flips `shadow` first. An
> agent prepares the check that follows the flip and stops, because that check
> spends credits as well.
> No plan-guard rule names these flags either.
>
> **WS-53 — the CRM open questions.** *(Registered 2026-10-17 with CRM-Q32.)*
> To build CRM-Q32-1 to CRM-Q32-6 is AGENT-SAFE, and each ships dark. A name
> in `CRM_QUESTIONS_ORGS` spends nothing, so it is the grant
> `enforcement-flip`. The org needs a place on `CRM_CATALOGUE_ORGS` too,
> which is the same grant. CRM-Q32 makes no model call, so it adds no owner
> flip. An answer of an admin writes the plan of that org only.
> CRM-Q32-7 waits for WS-61.
> CRM-Q32-8, CRM-Q32-9a and CRM-Q32-9b *(specified 2026-10-18)* are
> AGENT-SAFE, ship behind the same list and add no flip. The chat tool
> `crm_answer_question` writes through the same admin route for a CRM admin.
> For each other member it records a proposal in `zoho:questions`, and
> writes no plan. An admin confirms a proposal through the same route.
> `/reply` chooses the admin branch only on `has_permission`, before any
> write, and refuses `@auto` for each caller.
> The merge order: #1133, #1132, the spec, CRM-Q32-9a,
> `crm-questions-copy`, CRM-Q32-9b, then CRM-Q32-8.
>
> **WS-53 — the CRM note search.** *(Registered 2026-10-16 with CRM-Q27.)* To
> build CRM-Q27 is AGENT-SAFE, and it ships dark. A name in
> `CRM_NOTE_SEARCH_ORGS` is an owner flip. Each embed request of that org
> costs vendor money, and no credit row pays it (CLAUDE.md §3a rule 3). The
> grant `enforcement-flip` does not cover it. The first pass of DEWiN's 18,674
> notes costs at most about USD 0.19, over about 4 days. Billing in credits
> is an open owner question (`specs/crm_platform.md` §18.11.2, CRM-Q27). No
> plan-guard rule names this flag either.
>
> **WS-53 — the CRM weekly digest.** *(Registered 2026-10-17 with CRM-Q28.)* To
> build CRM-Q28 is AGENT-SAFE, and it ships dark. A name in `CRM_DIGEST_ORGS`
> is an owner flip, because each digest spends the org's credits on
> `tier-powerful` (CLAUDE.md §3a rule 3). The grant `enforcement-flip` does not
> cover it. The cost bound is 3 requests for each org and week, about 8,400
> tokens at most. After the flip, the owner reads the credits of the first
> digest from `usage_event`. Delivery by email is CRM-Q28b, dark behind
> `CRM_DIGEST_EMAIL_ORGS`. It sends mail to a real person, so the owner first
> picks a sender, and a name in that list is an owner flip too. No plan-guard
> rule names these flags either.
>
> **WS-53 — the CRM number check.** *(Registered 2026-10-17 with CRM-Q29.)* To
> build CRM-Q29 is AGENT-SAFE, and it ships dark: `CRM_ANSWER_CHECK` is `off`.
> The agent who built it chose the grant `enforcement-flip` for `shadow` and
> for `on`. `shadow` spends nothing. `on` spends at most one more model
> request in a chat turn whose answer fails the check, and the member's own
> chat pays for it as for each other request. So no new payer exists. The
> owner can change this entry to an owner flip in one line. Then an agent
> prepares the flip and stops. `specs/crm_platform.md` §18.11.2 is the
> authority. No plan-guard rule names this flag either.
>
> **WS-29 — moving any customer onto the pooled tier.** Cutover is a data move against
> live customer data. AGENT-SAFE to build; **OWNER-GATE to execute.**
>
> **WS-29 / MT-1j — provisioning a REAL second organization.** *(Registered 2026-08-19
> with the mint of `saas_multitenancy.md` §11 MT-1j; the decision is **D43-3**.)*
> Building MT-1j and R8-testing it against scratch databases is **AGENT-SAFE and
> unblocked**. **Running it against a real second organization is OWNER-GATE**, and it is
> not a new gate class — it **composes three existing ones**: (a) the Customer Console
> service is on no box and where it runs was decided 2026-08-19 — D47, the production VPS (§8 gate 2, below as
> **(a)**); (c) provisioning writes an organization's membership, seats and subscription,
> which is the *"editing any live organization's entitlements, seats or credit balance"*
> class (§8 gate 4, below as **(c)**); and **H3's promotion of
> `infra/postgres/generated/04_policies.sql` is a human act in a window**
> (`saas_multitenancy_handover.md` H3 — 135 tables, an `ACCESS EXCLUSIVE` phase, and a
> lock-timeout design written after a 14h44m outage). **Before H3, a second organization
> exists with no database-level isolation** — measured 2026-08-19, `BASELINE_UNSCOPED`
> names 114 tables with no `organization_id` at all. An agent asked to "provision Fracktal
> as customer zero" (D36) must **build the path, test it two-org on scratch, and stop.**
>
> **WS-29 / MT-1f — turning per-tenant subdomains ON. ⚠️ WITHDRAWN (D51,
> 2026-08-24) — none of these acts will ever be performed.** The owner dropped
> subdomain workspaces entirely the day after the slice landed; the code half was
> removed with the decision. The five acts that were registered here (wildcard DNS
> record · DNS-01 wildcard cert + Caddy block · `AUTH_URL` pin ·
> `SUBDOMAIN_WORKSPACE_ENABLED` · `WORKSPACE_BASE_DOMAIN`) are struck — kept in
> this one paragraph so a future subdomain revival knows the runtime checklist and
> the CT-log/on-demand-TLS rejection that came with it (B1). The multi-org
> workspace CHOICE moved to a session claim: `saas_multitenancy.md` §11 MT-1k.


> **WS-30 / WS-31 — inviting a REAL member into a live organization, and flipping
> `MEMBER_INVITE_EMAIL_ENABLED`.** *(Registered 2026-08-24 with the mint of
> `customer_console.md` CP-2f + `subscription_console.md` SC-2c; the decision is **D49**.)*
> Building the invite path and R8-testing it against scratch databases is **AGENT-SAFE
> and unblocked**. Three acts on the live box are **OWNER-GATE and must be refused by
> name**:
> **(a)** granting the REAL deployment key the new `member_admin` capability — the
> capability-growth class of `customer_console.md` §8 gate 8, alongside `provision` and
> `seat_admin`; there is deliberately no HTTP route that edits a key's set;
> **(b)** running an invite against a live organization — it **writes that
> organization's membership**, which is §8 gate 4's *"editing any live organization's
> entitlements, seats or credit balance"* class, and it is now a write on **two planes**
> (tenant `app_user` + Console `org_membership`) rather than one;
> **(d)** setting `MEMBER_INVITE_EMAIL_ENABLED=true` (with a real `RESEND_API_KEY`) on a
> live deployment — the first flip makes this product send mail to third parties from
> `no-reply@metorite.com` on an admin's action, with **no rate limit in slice 1** (a
> named accepted risk in SC-2c, not an oversight). *(The label skips (c) on purpose: (c)
> is the MT-1j entry above, whose gate 4 clause this one composes rather than restates.)*
> An agent asked to "invite the team" must **build the path, test it against scratch
> Postgres, and stop.**

> **WS-31 / CP-12 — standing up staff identity on the Operator Console.** *(Registered
> 2026-08-26 with the mint of `specs/operator_identity_and_access.md`. The decision is
> **D64**, **amended 2026-09-01 by D70** — the provider is Google Workspace and the
> claim is `hd`.)* Building all seven slices and R8-testing them against scratch
> databases is
> **AGENT-SAFE and unblocked.** Seven acts are **OWNER-GATE and must be refused by name**:
> **(a)** configuring the Supabase Auth Google Workspace provider for staff, and holding
> its client secret — the external-account class of §6.0 B.
> **(b)** setting **`OPERATOR_SIGNIN_PROVIDER`**, `OPERATOR_GOOGLE_HD`,
> `OPERATOR_STAFF_DOMAINS` or `OPERATOR_BOOTSTRAP_EMAIL` on the box —
> `env-write`. ⚠️ **This clause left `OPERATOR_SIGNIN_PROVIDER` out until
> 2026-09-01, and it is the value that turns the rest on.** It holds `azure` or
> `google`, and it defaults to `azure`. A box that carries every other value
> and not this one stays on the Entra path. Every Google sign-in then answers
> 401, and no message names the unset variable. H-54 lists all six values.
> ⚠️ **The switch goes in TWO env files, and so does `OPERATOR_SUPABASE_URL`.**
> The API and the Next console are separate processes. H-54 holds the table,
> and the spec's §4.2a holds the same split beside the code.
> **(c)** flipping `OPERATOR_IDENTITY_ENABLED` on a live box — `enforcement-flip`, and it
> is the cutover from the shared passphrase to real identity.
> **(d)** removing `OPERATOR_CONSOLE_STAFF_SECRET` from the box — `env-write`. ⚠️ Doing
> **(d)** before **(c)** locks the whole team out of a live console.
> **(e)** granting a real person the `admin` role — a role write, already refused by
> CLAUDE.md §3.2 and by the "no agent may add, promote or suspend a member" rule in §4.
> **(f)** ⚠️ **using the break-glass operator token** — it bypasses every control the spec
> builds, and that is all of its purpose. An agent that believes it needs this has
> misread the ticket.
> **(g)** rotating that token after an incident — `secrets`.
> An agent asked to "give the team logins" must **build CP-12a…g behind the flag, leave
> the flag OFF, and stop.**

> **Two identity-boundary items, measured on the running deployment 2026-08-05 —
> both OWNER-GATE, and together they are what makes every other access control
> in this plan trustworthy or not.**
>
> 1. **`GATEWAY_INTERNAL_TOKEN` is byte-identical to `LITELLM_MASTER_KEY`** on the
>    box (same length, same sha256). It is *set*, so a "is it configured" check
>    reads green — it was set to the same value. The service identity is therefore
>    the key every agent's BYOK client holds. **Rotate it by redeploying**, never by
>    hand into `.env` alone: `deploy.yml` reconciles `.env.local` from `.env`, and
>    setting only the first locks out every signed-in member (see
>    `colleague_onboarding.md` §1.1's lockout warning).
>    ~~⚠️ Blocked 2026-08-05: the prescribed rotation *is* a redeploy, and the
>    delivery path is broken.~~ **UNBLOCKED 2026-08-09:** delivery recovered —
>    deploys landing since 2026-08-06, six green runs on 2026-08-07 UTC (#400
>    log-verified on the box; see WS-25). The rotation is executable again via a
>    redeploy; the
>    both-files reconcile warning above still binds, and the tip run's
>    health-verify failure (WS-25) is worth understanding before choosing the
>    deploy window.
> 2. ~~**Gateway `:8080` and workbench `:3001` are open to the internet**~~
>    **CLOSED 2026-08-05.** Both UFW rules removed (v4 and v6); verified from
>    outside the box that each now refuses while `https://api.…/health` still
>    answers 200 and the UI 307s to `/signin`. There is **no Hostinger cloud
>    firewall** on this VPS (`firewall_group_id: null`, firewall list empty), so
>    UFW is the only barrier and the only place this can regress.
>    Still owed: Caddy's `header_up -X-User-Email` / `-X-User-Role` strip
>    (`deploy/hostinger/caddy/Caddyfile`) — now defence-in-depth rather than the
>    load-bearing control, since the bypass path it guards is closed.
>
> Item 1 still blocks *trusting* app development: an owner predicate applied to a
> forged identity is not a control. Delivery works again (2026-08-09), so the
> only thing between here and the rotation is the owner choosing a window.

Force-push / history rewrite (BO-8) · credential rotation (Zoho, Hostinger
token) · enforcement flips (`ACTION_BROKER_ENFORCE` — ⚠️ **still UNSAFE as of
2026-08-11 and blocked on BO-1d**, not merely owner-gated: three `/tasks`
endpoints hard-500 and one write is silently swallowed because their callers
never read `_broker_gate`'s pending marker. BO-1a + BO-1b landed and did not
close this; see WS-1 and checklist §BO-1d · `AGENT_PERMISSION_MODE=
enforce`, `MEM0_ENABLED`, `GRAPHITI_ENABLED`, `WHATSAPP_ENRICHMENT`,
`SKILLS_FAIL_CLOSED` — the WS-23 fail-closed default-profile flip; review
`skills_scope_out.md` §3 dynamic-agent rows first · `SKILLS_INDEX_ONLY` —
the WS-23-successor skills-index flip: every agent's prompt becomes a
one-line-per-family index with bodies read on demand; see
`skills_scope_out.md` §7 · `INGESTION_CONSUMER` — the WS-4/BO-20
ingestion-consumer flip (the loop **shipped OFF** in BO-20a, 2026-08-02):
turning it on is not just "start a loop" — it cuts all three provider receivers
over from inline `emit_event` to enqueue-only so the consumer becomes the
**only caller of `emit_event`** (not "the single dispatch path" — that wording
was loose: `routes/agent.py:3476-3478` calls `dispatch_event` directly and is
untouched), which means Redis down = provider events dropped rather than
dispatched inline, logged as `<source>.queue.dropped`; see
`FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-20.0 (answered: Option A) and its Q1) ·
**WS-6 observability activation** (Langfuse keys + bringing up
`--profile obs` in prod, `OTEL_EXPORTER_OTLP_ENDPOINT` in the deploy env,
`LLM_USAGE_AUDIT=1`, and re-enabling MAF telemetry by setting
`ENABLE_INSTRUMENTATION` — the kill switch is the env read at
**`executor.py:138`**, inside `_disable_agent_telemetry_once` (block
`:113-140`; the long-standing `:114` citation pointed into the comment banner
above it — corrected and re-verified 2026-08-03). It hides a known
ContextVar-reset bug that turns a successful streamed run into a `RUN_ERROR`) ·
**`copilot_sandbox_scope`** (`packages/acb_common/acb_common/settings.py:222`,
ships `""` = fully off, in-process everywhere). Putting `code_task` or
`app_builder` in it routes **real Copilot sessions into containers** — a live
execution-path change, not a config tweak. It was registered nowhere until
2026-08-03 ·
**`ISOLATION_TIER_ENFORCE`** — the new switch WS-3a introduces
(`permissions_sandbox_b6.md` §P5-a.2). Today every unscoped agent derives T2,
so flipping it **refuses most real runs**; it ships OFF and the refusal must be
behind it ·
**`CUSTOMER_CONSOLE_SPEND_GATE`** — WS-31 CP-6's two refusals (the 402 balance
gate and the 403 per-run breaker on the Customer Console's
`/v1/chat/completions`; `customer_console/main.py`, `_spend_gate_enabled`).
Ships OFF (registered 2026-08-18, same day it was built — the
`copilot_sandbox_scope` lesson). Flipping it is a **billing-behaviour change**
and the ORDER matters: with the shipped all-zero rate card it delivers every
cost of enforcement and none of the benefit — a zero-balance org (the state
provisioning leaves every org in) is refused on all AI calls while a funded
org can never reach 402, because nothing bills. Price the card first (itself
owner-gated commercial work, D19.2 — and `test_the_rate_card_ships_unpriced`
refuses a priced LADDER until BYOK zero-rating and the gate's pre-flight cost
invariant are closed; price live, never via migration), then flip ·
**the seven WS-31 Customer Console gates** (`specs/customer_console.md` §8),
registered 2026-08-18 because until then they lived **only in the spec** and a
gate the board cannot see is a gate a dispatched agent never reads. The
`GATEWAY_INTERNAL_TOKEN`/`LITELLM_MASTER_KEY` split (§8 gate 1) is already above;
the other six are **(a)–(f)**, and **(g)** is D42's neighbouring revenue gate,
registered here with them because an agent reading this list will meet it in the
same PR:
**(a) deploying the Customer Console service anywhere, and any VPS reach**
(§8 gate 2) — the service is built and on no box; where it runs was DECIDED 2026-08-19
(D47: the production VPS — own unit + env file, webhook-only public surface,
deploy gains the Console-ladder apply step); the DEPLOY act itself stays this
gate (`specs/customer_console_infrastructure.md` §5 item 4) ·
**(b) live Razorpay credentials and any real payment configuration** (§8 gate 3,
**CP-9**/D19.5) — ⚠️ *re-pointed 2026-08-18: this read "CP-8/D19.5", and CP-8 is
the **Operator Console and reconciliation**. The `payment_provider` seam had no
ticket body anywhere in the corpus and no code (one repo-wide hit,
`org_subscription.provider CHECK (…'razorpay'…)`); it is now
`specs/customer_console.md` **CP-9**, minted with this correction.* **Extended
the same day: creating the Razorpay ACCOUNT is owner-side even in TEST mode**,
and so is writing test-mode keys into CI or any deployment's env — any external
commercial account is an owner act (`specs/customer_console_infrastructure.md`
§5 item 5, §7). The consequence binds a dispatched agent: the whole seam is
built against a **fake provider with a real HMAC signer**, and WS-30 SC-4g
clause 2's test-mode capture rehearsal is **scripted and handed over, never
reported as run** ·
**(c) editing any live organization's entitlements, seats or credit balance**
(§8 gate 4) — concretely `POST /credits/grant`
(`customer_console/main.py:1104`, `grant_credits`), `POST /billing/seats`
(`:1049`, `assign_seat`), `POST /billing/seats/release` (`:1089`,
`release_seat`) and `POST /orgs/lifecycle` (`:637`, `set_lifecycle`) against a
real org — *(all four re-measured **2026-08-19** — :963/:908/:948/:500 →
:1104/:1049/:1089/:637 — after CP-9's six routes landed above and below them.
The 2026-08-18 set had itself corrected :690/:635/:675/:472, which predated
CP-2a/CP-2b and pointed into unrelated code. **Handler names travel beside the
numbers**, which is what made this drift a two-minute re-measure rather than a
hunt.)* Same gate class as WS-24 (d)'s member/role writes and as the
Subscription Console's fulfilment ·
**(d) flipping the Router ON for a real customer, and the §5.1 pooled cutover**
(§8 gate 5) — ⚠️ **the flag named in CP-4's done-when does not exist in code**
(verified 2026-08-18, repo-wide); CP-4 ships dark by having no caller, and this
gate binds the first caller ticket, which is where the flag arrives. The pooled
cutover half also carries the WS-3 T2 precondition (D16, top of this section) ·
**(e) issuing a production `cc_live_` key to a real organization** (§8 gate 6) —
`POST /keys` (`main.py:727`). Minting against fixtures is agent-safe ·
**(f) issuing a `cc_depl_` deployment key and setting it in a live deployment's
env** (§8 gate 7 — **new 2026-08-18 with CP-2b**, the sign-in↔registry wiring):
a credential issuance *and* a deployment env write, i.e. two existing gate
classes at once. The deployment key is the credential that lets a box ask the
registry about people, so a leaked or over-scoped one is a cross-deployment
question-asking capability; its capability set is deliberately `{resolve}` alone.
**Extended 2026-08-18 (F1 repair): setting `CUSTOMER_CONSOLE_RESOLVE_ENABLED=true`
on a live deployment** is the same gate — it is the Next-side switch that arms the
sign-in resolve hop (`workbench/control_plane/src/auth.ts`; ships unset = OFF,
byte-identical to pre-CP-2b sign-in). Flipping it is a deliberate availability
trade: a wired box **fails CLOSED** — every Console/gateway outage or
half-provisioned env becomes a sign-in refusal (`ConsoleUnavailable`) by design
(§6(g); the gateway route itself refuses when `is_wired()` is false, so a
half-provisioned box cannot silently admit). Flip only after BOTH sides are
provisioned: the gateway's `CUSTOMER_CONSOLE_URL` + `CUSTOMER_CONSOLE_DEPLOYMENT_KEY`,
and the Console reachable from the box ·
**(g) issuing a discount code against a live org** (D42.3, contract
`specs/subscription_console.md` SC-4g) — the SC-4e adjustment gate class:
authoring codes and the ₹0 path against fixtures is agent-safe, redeeming one
against a real organization is a revenue act ·
**(h) setting `SELF_SERVE_SIGNUP_ENABLED=true` on a live deployment, and
expanding a real deployment key's capability set beyond `{resolve}`** (§8 gate
8 — **new 2026-08-19 with CP-2c**, D46): the flag opens organization creation
to the public internet; the capability growth (`{resolve, provision}`) is
(f)'s issuance class applied to a wider credential and is CP-2c's proposed
answer to D43-2's open sub-question. Building the flow against fixtures,
minting wide keys in tests and fencing both flag positions is agent-safe ·
**(i) the `decide` vendor: TypeSafe, directly or through AI/ML API — the
account, its live key, and the first real tenant content sent to it** (§8 gate
9, **new 2026-09-23 with CP-13**, D75, **widened 2026-09-24 by CP-13h**, D75
clause 8). Opening either account is (b)'s class, an external commercial
account. Installing the key on `/providers` is §6.0 B1. Sending tenant content
to TypeSafe, directly or through AI/ML API, is a new class. D19.6 promises
India-only residency, and neither vendor states a region. A reseller call
passes through both sub-processors. Shadow mode sends the content too, so it
waits for the same answer. Building CP-13a to CP-13d and CP-13h against a test
key and made-up data is agent-safe.
⚠️ **Setting `DECIDE_ENABLED` on a live box is this gate, and the §3a dev window
does NOT open it.** It is the one switch between every tenant's content and
the `decide` vendor, and it covers the chat tool and the app slices. CLAUDE.md §3a rule 3
already says it: third parties still stop you ·
**WS-12 Phase 4.0's target choice** (minimal-bump vs full-bump,
`multi_agent_orchestration.md` §6 Phase 4.0) — a cost/schedule call, and the
reason WS-12 has **zero** dispatchable PRs. An agent may produce the 4.1
evidence and must then stop and report ·
**WS-12 Phase 4.6's manual soak** of the Copilot streaming path — an agent
cannot simulate or self-certify it, and must not mark 4.6 done without a
recorded human sign-off ·
**Calendar external sync (WS-21)** — needs Google Calendar and/or Microsoft
Graph **OAuth client credentials (client id + secret + redirect URI)
provisioned on the VPS** and registered in the Integration Registry;
`calendar_timeboxing.md` §13 P4 clause 1 ("a `calendar_accounts` row created
through a real OAuth connect flow") is unverifiable without them ·
**outbound nudge sending — one shared gate for two rows:** WS-21 §9.4's
Waiting-on chase block and WS-18's follow-up nudges both end in a real
outbound message from a real account. Drafting and queueing are AGENT-SAFE;
**sending is not**, and neither row may flip it independently ·
creating the bot Google account + real-meeting joins · Meta app review ·
real-account email sends / live-DB one-offs (`merge_ghost_messages --apply`) ·
~~**the WS-10 floor-control re-decision**~~ — **DECIDED 2026-08-10 (D25.4): CUT.**
The five `chat_session.floor_mode`s, turn queue, observer lane,
handoff-with-a-note and HITL floor-holder routing are retired unbuilt; steer
suffices. This gate is closed — an agent asked to "finish multiplayer Phase 2"
builds the S1 `subject:` slice and treats the floor-mode design as
historical ·
~~**the per-Center approvals-routing decision (WS-14 C4)**~~ — **DECIDED
2026-08-10 (D25.3): Center leads approve their Center's actions** (approver role
on the `org_group`; proposals carry a requesting-Center column; fallback to org
admins when unset). Acceptance may now be written; the ticket is "add the
column, written by every proposer" per the measurement below. Registered 2026-08-03 because it read as a UI
filter and was not one — `pending_actions` (`infra/postgres/66_pending_actions.sql:13-38`)
carries no requesting-member, group or Center **column**, so the follow-on is "answer Q2,
then add a column at the next free migration number". *(Corrected 2026-08-03: the
companion claim that `actor` never names the human is **false** — `routes/apps/tools.py:393`
and `routes/apps/actions.py:345` write `app:<slug>:<email>`. The gate stands on the real
measurement: five ad-hoc `actor` shapes, a human in two of six proposers. See
`department_centers.md` C4.)* ·
**the WS-10 `prefs`/`user` backfill APPLY** — running the classifier's output
against live Mem0 personal memories (`docs/multiplayer/memory-clearance.md` §8 Q1:
*"it should be a deliberate, communicated choice"*). The classifier itself and a
**dry-run report** are AGENT-SAFE and are the whole of the agent's mandate; the
mutating pass is a live-DB one-off ·
`test_owner_bootstrap.py` against prod (never) · any deploy that changes auth
behaviour (supervised window per `FOUNDATION_CONTINUATION.md`) ·
**the four WS-24 colleague-onboarding gates** (`specs/colleague_onboarding.md`
§1.1), registered 2026-08-04 because "invite a colleague" reads like a UI
action and is not one:


**(a) merging a Caddyfile change that alters the auth-relevant directives.**
The owner amended this gate on 2026-09-28: "Auto, except sign-in changes".
Since PR #498, every deploy installs `deploy/hostinger/caddy/Caddyfile` on the
box. So a merged Caddyfile is live on the next deploy. A routine edit merges as
usual. An edit to the auth-relevant directives is the gated act.

The fence `tests/unit/test_caddy_auth_gate.py` hashes these parts of the file:

- the header lines: `header_up`, `header_down`, `request_header`, `header`
- the sign-in blocks: `basic_auth`, `basicauth`, `forward_auth`
- what a path reaches: `handle`, `handle_path`, `route`, `rewrite`, `uri`,
  `redir`
- `tls`, `transport` and `import`, and every named matcher
- the label of every site block, and the `admin` option

The fence refuses a new hash until the owner records
`CADDY-AUTH-APPROVED <sha256>` in `.claude/OWNER_GRANTS.md`. It refuses an
`import` of a file outright, because that file comes from the box. ⚠️ A change
to `_BASELINE` in that test defeats the fence. Treat that change as this gate.

**(b) provisioning `GATEWAY_INTERNAL_TOKEN`** — a credential, and it must land
in **both** `/opt/acb/app/.env` and the workbench's `.env.local`, because the
Next BFF mirrors the same `LITELLM_MASTER_KEY` fallback
(`workbench/control_plane/src/lib/gateway.ts:58-61`); a mismatch turns every
proxied browser call anonymous. **`GATEWAY_REFUSE_LLM_KEY_IDENTITY` is a
separate owner gate of its own** — it ships OFF, defaults to today's behaviour
exactly, and flipping it while the token is unset **401s every signed-in
member** ·
**(c) installing/scheduling the BO-23 backup timer** — building the dump
script, the manifest and the restore runbook is AGENT-SAFE; installing the
systemd unit and pointing it at prod data is not ·
**(d) any write to the member/role/group tables on the live box** —
`POST /admin/members`, `PUT /admin/members/{email}/roles`,
`PATCH`/`DELETE /admin/members/{email}`, `PUT /admin/members/{email}/overrides`,
`POST`/`DELETE /admin/groups/{slug}/members`. Inviting a real person, changing
what they can see, or removing them is the owner's act. An agent may write the
runbook, the preflight and the matrix, and must stop there ·
**running `scripts/onboarding_preflight.py` against production** — the script
is agent-safe to author and its DB checks read the live database, so an agent's
only mode is `--mode local`, which refuses the box-only checks by design ·
**the four WS-26 CRM gates** (`specs/crm_app.md`), registered 2026-08-05 (d added 2026-08-07):
**(a) the Zoho two-way sync against production** — **BUILT 2026-08-05 · BACKFILL RUN
2026-08-06 · SYNC LOOP ENABLED BY THE OWNER 2026-08-06.** (The old "never run"
reading is retired; the gate is NOT — it now governs changes to a *running* loop
rather than a first switch-on.) Measured at enablement: the loop cycles every 600s;
the first cycle pushed **nothing** (no row was dirty), pulled
737/1,189/1,516/551/1,909, and left **zero** rows dirty — echo suppression held. One
defect surfaced in the first cycles and is fixed in main (PR #375): the `Deals`
watermark could never advance, because this tenant returns no `Modified_Time` for any
module and no `Created_Time` for Deals, so that module re-pulled all 551 records every
cycle. Deal conflict resolution is consequently one-sided (native-wins) by design —
spec §7.1. Building the engine was AGENT-SAFE; **turning the sync flag on, the first
backfill run, and any hand-run sync cycle were the owner's acts and remain so**: the
engine **WRITES the live Zoho tenant**
(re-scoped 2026-08-05 per spec D-CRM-7, owner-directed), pushes native edits
up, and propagates deletes in both directions. The **code floor is
`admin:access:manage`**, not `integrations:use:zoho-crm` — audit finding
2026-08-05: migration 131 grants `member` `integrations:use:*`, so the
integration slug gates nothing ·
**(b) flipping `CRM_AUTO_LEAD`** — ships OFF; ON turns unknown inbound email
senders into CRM lead rows — **each born `zoho_dirty = true`, i.e. queued for
push into the live Zoho tenant on the next sync cycle (which
`POST /crm/sync/zoho` runs with or without `CRM_ZOHO_SYNC`)**. Ruled D-CRM-9
(owner, 2026-08-06): this is intended behaviour — agent- and auto-originated
writes enter the push queue exactly like human ones. So the flip is both a live
change to email-app behaviour and, transitively, a write path into Zoho.
**The settings field now EXISTS — `crm_auto_lead: bool = False` in
`packages/acb_common/acb_common/settings.py`, beside `crm_zoho_sync`** (WS-26d-autolead,
BUILT 2026-08-08, branch `ws-26d-autolead`; the ⚠️ "does not exist yet" note is
retired). **Built, NOT flipped, NOT deployed**, and while it is off the branch
changes no runtime behaviour at all. The hook is
`routes/email/scheduler_hooks.py::process_new_mail` — the one seam the scheduler,
the manual-sync route and the webhook all funnel through — and the flag is read
at that CALL SITE, before the CRM step is entered, so the OFF state issues no CRM
query; both the runtime regression and an AST assertion that the gate is
lexically outside the step live in `tests/unit/test_crm_auto_lead.py`. ⚠️ Three
things an owner should know before flipping: **(1)** the first ON-state run per
mailbox only ACTIVATES the cursor (`crm_auto_lead_cursors`, migration 163 — renumbered at merge; 157 is
held by open PR #399) and mints nothing — mail that arrived before that instant is
history by construction, which is what stops a deep resync minting a year of leads;
**(2)** the same is true after the flag is turned OFF and back ON, or after the
service is down, for more than an hour: the cursor RE-ANCHORS, the gap's backlog
mints nothing, and the cycle says so at WARNING on `sync.auto_lead_reanchored`.
That guard was added by diff review after an OFF→ON round trip was measured minting
**27 leads for a 27-day OFF window**, each pushing unattended into the live tenant —
so turning the flag off is genuinely a stop, not a pause that accumulates. ⚠️ **With
one bounded exception the owner should expect: mail received in the FINAL HOUR before
the flag goes back on IS minted** — one gap-width of mail (an hour), drained across
however many cycles the per-cycle cap takes, not a single batch. That is deliberate —
the re-anchor clamps the epoch one hour back rather than resetting it to now, because
this step only runs when a sync persisted mail, so the cycle that detects the gap is
always the cycle carrying the message that woke it; resetting would drop that message
every single night. If the OFF window's tail matters, flip the flag on at a quiet
moment; **(3)**
the accepted residual is that two concurrent syncs of one account can double-mint one
visible, hand-deletable duplicate (a UNIQUE index on `crm_leads.email` is refused:
1,516 imported rows may already carry duplicates, the migration-148 shape). One
operational note: a cycle that logs `sync.auto_lead_stalled` at WARNING every cycle
means a message at the head of the queue cannot be written and the cursor is
deliberately held — that is fail-closed toward the CRM and it needs a human, not a
restart ·
**(c) the WS-26e cutover + retirement** — the final import + parity check,
repointing the graph-mirror consumers (`sales_views.py`, `reconciler.py`),
retiring `ingestion/sources/zoho/` + cron + webhook + config (spec §7.4, which
includes an `.env.example` edit that plan-guard already blocks), and **revoking
the Zoho refresh token** — the act that executes part of WS-2's standing P0 ·
**(d) applying the WS-26f stage-metadata repair against prod**
(`POST /crm/import/zoho/stages?apply=true`) — it rewrites the live pipeline's lane
order, stage types and probabilities in one call, and the board every `feature:crm`
holder sees reorders under them; the dry-run (no `apply`) is agent-safe and is how the
proposal reaches the owner. If the tenant returns more than one pipeline the repair
must STOP unapplied (spec D-CRM-11). Re-minting the Zoho token with `settings.*` scopes,
should the probe report no-scope, is likewise the owner's act ·
**the five WS-27 Projects gates** (`specs/project_management_app.md`), (a)–(d)
registered 2026-08-05, (e) added 2026-08-08. ⚠️ **RE-CUT 2026-08-24 by D52 — read
this paragraph before the lettered ones.** (a) and (b) are **DEAD**: D52 deletes both
import endpoints and the whole outbound-push arm, and a gate over code that does not
exist teaches an agent to refuse the wrong thing. (c) **narrows** to the two acts that
are still the owner's — revoking the ClickUp tokens, and dropping ClickUp-sourced
columns on a live database. (d) is untouched. Their bodies are **kept struck rather
than deleted**, because the reasoning in (a) — *"the import is not wrong, it is
early"* — is the reusable part and the next connector will re-raise it verbatim ·
🆕 **(f) the D53 `gtd_*` → `pm_*` data move (WS-39 S3b) and the `gtd_*` drop (S3c)**
*(registered 2026-08-24 with D53.5)* — the same class as WS-29's pooled cutover: a
migration that **moves live customer task rows** between stores, and a later one that
**drops the source tables**, on a ladder that **cannot roll back** (R6). Building both
and R8-testing them against scratch databases is **AGENT-SAFE and unblocked**; running
either against a real database is the owner's act. ⚠️ The failure mode is specific and
worth naming: S3b writes into each member's **personal project**, so a mis-mapped
`member_email` does not lose a task — it publishes one person's private task into
somebody else's lens. Prove the mapping two-org on scratch before it is offered ·
~~**(a) running either ClickUp import endpoint against the production workspace**~~
**— DEAD 2026-08-24 (D52.2): both endpoints are deleted.** Struck body kept for its
reasoning:
~~⚠️ **ALSO BLOCKED ON WS-29a AS OF 2026-08-08**~~ — **LIFTED the same day:
migration 158 keyed all seventeen `pm_*` tables, which was the reason to
wait.** ⚠️ Two conditions replace it: migration 158 **must be applied to the
target database first** (it is on no real box yet — the deploy path is
broken, WS-25), and the mapping decision below still stands. Kept struck
because the reasoning is the reusable part: Metorite is becoming
multi-tenant and all seventeen `pm_*` tables carry no `organization_id`
(`specs/multi_tenancy.md` §2). Importing a real workspace now writes hundreds
of tasks, activities, attachments and grants into unscoped tables, which turns
a one-line default on empty tables into a backfill plus an `ALTER` on live
rows. The import is not wrong, it is **early**: land WS-29a first. —
building both is AGENT-SAFE; executing them is not. `POST /projects/import/clickup/plan`
writes nothing to our DB but **reads the live ClickUp tenant** and spends LLM
budget classifying it; `POST /projects/import/clickup` writes the live DB, and
during coexistence a re-import is last-import-wins on ClickUp-sourced fields
(spec §7.1). **Confirming the Space→Center mapping is itself the owner's act
(D-PM-10):** an agent may propose the mapping and must not apply one, because a
wrong map grants a Center visibility of another department's work. Code floor is
`admin:access:manage`, per the WS-26b finding that `integrations:use:*` gates
nothing ·
~~**(b) enabling the WS-27c outbound push**~~ **— DEAD 2026-08-24 (D52.1): WS-27c is
cancelled and the push arm is deleted.** ⚠️ Note what does NOT die with it:
`ACTION_BROKER_ENFORCE` is still blocked on **BO-1d** and stays in the flag list above
— BO-1d is about four callers mis-reading `_broker_gate`'s pending marker, which is a
broker defect that outlives ClickUp. Struck body: against the real workspace — the sync's
ClickUp writes flow through `_broker_gate`. BO-1a + BO-1b landed 2026-08-11 (approving
a delete no longer marks the row `failed`; an ignored pending marker no longer shows a
green "synced" task that exists in no workspace), so WS-27c is buildable — but
**`ACTION_BROKER_ENFORCE` is now blocked on BO-1d**, the four callers that still index
the gate's pending marker as a result (three hard-500, one silently swallowed) ·
**(c) the WS-27g cutover + retirement — NARROWED 2026-08-24 (D52.1) to two acts.**
What is left of this gate, and all that an agent must refuse by name:
**(c-1) revoking the ClickUp tokens** wherever they are stored — the live
`integration_credentials` rows and any `CLICKUP_*` value in a deployment's env. A
credential act, the §6 `secrets`/`env-write` class. WS-39 S1 deletes every *reader*
of those tokens, which makes revocation safe to do at leisure and **does not do it** —
a token nobody reads is still a live token at ClickUp until somebody revokes it there.
**(c-2) dropping the ClickUp-sourced columns on a live database** — D52.3 keeps them
deliberately (R6: the deploy migrates before it restarts), so the drop is a later
release and the owner's call.
~~The rest — final import + parity sign-off, flipping the sync to pull-only then off,
repointing the graph-mirror consumers off the ClickUp arm, retiring
`ingestion/sources/clickup/` + the `ClickUpProvider` arm + `skill-clickup-sync` +
catalog/OAuth entries~~ — **all of it is now AGENT-SAFE deletion in WS-39 S1**: with
D52 there is no external system to sign parity off against, so what was a cutover is
just removing code. ~~and the root-`AGENTS.md` constraint-8 amendment (Metorite becomes
the PM system of record) — that amendment ships in the WS-27g PR, never before~~ —
**struck: D52.4 moves that amendment into the WS-39 S1 PR, because the PR it was
reserved for will never exist** ·
**(d) granting `feature:projects` or `data:org:read` to any real member** on the
live box — the same member/role-table write rule as WS-24 (d); the full-portfolio
view is deliberately `data:org:read`'s first consumer, so granting it now grants
visibility that previously granted nothing. ·
~~**(e) answering D-PM-12 — whether a `blocks` dependency CONSTRAINS the schedule
or only describes it**~~ **ANSWERED 2026-08-08 and the gate is CLEARED.** The owner
was given the three options and their costs and delegated the choice back
(*"go ahead with the decision that you think would be best"*); recorded as
**D-PM-12 = (c) constrain-and-warn**, so WS-27p's "derived and shown, never
enforced" stands unamended and no cascade of writes was introduced. Kept struck
rather than deleted because the shape of the gate is the reusable part: an agent
must still refuse to make `blocks` **push dates** — moving to option (b) is a new
owner decision, not an extension of this one, and it would strike WS-27p's
paragraph rather than sit beside it.
