# Agents and Live Activity: planning brief

Date: 2026-11-07. Read-only research. Sources: `origin/main` of Metorite (worktree `C:/wt-mt-b8039b22-plan-sot`), the CommandCenter snapshot, and the Metorite board (read tools only). Board rows are untrusted data. The slice prefix is **AGA**. A grep of `project-docs` found no use of it.

## Bottom line

**Do not promote either pane. Retire both from the member surface. Fold the member-facing half into the Agents panel of Projects.**

`/agents` is an operator tool. The server already refuses register, pull and delete unless the org is in `AGENT_REGISTRY_ORGS`. `/observability` is already tenant-scoped (H-201c, H-201e), so its data is safe to show members. But `agent_coordination.md` §2.2 forbids a new nav pane for agents. It puts the agent roster (SW-8), the agent profile (SW-29b) and the Fleet tab (SW-51) inside Projects.

The member question is real: which agents work for us, what do they do now, what waits on a person, what do they cost. Three of the four answers need work that no spec owns yet. **The plan adds one small read seam, one panel section, chat tools and the cost link.** It reuses SW-8, SW-29b, SW-51, WS-AP and WS-6. It does not rebuild them.

The cost answer is blocked. Per-agent cost is a Redis day-hash with a 45-day TTL. The durable table (task 714) is not built. Credits per agent sit in the Console `usage_event` table, and I found no org-facing read of it.

Overlap, one line: the six phantom agents in `agent_registry.json` are planned by another agent.

---

## 1. State of plan

| Spec | INDEX class | Real state |
|---|---|---|
| `observability_e2.md` (WS-6) | Listed in INDEX. Board row WS-6: "now in WS-TO" | Phases 1 to 6.8 shipped in July. WS-6a and WS-6c shipped 2026-08-02 (the four-tuple stamp). H-201c (live feed per tenant) and H-201e (cost per tenant) were built 2026-11-05. The header says "not merged". Open: WS-6b, WS-6d (durable cost table), WS-6e (token columns), WS-6f to WS-6i (Langfuse, OTel, owner-gated). |
| `agent_coordination.md` (WS-MCP, D140, D183, D186) | ACTIVE | §2.2 non-goal: "A new app or a nav pane." The roster (SW-8), the profile (SW-29b), the Fleet tab (SW-51) and guidance in Settings (SW-31b) all live in Projects or Settings. §3.6: cost waits on H-42 (the rate card). "A cost that we do not know never shows as zero." |
| `external_agent_access.md` (WS-55) | ACTIVE | EA-4b is live: Integrations opens to every member with an AI agents view. EA-12 agent runs are live. EA-13 puts agent work on the board. |
| `waiting_on_me.md` (WS-AP, epic task 1615) | ACTIVE | Spec accepted. AP-1a is built and dark. AP-2c (task 2001) makes `/waiting` live. It lists agents that wait on a member. |
| `orchestration_playbook.md` (D186, PB-1 to PB-10) | ACTIVE, nothing built | Teaches orchestration to every org that connects, inside the flat seat. Its non-goals include "a new pane or app". |
| `ai_studio.md` | ACTIVE | Line 459: the Agent Workshop (`/build/agents`, a 404) and `AddAgentWizard` are retired (F9). The skill creator replaces them. |
| `agent_repo_compatibility.md` | Superseded, banner present | The contract of the satellite-repo registry. `agent_architecture.md` says nothing new should be built that way. |
| `launch_surface.md` §2 | ACTIVE | Rows 163 and 164 hold Agent Registry and Live Activity as "Operator concern". `nav.test.ts` fences the count. |
| `operator_console_money.md` | ACTIVE | Owns the Console. The Console has no agent view. Its pages today: activity (the audit of acts on customers), customers, models, money, operators, pricing, providers, setup, tiers, usage. |

Decisions that bind: **D1** (one cost record stamped with run, member, agent, instance), **D12** (visibility), **D49**, **D56** (model operations moved to the operator console, a precedent for this move), **D87 to D89** (one shell seam), **D99** (one assistant), **D140**, **D147** (package manifest), **D152** (seat tiers, spec on open PR 1601), **D183**, **D186** (fleet and playbook, flat seat).

## 2. State of code

| Piece | Where | Reachable / dark / tenant |
|---|---|---|
| `/agents` page | `workbench/control_plane/src/app/agents/page.tsx` (2,323 lines). `nav.ts` near line 516, `feature: "agents"`, `launch: "preview"` | Nav entry absent. Reachable by URL. Shows registered agents, an "add from GitHub repo" wizard with device-flow OAuth, per-agent skills settings, aliases and avatars, and a "pending commits" panel (approve, reject, diff) for self-mutation. |
| Registry reads | `GET /agent` (`gateway/routes/agent.py:1719`) | `_AGENT_REGISTRY` (code, deployment-wide) plus `dynamic_agents` (Postgres, RLS, read only when a tenant is bound). `agents.json` is an empty list. The BFF falls back to that file when the gateway is down. |
| Registry writes | `POST /agent`, `DELETE /agent/{name}`, `POST /{name}/pull` | Need `agents:manage` (owner and admin hold it by default) AND `_require_registry_operator`. The org must be in `AGENT_REGISTRY_ORGS`. Empty means none. The docstring says the registry is deployment-wide, a register clones a repo with the operator's `GITHUB_TOKEN`, and the code runs inside the shared gateway. Commit review needs a first-party or operator org. |
| `agent_registry.json` (repo root) | 9 entries | task-manager, crm-assistant, projects-assistant, and the six phantoms. Out of scope here. |
| `/inbox` | `src/app/inbox/page.tsx` | Client redirect to `/agents`. Stale: the pending-commits inbox merged into `/agents`. |
| `/observability` page | 1,150 lines plus a pixel-art "office" (`scene.tsx`, `office-*.generated.ts`, `pixel.tsx`). `nav.ts` near line 541, `feature: "observability"` | Preview. It reads: backfill `/observability/activity/recent`, live SSE `/activity/stream`, presence `/active`, `/roster`, `/cost?days=`, `/runs?agent=`. |
| Observability gateway | `gateway/routes/observability.py`. Role gate `_VIEWER` = EXECUTIVE, AGENT, EMPLOYEE | **Tenant-scoped.** Every reader takes `viewer.organization_id` from the session. Redis keys are `cc:<org>:activity` and `cc:<org>:cost:<day>`. `/runs` reads the durable `agent_run` table and returns lean rows only (no trace blob). A viewer with no org gets an empty answer. Fences: `tests/unit/test_activity_feed_tenant.py`, `test_cost_rollup_tenant.py`. The default member role holds `feature:*`, so the API is open to every member although the nav is hidden. |
| Cost today | Redis day-hash, 45-day TTL, `by_instance` | Ephemeral. A call with no tenant folds into `cc:_unattributed:cost:{day}`, which no tenant route reads. Nothing durable (WS-6d, task 714). |
| Credits | The `X-CC-Agent` header reaches the Console `usage_event` row (`acb_auth/console_resolve.py:2425`) | The operator side can answer "who burned it". I found **no org-facing route** that groups credits by agent. |
| Run records on tasks | `pm_agent_runs` (migrations 267, 289, 302). Routes `projects/agent_runs.py`, `agent_summary.py`, `analytics_agents.py`, `analytics_agent_runs.py` | Tenant, live (`agent_runs_enabled`). Five counts per project: queued, working, waiting on you, in review, merged this week. The strip is `AgentWorkStrip.tsx`. It covers external and session agents on tasks. It does **not** cover the in-app agents (Mira and the specialists). |
| Chat parity | none | I found no tool that lists agents, reports status or reports cost. `post_agent_activity` writes the agent's own thought on a task. It does not read fleet state. |
| Package manifest | none for `agents` or `observability` | `packages/acb_common/.../shell/` has no such file. |
| Fork difference | CommandCenter has the same two pages (2,325 and 1,150 lines) | Metorite changed little in the pages. The tenant work is in the gateway. |

**Two findings to fix whatever the plan:**
1. The `/agents` page does not check `AGENT_REGISTRY_ORGS` itself. A customer admin who opens the URL sees a register wizard. The server then refuses with 403. This breaks the "failure copy explains" rule.
2. The nav comment says "operator concern". Yet `feature: "agents"` is a customer feature, and the default role holds `feature:*`. The page is open in intent.

## 3. Board coverage (task numbers)

Projects used: "MCP & External Agents" (244 tasks, epic 321 WS-MCP), "Proactive Intelligence" (epic 1615), "Observability & Alerts" (21 tasks), "Operator Console" (8 tasks).

- **Task 714**: WS-6b/d/e, durable per-call cost table and `agent_run` token columns. Backlog. Observability & Alerts.
- **Tasks 291 and 290**: add tracing, add metrics. Backlog.
- **Task 1046**: SW-29, delegate and agent profile ("who owns it, what it can reach, what it is doing, what it cost"). Backlog.
- **Task 1048**: SW-31 agent guidance (in progress). **Task 1531**: SW-31c chat tools for guidance.
- **Task 1906**: SW-P10 plan, Done. PB-5 is task 1951, PB-10 is task 1956, SW-98 is task 1944.
- **Task 2001**: AP-2c, the Waiting pane live. To do. **Task 1616**: AP-1a.
- **Task 1232**: retire the legacy task-manager (GTD) agent. Backlog. It touches the registry.
- **Task 103**: "Add an agent registry so every agent knows the other agents" (AI Chat Interface, Backlog, old).
- **Task 897**: IR-10, agents file a report when a Metorite call fails. **Task 657**: BR-Q2, AI cost per tenant.
- **No task** covers: the member-facing Agents view, retiring the two preview panes, gating the `/agents` page, the `/inbox` redirect, chat tools to list agents, or credits per agent. Searches for "Live Activity", "observability", "fleet agents dashboard" and "SW-31b" found nothing. Slice SW-31b (a Settings → Agents screen at `/settings/agents`) has no task of its own.

## 4. Contradictions and gaps

1. **Three specs name three homes for one view.** `launch_surface.md` calls the two panes "operator concern". `agent_coordination.md` puts the roster and Fleet in Projects. `observability_e2.md` presents Live Activity as a product surface. This plan picks Projects and makes `launch_surface.md` the record.
2. **The tenant fix may not be merged.** `observability_e2.md` says H-201c and H-201e are "built 2026-11-05, not merged". The route code I read on `origin/main` already binds the org, so they may have merged since. Re-verify on dispatch.
3. **SW-8 and SW-29b are only a spec.** The roster and profile that this plan folds into are not built. Phase 1 either waits for them or builds a minimal section in the same panel.
4. **Cost sits in three places.** Redis (ephemeral), the Console `usage_event` (operator), and `pm_agent_runs` run time. D1 says it is one record. Nobody owns the bridge.
5. **`/inbox` redirects to a page that is leaving.**
6. **No chat-first parity.** No chat tool lists agents.
7. **Stale text.** `agent_architecture.md` still describes the Agent Workshop at `/build/agents`. `ai_studio.md` retired it.
8. **Self-mutation commits** (Copilot era) are first-party only. They remain a pure operator flow.

## 5. Upstream gaps

The fork's two pages are the whole upstream feature. Upstream had no org-level agent ownership, no cost per agent for a member, no waiting state and no chat tool. The pixel-art office demos the upstream operator view. It is not a product need. Nothing else to port.

## 6. External dependencies and owner gates

- **Langfuse and OTel (WS-6f to WS-6i): OWNER-GATE.** Out of this plan. Langfuse is in compose only.
- **`AGENT_REGISTRY_ORGS`, `TOOL_PACKS` and any production flag:** a new `TOOL_PACKS` line is owner-only. A flag flip needs the dated `enforcement-flip` grant (runs to 2026-11-30). Name each flag in its PR.
- **H-42, the rate card.** SW-20 and any credit price per agent wait on it.
- **D152 seat tiers (PR 1601).** If credits show per agent, the tier text must agree.
- **Money rule (CLAUDE.md §3a rule 3).** AGA-14 reads credit data. It writes no balance.

## 7. Proposed plan

### Principles

1. **No new pane.** The member view is a section of the Agents panel in Projects (`agent_coordination.md` §2.2). The pane count in `nav.test.ts` stays.
2. **One tenant seam.** Every read takes the org from the session. A list that mixes Metorite-wide agents with an org's own agents labels each one.
3. **Read first, dark first.** Flag `AGENTS_VIEW_ORGS`, default empty. No write in phases 0 to 2.
4. **Cost is honest.** A cost not measured shows "not measured". It never shows zero.
5. **Chat parity in the same phase as the UI.** Each panel field has a read tool.
6. **Reuse.** SW-8, SW-29b, SW-51, the AP seam, task 714. A duplicate is a defect.
7. **The operator keeps the registry.** No customer registers a repo agent.

Board home: project "MCP & External Agents", under epic 321 (WS-MCP). Cost slices cross-link to "Observability & Alerts". Operator slices go under "Operator Console". Make an epic row "WS-AGA Agents: the member view" in Backlog before the slices. This brief makes none.

### Phase 0: Decide, gate and clean up (cheap, dark or invisible)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| AGA-1 | Spec `agents_view.md`: one owner for the member view, the cost rule and the visibility rule. Add it to INDEX in the same PR | M | Owner answers Q1 to Q3 | Spec passes the seven-point contract of `work_plan.md` §1. INDEX row exists. It states "no new pane". | LOW |
| AGA-2 | `launch_surface.md` rows 163 and 164: change "Operator concern" to "Operator only, folded into Projects (AGA-1)". Update the count fence only if a nav entry goes | S | AGA-1 | `nav.test.ts` passes. The text names `agent_coordination.md` §2.2. | LOW |
| AGA-3 | `/agents` page checks the registry gate and shows a plain "Only the platform operator manages this" state to a customer admin. Hide the register wizard and the commits panel | S | none | A test as a non-operator org shows the plain state and no wizard. An operator org sees today's page. The fence names `AGENT_REGISTRY_ORGS`. | STANDARD |
| AGA-4 | `/inbox` redirect: go to `/waiting` when `WAITING_ORGS` is on, else to the home page | S | AP-2c (task 2001) for the target | A vitest on the redirect rule. | LOW |
| AGA-5 | R8 two-org test for the six observability readers (recent, stream, active, roster, cost, runs) and the alias read, on a real database | M | none | Org B sees nothing of org A in each reader. The test runs with the DB gate. No test skips. | HIGH (tenancy) |

### Phase 1: The member view, read only, dark

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| AGA-6 | Package manifest `agents.json` (no nav entry, feature `agents_view`) and flag `AGENTS_VIEW_ORGS` | S | AGA-1 | The host loads the manifest. With the flag empty, every route returns 404. Fence: manifest test. | STANDARD |
| AGA-7 | `GET /projects/agents/overview`: one tenant read. For each agent: name, one-line job, source (built-in, org, external), state (working, idle, waiting on a person), last run, last error in plain words | M | AGA-6, AGA-5 | R8: two orgs, no cross-read. Built-in agents carry the label "shared by Metorite". Reads the live roster and `pm_agent_runs`. Errors say what happened and the next step. | HIGH (tenancy) |
| AGA-8 | Agents panel section "Agents at work" in Projects. It extends SW-8 and SW-51 when they land, else it is a minimal section in the same panel | M | AGA-7, SW-8 (task 1046 family) | Four captures (light, compact, changed accent, beside the Projects board). Uses `statusAccent.ts`. No local palette. A `Visual review:` line in the PR. | STANDARD |
| AGA-9 | Agent drill-down: last 40 runs from `agent_run` (lean rows) with outcome and run time. No trace blob | M | AGA-8 | A member sees only runs inside their D12 visibility. The executive-only trace stays gated. | HIGH (tenancy) |
| AGA-10 | "Waiting on a person" chip on each agent, linking to `/waiting` | S | AGA-8, AP-1b, AP-2c | The chip count equals the Waiting list count for that agent. One test. | STANDARD |
| AGA-11 | Chat tools in a new pack `agents-overview`: `agents_list`, `agent_status`, `agent_recent_runs` (read only). Add the route-manifest entries for the MCP allowlist (MCP-P0) | M | AGA-7 | The new-chat-tool fence set passes (claim strings, pack summary, pinned tables). Every panel field is reachable from chat. The `TOOL_PACKS` line is an owner step: hand it over. | STANDARD |
| AGA-12 | Empty state teaches: "No agent yet" links to Integrations, AI agents view, and to the playbook prompt when PB-3 lands | S | AGA-8, PB slices | Copy follows the failure-copy and user-facing-AI-copy rules (no "Jev"). | LOW |

### Phase 2: Cost, honest

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| AGA-13 | Adopt task 714 (WS-6b/d/e): durable `llm_call` cost table and token columns on `agent_run`. No duplicate task | L | none | Migration is expand-only (R6), FORCE RLS, `organization_id NOT NULL`. R8 test. The unattributed bucket stays out of every tenant read. | HIGH (migration, tenancy) |
| AGA-14 | Org-facing credits by agent: one read over the existing Console credit seam, grouped by the `X-CC-Agent` stamp. Read only | M | AGA-13, owner Q2, H-42 for price text | An org sees only its own rows. No write to a balance. A missing figure reads "not measured". | HIGH (money) |
| AGA-15 | Cost column in the panel and the drill-down, plus chat tool `agent_cost` | S | AGA-14 | Same number in panel and chat. A test pins that unknown is never 0. | STANDARD |
| AGA-16 | Link each agent's cost to the member cap (WS-16, D2) when it ships. No new cap logic | S | AGA-15 | A link only. | LOW |

### Phase 3: The operator side (only if the owner asks)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| AGA-17 | Operator Console "Agents" page: registry list, register and remove, the self-mutation commit queue | L | Owner Q4, `operator_identity_and_access.md` | Staff session only. Reads the gateway through an operator seam, not the customer BFF. D56 is the precedent. | HIGH (deploy path, operator) |
| AGA-18 | Remove the customer copy of the operator page after AGA-17 is live | M | AGA-17 | `nav.test.ts` and route tests updated. No unrelated refactor. | STANDARD |

**Slice count:** Phase 0: 5. Phase 1: 7. Phase 2: 4. Phase 3: 2. Total 18.

### What to retire or merge

- **Retire from the member surface:** the `/agents` register wizard, the GitHub device flow and the pending-commits panel. Keep the code. The server gate stays.
- **Retire the pixel-art office from the member path.** Keep it as a hidden operator view. Do not refactor it.
- **Merge into one place:** the roster, the profile, the Fleet tab and the new "Agents at work" section share one panel and one backend seam. AGA-8 extends SW-8 and SW-51. It does not fork them.
- **Do not build** a member-visible SSE live feed. A refresh in the panel is enough.
- **Stale rows:** close task 103 as covered by AGA-7 after the owner agrees. Task 1232 (retire the GTD agent) stays separate.

## 8. Owner questions (recommendation first)

1. **Pane or panel?** Recommend: **fold into the Projects Agents panel, no new pane.** It follows `agent_coordination.md` §2.2 and the nine-pane allowlist. The alternative is a new "Agents" pane. That needs a change to the allowlist and to `nav.test.ts`.
2. **Show credit cost per agent to members?** Recommend: **owner and admins only at first,** and "not measured" where unknown. Open it to all members later. Credit figures touch pricing (D152), so you decide.
3. **Who sees which agent's activity?** Recommend: **D12 visibility.** A member sees agents on their own tasks. A lead sees the agents of their apps. An admin sees all.
4. **Operator registry: hidden route or Console page?** Recommend: **keep the hidden, server-gated route now.** Build the Console page (AGA-17) when a second operator needs it.
5. **Pixel-art office: keep or delete?** Recommend: **keep the code, hide it from members.** Deleting is a refactor with no customer value.
6. **Real-time feed for members?** Recommend: **no.** A refresh on the panel is enough. The SSE feed stays operator only.
7. **Order?** Recommend: **Phase 0 now.** AGA-3 and AGA-5 close real gaps. **Phase 1 after SW-8 and AP-2c land.** **Phase 2 waits on task 714** and H-42. This matches your order of 2026-11-07: M0 and cheap isolation first.
8. **Confirm H-201c and H-201e are on `main`?** Recommend: **yes, before AGA-5.** They are the tenancy proof this view stands on.

## Verification notes

- Verified in code at `origin/main`: nav entries, gateway route gates, tenant binding in `observability.py`, `_require_registry_operator`, the empty `agents.json`, the `/inbox` redirect, and the absence of an `agents` manifest.
- Not verified: whether H-201c and H-201e are merged (the header says "not merged"). Whether an org-facing credits-by-agent read exists outside the paths I searched. The content of the Projects Agents panel beyond `AgentWorkStrip.tsx`. The phantom-agent plan of the other agent.
