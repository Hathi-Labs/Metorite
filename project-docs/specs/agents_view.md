# Agents: the member view in Projects (WS-AGA)

**Status: ACTIVE (AGA-1, 2026-11-11). AGA-6, AGA-7 and AGA-8 are built, dark (2026-11-11). AGA-9 is built, dark: the route and the Recent runs panel. The rest is not built.** Owner answers to Q1, Q2 and Q3 are open. The text marks each recommended answer "Proposed, owner to confirm". Phase 1 starts after the owner confirms Q1 and Q3.

**Verified against code on 2026-11-11 at `83e9e8b23` (`origin/main`).** Each path in §2 was read on that commit. Re-check each anchor when you dispatch a slice.

**Board:** WS-AGA in `work_plan.md` §2.1 (epic task 2103, Metorite project "MCP & External Agents", under epic 321). This spec is task 2104. The slices have the task numbers of §6.

**Single owner.** This file owns the member view of agents, the cost rule and the visibility rule. `agent_coordination.md` owns the Agents panel itself (SW-8, SW-29b, SW-51). Every other document that mentions the member view links here and adds nothing.

**Decisions that bind it:**

- D1 (one cost record), D12 (visibility), D49, D56 and D99.
- D87 to D89 (one shell seam), D140 and D183.
- D205 (building blocks) and D211 (the ladder).

D204 point 5 folds the member view into the Fleet panel.

**Gate summary.** Every slice is AGENT-SAFE to build dark. Two steps are OWNER-GATE: the `TOOL_PACKS` line for the new chat pack (AGA-11), and each production flip of `AGENTS_VIEW_ORGS` (needs the dated `enforcement-flip` grant). AGA-14 reads credit data and is a money read. It writes no balance (`CLAUDE.md` §3a rule 3).

---

## 1. The answer in one screen

**No new pane.** The member view of agents is a section of the Agents panel in Projects (`agent_coordination.md` §2.2, SW-8, SW-51). The pane count in `workbench/control_plane/src/lib/nav.test.ts` stays. This work adds no nav entry.

A member has four questions.

- Which agents work for us?
- What does each do now?
- What waits on a person?
- What does each cost?

Three answers need one small read seam, one panel section and chat tools. The fourth answer waits on a durable cost record (task 714).

The fork's two pages, Agent Registry (`/agents`) and Live Activity (`/observability`), stay operator tools. They leave the member surface. The server gate stays. The code stays.

### 1.1 Principles

1. **No new pane.** Rule: a nav entry for agents is a defect. Fence: `nav.test.ts` (the live count) and `src/lib/shell/seams.test.ts`.
2. **One tenant seam.** Every read takes the organization from the session and never from the request. A list that mixes Metorite-wide agents with the agents of one organization labels each row. Fence: the R8 two-org tests of §5.
3. **Read first, dark first.** The flag is `AGENTS_VIEW_ORGS`, empty by default. Phases 0 to 2 add no write.
4. **Cost is honest.** A cost that nobody measured reads "not measured". It never reads zero (§3).
5. **Chat parity in the same phase as the UI.** Each panel field has a read tool (`chat_parity.md`).
6. **Reuse.** SW-8, SW-29b, SW-51, the AP seam (`waiting_on_me.md`) and task 714. A duplicate of any is a defect.
7. **The operator keeps the registry.** No customer registers a repo agent.

### 1.2 The three owner questions

| Q | Question | Recommended answer | State |
|---|---|---|---|
| Q1 | Pane or panel? | Fold into the Projects Agents panel. No new pane. D204 point 5 and `agent_coordination.md` SW-51 item 5 already point this way. | **Proposed, owner to confirm (Q1)** |
| Q2 | Show the credit cost of each agent to members? | Owner and admins only at first. Show "not measured" where unknown. Credit figures touch pricing (D152), so the owner decides. | **Proposed, owner to confirm (Q2)** |
| Q3 | Who sees which agent? | D12 visibility. A member sees the agents on their own tasks. A lead sees the agents of their apps. An admin sees all. | **Proposed, owner to confirm (Q3)** |

Until the owner answers, the design below uses these answers. A different answer changes §3 and §4 only.

---

## 2. Scope and non-goals

### 2.1 In scope

- A read route, `GET /projects/agents/overview`: every agent the viewer may see, its state and its last run (AGA-7).
- A section "Agents at work" in the Agents panel of Projects (AGA-8).
- A drill-down of the last 40 runs of one agent, with outcome and run time (AGA-9).
- A "Waiting on a person" chip that links to `/waiting` (AGA-10).
- Chat tools in a new pack `agents-overview` (AGA-11).
- An empty state that teaches (AGA-12).
- Phase 0 clean-up: gates, a redirect and a tenancy proof (AGA-2 to AGA-5).
- Phase 2: cost, after task 714 (AGA-13 to AGA-16).

### 2.2 Non-goals

- A new app, pane or nav entry.
- A member-visible live feed (SSE). A refresh in the panel is enough. The feed stays operator only.
- A customer who registers or pulls a repo agent. The operator keeps the registry.
- A new cost record. D1 and task 714 own it.
- A rebuild of the roster, the profile or the Fleet tab. AGA-8 extends them.
- A cap or a price per agent. WS-16 and H-42 own them.
- Langfuse and OTel (WS-6f to WS-6i). These are OWNER-GATE and out of this plan.
- A plan for the six phantom entries of `agent_registry.json`. Another agent owns it.

### 2.3 Retire from the member surface

- **Retire** the `/agents` register wizard, the GitHub device flow and the pending-commits panel. Keep the code. Keep the server gate.
- **Keep** the pixel-art office (`src/app/observability/scene.tsx` and the files beside it) as a hidden operator view. Do not refactor it.
- **Build no** member-visible SSE feed.

---

## 3. Rule 1: the cost rule

**A cost that nobody measured reads "not measured". It never reads zero.** `agent_coordination.md` §3.6 already states this for run cost. This rule extends it to every cost figure in the member view, in the panel, in the drill-down and in chat.

- A figure comes from one source. Today that source is the Redis day-hash (45-day TTL) of `observability.py`. It is ephemeral. After AGA-13 the durable `llm_call` table is the source.
- A call with no tenant folds into the bucket `cc:_unattributed:cost:{day}`. No tenant read may return that bucket.
- Credits per agent come from the Console `usage_event` row, grouped by the `X-CC-Agent` stamp (`acb_auth/console_resolve.py`). No organization-facing route reads it today. AGA-14 adds one read, and no write.
- Who sees a credit figure: the owner and the admins (Proposed, owner to confirm, Q2). Other members see run time and "not measured".
- A price text waits on H-42, the rate card, and must agree with the D152 tiers.

**Fence (R7).** AGA-15 adds a test that pins "unknown is never 0" in the route, the panel and the chat tool. AGA-13 and AGA-14 add the R8 two-org test. Until then the rule is advisory.

---

## 4. Rule 2: the visibility rule

**A viewer sees an agent only through D12.** Tenancy is which company. Visibility is who inside it. These are two axes.

| Viewer | Sees (Proposed, owner to confirm, Q3) |
|---|---|
| Member | The agents on their own tasks, and their own agents |
| Lead of an app | The agents of the apps they lead |
| Admin | All agents of the organization |

- An agent gets no grant of its own (D140.2). The agent sees what its member sees.
- A drill-down shows only runs inside the viewer's D12 visibility. The executive-only trace blob stays gated, and the drill-down returns lean rows only.
- A shared Metorite-wide agent shows with the label "shared by Metorite". It shows its state and run count for this organization only.
- The route extends the loop of SW-51 over the roots that the viewer can see. It shows no row for a task the viewer cannot see.

**Fence (R7).** The R8 tests of AGA-5, AGA-7 and AGA-9 cover two organizations and, in AGA-7 and AGA-9, three viewer levels. The tests extend `tests/unit/test_projects_agent_focus_r8.py`. If the owner changes Q3, the tests change with it.

---

## 5. What exists today

Verified on 2026-11-11 at `83e9e8b23`.

| Piece | Where | State |
|---|---|---|
| `/agents` page | `workbench/control_plane/src/app/agents/page.tsx` | `nav.ts` entry "Agent Registry", `feature: "agents"`, `launch: "preview"`, comment "operator concern". The page holds no reference to `AGENT_REGISTRY_ORGS`. A customer admin sees the register wizard. The server then refuses with 403. |
| Registry reads | `GET /agent` in `apps/services/gateway/gateway/routes/agent.py` | Code registry plus `dynamic_agents`. |
| Registry writes | `POST /agent`, `DELETE /agent/{name}`, `PATCH /agent/{name}`, `POST /agent/{name}/pull` | Each needs `agents:manage` and `_require_registry_operator`. The org must be in `AGENT_REGISTRY_ORGS`. Empty means none. Fences: `tests/unit/test_agent_registry_operator_only.py`, `tests/unit/test_mutation_queue_operator_only.py`. |
| `/inbox` | `workbench/control_plane/src/app/inbox/page.tsx` | A client redirect to `/agents`. The comment is stale. |
| Observability gateway | `apps/services/gateway/gateway/routes/observability.py` | Tenant-scoped. Each reader takes `viewer.organization_id` from the session. Readers: `/activity/recent`, `/active`, `/roster`, `/cost`, `/runs`, `/activity/stream`. `/avatars` is the alias read. `/runs` reads the durable `agent_run` table, lean rows. |
| Tenant fixes | H-201c (live feed, #1739), H-201e (cost rollup, #1747), H-201 part 2 (#1717) | **On `main`.** The header of `observability_e2.md` says "not merged" and is stale. Fences: `tests/unit/test_activity_feed_tenant.py`, `tests/unit/test_cost_rollup_tenant.py`. Those are hermetic. AGA-5 adds the R8 proof. |
| `/observability` page | `workbench/control_plane/src/app/observability/page.tsx` | `nav.ts` entry "Live Activity", `launch: "preview"`. |
| Run records on tasks | `pm_agent_runs` (migrations 267, 289, 302) | Tenant, live under `agent_runs_enabled`. |
| Fleet summary | `GET /{project_id}/agent-summary` in `apps/services/gateway/gateway/routes/projects/agent_summary.py` | Exists. It has no `group_by` yet. SW-51 adds it. |
| Agent strip | `workbench/control_plane/src/app/projects/components/AgentWorkStrip.tsx` | Covers agents on tasks. It does not cover in-app agents. |
| Shell manifest `agents.json` | `packages/acb_common/acb_common/app_manifests/shell/agents.json` | **Exists.** It carries the nav entry of `/agents`. The brief of 2026-11-07 said no such file exists. That was wrong. See AGA-6. |
| `/waiting` | none in `src/app` | AP-1a is merged and dark. AP-2c (task 2001) is open. |
| Flag `AGENTS_VIEW_ORGS` | none | New in AGA-6. |

### 5.1 Shell manifest (R9)

The member view is a section of the Projects app. It builds no palette, bell, assistant rail or ⌘K listener. It declares:

- **Team and purpose:** Projects. "See which agents work for your company and what waits on a person."
- **Jobs:** "See my agents" (verb: open, form route: the Agents panel in Projects).
- **Search provider:** none. The Projects provider already finds tasks.
- **Needs provider:** the "Waiting on a person" items come from the AP seam. This spec adds none.
- **Home cards:** none.
- **Agent:** the Projects assistant, with the three tools of AGA-11.

---

## 6. The ladder and the slices

**Rung.** AGA-1 and AGA-5 to AGA-10 belong to MCP **Rung 3**. The demo is task 2897: "The owner sees the whole fleet of agents at a glance, and narrows what one agent may reach". The other slices are **Later** (D211). D204 point 5 parks the rest of the AGA tree. SW-51 item 5 makes AGA-1 and AGA-7 to AGA-10 sections of the Fleet tab.

**Tiers.** A HIGH slice keeps the full review loop.

### Phase 0: decide, gate and clean up

| ID | Task | Slice | Rung | Depends | Done when | Tier | Gate |
|---|---|---|---|---|---|---|---|
| AGA-1 | 2104 | This spec, with its INDEX row and its WS-AGA row | 3 | Q1 to Q3 recommended | The spec meets the seven points of `work_plan.md` §1. INDEX lists it. It says "No new pane". | LOW | AGENT-SAFE |
| AGA-2 | 2105 | `launch_surface.md` §2: the rows "Agent Registry" and "Live Activity" change from "Operator concern" to "Operator only, folded into Projects (AGA-1)" | Later | AGA-1 | `nav.test.ts` passes. The text names `agent_coordination.md` §2.2. The count fence changes only if a nav entry goes. | LOW | AGENT-SAFE |
| AGA-3 | none on the board | The `/agents` page checks the registry gate. A customer admin sees "Only the platform operator manages this". The wizard and the commits panel stay hidden. | Later | none | A vitest as a non-operator org shows the plain state and no wizard. An operator org sees today's page. The test names `AGENT_REGISTRY_ORGS`. | STANDARD | AGENT-SAFE |
| AGA-4 | none on the board | `/inbox` redirects to `/waiting` when `WAITING_ORGS` is on, else to the home page | Later | AP-2c (task 2001) | A vitest on the redirect rule. | LOW | AGENT-SAFE |
| AGA-5 | 2108 | An R8 two-org test for the six observability readers and the alias read, on a real database | 3 | none | Org B sees nothing of org A in each reader. No test skips. | HIGH (tenancy) | AGENT-SAFE |

The epic plan gives no task number for AGA-3 and AGA-4. The owner or the next session makes them. Do not guess one.

### Phase 1: the member view, read only, dark

| ID | Task | Slice | Rung | Depends | Done when | Tier | Gate |
|---|---|---|---|---|---|---|---|
| AGA-6 | 2109 | A shell manifest with no nav entry, and the flag `AGENTS_VIEW_ORGS` (empty). **Built, dark (2026-11-11).** §6.1 records the choice. | 3 | AGA-1 | The host loads the manifest. With the flag empty every route of this spec returns 404. A manifest test fences it. | STANDARD | AGENT-SAFE |
| AGA-7 | 2110 | `GET /projects/agents/overview`: one tenant read. Per agent: name, one-line job, source (built-in, org, external), state (working, idle, waiting on a person), last run, last error in plain words. **Built, dark (2026-11-11).** §6.1 records the shape. | 3 | AGA-6, AGA-5 | R8 with two orgs and no cross-read. Built-in agents carry "shared by Metorite". Each error says what happened and the next step. It follows §4. | HIGH (tenancy) | AGENT-SAFE |
| AGA-8 | 2111 | The section "Agents at work" in the Agents panel. It extends SW-8 and SW-51 when they land. Before they land, it is a minimal section in the same panel. | 3 | AGA-7, SW-8 | Four captures: light, compact, changed accent, beside the Projects board. It uses `src/lib/statusAccent.ts` and no local palette. The PR has a `Visual review:` line. **Built, dark (2026-11-11), owner preview pending.** §6.1 records the host. | STANDARD | AGENT-SAFE |
| AGA-9 | 2112 | The drill-down: the last 40 runs from `agent_run`, with outcome and run time. No trace blob. **Built, dark (2026-11-11): the route and the "Recent runs" list in each row of the Agents panel.** §6.1 records the shape. | 3 | AGA-8 | A member sees only runs inside their D12 visibility. The executive-only trace stays gated. | HIGH (tenancy) | AGENT-SAFE |
| AGA-10 | 2113 | The chip "Waiting on a person" on each agent, linked to `/waiting` | 3 | AGA-8, AP-1b, AP-2c | The chip count equals the Waiting list count for that agent. One test. | STANDARD | AGENT-SAFE |
| AGA-11 | 2114 | Chat tools in a new pack `agents-overview`: `agents_list`, `agent_status`, `agent_recent_runs`. Read only. Route-manifest entries for the MCP allowlist. | Later | AGA-7 | The new-chat-tool fence set passes (claim strings, pack summary, pinned tables). Each panel field is reachable from chat. | STANDARD | The code is AGENT-SAFE. The `TOOL_PACKS` line is **OWNER-GATE**: hand it over. |
| AGA-12 | 2115 | The empty state: "No agent yet" links to Integrations, AI agents, and to the playbook prompt when PB-3 lands | Later | AGA-8 | The copy follows the failure-copy and user-facing-AI-copy rules. It names no internal agent name. | LOW | AGENT-SAFE |

### Phase 2: cost, honest

| ID | Task | Slice | Rung | Depends | Done when | Tier | Gate |
|---|---|---|---|---|---|---|---|
| AGA-13 | 2116 | Adopt task 714 (WS-6b, WS-6d, WS-6e): the durable `llm_call` cost table and the token columns on `agent_run`. Make no second task. | Later | none | The migration is expand-only (R6), FORCE RLS, `organization_id NOT NULL`. The next free number comes at build time (R1). An R8 test. The unattributed bucket stays out of every tenant read. | HIGH (migration, tenancy) | AGENT-SAFE |
| AGA-14 | 2117 | Credits by agent for one org: one read over the Console credit seam, grouped by the `X-CC-Agent` stamp. Read only. | Later | AGA-13, Q2, H-42 for the price text | An org sees only its own rows. No write to a balance. A missing figure reads "not measured". | HIGH (money) | AGENT-SAFE. The pricing text waits on the owner (D152). |
| AGA-15 | 2118 | A cost column in the panel and the drill-down, and the chat tool `agent_cost` | Later | AGA-14 | The panel and chat show the same number. A test pins that unknown is never 0. | STANDARD | AGENT-SAFE |
| AGA-16 | 2119 | A link from each agent's cost to the member cap (WS-16, D2) when it ships. No new cap logic. | Later | AGA-15 | A link only. | LOW | AGENT-SAFE |

### Phase 3: the operator side (only if the owner asks)

| ID | Task | Slice | Rung | Depends | Done when | Tier | Gate |
|---|---|---|---|---|---|---|---|
| AGA-17 | 2120 | An Operator Console page "Agents": the registry list, register and remove, and the self-mutation commit queue | Later | Owner question on the operator registry, `operator_identity_and_access.md` | A staff session only. It reads the gateway through an operator seam and not the customer BFF. D56 is the precedent. | HIGH (deploy path, operator) | AGENT-SAFE to build. Any deploy is OWNER-GATE. |
| AGA-18 | 2121 | Remove the customer copy of the operator page after AGA-17 is live | Later | AGA-17 | `nav.test.ts` and the route tests change. No unrelated refactor. | STANDARD | AGENT-SAFE |

**Count.** Phase 0: 5. Phase 1: 7. Phase 2: 4. Phase 3: 2. Total 18.

### 6.1 Notes on the anchors

- **AGA-6 and the existing `agents.json`.** The file `app_manifests/shell/agents.json` exists and carries the nav entry of `/agents`. The slice must not remove that entry. It adds a second manifest file, or a contribution in `projects.json`. The builder picks one, reads `tests/unit/test_shell_manifests.py` and `tests/unit/test_agent_manifest.py`, and records the choice in the PR.
- **AGA-6, the choice (built 2026-11-11).** The slice adds a contribution in `projects.json`: one `gates` entry, `/projects/agents` with the feature `agents_view`. It adds no second file and no nav entry. The reasons:
  - The member view is a part of Projects (§5.1), so its gate belongs to the Projects package.
  - A `gates` entry is the existing seam for a route that is not a pane, as `workflows.json` shows.
  - A second file is a second package id for one part of one app.
  - `agents.json` keeps its one nav entry, as it was.
  - `agents_view` is not yet a slug of `FEATURES` or of `feature_catalog`, so no member holds it, and the workbench route stays closed. AGA-8 adds the slug when it draws the section.
  - Fence: `test_shell_manifests.py::test_the_agents_view_is_a_projects_gate_and_no_nav_entry`.
- **AGA-6, the flag.** `AGENTS_VIEW_ORGS` is a comma list of organization ids, or `*`. Empty is off. Its one reader is `agent_summary.agents_view_allowed`, and a SIGHUP reload applies a change (`docs/flag_reload.md`). Each flip is OWNER-GATE (`enforcement-flip`). Fence: `tests/unit/test_agents_overview_r8.py`.
- **AGA-4 and `/waiting`.** The page `/waiting` does not exist yet. AGA-4 waits for AP-2c.
- **AGA-7 and `agent-summary`.** The route `GET /projects/agents/overview` is new. The route `GET /{project_id}/agent-summary` stays the one summary route of SW-8 (it forbids a second one). The builder decides in the PR whether the overview is a mode of that route. If it is not, the PR names the reason.
- **AGA-7, the decision (built 2026-11-11).** The overview is not a mode of `agent-summary`. It lives in the same module, `agent_summary.py`. The summary counts the tasks of one project, and a project id that the caller cannot see answers 404. The overview reads the whole organization, and most of its agents hold no task.
- **AGA-7, the shape.** Each row holds `key`, `name`, `agent`, `job`, `source`, `label`, `state`, `runs`, `last_run` and `last_error`. The answer also holds `count`, `viewer` and `truncated`. The name of an outside agent is `agent_oauth.client_label(via)`, never the `client_name` that its connection declares.
  - `built_in` is the code registry of `gateway.routes.agent`, with the label "shared by Metorite". `org` is a row of `dynamic_agents`. `external` is one row for each `client_via` of the task runs.
  - An outside agent is `waiting_on_person` when an open run is `awaiting`. It is `working` when an open run waits in the queue or works, and `idle` otherwise.
  - A built-in or org agent reads the live presence of the tenant. Only an admin counts the live runs of other members.
  - The admin of §4 is the holder of `data:org:read`. The lead arm reads `authority.led_projects`. The member arm is the runs on the tasks that the member created or holds, and the member's own runs.
  - `last_error` holds `at`, `what` and `next_step`. It comes from a failed run, or from an open run on a revoked or expired connection.
  - A built-in or org agent has no `last_error` yet, because the live presence holds no error. AGA-9 reads `agent_run`.
- **AGA-8, the host (built 2026-11-11).** The Agents panel of SW-8 and SW-51 does not exist yet. The slice adds a minimal section in Projects. A toggle "Agents" beside "Needs attention" swaps the board for the section "Agents at work", as that lens does. The read goes through the `/api/projects` relay, so no new relay exists. A 404, 401 or 403 answer draws no toggle and no section. The section is read only. The overview holds no task for an agent, so a row names none. The workbench route `/projects/agents` and the `agents_view` feature slug stay for a later slice. Files: `components/AgentsAtWork.tsx`, `lib/agentsAtWork.ts`.
  - The chat tool waits for AGA-11 (`agents_list` in `PLANNED`). The route is in no door set.
- **AGA-9, the shape (backend built 2026-11-11).** The route is `GET /projects/agents/{agent_key}/runs` in `agent_summary.py`. It is a list of runs, not a third summary. `agent_key` is a `key` of the overview, and `parse_agent_key` checks its shape before any read.
  - The answer holds `key`, `source`, `runs_of`, `viewer`, `runs`, `count` and `truncated`. Each run holds `id`, `state`, `started_at`, `ended_at`, `run_seconds`, `task` and `error`. It holds at most 40 runs, newest first.
  - An outside agent reads its task runs in `pm_agent_runs` (`runs_of: task`). The read uses the `seen` text of the overview, so its count agrees with the `runs` of the overview. It leaves out a run on a task that the viewer cannot see. A task run carries the task id and its title.
  - A built-in or org agent reads its chat runs in `agent_run`, by `agent_name` (`runs_of: chat`). An admin reads each run of the organization. A lead or a member reads only their own runs, the rule of the live presence. A chat run has no task.
  - Only the chat fold writes `agent_run`. So a run that `agent_dispatch.py` starts on a task is not in the chat runs.
  - No run holds the trace, the traceback, the tool list, the model or the stored error text. A failed chat run reads one fixed sentence. The trace stays executive-only at `/debug/runs/{run_id}`.
  - Five cases answer one 404: an unknown key, a key of another organization, a key out of reach, a bad shape and the flag off. Its body names no input.
  - Fence: `tests/unit/test_agent_runs_drilldown_r8.py`. Its table `_sees` holds §4, so a change of Q3 changes one place. The chat tool `agent_recent_runs` is in `PLANNED` for AGA-11.

---

## 7. Verification commands

Run these after the slice named. The commands are pending until the slice ships.

| Slice | Command |
|---|---|
| AGA-2 | `cd workbench/control_plane && npx vitest run src/lib/nav.test.ts` |
| AGA-3, AGA-4 | `cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/agents src/app/inbox` |
| AGA-5 | `bash scripts/dev_db.sh`, then `eval "$(bash scripts/dev_db.sh --export)"`, then `uv run pytest -q -p no:cacheprovider tests/unit/test_activity_feed_tenant.py tests/unit/test_cost_rollup_tenant.py` and the new R8 file |
| AGA-6 | `uv run pytest -q -p no:cacheprovider tests/unit/test_shell_manifests.py tests/unit/test_agent_manifest.py` and `cd workbench/control_plane && npx vitest run src/lib/nav.test.ts src/lib/shell/seams.test.ts` |
| AGA-7, AGA-9 | `uv run pytest -q -p no:cacheprovider tests/unit/test_projects_agent_focus_r8.py tests/unit/test_agents_overview_r8.py tests/unit/test_agent_runs_drilldown_r8.py`, with the DB gate on |
| AGA-8, AGA-10, AGA-12 | `cd workbench/control_plane && npx tsc --noEmit && npx vitest run` for the panel, and the four captures |
| AGA-11 | `uv run pytest -q -p no:cacheprovider tests/unit/test_projects_chat_coverage.py` and the new-chat-tool fence set |
| AGA-13 to AGA-15 | `uv run pytest -q -p no:cacheprovider` for the new cost files, R8 on, and the migration ledger check |
| Every slice | `uv run ruff check .` and `uv run python scripts/affected_tests.py --run` |

---

## 8. Risk and owner gates

- **Tenancy (HIGH).** The view reads across agents, runs and credits. AGA-5, AGA-7, AGA-9, AGA-13 and AGA-14 keep the full review loop and the R8 proof.
- **Money (HIGH).** AGA-14 reads credit data. It writes no balance and charges no card.
- **Pricing.** A credit figure per agent touches D152. The owner answers Q2 first.
- **OWNER-GATE.** The `TOOL_PACKS` line (AGA-11). Each flip of `AGENTS_VIEW_ORGS` needs the dated `enforcement-flip` grant. Name the flag and the box in the same message. No migration reaches production without the backup evidence (`CLAUDE.md` §3a).
- **Stale text found.** `observability_e2.md` says H-201c and H-201e are "not merged". Both are on `main`. A slice that touches that spec fixes the header (R4).

## 9. Open items

- Q1, Q2 and Q3 await the owner (§1.2).
- The operator registry as a Console page (AGA-17) waits for the owner. The recommendation is to keep the hidden, server-gated route until a second operator needs it.
- Task 103 ("Add an agent registry so every agent knows the other agents") may close as covered by AGA-7 after the owner agrees. Task 1232 (retire the GTD agent) stays separate.
