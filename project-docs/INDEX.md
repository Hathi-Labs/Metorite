# INDEX — what is active, what is not (the classification of record)

**Created 2026-08-10 (D26, owner-directed consolidation).** This folder was
renamed from `ai-company-brain/` to **`project-docs/`** the same day — it holds
the project-management documentation: the work plan, the specifications for
every sub-app, and the overall architecture. *(The owner's suggested name
"Project Management documentation" was realized path-safe and short; "PM docs"
was avoided because the Projects app's own spec lives inside and the collision
would confuse exactly the agents this cleanup serves.)*

**How an agent navigates:** the repo root **`CLAUDE.md`** is loaded into every
session (D30) and routes you here; then start at **`work_plan.md` §1** (the spec contract and
the standing rules **R1–R8** — R6/R7/R8 are the engineering-practice rules from
D28 and bind every PR), then **§2** (the dispatch board — ordering, states,
gates). ⚠️ **Start at §2.0 if you want the shape of the work rather than a ticket** — it is the product roadmap (M0…M4) over the same rows, added 2026-08-26. The board row names the **owning spec**; build
only from owning specs listed **ACTIVE** here. Anything in DEFERRED or
HISTORICAL is banner-marked and dispatches nothing. `work_plan.md` §6 is the
owner-gate registry an agent must refuse by name. Decisions (**D1–D75**, current 2026-09-23, with D74 reserved by `credit_pricing.md` — this read "D1–D54" until then) live in
`work_plan.md` §3 and are never re-litigated in specs.

**The two documentation roots:** `project-docs/` (this folder) = plan +
product/app specs + architecture. **`docs/`** (repo root) = engineering
reference tied to code (`docs/multiplayer/` — ⚠️ contains one ACTIVE owning
spec, listed below — `docs/workflow-editor/`, `docs/app-workshop/`, design
limitations). Do not add product specs to `docs/`.

---

## ACTIVE — owning specs of live board rows (dispatch happens from these)

| Spec | Board row(s) |
|---|---|
| `work_plan.md` | THE BOARD — §1 contract · §2 rows · §3 decisions · §4 owners · §6 gates |
| `HANDOFF.md` | THE QUEUE (D39) — what the last session left unfinished, injected at session start by `.claude/hooks/session-handoff.mjs`. ⚠️ **Actions, never state**: `work_plan.md` §2 stays the only current-state authority, and every entry carries a **Check** that re-derives whether it is still real. Sessions delete entries whose Check passes; `/handoff` is the workflow. |
| `GO_LIVE_RUNBOOK.md` | HISTORICAL (2026-08-23) — the multi-tenant RLS cutover sequence for Fractalworks + customer #2. ⚠️ **Not current instructions**: it carries a dated supersession header naming four claims the tree has moved past (D49 retired Center packages, D52 removed ClickUp, PRs #61–69 all merged). Kept for the cutover design and its reasoning, which nothing else records. |
| `../FOUNDATION_BUILDOUT_CHECKLIST.md` *(repo ROOT)* | WS-1 · WS-4 · WS-5 |
| `specs/saas_multitenancy.md` · `specs/saas_multitenancy_implementation.md` · `specs/saas_multitenancy_handover.md` | WS-29 — hand the **handover** to the executing agent. *(All three named in full: the shorthand `(+ _implementation, _handover)` read as complete to a human and as a gap to `test_index_completeness.py`, which is the one reader that cannot ask.)* |
| `specs/customer_console.md` | WS-31 — the central subscription/seat/AI-metering service (D32). 🆕 ⭐ **§6A.14 is CP-13** (D75, 2026-09-23): the `decide` task, TypeSafe's Jev behind `POST /v1/decide`, the Operator Console first and the app layer second. CP-13a to CP-13d are BUILT (2026-09-23 and 2026-09-24), and CP-13e to CP-13g are spec only. ⭐ **§6A is the owning record of the AI-management refactor** (D56, 2026-08-26): the artefact-by-artefact inventory for moving model, provider-key and AI-subscription administration **out of the Command Center and into the Operator Console**, reuse-first. **CP-10** is the ticket, and **CP-5** is its removal half. ⭐ **§6B is CP-11** (D57, 2026-08-26) — **the serving hop**: credits, the two different "AI API keys", and the measured fact that **nothing calls the Router**, which is what makes operator configuration inert today. **Order: CP-10 s1 → CP-11 → rest of CP-10.** `work_plan.md` §4 names this spec the single owner of *AI model & provider administration*. |
| `specs/customer_console_infrastructure.md` | WS-31 — **where** it runs; owner decision session (Supabase/Azure/VPS; Firebase disqualified in §3) |
| `specs/ai_metering_and_analytics.md` | WS-31 — **the AI tier vocabulary a customer sees, and every surface that reports AI use** (owner directive, 2026-08-29). Owns **D-AI-1** (a tier has a permanent slug and a display label, so a label may change without a slug rename that would break every past invoice), **D-AI-2** (an image follows the chat model when that model declares `vision`, and falls to `tier-vision` when it does not — the second call is the cost), **D-AI-3** (`tier-stt`/`tier-tts`/`tier-embed` never reach a customer picker) and **D-AI-4** (an app declares a default tier). §2 is the measured state, §6 proposes seven analytics nobody asked for — **A1 margin per organization is the one that matters** — and §8 is the slice order. ⚠️ §7 lists the five owner gates that keep `usage_event` at 0 rows, so every surface here ships to an empty table and must say so. |
| `specs/credit_pricing.md` | WS-31 — **the economics of one credit** (owner directive, 2026-09-04, from the credit-pricing design document). Owns what a credit is worth (**PEG**), what margin we take (**M**, per tier), and how we hold and settle one charge. Proposes **D71** (bill from the peak vendor rate always, and keep the off-peak rate for reporting), **D72** (the customer card moves to credits per million tokens), **D73** (margin is per tier, never global) and **D74** (do credits expire — owner call). ⚠️ **Owns the economics only** — `customer_console.md` §6A.11 to §6A.13 keep owning the vendor feed, `tier_rate_card` and `credit_price`, and `ai_metering_and_analytics.md` keeps owning the tier vocabulary and every reporting surface. 🔴 **§8 maps every table the design document proposes onto the live table that already holds it** — six of the eight already exist, and building them fresh would be a second seam (CLAUDE.md §4). Slice 1 (the partition assert) is the one live defect: measured 2026-09-04, a sibling-convention usage report undercharges by 27 percent in silence. |
| `specs/operator_identity_and_access.md` | WS-31 — **CP-12** (D64, 2026-08-26, **amended by D70 on 2026-09-01**): **who a platform operator is, what each may do, and what the audit log says afterwards.** Staff sign-in through Supabase Auth + the **Google Workspace** provider, pinned to our directory by the `hd` hosted-domain claim *(this said "Microsoft" until D70 — we have no Entra directory, and this console refuses email OTP)* · the `viewer`/`editor`/`admin` matrix · adding and removing operators · time-boxed elevation and an alerted break-glass path · a real actor in `control_audit` instead of the literal `operator`. ⚠️ **Owns the staff side only** — `customer_console.md` CP-8 keeps owning the customer-management surface, and this spec adds no route to it. ⚠️ **D64.5: operators reach the commercial record only** — no tenant content, no impersonation. |
| `specs/subscription_console.md` | WS-30 — ⚠️ SC-1/SC-2's "Centers & add-ons panel" and "users × Centers seat grid" are superseded by D49: one flat plan, so the grid is one column. `specs/launch_surface.md` §6.2 owns the surface. |
| `specs/organization_identity.md` | WS-32 — the customer's own mark inside the product (logo · display name · branding on invoices) |
| `specs/marketing_site.md` | WS-33 — the public face on the apex (`metorite.com`): one static page whose CTA lands on CP-2c's signup flow (D46) |
| `specs/launch_surface.md` | WS-34 — **the launch surface of record (D49)**: which apps go live, which stay `preview`, flat ₹500/user/month pricing, the seat lifecycle a customer admin drives, and the nav-resolution contract. ⚠️ Supersedes `saas_multitenancy.md` §2.4b as the **pricing shape of record** and withdraws the Centers surface (the code, features and group grants all stay). |
| `specs/future_modules_roadmap.md` | D21 roadmap (no rows until specced) |
| `specs/crm_app.md` | WS-26 |
| `specs/project_management_app.md` | WS-27 · **WS-39** — ⚠️ **§12 is the 2026-08-24 re-cut (D52/D53) and wins over §6/§7/§11**: ClickUp retired outright (no connector, no importer, no sync), and `pm_tasks`/`pm_task_personal` are the **only task store in the product**. §7's whole migration path is superseded. §11 is history, not a plan. Owns **D-PM-38** (how a view shows a subtask: one Nested/Separate/Hidden setting, the "↳ Parent" crumb, and the owner's four decisions of 2026-09-26). ⚠️ D-PM-38 is ACTIVE and binding: its rule is §12.9, and §11.38 is only its build record. |
| `specs/projects_ai_chat.md` | WS-27 · **WS-27bm** — the AI chat inside the Projects app (owner directive 2026-09-22). One chat seam, one confirmation seam, one authorization rule. Owns **D-PM-35** (the chat cannot hard-delete until WS-40 answers who may), **D-PM-36** (a chat write carries `meta.via`) and **D-PM-37** (every Projects route is mapped or excluded in `skill_projects.manifest`, and `tests/unit/test_projects_chat_coverage.py` fails otherwise). §10 is the slice order. S1 (the reads) and S2 (the fifteen daily writes, each behind a card) built 2026-09-22. S2b to S6, and S7a (team capacity), built 2026-09-23. S7b (fit and rebalancing), S7c (conflicts) and S7d (plan with capacity, no phases) built 2026-09-24. S8 (documents and downloads from the chat, §14) built 2026-09-24. S9 (entity pills in the chat, §15) built 2026-09-24. S7e (on-the-fly analysis, §13.7) built 2026-09-24. S10 (chat follow-ups, §16) built 2026-09-25. S11 (the Forecast reads the schedule, §17) built 2026-09-26. §4.4 is the reuse map: the chat extends the shared chat, AG-UI and confirmation stack and adds no seam. |
| `specs/projects_reports.md` | WS-27 · **WS-27bn** — the reporting system of the Projects app (owner directive 2026-09-24). R1 BUILT 2026-09-24 (the builder and the preview route). R2 BUILT 2026-09-24 (the templates and the Reports home). R2b BUILT 2026-09-24 (the visual report body: the Analytics panels, four tiles and text bars in the email). R3a BUILT 2026-09-25 (the `outlook` section, the ageing bands in `stuck`, and T5 live). R3b BUILT 2026-09-25 (the `rebalance` section, read only). R3c BUILT 2026-09-26 (the `hygiene` section, and T13 live). R3d next. R3d to R9 are spec only. A report is a template, a scope, a period and a delivery. The morning report is template T1, "Team pulse". It adds a builder, templates, four sections, a person and team scope, stored runs, an AI summary on request and "Email this report", and no second arithmetic: every number comes from the S7 reads in `projects_ai_chat.md` §13 and from `analytics.py`. §7.1 owns who may report on whom. Phase 1 renders on request only, and a timer and Workflows come later. §9 records the owner's answers of 2026-09-24. |
| `specs/people_center_app.md` | WS-28 |
| `specs/department_centers.md` | WS-13 · WS-14 · WS-15 · WS-16 — **all four PARKED by D49** (2026-08-24): the Centers *surface* is withdrawn, so nothing dispatches from this spec today. It stays ACTIVE as the **design record** and §5 remains the Center roster of record (D22) — `lib/centers.ts`, the `center.*` features and the `group:<slug>` slice grants are all still live. See `specs/launch_surface.md` §5. |
| `specs/colleague_onboarding.md` | WS-24 |
| `specs/deploy_delivery_path.md` | WS-25 |
| `specs/email_app_master_plan.md` | WS-17 |
| `specs/task_manager_app.md` | WS-18 · **WS-39** — ⚠️ **§13 (D53) wins over the body**: Tasks is the **personal lens over Projects**, not an app with its own store. `gtd_*` is retired; the `gtd_*` schema described above is the app *as built*, not a build target. |
| `specs/my_tasks_cutover.md` | **WS-39 phase 2** — the Tasks app becomes **My Tasks** (D73, 2026-09-23). Owns the lens tail (S6a-d), the cutover (S7), the `gtd_*` drop and the schema rename (S8), and the code-name sweep (S9). Wins over `task_manager_app.md` §13 where they disagree. |
| `specs/note_taker_app.md` + `specs/meeting_bot_platform_plan.md` | WS-19 |
| `specs/whatsapp_message_manager.md` | WS-20 |
| `specs/calendar_focus_os.md` + `specs/calendar_timeboxing.md` | WS-21 · **WS-39** — ⚠️ **§10 (D54)**: Calendar becomes its own `live` pane at `/calendar` under Personal Center. 🔴 **Correction both these specs carry:** `gtd_time_blocks` and `calendar_accounts` **do not exist** (measured 2026-08-24) — `calendar_timeboxing.md` §13 P4 cites them as built. The calendar persists to `gtd_items` directly, plus `user_settings`/`calendar_day_state`/`calendar_rollover_log`. |
| `specs/skills_registry.md` + `specs/skills_scope_out.md` | WS-23 |
| `specs/workflows_app.md` | WS-11 |
| `specs/multi_agent_orchestration.md` | WS-12 — **Phase 4 ONLY** (D6); rest superseded |
| `specs/agent_architecture.md` | WS-8 |
| `specs/memory_architecture.md` | WS-9 |
| `specs/observability_e2.md` | WS-6 |
| `specs/permissions_sandbox_b6.md` | WS-3 |
| `../docs/multiplayer/memory-clearance.md` *(in `docs/`!)* | WS-10 (S1 only — floor control CUT, D25.4) |

## ⚠️ BOARD ROWS WITH NO OWNING SPEC — a gap, listed so it is visible

| Board row | What is missing | Why it matters |
|---|---|---|
| **WS-36 — per-tenant restore** | No spec anywhere. Nearest prose: `saas_multitenancy.md` §6.6, `saas_multitenancy_handover.md` H8, `specs/backup_and_restore.md` (whole-cluster only) | §1's contract cannot be satisfied without one, so the row is 🔴 not dispatchable. Writing the spec is the ticket |
| **WS-37 — trust & compliance** | No spec. Nearest prose: `saas_operations_doctrine.md` §2.7 + §3.3 | Same. ⏳ §3.3 carries a **November 2026** DPDP date that is not ours to move |

*(Both were named **unowned** by `saas_operations_doctrine.md` §5 on 2026-08-12 and carried no board row for fifteen days. The rows exist as of 2026-08-26; the specs do not.)*

## CONTRACTS & DOCTRINE — binding rules; read before building, no rows of their own

| Spec | Role |
|---|---|
| **`specs/saas_operations_doctrine.md`** | **HOW a SaaS platform is run (D33): the eight capability domains · the Indian GST/RBI-e-mandate/DPDP layer that changes product design, not just paperwork · the twelve-finding audit of what Metorite assumed as a personal brain · the gap table. §4's verdicts and §6's ordering BIND; §2 is advisory.** |
| **`specs/development_and_delivery_framework.md`** | ✅ **ADOPTED 2026-08-26 by D55 — board row **WS-38** (see ACTIVE above). ⏳ Carries **Phase 0** (§3.5: direct-to-production until customer #2 / a second contributor / H3) and the **environment parity matrix** (§3.4) and the **end-to-end contributor workflow** (§7.7). 🔴 D-D and D-F still owed.** How we ship to live customers while still building: branching (trunk + two fast-forward promotion refs; **no long-lived `develop`**), the staging environment as a *nightly re-derivation* of production rather than a maintained copy (this is P-1), the **two delivery planes** (tenant ladder vs. the Customer Console's unwired one), what CI must gate, how the operator dashboard / seats / AI credits are proven, and what a second developer needs. **Amends `engineering_practice.md` §1's "no staging" on a premise change, and that amendment is the owner's to record (§9 D-A).** **WS-38 minted 2026-08-26** — T-1 → T-2 → T-3/T-6 → T-5 is the order; T-7/T-8/T-9 wait on D-D. |
| **`specs/engineering_practice.md`** | **HOW we build (D28): environments, deploy≠release + rings, expand/contract migrations, what a test is worth when an agent wrote it, agent work-partitioning, security once users are not colleagues, definition of done. Its binding rules are R6/R7/R8 in `work_plan.md` §1 — read those first if you read nothing else.** |
| `specs/user_management_contract.md` | The rules every app must follow (identity, lifecycle, permissions) |
| `specs/org_access_control.md` | The access model of record |
| `specs/tenancy_and_visibility.md` | §2–§5 visibility doctrine (D12) — ⚠️ §1/§6 SUPERSEDED by D15 |
| `specs/groups_sessions_authority.md` | Session authority + intersection rule |
| `specs/generative_ui_2.md` | Chat HITL model (shipped doctrine) |
| `AGENTS.md` | This folder's guide + spec index |
| `agent_repo_compatibility.md` | Agent-repo contract |
| `specs/backup_and_restore.md` | BO-23 operations + restore runbook |

## REFERENCE — background; may be stale, verify before relying

`system_architecture.md` · `reference.md` *(both carry stale-warning
banners)* · `project_plan.md` *(superseded for sequencing by `work_plan.md`)* ·
`specs/core_module_map.md` *(internal engineering subsystems — NOT the product
module map; that is `saas_multitenancy.md` §2.4b)* ·
`agents-workspaces-artifacts.md` · `specs/agent_file_and_memory_framework.md` ·
`specs/agent_persistence_implementation.md` · `specs/agent_coding_skill.md` ·
`specs/llm_caching_memory.md` · `specs/mcp_plugin_integration.md` *(Phase A
shipped; B/C research)* · `specs/task_manager_hr_planning_and_memory.md` *(HR
data layer WS-28 reads)* · `specs/multi_tenancy_leak_audit.md` *(the 14-finding
audit feeding WS-29 MT-1i)* · `specs/multi_tenancy.md` *(#399's measured
record — SUPERSEDED for architecture, banner at top)*

## DEFERRED / PARKED / HISTORICAL — banner-marked; nothing dispatches from these

| Spec | Why |
|---|---|
| `specs/drawio_integration.md` + `specs/drawio_diagram_svc_contract.md` | ⏸ PARKED by owner (D25.7) |
| `specs/chat_ux.md` | Superseded by generative_ui_2 §2 (protocol reference; §12 VII–XI items open but unscheduled) |
| `specs/live_meeting_copilot.md` · `specs/whatsapp_calls_note_taker.md` | Future scope, no board row |
| `specs/multi_user_organization_research.md` · `specs/note_taker_research_2026-07.md` · `specs/paca_pm_research_2026-08.md` · `specs/plane_pm_research_2026-08.md` · `specs/multiplayer_prior_art_qm_2026-08.md` | Research inputs — reference-only by their own declaration |
| `specs/agent_platform_hardening_2026-07.md` · `specs/competitive_hardening_2026-07.md` · `specs/harness_hardening_2026-07.md` · `specs/task_manager_harness_2026-07.md` · `specs/single_agent_chat_bug_audit_2026-07.md` · `specs/chat_agent_framework_review_2026-07.md` | 2026-07 audit/build records — history, superseded where D15/D25 touched them |
| `specs/calendar_ai_review.md` · `specs/calendar_ux_review.md` | Calendar sub-docs — consult via `calendar_focus_os.md` §9 (ux_review is the sole home of the block-reminders item) |
| `HANDOVER.md` | #399's branch handover — EXECUTED (merged 2026-08-09); historical |
| `archive/` | Pre-consolidation archives |

**Rule (binds all future docs):** a new spec enters this INDEX in the same PR
that creates it, in exactly one section. A spec leaving ACTIVE gets a banner in
the same PR. An agent finding a spec absent from this INDEX should treat that
as a defect and say so.
