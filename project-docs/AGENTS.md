# AGENTS.md — Planning Folder Navigation Guide

> **For AI agents:** Read this file first. It tells you what this project is and which file to read for each concern. **For what is built and what to do next, this file deliberately owns nothing:** `work_plan.md` §2 is the dispatch board; each owning spec's status header is the completion record (rule R4). A status table that lived here went stale and lied — it was retired on 2026-08-09 (work_plan.md §5 residual 1).
> **Organisation:** Fracktal Works · **Project:** Metorite · **Last updated:** 2026-08-09

---

## What Metorite Is

Metorite is a **headless, self-mutating agent orchestration platform** for running a company — and, since 2026-08-08, **a product being prepared for sale to other companies** (WS-29, decision D15: tenant = `organization_id` row isolated by Postgres RLS; a deployment is a placement, not a tenant boundary; see `specs/saas_multitenancy.md`).

When a company event fires (webhook from ClickUp/Zoho, cron schedule, or ambient signal), it:
1. Resolves the target specialist agent (persistent local clone or in-repo `apps/agents/*`).
2. Runs `git pull --ff-only` to pick up merged changes.
3. Injects credentials from the Integration Registry into the MAF orchestration context.
4. Executes the agent task (skills as MAF tools or MCP servers, `HandoffBuilder` routing).
5. On failure: spawns an isolated Copilot SDK mutation container, applies a tested fix, opens a GitHub PR as audit record.

Operators interact via a thin **Control Plane** (Next.js) with chat Q&A, HITL approvals, and observability. There is no in-app agent/skill editor — all authoring happens in VS Code + Git.

## Where state lives (read in this order)

1. **Root `AGENTS.md`** — global constraints (11, including D15 tenancy rules) and the DOX contract.
2. **`work_plan.md`** — the dispatch board: WS-0…WS-29 rows (§2), the agent-ready spec contract + standing rules **R1–R8** (§1 — R6/R7/R8 are the engineering-practice rules, D28), decisions D1–D28 (§3), single-owner registry (§4), remediation record (§5), owner-gate registry (§6). **For ordering and ownership it wins over every spec, including `project_plan.md` §6.**
3. **The owning spec** for your concern — see the index below. Its status header is authoritative for that feature's state.
4. `FOUNDATION_BUILDOUT_CHECKLIST.md` (repo root) — foundation items BO-1…BO-23.

Milestone history, kept to one line: M1 core engine 2026-05-25 · M2 self-mutation + multi-agent 2026-06-12 · M2.5–M2.9 (streaming, hardening, tool injection, memory, email) through 2026-07 · foundation audit + app buildout 2026-07/08 · WS-29 multi-tenancy started 2026-08-08.

---

## File Index — What to Read for Each Concern

| Concern | File |
|---|---|
| **What order, who owns it, what's gated** (single source) | [`work_plan.md`](work_plan.md) |
| **HOW we build** — environments, release rings, migrations, testing, agent work-partitioning, security posture, definition of done | [`specs/engineering_practice.md`](specs/engineering_practice.md) (D28; binding rules R6/R7/R8) |
| **Requirements + long-horizon roadmap** (sequencing yields to work_plan) | [`project_plan.md`](project_plan.md) |
| **System design: containers, data model, ADRs** (⚠️ stale-warning in header) | [`system_architecture.md`](system_architecture.md) |
| **How to maintain an existing external agent repo** (⚠️ superseded premise) | [`agent_repo_compatibility.md`](agent_repo_compatibility.md) |
| **Library notes: MAF, Copilot SDK, memory** (⚠️ stale-warning in header) | [`reference.md`](reference.md) |
| **Workspace / artifact model for agents** | [`agents-workspaces-artifacts.md`](agents-workspaces-artifacts.md) |
| **Per-feature specs** | [`specs/`](specs/) — index below |

### Per-feature specs (`specs/`)

Status: 🟢 live/shipped · 🔄 in progress · 🔲 planned/not started. *(Index completed 2026-08-09 — 16 missing rows added; statuses are one-line pointers, the spec's own header wins.)*

**Only forward-looking / living specs are listed.** Shipped-or-historical specs live in [`specs/archive/`](specs/archive/README.md). Foundation status of record is `FOUNDATION_BUILDOUT_CHECKLIST.md` (BO-*).

| Spec | Concern | Status |
|---|---|---|
| [`saas_multitenancy.md`](specs/saas_multitenancy.md) | **⭐ SaaS multi-tenancy (WS-29)** — architecture of record for selling Metorite: tenancy = `organization_id` + RLS at the connection seam (D15), modules/entitlements, AI credit resale, billing; §6 blockers; §11 tickets MT-0…MT-5 | 🟢 architecture of record (2026-08-08); Phase 0 built; H1 scratch-verified 2026-08-09, prod apply = PR #404 |
| [`saas_multitenancy_handover.md`](specs/saas_multitenancy_handover.md) | **⭐ WS-29 execution runbook** — H1→H8 with gates; §0 paste-ready brief; H2 (561 call sites) is the long pole; H2-before-H3 is non-negotiable | 🟢 in execution — H1 scratch gate passed 2026-08-09 |
| [`saas_multitenancy_implementation.md`](specs/saas_multitenancy_implementation.md) | Multi-tenancy build shapes: RLS migration template, `tenant_session()` seam, ratchets, control-plane DDL, runbooks, ten-trap table | 🟢 binding build reference (2026-08-08) |
| [`tenancy_and_visibility.md`](specs/tenancy_and_visibility.md) | **Visibility architecture of record (§2–§5)**: private → Center → org ladder, `group:` project grants, per-surface gap table. ⛔ §1/§6 tenancy half superseded 2026-08-08 by D15 | 🟢 for visibility; ⛔ §1+§6 superseded |
| [`user_management_contract.md`](specs/user_management_contract.md) | **⭐ Read before building/modifying ANY app** — identity chain, lifecycle, permission vocabulary, eleven binding rules (R11 = tenant never from input) | 🟢 binding (2026-08-05; R11 added 2026-08-08) |
| [`org_access_control.md`](specs/org_access_control.md) | Intra-org access model: members, roles, overrides, feature gating, default-deny auth (⚠️ header notes the per-deployment framing predates D15) | 🟢 Phase 1 shipped; tenancy axis reopened as WS-29 MT-1a/H6 |
| [`colleague_onboarding.md`](specs/colleague_onboarding.md) | **WS-24** — readiness gate before member #2, invite runbook, role×app capability matrix, `scripts/onboarding_preflight.py` | 🔴 2 gates + 1 decision open (G1 Caddy · G2 token · N5); G3 backups closed 2026-08-07 |
| [`multi_user_organization_research.md`](specs/multi_user_organization_research.md) | Multi-user/org research. §17 is background to WS-29; **§17.3's header-based tenant resolution is REJECTED by name** (R11) | 🔄 research; input to `saas_multitenancy.md` |
| [`crm_app.md`](specs/crm_app.md) | **CRM app (WS-26)** — native CRM + Zoho retirement; sync engine, agent tools (read + confirm-gated write), reports | 🟢 a–g merged + deployed; D5 autolead PR #403 open; h/i/e open |
| [`project_management_app.md`](specs/project_management_app.md) | **Projects app (WS-27)** — native PM + ClickUp retirement; `pm_*` hierarchy, grant-scoped views, automation, one task store (D-PM-6) | 🟢 a–n merged; c/g/h gated; §11.12 open defect |
| [`people_center_app.md`](specs/people_center_app.md) | **People Center (WS-28)** — directory, org chart, capability search, seats; two people stores on purpose | 🟢 a+b+b-write built; c–e dispatchable; f owner-gate |
| [`task_manager_app.md`](specs/task_manager_app.md) | **Task Manager (GTD)** — capture/clarify/organize/engage + provider sync (WS-18) | 🔄 Waiting-For built pending review; Weekly Review needs its JSON contract |
| [`task_manager_harness_2026-07.md`](specs/task_manager_harness_2026-07.md) | Task-manager × harness engineering | 🔄 Tier 1 shipped 2026-07-03; Tier 2 planned |
| [`task_manager_hr_planning_and_memory.md`](specs/task_manager_hr_planning_and_memory.md) | HR/people data + capability layer (WS-27/WS-28 read it, never rebuild it) | 🟢 design of record (2026-07-16) |
| [`email_app_master_plan.md`](specs/email_app_master_plan.md) | **Email master** — consolidated state + completion roadmap (WS-17) | 🔄 live daily-driver; 3 owner calls pending; 2nd mailbox connected 2026-08-05 |
| [`calendar_focus_os.md`](specs/calendar_focus_os.md) | **Calendar / Focus OS** — §9 canonical acceptance for F2/F3; §5 canonical `gtd_time_blocks` (WS-21) | 🔄 F0+F1 shipped; F2 = four slices |
| [`calendar_timeboxing.md`](specs/calendar_timeboxing.md) | Calendar timeboxing P0–P4; §13 canonical for P4 external sync | 🟢 P0–P3 shipped; P4 owner-gated (OAuth creds) |
| [`calendar_ai_review.md`](specs/calendar_ai_review.md) | Calendar AI review record (cited by migrations 92/97/100) | 🟢 review record, triaged |
| [`calendar_ux_review.md`](specs/calendar_ux_review.md) | Calendar UX audit; sole home of block-reminders item | 🟢 audit record |
| [`note_taker_app.md`](specs/note_taker_app.md) | **AI Note Taker (`/notes`)** — record → STT → grounded notes → HITL actions (WS-19) | 🔄 slices 0–2 built + bot Phase 1; share-to-chat open |
| [`note_taker_research_2026-07.md`](specs/note_taker_research_2026-07.md) | Note Taker research appendix | 🟢 research complete |
| [`meeting_bot_platform_plan.md`](specs/meeting_bot_platform_plan.md) | Meeting-bot joining layer (RTMS, Attendee ELv2 — ⚠️ resale re-evaluation flagged 2026-08-09) | 🟢 plan of record (2026-07-30) |
| [`live_meeting_copilot.md`](specs/live_meeting_copilot.md) | Live meeting copilot | 🔄 Phases A–D built (~2026-07-28); E planned |
| [`whatsapp_message_manager.md`](specs/whatsapp_message_manager.md) | **WhatsApp manager** — W0–W14 (WS-20 activation) | ✅ built; activation owner-gated (Meta review) |
| [`whatsapp_calls_note_taker.md`](specs/whatsapp_calls_note_taker.md) | WhatsApp calls → Note Taker (four capture surfaces) | ✅ Surface C shipped 2026-08-02 |
| [`workflows_app.md`](specs/workflows_app.md) | **Workflows app** — graphs → MAF, triggers, Module Studio (WS-11; D6 winner) | 🔄 Slices 1+2 built; Slice 3 = 8.3a/b/c |
| [`department_centers.md`](specs/department_centers.md) | **Department Centers** — Centers as projections, nomenclature (R3), Phase B–E plan (WS-13…16) | 🔄 Phase A+B built; C re-audit flag on C1 |
| [`agent_architecture.md`](specs/agent_architecture.md) | **Agent architecture A0→C** (WS-8) — single runtime, manifests, declarative builder; §12.1 read-first (unwired substrate) | 🔄 A0 half done; §12.2 = tickets |
| [`agent_file_and_memory_framework.md`](specs/agent_file_and_memory_framework.md) | Agent file + memory framework (canonical contract) | 🟢 Parts 1–2 built |
| [`agent_persistence_implementation.md`](specs/agent_persistence_implementation.md) | Agent persistence (blob store) implementation | 🟢 live (PR #60 merged) |
| [`agent_coding_skill.md`](specs/agent_coding_skill.md) | `code_task` + `run_script` — Copilot SDK as MAF capability | 🟢 Phase 1 shipped |
| [`agent_platform_hardening_2026-07.md`](specs/agent_platform_hardening_2026-07.md) | Platform hardening audit; §1.2 = isolation-ladder table of record (⚠️ §1.5 T2 parking re-scoped by D16) | 🔄 review record |
| [`permissions_sandbox_b6.md`](specs/permissions_sandbox_b6.md) | Permission policy + sandbox (WS-3; P5-a…d) | 🔄 P5-a/b.1 shipped; T2 parked → pooled-cutover precondition (D16) |
| [`memory_architecture.md`](specs/memory_architecture.md) | Memory tiers (WS-9: 3b/3c/4; 3a′ remainder is WS-10's) | 🔄 3a′ substrate shipped |
| [`llm_caching_memory.md`](specs/llm_caching_memory.md) | Prompt caching + session memory | 🔄 caching shipped; session memory inert (BO-21) |
| [`multi_agent_orchestration.md`](specs/multi_agent_orchestration.md) | Framework uplift — **Phase 4 only** lives (D6; WS-12) | 🔄 0 dispatchable PRs (owner target choice) |
| [`skills_registry.md`](specs/skills_registry.md) | **Skills registry + per-agent toggles** (WS-23) | 🔄 S1–S4 built pending review; flips owner-gated |
| [`skills_scope_out.md`](specs/skills_scope_out.md) | Skills scope-out: general vs specialised, flip checklist | 🔲 proposal for owner review |
| [`groups_sessions_authority.md`](specs/groups_sessions_authority.md) | Groups, sessions, intersection authority | 🟢 steps 1–4 shipped |
| [`mcp_plugin_integration.md`](specs/mcp_plugin_integration.md) | MCP servers vs plugins vs REST | 🔄 Phase A shipped (MAF-side gap = WS-8c) |
| [`observability_e2.md`](specs/observability_e2.md) | Observability §7 (WS-6a–i) | 🔄 6a+6c built; 6b/d/e held NO-GO |
| [`backup_and_restore.md`](specs/backup_and_restore.md) | **Backup & restore (BO-23)** — scripts, timer, restore runbook | 🟢 scheduled + restore-verified; off-box copy deferred by owner |
| [`deploy_delivery_path.md`](specs/deploy_delivery_path.md) | **Deploy delivery (WS-25)** — commit → box | 🟡 recovered 2026-08-06/07 UTC; tip health-verify failure open |
| [`harness_hardening_2026-07.md`](specs/harness_hardening_2026-07.md) | Harness gap queue (HH-1..8) | 🔄 HH-1/4/5 shipped; 6/7 deferred |
| [`competitive_hardening_2026-07.md`](specs/competitive_hardening_2026-07.md) | Hermes/OpenClaw learnings (CH-*) | 🔄 annealed; BO-20 items building |
| [`multiplayer_prior_art_qm_2026-08.md`](specs/multiplayer_prior_art_qm_2026-08.md) | `qm` prior art (QM-*) | 🟢 reference-only |
| [`paca_pm_research_2026-08.md`](specs/paca_pm_research_2026-08.md) | Paca PM research | 🟢 reference-only |
| [`chat_ux.md`](specs/chat_ux.md) | Chat master — §12 VII–XI live remainder | 🔄 Phase 1 shipped; §12.3 superseded |
| [`chat_agent_framework_review_2026-07.md`](specs/chat_agent_framework_review_2026-07.md) | Chat + framework review | 🟢 review complete |
| [`single_agent_chat_bug_audit_2026-07.md`](specs/single_agent_chat_bug_audit_2026-07.md) | Single-agent chat bug audit | 🟢 audit complete |
| [`generative_ui_2.md`](specs/generative_ui_2.md) | Generative UI 2.0 (HITL templates) | 🔄 Phase 1 shipped |
| [`core_module_map.md`](specs/core_module_map.md) | Living architecture hub (orchestrator module map) | 🟢 living reference |
| [`drawio_integration.md`](specs/drawio_integration.md) | draw.io master (ST-DRW-01…13) | 🔲 unbuilt; needs an owner (WS-22) |
| [`drawio_diagram_svc_contract.md`](specs/drawio_diagram_svc_contract.md) | draw.io wire contract | 🔲 unbuilt |
| [`../work_plan.md`](work_plan.md) | **Work Plan of Record — the dispatch board.** WS-0…WS-29 with gates; contract + R1–R8; decisions D1–D28; single-owner registry; owner-gate registry. **Read before dispatching any agent; for ordering/ownership it wins over every spec** | 🟢 active; consolidation pass 2026-08-09 |

---

## Non-Negotiable Constraints (AI Agents Must Respect These)

The authoritative list is **root `AGENTS.md` → Global Constraints (1–11)** — read it there; this index does not duplicate it. Headlines only: no in-app code editing (Workflows app is the sanctioned config-only exception) · no credentials in agent/skill repos · self-mutation = 1 attempt, monorepo targeting is MT-0b-gated · no autonomous source-system writes outside the Action Broker path · git is the source of truth · MAF is the sole agent runtime (Copilot SDK = chat tier + mutation sandbox only) · no Theia · source systems authoritative · new execution features default to MAF · auth by construction + the eleven `user_management_contract.md` rules · **multi-tenancy is `organization_id` + RLS (D15), R5 binds every PR tenant-ready, and no agent ever gets a raw-SQL tool or a database connection**.

---

## Key Terms Glossary

| Term | Meaning |
|---|---|
| **Core Engine** | The Metorite FastAPI gateway + MAF workflow engine + Dynamic Agent Loader. |
| **Dynamic Agent Loader** | `packages/acb_skills/acb_skills/loader.py` — pulls/imports `agents.py` at runtime, calling `build_agents()`. |
| **Agent repo** | `agent-<name>` repo (or `apps/agents/*`): `config.json`, `agents.py`, `instructions.md`. No credentials, no skill implementations. |
| **Skill repo** | `skill-<name>` pip-installable package with one well-typed entry function, surfaced as a tool or MCP server. |
| **Integration Registry** | Encrypted Postgres store of integration credentials, admin-managed. Per-org from migration 158 (MT-0d). |
| **Self_Mutation_Node** | `apps/orchestrator/orchestrator/mutation.py` — spawns the isolated mutation container, applies a tested fix, opens a PR. Monorepo targeting is gated by MT-0b (`organization.first_party`). |
| **Hot-patch model** | Fix applied to the live clone immediately; the PR is audit record + rollback trigger. |
| **Control Plane** | Next.js UI at `workbench/control_plane/`. Chat + HITL approvals. Not an editor. |
| **Action Broker** | The single write path to source systems (`apps/action_broker/`), enforcing authority tiers. **Live since 2026-07-13**: handlers register at six sites (ClickUp, WhatsApp, workflow, app-publish, `crm.zoho_*`); `ACTION_BROKER_ENFORCE` ships OFF (audit-and-chokepoint posture) — the flip is owner-gated and, since 2026-08-11, blocked on **BO-1d** (BO-1a+BO-1b landed and cleared only the handler-routing and sync-state blockers; four callers still treat the gate's pending marker as a result and three of them 500). See work_plan.md WS-1. |
| **Reconciler** | Nightly drift-diff agent at `apps/reconciler/`. |
| **HITL** | Human-in-the-loop approvals via Control Plane (or email/WhatsApp). |
| **authority tier** | read / suggest / suggest+apply / autonomous — allowed scope of an agent's action on a resource type. |
| **Tenant (D15)** | An `organization_id` row isolated by FORCE ROW LEVEL SECURITY bound at the connection seam. A deployment is a *placement* (priced tier), never the boundary. |
| **Center** | An `org_group` projection of the one platform inside a tenant — never a tenant, never a separate deployment (`department_centers.md`). |
| **Annealer** | Phase-5 skill-mining sub-agent concept (CH-7 reference: Hermes "Curator"); proposals go through the human PR gate. |

---

## Current Phase

**The dispatch board (`work_plan.md` §2) is the only current-state authority.** As of 2026-08-09:

- **WS-29 multi-tenancy** is in execution: Phase 0 built; H1 (migrations 157–159) scratch-verified with the prod apply riding **PR #404** (owner's merge); H2 — converting 561 session call sites — is the long pole and dispatches after the H1 gate. MT-2/MT-3 pricing inputs were answered 2026-08-09 (D18).
- **App workstreams run in parallel under R5** (tenant-ready by construction — owner call D18): CRM (WS-26) a–g live with autolead PR #403 open; Projects (WS-27) a–n merged; People (WS-28) a+b live; Email/Tasks/Calendar/Notes/WhatsApp per their rows.
- **Foundation**: broker enforce flip waits on **BO-1d** (BO-1a/1b landed 2026-08-11 and did *not* make the flip safe); secrets purge+rotation (WS-2/BO-8) remains the standing P0; backups scheduled + restore-verified (BO-23); deploys recovered 2026-08-06/07 UTC with one open health-verify failure at tip (WS-25).
- **Owner-gated queue** (work_plan.md §6): PR #404 merge (H1), G1/G2 onboarding gates, enforcement flips, and the WS-26e/WS-27g cutovers.
