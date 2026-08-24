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
gates). The board row names the **owning spec**; build
only from owning specs listed **ACTIVE** here. Anything in DEFERRED or
HISTORICAL is banner-marked and dispatches nothing. `work_plan.md` §6 is the
owner-gate registry an agent must refuse by name. Decisions (D1–D26) live in
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
| `../FOUNDATION_BUILDOUT_CHECKLIST.md` *(repo ROOT)* | WS-1 · WS-4 · WS-5 |
| `specs/saas_multitenancy.md` (+ `_implementation`, `_handover`) | WS-29 — hand the **handover** to the executing agent |
| `specs/customer_console.md` | WS-31 — the central subscription/seat/AI-metering service (D32) |
| `specs/customer_console_infrastructure.md` | WS-31 — **where** it runs; owner decision session (Supabase/Azure/VPS; Firebase disqualified in §3) |
| `specs/subscription_console.md` | WS-30 — ⚠️ SC-1/SC-2's "Centers & add-ons panel" and "users × Centers seat grid" are superseded by D49: one flat plan, so the grid is one column. `specs/launch_surface.md` §6.2 owns the surface. |
| `specs/organization_identity.md` | WS-32 — the customer's own mark inside the product (logo · display name · branding on invoices) |
| `specs/marketing_site.md` | WS-33 — the public face on the apex (`metorite.com`): one static page whose CTA lands on CP-2c's signup flow (D46) |
| `specs/launch_surface.md` | WS-34 — **the launch surface of record (D49)**: which apps go live, which stay `preview`, flat ₹500/user/month pricing, the seat lifecycle a customer admin drives, and the nav-resolution contract. ⚠️ Supersedes `saas_multitenancy.md` §2.4b as the **pricing shape of record** and withdraws the Centers surface (the code, features and group grants all stay). |
| `specs/future_modules_roadmap.md` | D21 roadmap (no rows until specced) |
| `specs/crm_app.md` | WS-26 |
| `specs/project_management_app.md` | WS-27 · **WS-39** — ⚠️ **§12 is the 2026-08-24 re-cut (D52/D53) and wins over §6/§7/§11**: ClickUp retired outright (no connector, no importer, no sync), and `pm_tasks`/`pm_task_personal` are the **only task store in the product**. §7's whole migration path is superseded; §11 is history, not a plan. |
| `specs/people_center_app.md` | WS-28 |
| `specs/department_centers.md` | WS-13 · WS-14 · WS-15 · WS-16 — **all four PARKED by D49** (2026-08-24): the Centers *surface* is withdrawn, so nothing dispatches from this spec today. It stays ACTIVE as the **design record** and §5 remains the Center roster of record (D22) — `lib/centers.ts`, the `center.*` features and the `group:<slug>` slice grants are all still live. See `specs/launch_surface.md` §5. |
| `specs/colleague_onboarding.md` | WS-24 |
| `specs/deploy_delivery_path.md` | WS-25 |
| `specs/email_app_master_plan.md` | WS-17 |
| `specs/task_manager_app.md` | WS-18 · **WS-39** — ⚠️ **§13 (D53) wins over the body**: Tasks is the **personal lens over Projects**, not an app with its own store. `gtd_*` is retired; the `gtd_*` schema described above is the app *as built*, not a build target. |
| `specs/note_taker_app.md` + `specs/meeting_bot_platform_plan.md` | WS-19 |
| `specs/whatsapp_message_manager.md` | WS-20 |
| `specs/calendar_focus_os.md` + `specs/calendar_timeboxing.md` | WS-21 · **WS-39** — ⚠️ **§10 (D54)**: Calendar becomes its own `live` pane at `/calendar` under Personal Center. 🔴 **Correction both these specs carry:** `gtd_time_blocks` and `calendar_accounts` **do not exist** (measured 2026-08-24) — `calendar_timeboxing.md` §13 P4 cites them as built. The calendar persists to `gtd_items` directly, plus `gtd_settings`/`gtd_day_state`/`gtd_rollover_log`. |
| `specs/skills_registry.md` + `specs/skills_scope_out.md` | WS-23 |
| `specs/workflows_app.md` | WS-11 |
| `specs/multi_agent_orchestration.md` | WS-12 — **Phase 4 ONLY** (D6); rest superseded |
| `specs/agent_architecture.md` | WS-8 |
| `specs/memory_architecture.md` | WS-9 |
| `specs/observability_e2.md` | WS-6 |
| `specs/permissions_sandbox_b6.md` | WS-3 |
| `../docs/multiplayer/memory-clearance.md` *(in `docs/`!)* | WS-10 (S1 only — floor control CUT, D25.4) |

## CONTRACTS & DOCTRINE — binding rules; read before building, no rows of their own

| Spec | Role |
|---|---|
| **`specs/saas_operations_doctrine.md`** | **HOW a SaaS platform is run (D33): the eight capability domains · the Indian GST/RBI-e-mandate/DPDP layer that changes product design, not just paperwork · the twelve-finding audit of what Metorite assumed as a personal brain · the gap table. §4's verdicts and §6's ordering BIND; §2 is advisory.** |
| **`specs/development_and_delivery_framework.md`** | 🟠 **PROPOSED 2026-08-24 — binds nothing yet.** How we ship to live customers while still building: branching (trunk + two fast-forward promotion refs; **no long-lived `develop`**), the staging environment as a *nightly re-derivation* of production rather than a maintained copy (this is P-1), the **two delivery planes** (tenant ladder vs. the Customer Console's unwired one), what CI must gate, how the operator dashboard / seats / AI credits are proven, and what a second developer needs. **Amends `engineering_practice.md` §1's "no staging" on a premise change, and that amendment is the owner's to record (§9 D-A).** Proposes **WS-38** — not minted, so nothing dispatches from here yet. |
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
