# Agent coordination — many agents and many people on one board

**Status: ACTIVE (SW-0, 2026-10-30).** This spec gates every SW slice (D135.4). The decisions are **D140** in `work_plan.md` §3.

**Verified against code on 2026-10-30** (origin/main of that day). §6 holds each anchor and the commit it was read at. Re-verify each anchor at dispatch (`work_plan.md` §1 item 4).

**Board row:** **WS-MCP** in `work_plan.md` §2.1. **Metorite:** epic 321 (Agent swarm coordination), task 331 (SW-0). **Owner directives:** D135 (2026-10-28), and the owner's ask of 2026-10-30. The owner asked to "build out the Projects app-related tasks and the MCP-related tasks completely". The goal is "full capability for agent orchestration".

**What is built, and where its record lives.** `external_agent_access.md` §12 is the as-built record of runs, the lease, `my_work`, session keys, the stranded sweep and Needs attention. This spec does not copy it. This spec owns the coordination model, the decisions of D140 and the order of the SW slices.

**Production on 2026-10-29** (measured by the session that wrote the research comment on epic 321):

| Flag | Production | What it gates |
|---|---|---|
| `AGENT_RUNS_ENABLED` | `true` | Runs, the lease, `my_work` groups 1 and 2, Needs attention |
| `AGENT_RUN_SWEEP_ENABLED` | unset, so off | The stranded sweep of SW-10a. No fingerprint exists, so `stranded` reads null on every run |
| `AGENT_MCP_SESSIONS_ENABLED` | unset, so off | The session key of SW-7. Two terminals on one connection share one run slot |

**Single owner.** This file owns the topic. `external_agent_access.md` owns the inbound door (OAuth, scopes, the tool allowlist) and §12 owns the built record. `external_agent_dispatch.md` owns the outbound door (WS-56). `project_management_app.md` owns the task store and D-PM-4. Each of them links here for the coordination model and adds nothing.

---

## 1. The answer in one screen

Many agents and many people work on one Metorite board at the same time. Metorite is the place where they coordinate (D135).

- **One agent run executes a task at a time.** The lease is the one open run of a task. A second agent gets 409 `task_held` with the holder's name, and it takes the next task.
- **The member is the only authority.** An agent acts as its member, narrowed by the connection's scopes and pins. A label, a delegate, a plan or guidance grants nothing.
- **No task dies in silence.** Each open task that an agent holds has a live path. When it loses that path, Metorite nudges the member once, then hands the task to a person. It never reassigns.
- **Pull first, push later.** Every wake-up lands as a `needs_agent` run that `my_work` serves. Push to an agent host waits for WS-56.
- **A task has a delegate beside its assignees** (SW-29). A person stays accountable. The agent does the work.
- **Agents coordinate on the board, not in chat.** They use claims, handoff notes, "affects" notes, wake-ups, shared numbers and the conflict radar.

The next slices fix liveness first, because the board shows most agent work as idle today (task 1045).

---

## 2. Scope and non-goals

### 2.1 In scope

- The model of §3: the nouns, the run states, the liveness contract, wake-ups, notes, sequences and cost.
- The design of the agent session identity and its trust limits (SW-6, §3.7).
- The slices SW-0 to SW-36, and the two liveness bugs of tasks 1045 and 1054 (§5).
- The MCP tools and arguments that these slices add, through the allowlist of `external_agent_access.md` §9.5.
- The Projects surfaces that these slices add. These are the card states, the agent roster, the agent profile, the delegate field, guidance in Settings and the run plan.

### 2.2 Non-goals

- **Paperclip's runtime and adapters.** We adapt its rules. We do not adopt its code.
- **Agents that hire agents.** No CEO agent and no org chart of agents.
- **A permission default that skips the member's approval.** No `dangerouslySkipPermissions` default.
- **Company-wide default visibility.** It conflicts with D12.
- **A second principal.** An agent gets no grants of its own (D140.2).
- **A webhook push in this spec.** SW-17 waits for WS-56.
- **A new app or a nav pane.** Every surface lives inside Projects or Settings. The nine-pane allowlist of `launch_surface.md` §2 binds.
- **The app parity of D136** (CRM, Email and the other apps over the MCP). Epic 495 and the MCP-P slices own it.
- **The MCP gap tasks of epic 354** (MCP-G1 to MCP-G11, MCP-Ga to MCP-Gd). They stay in that epic. This spec names them where a slice depends on one.

### 2.3 Rules that bind every slice

1. **R5, R6, R7 and R8.** A new table is tenant-scoped under FORCE ROW LEVEL SECURITY. A migration is expand only. Each rule names its fence. Each query runs on a real database.
2. **R1.** Take each migration number at build time, and check it again at merge.
3. **Ship dark.** New behaviour lands behind a flag, default off. A bug fix may go live at the merge under `agent_runs_enabled` when it only adds an optional argument. SW-10a part 1 did this. The slice says so.
4. **One seam each.** One link tool, one notes path through `my_work`, one sequence store, one run state SQL and one lease rule.
5. **Every body is untrusted text.** A value that a person or another agent wrote leaves the edge as `{"untrusted_text": ...}` (`external_agent_access.md` §9.4).
6. **Chat parity.** A new route gets a row in `skill_projects/manifest.py`, or `test_projects_chat_coverage.py` fails (D-PM-37). A new edge write route joins `MCP_WRITE_ROUTES`, or `test_mcp_edge_coverage.py` fails.
7. **The risk tier follows `next-ticket.md` step 1b.** A migration, identity, auth or money makes a slice HIGH.

---

## 3. The model

### 3.1 The nouns

| Noun | What it is | Where it lives | Built |
|---|---|---|---|
| **Member** | A person in one organization. The only principal | `app_user`, the session of `get_db()` | Yes |
| **Connection** | One sign-in of one agent client for one member. It holds a scope list and pins, and the list is a ceiling | `agent_connection` (migration 248) | Yes (WS-55) |
| **Session** | One MCP session on one connection. Each terminal of Claude Code is one session | `pm_agent_runs.session_key` (migration 302), signed `X-Agent-Session` | Built, dark (SW-7) |
| **Client stamp** | The name of the client: `mcp:<host>:<slug>`, for example `mcp:claude.ai:claude-code` | `pm_agent_runs.client_via`, `meta.via` on every activity | Yes (EA-13a) |
| **Agent** | What the board shows as one worker. An external agent is a member and a client stamp. An internal agent is `agent:<name>` (D-PM-4). §3.7 holds the rules | the agent reference of §3.7 | Partly. SW-29 adds the profile |
| **Run** | One piece of agent work on one task. It holds typed activities | `pm_agent_runs` (migration 267, widened by 289) | Yes (EA-12) |
| **Activity** | `prompt`, `thought`, `action`, `response`, `elicitation` or `error`. `thought` and `action` are ephemeral | `pm_agent_run_activities` | Yes |
| **Signal** | `continue`, `stop` or `select` on an activity. SW-33 adds `auth_request` | `pm_agent_run_activities.signal` | Partly |
| **Lease** | The execution slot of a task: its one open run. It holds while the run's newest activity is newer than two hours | the index `pm_agent_runs_one_open_per_task` (migration 289), `filters.lease_held_sql` | Yes (EA-14a) |
| **Delegate** | The one agent that a task is handed to, beside its assignees | `pm_tasks`, new columns | No. SW-29a |
| **Guidance** | The org's written rules for agents, at the org, a space or a project | a new tenant table | No. SW-31 |
| **Plan** | A checklist on a run that the agent keeps current | `pm_agent_runs.plan`, a new column | No. SW-32 |
| **Wait record** | A named owner and action for a task that waits on a person or a system | `pm_tasks`, new columns | No. SW-9 |
| **Strand** | The fingerprint of a run with no live path, and later its recovery record | `pm_agent_run_strands` (migration 305) | Built, dark (SW-10a) |
| **Handoff note** | Four short texts when an agent stops: done, left, evidence, next | a `response` activity with `content.handoff` | No. SW-24 |
| **Affects note** | A note from one task to another: "my change affects yours" | `pm_task_links` with the type `affects` | No. SW-25 |
| **Sequence** | A named counter of a project, for example the migration number | a new tenant table | No. SW-27 |
| **Check** | A read-only command that proves a task is still open | `pm_tasks.check_text` and its result columns | No. SW-23 |

### 3.2 The run states

A run has a stored **status** and a derived **state**. The gateway sets every status. An agent never writes one directly (`external_agent_access.md` §12.1).

**The stored statuses.** `created`, `in_progress`, `awaiting`, `stopping` (the four open ones), then `completed`, `stopped`, `failed`, `abandoned` and `released`. Migration 289 added `abandoned` (another session took the task over after the lease) and `released` (the holder gave the task back).

**The derived states**, from one SQL text, `filters.AGENT_RUN_STATE_SQL`:

| State | Rule | The card (EA-13g, `agentCardLook.ts`) |
|---|---|---|
| `queued` | Status `created` | A still, thin, solid accent edge (EA-13h: no dashed edge) |
| `working` | Status `in_progress`, an activity in the last 5 minutes, and no answer waits | A moving accent edge and a live line |
| `waiting` | **New (task 1045).** Status `in_progress`, and the newest activity set a `waiting` time that has not passed | A still accent edge: "Claude Code · waiting on CI · about 25 min" |
| `answered` | Status `in_progress`, and the newest activity is a member's prompt that no agent read yet | The accent edge, "Answered · agent picks up next" |
| `stale` | Status `in_progress`, and no activity for 5 minutes (or past the `waiting` time) | An accent glow that breathes slowly (EA-13h). The pill says "idle", and the line shows only the agent's last note |
| `awaiting` | Status `awaiting`: a question waits on a named member | A still amber edge, "Needs you" |
| `stopping`, `failed` | The status itself | Muted, or a still red edge |

**`stranded` is a flag, not a state.** A run is stranded when three things are true (`filters.stranded_clause`). It is `in_progress`, and its state is `stale`. It has no live path: its lease ended, or its connection is revoked or expired. The run wire carries `stranded: {reason, idle_minutes, note}`.

While the sweep is off, no fingerprint exists, so `stranded` is null. That is why the two quiet runs of 2026-10-29 showed `stranded: null` after more than 4 hours.

**`waiting` comes first in the `in_progress` arm.** The order is `waiting`, then `stale`, then `answered`, then `working`. Any later activity clears the `waiting` time, so the rule holds only while the waiting line is the newest activity. A member's answer clears it too, so the card then reads `answered`.

### 3.3 The liveness contract

**The rule (D140.3).** Each open task that an agent holds has a live path. A live path is one of these:

1. **A working run.** An activity in the last 5 minutes.
2. **A waiting run.** A `waiting` line whose time has not passed. The time is at most 120 minutes (D140.8).
3. **A question.** An `awaiting` run, addressed to a named member. It keeps the task for three leases.
4. **A wait record** (SW-9). A named owner who can see the task, and a concrete action.
5. **An open blocker.** An open `blocks` link from another open task.

**Recovery, when no live path exists** (SW-10a, `external_agent_access.md` §12.6):

1. **First miss.** The sweep writes one fingerprint `(run, reason)` and sends ONE bell to the run's member. The run reads `needs_agent` `stranded`, so the member's next session gets it from `my_work`.
2. **Second miss.** One lease later, with no activity, the sweep turns the fingerprint into a recovery record with its evidence. The task shows in Needs attention (SW-11a) with a named owner and two acts, Resume and Close the run.
3. **Never.** The sweep never reassigns the task, never picks a substitute agent and never marks the task done.

**Two clocks, two meanings.** The 5-minute **stale band** says whether an agent works on the task now. It is display only. The 2-hour **lease** says whether the run still keeps the task.

A quiet run keeps its task until the lease ends. An orchestrator can wait 20 to 50 minutes on sub-agents or CI and post nothing (task 1045). The `waiting` line tells the board the truth during that wait.

**The lease clock** reads the newest activity of any author, a member's answer too (D140.10). Resume (SW-11a) depends on this: its member prompt renews the lease. Task 1045b adds linked events (a pull request push or a check event) to the clock, and keeps them out of the answered rule.

### 3.4 Wake-ups

**Every wake-up is a `needs_agent` run** (D140.4). `list_agent_runs(needs_agent=true)` and `my_work` group 1 serve it. The agent's next session reads it. Nothing pushes.

| Event | The run reads | Slice |
|---|---|---|
| A member asks an agent ("Ask an agent", EA-12d) | `needs_agent` `queued` | Built |
| A member answers a question | `needs_agent` `answered` | Built |
| A member presses Stop | `needs_agent` `stopping` | Built |
| The sweep finds no live path | `needs_agent` `stranded` | Built, dark (SW-10a) |
| A member presses Resume | `needs_agent` `answered` | Built (SW-11a) |
| The last open blocker closes | `needs_agent` `queued`, with the reason "blockers resolved" | SW-15 |
| A reviewer requests changes | `needs_agent` `queued` | SW-18 |
| A person delegates a task, or writes `@agent` | a `created` run on that agent | SW-30a |

**Each wake is idempotent.** A wake keys on a fingerprint, so a status flap or a second gateway process wakes once. SW-10a keys on `(run, reason)`. SW-15 keys on `(task, the set of closed blockers)`.

**Push is later (SW-17).** It delivers the same `needs_agent` run to a host that the member registered. A delivered wake never proves that work started. The lease is the proof.

### 3.5 Notes, numbers and the radar

- **A handoff note** (SW-24) is how an agent stops with evidence. `release_task` and `finish_agent_run` take `handoff: {done, left, evidence, next}`. The note is a `response` activity with `content.handoff`, so the six activity types do not change. `get_task_context` returns the newest note first.
- **An affects note** (SW-25) is how one agent tells another what its change touches. It is a `pm_task_links` row of the new type `affects`, with a short note. The holder of the other task gets it once in `my_work`, with the reason `note_from_task`.
- **A sequence** (SW-27) hands out each number once. An agent calls `reserve_number(project_id, sequence, task_id)` and gets the next value in one transaction. It ends the R1 class of clash at the source.
- **The conflict radar** (SW-26) catches what a sequence cannot prevent. Two open runs whose pull requests change the same file, or add the same new number, each get an affects note. Needs attention lists the pair as "Will collide".
- **A Check** (SW-23) is the field that lets `HANDOFF.md` retire into tasks (D135.1). An agent runs it, and a pass closes the task with its evidence.

**One link tool.** SW-16 adds `add_task_link` and `remove_task_link` for `blocks`. SW-25 adds `affects` and `relates_to` to the same two tools. The name `set_dependencies` in the first text of SW-16 changes to `add_task_link`, so no second link tool exists.

### 3.6 Cost

**What we measure today.**

- **In-app agents** spend the org's AI credits (`customer_console/credits.py`).
- **External agents** run on the member's own AI account. Metorite sees no tokens unless the agent reports them.
- **The Overview** of Reports measures each agent run: its outcome, its success rate, its run time and the silent runs (`analytics_agents.py`, RA-2, and `analytics_agent_runs.py`, RA-4). It uses the agent run time when no cost exists.

**What comes later.** SW-20 adds an optional, self-reported `usage` on `finish_agent_run`, labelled as self-reported, and a `request_depth` on tasks that an agent makes. It waits on H-42, the rate card. The agent profile (SW-29b) shows the run time now and the usage after SW-20. A cost that we do not know never shows as zero.

### 3.7 The agent session identity and its trust limits (SW-6)

SW-6 asks four questions. This section answers them, and it is the design that SW-7b, SW-8 and SW-29 build from.

1. **What is one agent?**
   - **On the board, one agent is one member and one client stamp.** "Claude Code (Ravi)" is one agent, on every machine and through every sign-in.
   - **For execution, one worker is one MCP session of one connection.** The edge mints the session key when the session starts (SW-7). The client never chooses it.
   - **With SW-7 off, one worker is one connection.** Two terminals on one connection then share one run slot. The protocol says so (§12.4 step 3).
2. **What can the client name?** A free-text `agent_label`, for example `backend-worker-3`. It is display data only. The gateway cleans it with the `_clean_body` rules and caps it at 40 characters (SW-7b).
3. **What does it grant?** Nothing. Visibility and writes stay the member's, narrowed by the connection's scopes and pins (R5, `external_agent_access.md` §7.1). The same holds for a delegate, a plan and guidance.
4. **What about in-app agents?** The dispatch sink (`agent_dispatch.py`) writes `agent:<name>`. The board maps it to the same reference as an external agent, so the board has one agent vocabulary (D-PM-4).

**The agent reference.** One shape names an agent on every wire and surface:

- An external agent: `{kind: "external", member, client_via, client_name}`.
- An internal agent: `{kind: "internal", name}`.

The delegate (SW-29a) stores this reference. It names the member and the stamp, never a connection id or a session key. A connection changes at each sign-in, and a session ends with its terminal. The stamp stays.

**The trust limits, each with its fence.**

| Limit | Fence |
|---|---|
| A label never reaches an authorization check | SW-7b: a structural test that no module of `acb_auth` and no visibility seam reads `agent_label` |
| A delegate never widens what an agent can see | SW-29a: an R8 test that a delegate on a task the agent's member cannot see gives the agent nothing |
| The session key comes only from the signed header | Built: `test_mcp_edge_signing.py` |
| Guidance never overrides the server's rules | SW-31: a prompt fence in `test_mcp_edge_prompts.py` |

### 3.8 Metorite and Linear, side by side

The source is the Linear research of 2026-10-30, the comment on epic 321. Appendix B holds the full table.

| Part | Linear | Metorite | Where Metorite differs, and why |
|---|---|---|---|
| Who the agent is | An app user, installed by OAuth `actor=app`. Not a billable seat | A connection that acts as its member | **The member is the only authority** (D140.2). A second principal breaks the one subject grammar |
| Delegate | Assigning to an agent sets `delegate`. The human stays assignee | SW-29a adds `delegate` beside the assignees | The same, after SW-29a |
| Trigger | Delegation, an @mention or the API. A webhook `AgentSessionEvent` (`created`, `prompted`) | "Ask an agent", a claim, and SW-30a's "Delegate to…" and `@agent`. The agent pulls with `my_work` | **Pull first, push later** (D140.4). Claude Code connects to our server and cannot take a push |
| First answer | Acknowledge in 5 s, first activity in 10 s, or "unresponsive" | "Not picked up yet" after 2 minutes (SW-30a) | An agent pulls at the start of its session, so seconds do not apply |
| States | pending, active, awaitingInput, error, complete, stale | queued, working, waiting, answered, stale, awaiting, stopping, failed, and the flag stranded | More states, because Metorite also has a lease and a takeover |
| Stale | 30 minutes with no activity | A 5-minute stale band for display, and a 2-hour lease for the slot | **Two clocks.** An orchestrator waits on long CI with no post. The lease must outlast the wait, and the card must not lie (D140.10, task 1045) |
| Activities | thought, action, elicitation, response, error, prompt | The same six | The same |
| Plan | A checklist, whole-list replace, four statuses | SW-32: the same, capped at 30 steps | Caps added (D140.7) |
| Signals | `stop`, `select`, `auth` | `continue`, `stop`, `select`. SW-33 adds `auth_request` | The same, after SW-33 |
| Guidance | Workspace and team, team wins, with history | SW-31: org, space and project, the nearest wins, with history | One more level, because Metorite has spaces (D140.6) |
| One worker per issue | Not stated in the sources read | One open run per task, 409 `task_held` | **Many agents of many members share one board** (D135) |
| MCP | `/mcp` and `/mcp/readonly`, OAuth 2.1 with dynamic registration | `/mcp`, OAuth 2.1 with Client ID Metadata Documents, 25 tools, one prompt, no resources | SW-34 adds resources and `/mcp/readonly` |

---

## 4. The decisions (D140)

`work_plan.md` §3 holds them as **D140**. They are summarized here so a builder reads them beside the slices. §3 is the record.

**Who decided what.** Items 1 to 4 are owner calls: the owner set them in task 331, under D135. Items 5 to 10 are agent defaults of this spec. They stand unless the owner objects. Item 5 waits on the owner's answer to Q2 of §8.

1. **The execution slot sits beside the assignees.** D-PM-4 stays: a task keeps many assignees. The slot is the one open run of a task, which the index of migration 289 enforces. `held_by` is its read. It is not a column on `pm_tasks`, because the index already decides a race.
2. **The member is the only authority.** A session label, an agent label, a delegate, a plan and guidance are display or context data. None of them reaches an authorization check.
3. **The liveness contract.** Each open task that an agent holds has a live path (§3.3). Recovery nudges once, then escalates to a person. It never reassigns.
4. **Pull first, push later.** Wake-ups land as `needs_agent` runs that `list_agent_runs` and `my_work` serve. Outbound push waits for WS-56 (SW-17).
5. **A delegate field beside the assignees.** A task names at most one agent as its delegate, by the agent reference of §3.7. The human assignee stays accountable. The default holds until the owner answers Q2. A member may delegate to another member's agent only when that agent is shared. Its member or an admin shares it. Q2 stays open for the owner.
6. **Agent guidance, nearest first.** A task gets the guidance of its project, then of each parent up to its space, then of the org. Each block names its source and its version. It leaves the edge as untrusted text, and it never overrides the server's rules.
7. **A run plan is a whole-list replace.** Each call sends every step. At most 30 steps of 200 characters, each `pending`, `in_progress`, `completed` or `canceled`.
8. **The `waiting` state is capped at 120 minutes.** After that time, with no new activity, the run reads `stale`. A waiting line renews the lease as any activity does, and the cap never passes one lease.
9. **The stranded sweep goes on in staging first.** The nudge verb (task 1054), the `waiting` state (1045a) and the linked events (1045b) land before the flip. Production follows after the staging test of SW-10b passes.
10. **The lease is two hours, from the newest activity of any author.** A question keeps the task for three leases. This replaces the 15-minute TTL of the first text of SW-4.

---

## 5. The slices

Each slice names its scope, its acceptance ("done when"), its fence (R7), its risk tier and its gate label. **AGENT-SAFE** means an agent may build it. **OWNER-GATE** means the act needs the owner, or a dated grant in `.claude/OWNER_GRANTS.md` that names the gate id.

### 5.0 The order to build

1. **Liveness, now.** 1045a and 1054 in parallel, then 1045b and 1045c.
2. **SW-4**, one lease rule everywhere.
3. **SW-10b**, the sweep on staging, then production.
4. **Delegation.** SW-29a, then SW-8 and SW-29b, then SW-30a.
5. **Guidance and plans.** SW-31 and SW-32 in parallel.
6. **Multi-agent safety.** SW-16, then SW-24 and SW-27 in parallel, then SW-25, then SW-26.
7. **The rest, in this order.** SW-9, SW-15, SW-23, SW-5, SW-7a, SW-7b, SW-18, SW-33, SW-34a, SW-34b, SW-19a, SW-19b, SW-19, SW-35 and SW-36.

**A parallel lane.** SW-19c and SW-28 share no file with the steps above. Build them at any time.

**Later, the owner decides.** SW-17, SW-20, SW-21, SW-22 and SW-30b.

**Why this order.** On 2026-10-29, 8 of 9 open runs read `stale`, and most were not idle (task 1045). A board that lies about its agents cannot coordinate them. Delegation comes next, because the owner's ask of 2026-10-30 is to hand work to agents. Guidance and plans make delegated work legible. The safety slices matter once many agents work at once.

### 5.1 P0 — the spec

#### SW-0 · This spec, its INDEX row, the board row and D140

- **Status.** This file.
- **Done when.** `INDEX.md` lists this file as ACTIVE. The WS-MCP row names it. D140 is in §3. `spec-auditor` returns GO for P1 and P4.
- **Fence.** `test_index_completeness.py`, `test_doc_names_resolve.py` and `ste-lint.mjs` on this file.
- **Risk.** LOW. **Gate.** AGENT-SAFE.

### 5.2 P1 — the exclusive execution lease

**Built.** SW-1, SW-2, SW-3 and SW-1a are EA-14a (#1192, `ffc09bb68`, 2026-10-25). SW-13c is #1300. The build put the slot in an index on `pm_agent_runs`, not in columns on `pm_tasks` (D140.1). Fence: `test_agent_run_exclusive.py`.

#### SW-4 · One lease rule everywhere

- **Scope.**
  1. `AgentRunPanel.tsx` reads `lease_expired`. So for a dead connection, it does not say that the next claim can take the task. `external_agent_access.md` §12.3 names this gap. Make the panel read the server's holder-free answer, which `held_by.stale` already carries.
  2. Cap a `waiting` time at one lease. When `AGENT_RUN_LEASE_SECONDS` is below 7200, the cap is the lease in minutes, not 120.
  3. Add an R8 test with a moved clock, if `test_agent_run_exclusive.py` lacks one. A claim at the lease plus 1 minute takes the task over. A claim at the lease minus 1 minute gets 409.
- **Not in scope.** The 15-minute TTL of the first text (D140.10). The end of a run when its task closes is H-409 item 1, Metorite #748.
- **Done when.** The panel, the card and `held_by.stale` agree for a dead connection. The moved-clock test passes on a real database.
- **Fence.** `test_agent_run_exclusive.py`, `agentRunsPanel.test.ts`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** 1045a for item 2.

#### SW-5 · Show the lease on the board and in the MCP tools

- **Built.** `held_by` on `get_task` and `list_tasks`, the `unheld` filter, the fixed text of a 409 `task_held`, and the Release button (EA-14a).
- **Scope left.**
  1. List what EA-13g already draws. Read `agentCardLook.ts` and `TaskBoard.tsx`. By `external_agent_access.md` §12.2 item 7, the card draws an edge for each run state. For `working` and `answered` it draws a live line with the client glyph and `agent_note`. It draws "Needs you" with the question, and "<client> idle". The panel has the Release button (EA-14a). That list names no member and no start time for the holder.
  2. Add only what is missing: a holder line on the card and in the task panel, "Worked by <client> for <member> since <time>".
  3. Add the filters "Held by an agent" and "Free" on the board. The server filters them through `unheld`.
- **Done when.** The two filters page on the server. `visual-review` passes in light mode, at compact density and under a changed accent.
- **Fence.** A vitest for the holder line, `test_projects_ui_actions.py` for the filter.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE.

### 5.3 P2 — an identity for each agent session

#### SW-6 · The session identity and its trust limits

- **Status.** Written in §3.7 of this spec.
- **Done when.** `spec-auditor` returns GO for SW-7b and SW-8.
- **Risk.** LOW. **Gate.** AGENT-SAFE.

#### SW-7 · One key for each MCP session

- **Built.** #1312, dark behind `agent_mcp_sessions_enabled` (`external_agent_access.md` §12.5). Migration 302.

#### SW-7a · Turn on the session key, staging first

- **Scope.** Set `AGENT_MCP_SESSIONS_ENABLED=true` on the edge unit of staging, and restart that unit. On production the unit is `acb-agent-edge`. Check with a real Claude Code. Then do the same on production.
- **Done when.** Two terminals on one connection hold two runs on two tasks. A resume keeps its run. A key change gives 404, and the client starts a new session.
- **Evidence.** The flag in the edge env file, the unit restart, and `my_work` from both terminals.
- **Risk.** STANDARD (a flag flip). **Gate.** OWNER-GATE `enforcement-flip`. The dated grant opens it until 2026-11-30.

#### SW-7b · A display label for each session

- **Scope.** `claim_task` and `start_agent_run` take an optional `agent_label`. The gateway cleans it and caps it at 40 characters. Migration, expand only: `pm_agent_runs.agent_label TEXT NULL`. The card, the run panel and the roster show it beside the client name.
- **Done when.** Two sessions with two labels show two names on the board.
- **Fence.** The structural test of §3.7. An edge schema test for the argument.
- **Risk.** HIGH (identity, a migration). **Gate.** AGENT-SAFE. **Depends on.** SW-6.

#### SW-8 · An agents roster for each project

- **Scope.** One panel in the Projects app, grouped by member, then agent, then session. Each row shows the task it holds, the run state, the latest line and the last activity time. Its acts: open the task, ask the agent, and stop the run (the member only). Extend `GET /projects/{project_id}/agent-summary`. Add no second summary route.
- **Chat first.** The assistant answers "what are my agents doing?" from the same route. It is a class A read.
- **Done when.** `visual-review` passes at all four widths. The read is a class A tool in `skill_projects/manifest.py`.
- **Fence.** A route test with two members and three sessions, and a vitest for the panel.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** SW-5. SW-7a for one row for each session.

### 5.4 P3 — the liveness contract

**Built.** SW-11a, Needs attention (#1345). SW-10a, the sweep, is #1333, dark.

#### 1045a · The `waiting` state

- **Scope.**
  1. `post_agent_activity` takes an optional `waiting: {on, until_minutes, note}`. `on` is `subagents`, `ci`, `review`, `deploy` or `member`. `until_minutes` is 1 to 120. 1045a ships this fixed cap of 120 minutes. SW-4 later lowers the cap to the lease when the lease is shorter. `note` holds at most 200 characters.
  2. Migration, expand only: `pm_agent_runs.waiting_on TEXT NULL`, with a CHECK of the five values, and `waiting_until TIMESTAMPTZ NULL`.
  3. `_write_activity` is the one writer. An activity with `waiting` sets both columns. Any other activity clears them.
  4. `AGENT_RUN_STATE_SQL` gains `waiting` first in its `in_progress` arm (§3.2). `WORKING_STATES` gains `waiting`, so the strip counts do not change.
  5. The run wire and the edge gain `waiting: {on, until, note}`. The note leaves as untrusted text.
  6. `AGENT_STATES`, `agentCardLook` and the pill gain `waiting`: a still accent edge. It does not move, because motion means working (EA-13g). The group-by gains a lane "Waiting".
  7. The text that tells agents. Change the server instructions (`mcp.py`), the `work_on_task` steps (`prompts.py`) and `next-ticket.md`. Each says: post a `waiting` line before a wait of more than 2 minutes. A wait on a person longer than 120 minutes is a question (`ask_on_task`) or a wait record (SW-9).
- **Done when.**
  - R8: a run with `waiting {on: ci, until_minutes: 30}` reads `waiting` at minute 20 and `stale` at minute 31.
  - R8: a thought at minute 10 clears the wait, and the run reads `working`.
  - `until_minutes` of 121 gets 422.
  - The stranded clause never matches a run that reads `waiting`.
  - A vitest maps `waiting` to its look in light and dark.
- **Fence.** `test_agent_runs_r8.py`, `test_mcp_edge_agent_runs.py`, `test_mcp_edge_prompts.py`, `agentCardLook.test.ts`.
- **Live.** It goes live at the merge under `agent_runs_enabled`. It only adds an optional argument, and the state shows only when an agent sends it.
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE.

#### 1045b · Linked events count as life

- **Scope.**
  1. The GitHub webhook handles two events in `webhook.py`: `_on_pull_request` with the action `synchronize`, and `_on_checks`. For each event, it finds each `in_progress` run of a task that references that pull request.
  2. Migration, expand only: `pm_agent_runs.last_signal_at TIMESTAMPTZ NULL`, with no default and no backfill. For each run, the webhook sets this column to now. It writes one line through `_system_activity`, for example "CI passed on PR 1473". The line holds fixed words and numbers only, and no text from the payload.
  3. One new helper, `filters.run_life_at_sql(alias)`, is the newer of `last_activity_at` and `last_signal_at`. The stale band, `lease_held_sql` and `strand_fits_sql` read it. Find every reader of the run clock at dispatch, and change each one.
  4. ⚠️ **The answered rule does not read it.** `AGENT_RUN_ANSWER_WAITS_SQL` compares `last_activity_at` with `last_agent_activity_at`. A linked event that moved `last_activity_at` would make the run read `answered`, and `needs_agent` would list it. So a linked event moves `last_signal_at` only.
- **Not in scope.** A push to a branch with no pull request. Check at dispatch whether the GitHub App subscribes to `push`.
- **Done when.**
  - A webhook test: a check event on a linked pull request refreshes a stale run, and it reads `working`.
  - R8: a check event on a run whose newest activity is an agent thought leaves the run out of `answered` and out of `needs_agent`.
  - R8: a linked event renews the lease, and the stranded clause stops matching.
- **Fence.** `test_agent_runs_r8.py`, `test_agent_run_sweep_r8.py`, a webhook test beside the GitHub fences.
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE. **Depends on.** 1045a.

#### 1045c · The card of a stranded run

- **Scope.** A run with `stranded` set draws in the warning tone with "no word since 14:31". It offers Release to a person who may end the run. The data comes from the `stranded` field of the run wire.
- **Done when.** A vitest maps a stranded run to its look in light and dark. `visual-review` passes.
- **Fence.** `agentCardLook.test.ts`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** 1045a. Real data needs SW-10b.

#### 1054 · A verb for the nudge in the Projects bell

- **Scope.** `VERB` in `app/projects/lib/notifications.ts` lacks `nudge`, so the bell reads "updated". Add the verb and the copy, for example "Claude Code has gone quiet on 'Fix the export' since 14:31. Resume or release it." Link the row to the run panel. Check every kind of `NOTIFICATION_KINDS` the same way.
- **Done when.** Each run notice kind reads its own verb.
- **Fence.** A vitest in `notifications.test.ts` checks that every kind of `NOTIFICATION_KINDS` (`notifications.py`) has a verb. The fixture `tests/fixtures/notification_kinds.json` (new) holds the kinds. The pytest `tests/unit/test_notification_kinds_fixture.py` (new) checks the fixture against `notifications.py`, so the two lists cannot drift.
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Do this before** SW-10b.

#### SW-9 · A named waiting path

- **Scope.** A wait record on the task: one owner, one action and an optional date. Migration, expand only: `pm_tasks.wait_owner TEXT NULL`, `wait_action TEXT NULL`, `wait_until TIMESTAMPTZ NULL`, `wait_set_by TEXT NULL` and `wait_set_at TIMESTAMPTZ NULL`. Columns, not JSONB, because the sweep and Needs attention filter on the owner.
- **Behind the flag `agent_wait_path_enabled`:**
  - An agent's move into a waiting lane must carry a live path of §3.3: an open blocker, an open question, or a wait record.
  - `finish_agent_run` with open work must carry one too.
  - A move with only prose gets 422, and the text names the missing field.
- **Rules.** `wait_owner` is a member who can see the task. The owner gets one bell. `pm_task_personal.waiting_on` stays the member's own overlay, and it never holds an agent.
- **Needs attention** gains "Waiting on no one" (moved from SW-11a) and "The wait passed its date".
- **Done when.** An agent cannot park a task with only a comment. The named owner gets one bell.
- **Fence.** R8 route tests for each valid path, and a 422 test for prose only.
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE.

#### SW-10b · Turn on the stranded sweep, staging first

- **Scope.**
  1. Check that 1054, 1045a and 1045b are merged. Without 1045b, the sweep can nudge about a run that CI keeps alive.
  2. Set `AGENT_RUN_SWEEP_ENABLED=true` on the gateway unit of staging, and restart it. Set `AGENT_RUN_LEASE_SECONDS=600` there for the test only. On production the unit is `acb-gateway`.
  3. Kill a Claude Code session in the middle of a run.
  4. Then set the flag on production, with the default lease. Read `pm_agent_run_strands` each day for one week.
- **Done when (staging).** The member gets exactly one nudge, then one recovery record one lease later, then nothing more until someone acts. Needs attention lists the task. Put the lease back to the default after the test.
- **Done when (production).** The log line `gateway.agent_run_sweep` reads `started=True`. Each new strand row matches a run that really went quiet.
- **Evidence.** The flag in the env file, the log line, and the strand rows. Report each production act in the same message (CLAUDE.md §3a rule 2).
- **Risk.** STANDARD (a flag flip). **Gate.** OWNER-GATE `enforcement-flip`. The dated grant opens it until 2026-11-30. The nudge is a bell inside the app, not mail.

#### SW-28 · Untracked agent work shows on the board

- **Scope.** As task 870. A pull request of a connected repo with no task reference lands in Needs attention as "Work with no task". A comment or a reference from a connection that holds no claim gets a one-line reminder, and the write still lands. The agent strip counts "work with no task".
- **Done when.** A pull request with no task shows within one sync, and leaves when the reference arrives. An agent that comments with no claim gets the reminder.
- **Fence.** `test_needs_attention_r8.py` for the kind, and an edge test for the reminder.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE.

### 5.5 P4 — task context and the agent protocol

**Built.** SW-12 (`get_task_context`), SW-13 (`my_work`) and SW-14 (the protocol) are EA-15 (#1295). SW-13b is #1299. Fence: `test_mcp_edge_context.py`.

#### SW-23 · A task carries a Check

- **Scope.** As task 857. `pm_tasks.check_text` with `check_last_run_at`, `check_last_result` (`open`, `passed` or `cannot_run`) and `check_last_evidence`. `create_task` and `update_task` take `check`. A new tool `report_check(task_id, result, evidence)`. A pass moves the task to done, only when the caller may move it. `my_work` gains the reason `check_due` for a Check that has not run for 7 days. `get_task_context` returns the Check first.
- **Done when.** An agent reads a Check from `my_work`, runs it and closes the task with the evidence. A `cannot_run` never closes a task. R8 runs as the NOBYPASSRLS role.
- **Fence.** A new R8 suite, `test_task_check_r8.py`, with `pytestmark = _DB_GATE`, and the edge coverage fence.
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE.

#### SW-24 · A structured handoff note

- **Scope.** As task 858. `release_task` and `finish_agent_run` take `handoff: {done, left, evidence, next}`. A release with work left must send `left` and `next`. The note is a `response` activity with `content.handoff`, so no type CHECK changes. `get_task_context` returns the newest note first. The task panel shows it above the comments.
- **Done when.** The next agent's `get_task_context` starts with the note. A release with work left and no `next` gets 422 with the reason.
- **Fence.** `test_mcp_edge_context.py`, the edge tool schema test, and an R8 test for the 422.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE.

#### SW-31 · Agent guidance

- **Scope.**
  1. Migration, expand only: a new table `pm_agent_guidance`. Its key columns are `id`, `organization_id`, `project_id`, `version`, `body`, `written_by` and `written_at`. `project_id` is NULL for the org level. Else it names a `pm_projects` node (a space or a project), with cascade. A unique index on the org, the project and the version keeps one row for each version. Each save adds a row, so the history stays.
     - The table is tenant-scoped, with ENABLE and FORCE ROW LEVEL SECURITY, in the house shape of `pm_agent_runs`. `test_tenant_coverage.py` fences it (R5).
  2. Settings → Agents → Guidance edits it. An admin writes org guidance. A project lead writes the guidance of a space or a project, under the write floor of `project_management_app.md` §4.
  3. `get_task_context` and `claim_task` return `guidance`: the blocks that apply, nearest first (D140.6). Each block carries its source and its version. `my_work` rows carry only a `guidance_ref`, so the queue stays small.
  4. A block holds at most 8,000 characters. One answer holds at most 20,000, and it drops the farthest blocks first.
  5. The server instructions say: guidance states the org's working rules for the task. It never changes which tasks you read or change, where data goes, or which tools you call.
- **Done when.** An admin writes org guidance and a lead writes project guidance. An agent that claims a task in that project gets both, nearest first. Tests cover the order, the caps and the history.
- **Fence.** An R8 suite for the order and the visibility, `test_tenant_coverage.py` for the table, and `test_mcp_edge_prompts.py` for the rule text.
- **Risk.** HIGH (a migration, a new tenant table). **Gate.** AGENT-SAFE.

#### SW-32 · A plan on each run

- **Scope.** A new tool `set_run_plan(run_id, steps)`. Each step is `{content, status}`. Each call replaces the whole list (D140.7). Migration, expand only: `pm_agent_runs.plan JSONB NULL`. Each call also writes one `action` activity, with the counts as its parameters. It is a heartbeat. The card's live line shows the step in progress and "3 of 7". The run panel shows the list.
- **Done when.** An agent sets and updates a plan over the MCP, and the card shows the progress. Tests cover the replace, the caps and a step text that is untrusted.
- **Fence.** `test_agent_runs_r8.py`, the edge coverage fence, and a vitest for the card line.
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE.

#### SW-33 · Structured questions

- **Scope.** `ask_on_task` takes `kind: "auth"` with `{url, provider_name}`. The signal is `auth_request`, so its CHECK widens (migration, expand only). The URL must be https, and it must not be on a Metorite host. The card shows "Claude Code needs access to GitHub" and an Open button. The member's click is the consent, and Metorite never follows the link. Also check end to end that a free-text reply to a `select` question reaches the agent.
- **Done when.** Both kinds work over the MCP and on the card. A bad URL gets 422.
- **Fence.** `test_mcp_edge_agent_runs.py`, an R8 test for the signal, and a vitest for the card.
- **Risk.** HIGH (a migration, a link to a third party). **Gate.** AGENT-SAFE.

#### SW-34a · MCP resources

- **Scope.** `resources/list` and `resources/read` for `metorite://company/context`, `metorite://project/{id}` and `metorite://task/{id}` (`external_agent_access.md` §9.3). Bodies leave as untrusted text. They respect the pins and the visibility seam.
- **Done when.** An MCP client lists and reads the three resources. A resource that the member cannot see answers not found.
- **Fence.** A transport test beside `test_mcp_edge_session.py`, and the coverage fence.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE.

#### SW-34b · A read-only door

- **What exists (read 2026-10-30).** The edge already lists tools by scope. `tools.listed` and `tools.listed_for` keep a tool only when the caller's scopes cover its `required_scope`. The `tools/call` check of `mcp._dispatch` answers 403 `insufficient_scope` for a tool that the scopes do not cover. The gateway refuses a run write without `projects:write` (`agent_runs._require_write_scope`). The consent page builds its scope rows in `consent.ts` (`SCOPE_COPY`, `scopeRows`, `grantedScopes`). §6 holds the anchors.
- **Scope.**
  1. Read the anchors above again, and record any change in this slice.
  2. Serve `https://mcp.metorite.com/mcp/readonly`. For each call on that path, the edge narrows the caller's scopes to the read scopes. It does this before `tools/list` and before the `tools/call` check. So the two existing checks do the work, and no second scope rule exists.
  3. The consent page offers "Read only", which grants the read scopes only.
- **Done when.** A connection through the read-only path lists only reads. A call of each write tool gets 403 `insufficient_scope` at the edge.
- **Fence.** An edge test that walks every tool of `MCP_WRITE_ROUTES` on the read-only path.
- **Risk.** HIGH (auth). **Gate.** AGENT-SAFE.

### 5.6 P5 — dependencies, delegation, wake-ups and shared resources

#### SW-29a · A delegate beside the assignees

- **Scope.**
  1. Migration, expand only: two nullable columns on `pm_tasks` that hold the agent reference of §3.7. Fix the names and the CHECK at dispatch.
  2. Views, filters, the group-by and the agent strip read the delegate. `get_task`, `list_tasks` and `my_work` return it.
  3. The card and the panel read "Owner: Ravi · Agent: Claude Code (Ravi)".
  4. `my_work` lists a task delegated to the caller's agent, with the reason `delegated`.
  5. A delegation to another member's agent works only when that agent is shared (D140.5). Q2 of §8 is open for the owner, and its answer can change this rule.
- **Done when.** A task shows its owner and its agent. The filters work by delegate. R8 proves that a delegate cannot widen what the agent sees.
- **Fence.** A new R8 suite, `test_task_delegate_r8.py`, and the structural test of §3.7.
- **Risk.** HIGH (identity, a migration). **Gate.** AGENT-SAFE. **Depends on.** SW-6. Q1 of §8 for the `agent:<name>` assignee.

#### SW-29b · An agent profile

- **Scope.** A page for one agent, reached from the roster (SW-8). It shows the name, the kind, the member who owns it, its connection, and its scopes and pins. It also shows its open runs, its history, the success rate and the run time (RA-4), and its guidance (SW-31). Its act is Stop all, for the member only. An org's shared agents list here too, after Q2.
- **Done when.** The profile lists the agent's runs, and Stop all stops each open run of that agent.
- **Fence.** A route test, and a vitest for the page.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** SW-8 and SW-29a.

#### SW-30a · Hand a task to an agent

- **Scope.**
  1. "Delegate to…" on a card, in the task panel and on the bulk bar. It lists the member's own agents, the shared agents of the org, and the internal agents. A pick sets the delegate and opens a `created` run, "Queued for <agent>", with an optional instruction.
  2. A new tool `delegate_task(task_id, agent, instruction)`, so one agent can hand a subtask to another.
  3. `@agent` in a comment opens a run with the comment as its prompt. A later `@agent` on an open run is a `prompt` activity.
  4. A run with no agent activity 2 minutes after its start reads "Not picked up yet".
  5. Delivery is the pull path. The agent sees the run in `my_work`.
- **Done when.** A person delegates a task from the board, and that agent's next `my_work` lists it. An `@agent` comment opens a run. `delegate_task` works over the MCP. A delegation to an agent that the member may not use gets 403.
- **Fence.** R8 route tests, the edge coverage fence, and a vitest for the dialog.
- **Risk.** HIGH (identity). **Gate.** AGENT-SAFE. **Depends on.** SW-29a.

#### SW-30b · Cloud agent connectors

- **Scope.** A connector that starts a cloud agent's job and mirrors its status into the run. Candidates: the GitHub Copilot coding agent, Codex cloud, Cursor background agents and Jules.
- **Gate.** OWNER-GATE. Each is a third party, and WS-56 has no decision yet (Q3 of §8). **Risk.** HIGH.

#### SW-15 · Wake the agent when its last blocker closes

- **Scope.** As task 346. When a task moves to done, find each task that it blocks. Do three things for each one whose LAST open blocker just closed. Emit `pm.task.unblocked`. Send the bell kind `unblocked` to the assignees. Queue the agent of an open or recent run with the reason "blockers resolved". A cancelled blocker does not count as closed. A task with a wait record (SW-9) is not woken by blockers alone.
- **Done when.** A is blocked by B and C. Close B: no wake. Close C: one wake. Reopen C and close it again: no second wake.
- **Fence.** R8 tests for each case.
- **Risk.** HIGH (the fingerprint store is a migration). **Gate.** AGENT-SAFE. **Depends on.** SW-9.

#### SW-16 · Agents split work and set dependencies, inside a fence

- **Scope.**
  1. `add_task_link` and `remove_task_link` over the MCP, for `blocks`, through `POST /projects/tasks/{task_id}/links`. Class B, with one confirm card in chat.
  2. **The write fence, behind a flag.** An agent run may change only the task it holds, that task's descendants, and a comment on the direct parent. For anything else it makes a new task.
  3. **Fan-out once.** The protocol tells agents to key each subtask `<parent short_id>:<step>` with `create_task`'s `idempotency_key`.
- **Done when.** An agent that holds A makes A1 and A2, and sets A2 blocked by A1. The same agent gets 403 on sibling task B, and the text names the fence. A retried fan-out makes no second tree.
- **Fence.** R8 route tests, an edge test for the tools, and a test for the retried fan-out.
- **Risk.** HIGH (a write authority rule). **Gate.** AGENT-SAFE.

#### SW-25 · Affects notes between tasks

- **Scope.** As task 859. The link type `affects`, with a short note (widen the CHECK of `pm_task_links`, expand only). `add_task_link` takes `affects` and `relates_to` with a `note`. `my_work` gains the reason `note_from_task`, once per note. A person holder gets the bell. The task panel lists "Affects" and "Affected by".
- **Done when.** Agent A links its task to task B as `affects`. Agent B sees the note on its next `my_work`, once. R8: a link to a task that the caller cannot see is refused, and the answer never shows that the task exists.
- **Fence.** A new R8 suite for the link and the visibility.
- **Risk.** HIGH (a migration, visibility). **Gate.** AGENT-SAFE. **Depends on.** SW-16.

#### SW-26 · The conflict radar

- **Scope.** As task 860. For each open run with a pull request reference, read the changed files from the GitHub integration. Two runs that change one file each get an affects note. A project names its patterns of new numbered things, for example a new migration file or a new D heading. Two pull requests that add the same number get a note at once. Needs attention lists the pair as "Will collide".
- **Done when.** Two open pull requests that add one migration number both get a note within one sync. A note never names a task that its reader cannot see.
- **Fence.** An R8 suite, and `test_needs_attention_r8.py` for the kind.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** SW-25. Read what the GitHub store holds at dispatch.

#### SW-27 · Project sequences

- **Scope.** As task 861. A tenant table of named sequences for each project, with the reservations. `reserve_number(project_id, sequence, task_id)` returns the next value in one transaction, and the reservation names the task and the session. A reservation whose task closes unused shows in Needs attention. For this repo, the sequences `migration`, `decision` and `handoff`, seeded from main at build time. `/next-ticket` and `/handoff` call it.
- **Done when.** R8: 20 parallel calls get 20 different numbers, as the NOBYPASSRLS role. A second org never sees or moves the first org's sequence.
- **Fence.** A new R8 suite, `test_project_sequences_r8.py`, and `test_tenant_coverage.py`.
- **Risk.** HIGH (a new tenant table, concurrency). **Gate.** AGENT-SAFE.

#### SW-35 · Triage suggestions

- **Scope.** As task 1052. A task in triage gets a suggested project, assignees or a delegate, tags, and up to 3 likely duplicates. A person accepts each in one click. It reuses `intake.py`, `candidates.py` and `analytics_rebalance.py`, and adds no second engine. It meters AI credits.
- **Done when.** A new triage task shows suggestions that a person accepts in one click. Tests cover the proposal-only default.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE to build dark. OWNER-GATE to turn on for a live org, because it spends credits.

#### SW-17 · Push a wake-up to an agent host

- **Status.** Deferred. **Gate.** OWNER-GATE: it waits on a decision for WS-56 (`external_agent_dispatch.md`). **Risk.** HIGH.
- **Rules when it comes.** Save each wake with an idempotency key and merge duplicates. A delivered wake is never proof that work started. The member turns it on for each host, and Metorite owns the app.

### 5.7 P6 — review and oversight

#### SW-18 · A person accepts agent work before it is done

- **Scope.** As task 349. A project setting "Agent work needs review", default off, with an optional default reviewer. When it is on, an agent's move to done lands in the review lane, and the task records the reviewer. Approve moves it to done and closes the run. Request changes needs a comment, and it queues the agent. Only the named reviewer or a project lead decides.
- **Done when.** With the setting on, `set_task_status(done)` from an agent lands in review. Approve marks it done. Request changes queues the agent.
- **Fence.** R8 route tests, and an edge test for the redirected status.
- **Risk.** HIGH (a write authority rule). **Gate.** AGENT-SAFE.

#### SW-36 · Agent-drafted project updates

- **Scope.** As task 1053. An agent drafts a weekly update for a project from the merged pull requests, the done tasks, the open runs and the blocked tasks. Each line links its source. The draft waits for the project lead, who edits and posts it. It reuses the report surface that exists.
- **Done when.** A lead gets a draft each week, edits it and posts it. Tests prove that it never posts without the lead.
- **Risk.** LOW. **Gate.** AGENT-SAFE to build dark. OWNER-GATE to turn on for a live org, because it spends credits.

### 5.8 P7 — dogfood (decided yes, D135)

#### SW-19c · Repo hooks keep each session on its task

- **Scope.** As task 871. The session-start hook prints the six steps of CLAUDE.md "Work under a Metorite task", and the tasks that this member's sessions hold. A PreToolUse hook on `gh pr create` reminds an agent when the body has no task URL. It warns in build and blocks in a `harden` pull request.
- **Done when.** A `gh pr create` with no task URL gets the reminder, and the hook tests cover both paths.
- **Risk.** LOW. **Gate.** OWNER-GATE `guard-write` for the entry in `.claude/settings.json`. The dated grant opens it until 2026-11-30.

#### SW-19a · The session-start hook reads Metorite

- **Scope.** As task 862. `session-handoff.mjs` reads `my_work` and the `check_due` rows with a short timeout. With no answer or no credential, it prints the snapshot of SW-19b with its date. It never blocks a session and never prints a token.
- **Fence.** `node .claude/hooks/session-handoff.test.mjs`.
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** SW-23 and SW-19b.

#### SW-19b · Retire HANDOFF.md as a source

- **Scope.** As task 863. Each entry gets a Metorite task with its Check. A nightly job writes a read-only snapshot file from Metorite. `HANDOFF.md` keeps a short break-glass section for the time when Metorite is down. `/handoff`, `next-ticket.md`, CLAUDE.md row 5a and `test_handoff_queue.py` change to match. The H-id sequence moves to SW-27.
- **Fence.** `test_handoff_queue.py`, and a new test that refuses a hand edit of the snapshot.
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** SW-23 and SW-27.

#### SW-19 · /next-ticket claims before it builds

- **Scope.** As task 350. `next-ticket.md` calls `my_work`, then `claim_task`, and a 409 makes it take the next row. A number reservation goes through SW-27. D135 answered the owner question of this slice.
- **Done when.** Three parallel sessions on the dev box run `/next-ticket` for one day, and no two build the same item.
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** SW-19a and SW-27.

### 5.9 P8 — deferred, the owner decides

| Slice | What | Waits on | Gate |
|---|---|---|---|
| SW-20 | Cost for each agent, task and request. A self-reported `usage` first | H-42, the rate card | OWNER-GATE |
| SW-21 | Goals linked to projects, given to agents as context | A product-shape call. Q4 of §8 | OWNER-GATE |
| SW-22 | A watchdog: a verifier agent for a finished subtree | P3 live, then SW-10, SW-15 and SW-16 | OWNER-GATE |

---

## 6. The anchors

Read at `9d41f51d4` on 2026-10-30. Line numbers move. Find each symbol by name at dispatch.

**The gateway, runs and the lease** (`apps/services/gateway/gateway/routes/projects/`):

| Anchor | What |
|---|---|
| `agent_runs.py:149` | `TASK_HELD` |
| `agent_runs.py:536` | `_NEEDS_AGENT_REASON_SQL` |
| `agent_runs.py:901` | `_write_activity`, the one writer of an activity (1045a) |
| `agent_runs.py:1099` | `_move_run`, the status machine and the session key |
| `agent_runs.py:1301` | `_system_activity`, a server line that does not move the clock (1045b) |
| `agent_runs.py:1374` | `_gate_task`, the claim, the start and the takeover |
| `agent_runs.py:1519` | `POST /tasks/{task_id}/agent-runs` |
| `agent_runs.py:1639` | `GET /agent-runs`, the `needs_agent` filter |
| `agent_runs.py:1740` | `POST /agent-runs/{run_id}/activities` (1045a) |
| `agent_runs.py:1877` | `POST /agent-runs/{run_id}/release` (SW-24) |
| `agent_runs.py:1961` | `POST /agent-runs/{run_id}/resume` |
| `agent_runs.py:2230` | `open_run_for_claim` |
| `filters.py:940` | `AGENT_RUN_STALE_AFTER_S`, the 5-minute band |
| `filters.py:946` | `AGENT_RUN_LEASE_DEFAULT_S`, 7200 |
| `filters.py:1003` | `lease_held_sql`, the lease clock (1045b) |
| `filters.py:1035` | `AGENT_RUN_ANSWER_WAITS_SQL`, the answered rule (1045b ⚠️) |
| `filters.py:1055` | `AGENT_RUN_STATE_SQL`, the one state text (1045a) |
| `filters.py:1094` | `stranded_clause` |
| `filters.py:1119` | `strand_fits_sql` (1045b) |
| `filters.py:1175` | `unheld_clause` (SW-5) |
| `filters.py:1389` | `WORKING_STATES` (1045a) |
| `tasks.py:2313` | `POST /tasks/{task_id}/claim` |
| `tasks.py:2483` | `POST /tasks/{task_id}/links` (SW-16) |
| `relations.py:228` | `GET /tasks/{task_id}/relations` |
| `agent_summary.py:76` | `GET /{project_id}/agent-summary` (SW-8) |
| `needs_attention.py:289` | `GET /needs-attention` (SW-9, SW-26, SW-28) |
| `notifications.py:70` | `NOTIFICATION_KINDS` (1054) |
| `agent_dispatch.py` | the `agent:<name>` dispatch sink (SW-29a, Q1) |
| `candidates.py:451`, `intake.py:261` | the intake and the candidates (SW-35) |

**The sweep and the lifespan** (`apps/services/gateway/gateway/`):

| Anchor | What |
|---|---|
| `agent_run_sweep.py:91` | `SWEEP_EVERY_SECS`, 300 |
| `agent_run_sweep.py:111` | `sweep_enabled` |
| `agent_run_sweep.py:332` | `run_org`, one tick for one org |
| `main.py:553` | the start, and the log line `gateway.agent_run_sweep` (SW-10b) |
| `routes/github/webhook.py:287` | `_on_pull_request` (1045b) |
| `routes/github/webhook.py:337` | `_on_checks` (1045b) |
| `routes/shell/needs.py:489` | `_VERB` of the shell bell, which has `nudge` |

**The edge** (`apps/services/agent_edge/agent_edge/`):

| Anchor | What |
|---|---|
| `tools.py:1308` to `tools.py:5067` | The 25 tools, by their `name=` lines. Reads: `list_tasks` (1308), `search_tasks` (1421), `whoami` (1492), `list_projects` (1654), `get_project` (1811), `get_task` (2365), `get_task_timeline` (2567), `get_task_context` (2830), `my_work` (3125), `get_agent_run` (4865), `list_agent_runs` (4937) |
| the same | Writes: `set_task_status` (3226), `add_task_reference` (3300), `create_task` (3792), `update_task` (3928), `add_comment` (4087), `claim_task` (4191), `set_assignees` (4288), `start_agent_run` (4441), `post_agent_activity` (4522), `ask_on_task` (4589), `request_task_archive` (4653), `delete_task` (4785), `finish_agent_run` (5020), `release_task` (5067) |
| `mcp.py:127` | `_ROUTINE_INSTRUCTIONS` (1045a, SW-31) |
| `mcp.py:457` | `_dispatch`. No `resources/*` method today (SW-34a) |
| `mcp.py:442` to `mcp.py:449` | The `tools/call` scope check, 403 `insufficient_scope` (SW-34b) |
| `mcp.py:480` | `tools/list` calls `tools.listed_for` (SW-34b) |
| `tools.py:971` | `Tool.required_scope` |
| `tools.py:5124`, `tools.py:5151` | `listed` and `listed_for`, the tool list by scope (SW-34b) |
| `prompts.py:239` | `_steps` of `work_on_task` (1045a) |

**Shared code and settings:**

| Anchor | What |
|---|---|
| `packages/acb_auth/acb_auth/agent_oauth.py:567` | `MCP_WRITE_ROUTES` |
| `packages/acb_auth/acb_auth/agent_oauth.py:593` | `MCP_ROUTES` |
| `packages/acb_common/acb_common/settings.py:1734` | `agent_runs_enabled` |
| `packages/acb_common/acb_common/settings.py:1741` | `agent_mcp_sessions_enabled` (SW-7a) |
| `packages/acb_common/acb_common/settings.py:1747` | `agent_run_sweep_enabled` (SW-10b) |

**The UI** (`workbench/control_plane/src/`):

| Anchor | What |
|---|---|
| `app/projects/lib/notifications.ts:72` | `VERB`, which lacks `nudge` (1054) |
| `app/projects/lib/agentCardLook.ts` | the card look of EA-13g (1045a, 1045c) |
| `lib/statusAccent.ts:446` | `AGENT_STATES` (1045a) |
| `app/projects/components/AgentRunPanel.tsx:478` | reads `lease_expired` (SW-4) |
| `app/projects/components/AskAgentDialog.tsx` | "Ask an agent" (SW-30a) |
| `app/connect/authorize/consent.ts:106` | `SCOPE_COPY` of the consent page. `scopeRows` is at :133 and `grantedScopes` at :150 (SW-34b) |

**Migrations** (`infra/postgres/`): `146_projects.sql:283` (the `link_type` CHECK, SW-25), `188_projects_personal_overlay_fields.sql` (`waiting_on` of the member overlay, SW-9), `267_pm_agent_runs.sql`, `289_agent_run_exclusive_claim.sql`, `302_agent_run_session_key.sql` and `305_pm_agent_run_strands.sql`. The highest number on main is 315.

---

## 7. Verify commands

Run R8 suites on a real database. Start it first:

```sh
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
```

Run the frontend checks in `workbench/control_plane`. A file that a slice adds is marked "new".

| Slice | Command |
|---|---|
| SW-0 | `node .claude/hooks/ste-lint.mjs project-docs/specs/agent_coordination.md` and `uv run pytest -q -p no:cacheprovider tests/unit/test_doc_names_resolve.py tests/unit/test_index_completeness.py` |
| SW-1 to SW-3, SW-13c | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_run_exclusive.py` |
| SW-4 | the same, and `npx vitest run src/app/projects/components/agentRunsPanel.test.ts` |
| SW-5 | `uv run pytest -q -p no:cacheprovider tests/unit/test_projects_ui_actions.py`, and the vitest of the holder line (new) |
| SW-7, SW-7a | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_session.py tests/unit/test_mcp_edge_signing.py tests/unit/test_agent_run_exclusive.py`, then the two-terminal check of SW-7a |
| SW-7b | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_label_fence.py` (new) `tests/unit/test_mcp_edge_coverage.py` |
| SW-8, SW-29b | `uv run pytest -q -p no:cacheprovider tests/unit/test_projects_agent_focus_r8.py tests/unit/test_projects_chat_coverage.py`, and the vitest of the panel (new) |
| 1045a | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_runs_r8.py tests/unit/test_mcp_edge_agent_runs.py tests/unit/test_mcp_edge_prompts.py` and `npx vitest run src/app/projects/lib/agentCardLook.test.ts` |
| 1045b | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_runs_r8.py tests/unit/test_agent_run_sweep_r8.py`, and the webhook test (new) |
| 1045c | `npx vitest run src/app/projects/lib/agentCardLook.test.ts` |
| 1054 | `npx vitest run src/app/projects/lib/notifications.test.ts` and `uv run pytest -q -p no:cacheprovider tests/unit/test_notification_kinds_fixture.py` (new, reads `tests/fixtures/notification_kinds.json`) |
| SW-9 | `uv run pytest -q -p no:cacheprovider tests/unit/test_task_wait_r8.py` (new) `tests/unit/test_needs_attention_r8.py` |
| SW-10, SW-10b | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_run_sweep_r8.py`, then on the box `journalctl -u acb-gateway | grep gateway.agent_run_sweep` |
| SW-11 | `uv run pytest -q -p no:cacheprovider tests/unit/test_needs_attention_r8.py` and `npx vitest run src/app/projects/lib/needsAttention.test.ts` |
| SW-12 to SW-14, SW-13b | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_context.py tests/unit/test_mcp_edge_prompts.py` |
| SW-15 | `uv run pytest -q -p no:cacheprovider tests/unit/test_blocker_wake_r8.py` (new) |
| SW-16, SW-25 | `uv run pytest -q -p no:cacheprovider tests/unit/test_task_links_mcp_r8.py` (new) `tests/unit/test_mcp_edge_coverage.py` |
| SW-18 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_review_r8.py` (new) |
| SW-19a, SW-19c | `node .claude/hooks/session-handoff.test.mjs`, and the test of the PR hook (new) |
| SW-19, SW-19b | `uv run pytest -q -p no:cacheprovider tests/unit/test_handoff_queue.py` |
| SW-23 | `uv run pytest -q -p no:cacheprovider tests/unit/test_task_check_r8.py` (new) `tests/unit/test_mcp_edge_coverage.py` |
| SW-24 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_context.py tests/unit/test_agent_runs_r8.py` |
| SW-26 | `uv run pytest -q -p no:cacheprovider tests/unit/test_conflict_radar_r8.py` (new) `tests/unit/test_needs_attention_r8.py` |
| SW-27 | `uv run pytest -q -p no:cacheprovider tests/unit/test_project_sequences_r8.py` (new) `tests/unit/test_tenant_coverage.py` |
| SW-28 | `uv run pytest -q -p no:cacheprovider tests/unit/test_needs_attention_r8.py`, and the edge reminder test (new) |
| SW-29a | `uv run pytest -q -p no:cacheprovider tests/unit/test_task_delegate_r8.py` (new) `tests/unit/test_tenant_coverage.py` |
| SW-30a | `uv run pytest -q -p no:cacheprovider tests/unit/test_task_delegate_r8.py tests/unit/test_mcp_edge_coverage.py` |
| SW-31 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_guidance_r8.py` (new) `tests/unit/test_tenant_coverage.py tests/unit/test_mcp_edge_prompts.py` |
| SW-32 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_runs_r8.py tests/unit/test_mcp_edge_coverage.py` |
| SW-33 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_agent_runs.py tests/unit/test_agent_runs_r8.py` |
| SW-34a, SW-34b | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_session.py tests/unit/test_mcp_edge_coverage.py`, and the read-only walk (new) |
| SW-35 | `uv run pytest -q -p no:cacheprovider tests/unit/test_triage_suggestions.py` (new) |
| SW-36 | `uv run pytest -q -p no:cacheprovider tests/unit/test_project_update_draft.py` (new) |

---

## 8. Open owner questions

Each question carries a recommendation. A slice that waits on one says so.

| # | Question | Recommendation | Blocks |
|---|---|---|---|
| Q1 | Does an `agent:<name>` assignee move into the delegate slot? | **Yes, by expand and contract.** SW-29a reads an `agent:<name>` assignee as the delegate. A new delegation writes the delegate, and the dispatch sink listens to both for one release. A later slice stops new `agent:<name>` assignee writes. D-PM-4 keeps one actor vocabulary | SW-29a |
| Q2 | May a member hand a task to another member's agent? | **Yes, to a shared agent only.** An agent is private to its member by default. Its member, or an admin, can share it with the org in its profile. A shared agent still acts as its own member, so it sees only what that member sees. The timeline says "delegated by Priya" | SW-29a item 5, SW-29b, SW-30a |
| Q3 | Which cloud agents get a connector in SW-30b? | **The GitHub Copilot coding agent first.** It has a documented member-credential API (`external_agent_dispatch.md` §3), and the GitHub App already exists. The others wait for the WS-56 decision | SW-30b |
| Q4 | Do cost attribution and goals wait for H-42? (The open question of epic 321) | **Cost waits for H-42. Goals fold into WM-5** (task 984, goals and key results), so Projects gets one goal object | SW-20, SW-21 |
| Q5 | May a person with `admin:members:read` end another member's run? (H-409 item 2) | **No. Use `admin:members:manage`.** Ending a run is a write | H-409, SW-29b Stop all |

---

## 9. What this spec could not settle

- **The column names of the delegate.** SW-29a fixes them at dispatch, under the rules of §3.7 and Q1.
- **The `push` event.** It is not known whether the GitHub App subscribes to `push` (1045b).
- **The board half of SW-5.** EA-13g may already draw part of the holder line. Read it at dispatch.
- **The production flags.** This spec uses the reading of 2026-10-29. Read the env files of the box again before SW-7a and SW-10b.
- **The read-only door.** The edge already hides write tools from a connection with read scopes only, and it refuses their call (SW-34b, "What exists"). It is not known yet how a client keeps a separate token for the path `/mcp/readonly`. SW-34b settles it.
- **Stale status lines in `external_agent_access.md`.** Its header still calls SW-7, SW-10a and SW-11a "not merged". They merged as #1312, #1333 and #1345. The next slice that touches that file fixes the lines.

---

## Appendix A. Paperclip comparison

**Source.** Paperclip at commit `410e5c6`, read on 2026-10-24, compared with Metorite `f32244eac` on 2026-10-25. The epic 321 description and the phase tasks P1 to P8 hold the research. We adapt the rules. We do not adopt the runtime.

**The files we read:**

| Short name | File |
|---|---|
| execution-semantics | https://github.com/paperclipai/paperclip/blob/410e5c6/doc/execution-semantics.md |
| SPEC | https://github.com/paperclipai/paperclip/blob/410e5c6/doc/SPEC.md |
| SPEC-implementation | https://github.com/paperclipai/paperclip/blob/410e5c6/doc/SPEC-implementation.md |
| execution-github-identity | https://github.com/paperclipai/paperclip/blob/410e5c6/doc/execution-github-identity.md |
| the skill | https://github.com/paperclipai/paperclip/blob/410e5c6/skills/paperclip/SKILL.md |
| dependency-wake-recovery | https://github.com/paperclipai/paperclip/blob/410e5c6/doc/dependency-wake-recovery.md |
| agents-runtime | https://github.com/paperclipai/paperclip/blob/410e5c6/docs/agents-runtime.md |
| TASK-WATCHDOG | https://github.com/paperclipai/paperclip/blob/410e5c6/doc/TASK-WATCHDOG.md |

**The ideas, and what Metorite does with each:**

| Paperclip idea | Where | Metorite | Slice |
|---|---|---|---|
| Atomic checkout: `checkout_run_id` and `execution_run_id`, a 409, and "never retry a 409" | execution-semantics §5, SPEC §8 | One open run per task (migration 289), 409 `task_held`, a fixed edge text | SW-1 to SW-3, built |
| An agent is its own principal, with `responsible_user_id` on each row, and the agent ∩ the user | SPEC-implementation, execution-github-identity | **Changed.** The member is the only principal. A session gets a key and a label for display only | SW-6, SW-7, SW-7b |
| `blocked` needs a routable waiting path. "Prose-only blocked routes to nobody" | execution-semantics §3 | A wait record, and the `waiting` state | SW-9, 1045a |
| The liveness contract | execution-semantics §8 | D140.3 | §3.3 |
| Stranded-work recovery: auto-recover, then explicit recovery, then a person, and no auto-reassignment | execution-semantics §9, §13 and §14 | Nudge once, then a recovery record. Never reassign | SW-10a, built dark |
| Silence marks, "critical" at 15 minutes | execution-semantics §12 | **Changed.** A 5-minute stale band and a 2-hour lease (D140.10) | SW-4 |
| No "needs attention" storm on deploy | execution-semantics §3 | Needs attention is prospective only, with no backfill | SW-11a, built |
| `heartbeat-context`: the task, each parent, the project, the goal, a comment cursor | the API | `get_task_context`, with no goal yet | SW-12, built |
| The inbox and the protocol: inbox, checkout, context, work, a final disposition | the skill (792 lines) | `my_work`, `work_on_task` and the six steps | SW-13, SW-14, built |
| The parent is structure, and "blocked by" is the dependency. `issue_blockers_resolved` wakes on the last blocker | execution-semantics §6, dependency-wake-recovery | A wake on the last blocker, and a cancelled blocker does not count | SW-15 |
| Child-to-parent reports only as comments on the direct parent. The "courier" pattern for lateral work | execution-semantics §6 | The write fence of SW-16, and affects notes | SW-16, SW-25 |
| Exact-once decomposition | execution-semantics §7 | `idempotency_key` of `<short_id>:<step>` | SW-16 |
| Idempotent wake requests that coalesce (`agent_wakeup_requests`, `coalesced_count`) | agents-runtime §3.2 | Fingerprints now, and push later | SW-15, SW-17 |
| `in_review` with `currentParticipant` and `returnAssignee` | the skill, step 6, execution-semantics §3 | One review stage with a named reviewer | SW-18 |
| `cost_events`, billing codes, `request_depth`, `budget_policies` | SPEC | A self-reported `usage` first, after H-42 | SW-20, deferred |
| `goals` and `project_goals`, resolved from the task up to the company | SPEC | Folds into WM-5 (Q4) | SW-21, deferred |
| A task watchdog: a verifier for a stopped subtree, once per fingerprint | TASK-WATCHDOG, execution-semantics §11 | Deferred until P3 is live | SW-22, deferred |

**What we do not adopt.** The execution runtime and its adapters. Agents that hire agents (a CEO agent and an org chart). Defaults that skip permissions. Company-wide default visibility, which conflicts with D12.

---

## Appendix B. Linear comparison

**Source.** The research comment of 2026-10-30 on epic 321, from Linear's own pages: https://linear.app/developers/agents, https://linear.app/developers/agent-interaction, https://linear.app/developers/agent-signals and https://linear.app/developers/aig.

| Linear | What it does | Metorite now | Metorite after | Slice |
|---|---|---|---|---|
| Agent as an app user | OAuth `actor=app`. Not a billable seat. It can be mentioned, take issues and comment | A connection that acts as its member | The same, with a profile | SW-29b |
| `delegate` | An issue given to an agent sets the delegate. The human stays the assignee and keeps it in "My issues" | An `agent:<name>` assignee starts an internal run only | A delegate beside the assignees | SW-29a |
| Triggers | Delegation, an @mention, `agentSessionCreateOnIssue` and `agentSessionCreateOnComment` | "Ask an agent" to the member's own connection, and a claim | "Delegate to…", `delegate_task` and `@agent` | SW-30a |
| `AgentSessionEvent` | A webhook, `created` with `promptContext` and guidance, `prompted` with a follow-up | No push. The agent pulls `my_work` | Push to a host after WS-56 | SW-17 |
| Response time | Acknowledge in 5 s, a first activity in 10 s | No rule | "Not picked up yet" after 2 minutes | SW-30a |
| Session states | pending, active, awaitingInput, error, complete, stale | queued, working, answered, stale, awaiting, stopping, failed | Adds `waiting`, and the stranded card | 1045a, 1045c |
| Stale | 30 minutes with no activity. A new activity recovers it | A 5-minute band, a 2-hour lease | A `waiting` line of up to 120 minutes | 1045a |
| Activities | thought, action, elicitation, response, error, prompt. thought and action can be ephemeral | The same six | The same | Built |
| Plans | A checklist of `{content, status}`. Each update replaces the whole list | None | `set_run_plan`, capped | SW-32 |
| External URLs | A link on the session, for example the PR | `external_link` on the run, and `pm_task_refs` | The same | Built |
| Signals | `stop`, `select`, `auth` | `continue`, `stop`, `select` | Adds `auth_request` | SW-33 |
| Guidance | Workspace and team markdown in Settings → Agents, with history. Team wins | The fixed server instructions only | Org, space and project, with history. The nearest wins | SW-31 |
| Agent Interaction Guidelines | Disclose, use native patterns, give fast feedback, show state, respect disengagement, keep a human accountable | The client name on every byline, the card states, Stop, and the member accountable | The delegate never replaces the assignee | SW-29a |
| MCP server | `/mcp` and `/mcp/readonly`, OAuth 2.1 with dynamic registration | `/mcp`, 25 tools, one prompt, no resources | Resources and `/mcp/readonly` | SW-34a, SW-34b |
| Triage Intelligence | Suggests a team, project, assignee, labels and duplicates | None | Proposals that a person accepts | SW-35 |
| Agent-assisted project updates | An agent drafts the update | None | A draft that the lead posts | SW-36 |
| Coding sessions (Claude Code, Codex) | Linear starts them | Not built | Cloud agent connectors | SW-30b, WS-56 |
| Skills and Automations | Linear Agent with skills and automations | Out of scope here | Studio (WS-STU) | — |

---

## Changelog

- **2026-10-30.** Written for SW-0 (Metorite task 331), from epic 321, its phase tasks, the slice tasks and the code at `9d41f51d4`. Records D140.
- **2026-10-30, spec-audit fix round.** The audit gave GO-NARROWED for P1 and P4. This round names the migrations of 1045b and SW-31, and the table of SW-31 with FORCE RLS. It makes D140.5 agree with Q2, marks the owner calls and the agent defaults, and adds 1045b before SW-10b. It also names the SW-34b anchors, the 1054 fixture and what EA-13g draws for SW-5.
