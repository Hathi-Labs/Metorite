# Agent coordination — many agents and many people on one board

**Status: ACTIVE (SW-0, 2026-10-30). SW-43a (withdraw an answer) is built and dark, 2026-11-05. SW-43b and SW-43c are open.** **P9 (§3.10, §5.10, D183) was planned on 2026-11-06. No P9 slice is built.**

This spec gates every SW slice (D135.4). The decisions are **D140** in `work_plan.md` §3.

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
- The slices SW-0 to SW-36, the two liveness bugs of tasks 1045 and 1054, and the P9 slices SW-44 to SW-63 (§5).
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
3. **Ship dark.** New behaviour lands behind a flag, default off. A bug fix may go live at the merge under `agent_runs_enabled`. It must only add an optional argument or an optional result field. SW-10a part 1 did this. The slice says so.
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
2. **A waiting run.** A `waiting` line whose time has not passed. The time is at most 120 minutes (D140.8). A run parked on its CI (SW-60) may wait longer, up to the cap of Q13.
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

### 3.9 Withdraw or change an answer (SW-43)

**The incident.** On 2026-11-05 the owner picked "Restart it now" by mistake. The run went to `run_answered` and `my_work` listed it to every session. The app could not take the answer back.

- **Window rule.** A member may take an answer back while these facts hold. The run is `in_progress`. The answer is the newest answer of the run, written by the run's own member. No agent has read it, and no agent activity follows it. The window closes when the agent reads or posts, whichever is first. An agent write carries `actor_via`. One text decides it, `answer_changeable_sql` in `filters.py`. The route and the run wire both read it.
- **Withdraw.** One UPDATE on the answer row. It sets `in_reply_to` to NULL, adds `content.withdrawn` (`at`, `by`) and keeps the old question id in `content_metadata.question_id`. The four open-question reads then see the question open again, with no SQL change. The run moves to `awaiting` (`member_withdraw` in `_MOVES`), so it leaves the `needs_agent` list.
- **Read stamp.** An agent read of the run stamps `content.read_at` once on the live answer. The reads are `get_agent_run` and a claim that finds the open run. The stamp takes the lock of the run, and only while the flag is on. A read by the member stamps nothing.
- **Marker.** A quiet member `prompt` with `content.withdraws` set to the answer id and `in_reply_to` NULL. An agent that long polls with `after` sees it. The timeline shows "Withdrew the answer".
- **Race.** The route takes the same `FOR UPDATE` lock of the run as an agent post. The check runs under the lock. So one write wins. When the agent wins, the member gets 409 "The agent already has your answer. Send a comment instead."
- **Protocol.** An agent posts a thought that names the answer id before it acts on an answer. The thought ends the window.
- **Flag.** `agent_answer_change_enabled`, default off. Off: the route answers 404 and the run wire has no `answer` field. No migration.
- **Slices.** SW-43a withdraw (built dark). SW-43b a replace route, with the option `confirm: true` and the card text "Are you sure?", and a Change button. SW-43c the chat tool, the agent text on by default, and the flag on.

### 3.10 Many people, each with their own agents (P9, D183)

**The ask.** The owner asked on 2026-11-06 (task 1874) to "see how Metorite itself can be improved in its MCP to orchestrate multiple people's agents, approvals etc".

**The owner's direction of 2026-11-06 (task 1872).**

- Each contributor, the owner included, takes the tasks of ONE app and works in one app at a time. The contributors are not chosen yet.
- GitHub stays the merge gate. Local CI is for speed.
- The GitHub connection should live in Metorite (WS-54).

**How to read this section.** Each item has two parts. "Already planned" names the slice or task that holds the idea. "New" is what this amendment adds. A new slice is in §5.10.

**Production flags.** They come from the brief of task 1874, and nobody measured them again. `GITHUB_APP_ENABLED` and `AGENT_RUN_SWEEP_ENABLED` are unset. 478 pull request links hold no check state. The lease is 2 hours.

The 187 loose tasks and the missing CRM project come from a comment on task 1872. Check them again at dispatch (SW-44).

**Rules that bind P9.**

1. **The lead is already an authority, and `authority.py` is its one seam.** A lead of a project, or of any ancestor, has write authority on the subtree. It covers delete, move and grants (D-OP-11, D-OP-12, D-OP-14). Setting `lead` is a grant write. P9 adds no second owner table.

   A rule that keys on the lead reads it through `authority.py`. SW-46 and SW-48 make the lead-walk SQL of `authority.py` public and embed it, so no SQL text copies the walk. An addressee, a role, a label and a warning route work. They grant and deny nothing (D140.2).
2. **Nothing frees a task by itself.** A late answer, a stuck run or a full review queue never reassigns or releases a task (D140.3).
3. **Warn first.** Each rule that can stop an agent ships as `off`, then `warn`, then `refuse`. The flip to `refuse` is an `enforcement-flip`.
4. **One seam each.** The app lead is `pm_projects.lead`, read through `authority.py`. The Waiting spec (PR 1772, accepted) owns the addressee ladder of a question. A flag for each organization follows `docs/flag_reload.md`.

#### 3.10.1 App ownership (SW-44, SW-45)

**Already planned.** Nothing plans app ownership for agents. Two parts exist. `pm_projects.lead` holds a lead for each project (`146_projects.sql`). `authority.py` gives the lead of a project, or of any ancestor, the write authority on the subtree. SW-31 uses the project lead as the write floor of guidance.

SW-18 names the project lead as a last-resort reviewer.

**New.**

- **The unit is an app project.** It sits under "Metorite Application". The **app lead** of a task is a lead of its project or of an ancestor, by the rule of `authority.py`. For routing, the nearest lead is the first addressee.
- **A shared seam is a project too, with its own lead.** The seams are tenancy and security, the shell, the design system, the chat orchestrator with its packs, migrations, and Platform and CI/CD.
- **Setting a lead is a grant write** (`authority.py`). It gives the lead delete, move and grant power on the subtree. So SW-44 is HIGH, and the lead names come from the owner (Q6).
- **Two board gaps close first** (task 1872). CRM has no project. 187 tasks sit directly in "Metorite Application". A Needs attention row, "App project with no lead", keeps the gap shut.
- **A member's own apps** are the projects where the member is a lead, or holds a write grant on the project itself. A role does not count. Read the grant rule again at dispatch.
- **`my_work` prefers own apps.** `my_work` lists assigned tasks and runs only. It does not list free tasks. SW-45 adds a group, reason `own_app_free`: up to 5 unheld, unassigned tasks in the member's own apps, best priority first. Each row carries `app_project`. The name `app` is taken by migration 342 (the door app).
- **A claim of an unassigned task outside the member's apps warns. It does not refuse.** The reasons:
  1. A refusal blocks real cross-app work. A shell change touches every app, and a night hotfix has no time for a hand-off.
  2. A refusal teaches an agent to drop a task that the owner asked for by name.
  3. A warning leaves a line in the timeline, so the lead sees the claim.
  4. The mode `refuse` can follow after one week of data (Q6).
- **A task assigned to the member always passes.** Someone chose that member.
- **The hard gate for a seam is GitHub.** CODEOWNERS on the seam paths makes the seam owner review the change (ONB-7, task 1872, which has no spec yet). The board only routes and warns.

#### 3.10.2 Approvals to the right person (SW-46, SW-48)

**Already planned.**

- **D-EA-14 and EA-18** (`external_agent_access.md` §12.8, tasks 1788 and 1806 to 1808, 1832). One list of the questions that wait on a person. The task creator, the assignees, the run's member and the roles owner, admin and manager see and answer. Each answer names its author. Task 1806 is built, dark and not merged on 2026-11-06. It holds `person_may_answer_sql` and `may_answer_run`, behind `AGENT_QUESTIONS_ORGS`.
- **The Waiting spec** (PR 1772, accepted on 2026-11-06). It owns the list at `/waiting`. It also owns the **addressee ladder** of §4.5 (AP-15, task 1875). The ladder runs: the run's member, the assignees and watchers, the project lead (with the `authority.py` walk), and last the holders of `data:org:read`. The ladder stores no addressee column. An owned wait opens to its circle after 24 hours (AP-Q9). AP-16 (task 1876, AP-Q10, open) lets an agent name one person of the circle. AP-14 (forward) got "not now".
- **Task 1750 (CIV-14k, PR 1741).** The first "Waiting for you" list. AP-2 moves it.
- **SW-43 (§3.9).** Withdraw (SW-43a, built dark). Change and `confirm: true` (SW-43b, task 1801). The chat tool (SW-43c, task 1802).
- **SW-18.** A named reviewer, or the project lead, accepts agent work.
- **Task 1731 (CIV-14f, no spec).** An answer watcher on the home box wakes the session that asked. **MCP-G4** (merged, #1742): an answer reaches only the session that asked.

**New.** P9 builds on the two specs above. It does not copy them.

- **Gap 1: the answer set has no lead.** D-EA-14 lists the creator, the assignees, the run's member and three roles. A lead who is none of these cannot answer a question on their own app. SW-46 adds the lead to `person_may_answer_sql`, the one text. It adds no second flag. `AGENT_QUESTIONS_ORGS` gates it.
- **Gap 2: a preview or a review goes to the run's member first.** A contributor's agent asks "Approve this preview?", and the contributor answers their own question. The owner wants the lead. `ask_on_task` takes `purpose`: `question` (the default), `preview` or `review`. For `preview` and `review`, the ladder of AP-15 starts at the nearest lead instead of the run's member. If the lead is the run's own member, the ladder goes on to the next. The ladder stays a read-time rule, with no stored column.
- **Invariant.** The ladder drops each person who is outside the answer set or cannot open the task. So an addressee always answers with 200. V5 of the Waiting spec says "only the addressee answers". D-EA-14 sets a wider group. The wider group decides who may answer. The ladder decides who is told first.
- **The answer returns to the asker.** The run stays in the asking member's queue, and the answer wakes that member's box through task 1731. The `my_work` row of the answered run carries `answered_by`.
- **The answerer may withdraw or change.** The window rule of §3.9 says "written by the run's own member". SW-46 widens it to "written by the person who answered".
- **A costly option asks twice.** An option that stops machines, ends sessions, deletes data or restarts a box carries `confirm: true` (SW-43b). The text goes in the `work_on_task` steps or the SW-56 block. The server instructions are over budget (task 1803).
- **The lead acts on a teammate's run.** SW-48 lets the app lead Release and Resume a run on their app. Q5 and Q9 hold the open choice.

#### 3.10.3 Review capacity (SW-49)

**Already planned.** MCP-G6 (task 360) caps the open runs of one connection, 5 by default, and answers 429 over the limit. The rate limit of 120 writes in 600 seconds exists. Both cap what one connection holds. Neither counts what waits on a person.

**New.**

- **The review queue of a person** is the items that wait on them. These are open questions addressed to them. They are review-lane tasks that name them as reviewer (SW-18). They are open pull requests that name them as reviewer (WS-54).
- **A cap for each person**, `review_cap`, 5 by default (Q8). An admin sets it in Integrations beside the cap of MCP-G6.
- **At the cap, new work on that person's apps stops.** `claim_task` and `start_agent_run` answer 429 `review_capacity` with the count, the cap and the age of the oldest item. `my_work` leaves out those new-work rows and says why in one line. Runs that exist go on.
- **Warn first.** In mode `warn` the claim works and carries a warning.
- **The signal is the age of the queue.** The fleet view (§3.10.6) shows "waiting on you: 4, oldest 3 h".

#### 3.10.4 The identity of each fleet (SW-50)

**Already planned.** §3.7: on the board one agent is one member and one client stamp. For execution one worker is one MCP session. SW-7 (built, dark), SW-7a (turn it on) and SW-7b (a label) fix the session.

SW-8 is the roster. SW-29a shows "Owner: Ravi · Agent: Claude Code (Ravi)", the delegate model of Linear (§3.8). D-EA-5 opened on 2026-11-06 (task 1819, PR 1764): a per-box token, with one stamp for each box and not for each session.

**New.**

- **No connection or token belongs to two people.** Each person signs in with their own account. A box token belongs to the member who made it.
- **Every surface that shows a run names the human and the box.** The card, the panel, the bell, the fleet view and `get_agent_run` read "Claude Code for Ravi · on metorite-home". The box name comes from the connection of a box token. This is display data (D140.2).
- **SW-7a comes before the second contributor.** Without it, two terminals of one person share one run slot.

#### 3.10.5 Stuck work across people (SW-47)

**Already planned.** D140.3 and §3.3. SW-10a, the sweep, is built and dark. SW-10b turns it on. 1045a, 1045b and 1045c carry `waiting`, linked events and the stranded card.

SW-9 is the wait record. Task 1730 (CIV-14e, PR 1736) flags a run that waits on the owner with no question. SW-11a is Resume, which writes `resume: true`. H-409 and Q5 ask who may end another member's run. A question keeps a task for three leases, 6 hours by default.

The Waiting spec (AP-15, AP-Q9) opens an unanswered question to its circle after 24 hours.

**New.**

- **Who hears of it.** The first nudge goes to the run's member only. The recovery record goes to the run's member and the app lead.
- **One row for a question that nobody answers.** At three leases a question stops being a live path (§3.3), so the stranded sweep starts. The sweep writes one fingerprint with the reason `question_unanswered` for an `awaiting` run. It writes no second `agent_quiet` fingerprint for the same run. The result is one bell and one Needs attention row. The row offers Answer and Release the run.
- **The slot stays held.** A release would let a second agent start on the work, and the first agent would lose its context. A person decides.
- **The question timing is the Waiting spec's.** The circle opens at 24 hours (AP-Q9). P9 sets no second clock for a question.
- **A resume hand-off restarts work.** The agent continues and does not ask again. The run's member or the app lead writes it. The home box watcher (task 1731) starts the session. A box with no watcher leaves the run in `my_work`, where the next session pulls it.
- **The sweep goes live before people join.** SW-10b is on the critical path (§5.0a). Staging is retired (D144), so SW-10b follows its own order.

#### 3.10.6 One view of every fleet (SW-51)

**Already planned.** SW-8 is a roster for each project. SW-29b is the profile of one agent. The agent strip and the Reports Overview (RA-2, RA-4) count runs and run time. SW-20 holds cost, deferred behind H-42 (Q4).

Task 1064 (SW-38, in no spec yet) plans caps and cost on the roster. AP-12 is a team lens for questions.

**New.**

- **A Fleet tab in the Agents panel of Projects.** It reads the route `GET /projects/{project_id}/agent-summary`, once for each root that the viewer can see. It adds no nav pane. The owner and admins see every run they may see. A lead sees the runs of their apps. Visibility stays the seam of D12.
- **Rows group by person, then box, then agent, then session.** Each row shows the task, the app, the state, the age, the last line and the flags blocked, stale, stranded and "waits on a question".
- **A person line** shows run time today and in 7 days (measured), and "waiting on you: N, oldest age".
- **The pull request and its check state show** when item 8 is on.
- **Cost reads "unknown"**, never zero (§3.6). Three ways to fill it (Q4): a self-reported `usage` (SW-20), a Claude Code OpenTelemetry export for each person, or none. The OpenTelemetry way needs an ingest route and a per-person trust rule. No slice plans it.
- **Chat parity.** The assistant answers "what are my team's agents doing?" from the same route (class A read).

#### 3.10.7 Collisions below the task (SW-52)

**Already planned.** SW-27 reserves numbers (task 861). SW-26 is the conflict radar (task 860). SW-25 is the affects note. SW-28 shows work with no task (task 870). `premerge.sh` stays the R1 gate.

**New.**

- **SW-27 comes before the second contributor.** Two people double the chance of a clash on a migration or a D number.
- **A note reaches the owners.** SW-26 and SW-28 send it to the run members. P9 adds the lead of each app. A change on a seam path also goes to the seam lead. The seam paths sit in a setting of the seam project (`owned_paths`). CODEOWNERS stays the GitHub gate. The two maps are kept in step by hand, and the slice is advisory on that point (R7).
- **SW-26 and SW-28 need the GitHub store.** They wait for item 8.

#### 3.10.8 GitHub on the board (SW-53, SW-54, SW-55, SW-58)

**Already planned.** `github_integration.md` (WS-54). GH-1 to GH-7 are built and dark behind `github_app_enabled`. GH-0 (register the Apps) and GH-8 (go live) wait.

G-3 auto-links a pull request by the task URL or `mt-<8 hex>`. GH-5 reads the checks. GH-6 (G-4) moves a task to Done on merge, only for agent-worked tasks. 1045b counts linked events as life.

G-9 asks the App for no Contents permission. G-6 allows one installation for each organization.

**Not planned.** A per-organization switch. Required checks. CODEOWNERS. The merge and the review stage together. Pull requests from people outside Metorite.

**New.**

- **A per-organization switch (SW-53).** `GITHUB_APP_ORGS` is a list of organizations, and it reloads with no restart. `github_app_enabled` stays the master: off means off for all. A delivery for an organization not on the list gets 202 and writes nothing.
- **Go live behind the per-organization switch.** Staging is retired (D144). GH-8 still names a staging step, and its text is old. Turn it on for one organization on production. The install on a customer's GitHub account stays an OWNER-GATE act.
- **Required checks on the task (SW-54).** An admin lists the names of the required checks. The default is empty. The 7 names of `ci_velocity.md` §4 seed the HathiLabs organization only. The task shows "5 of 7 required". The App gets no new permission. Reading branch protection would need Administration, and G-9 forbids it (Q10).
- **The board does not read CODEOWNERS.** Reading a file needs the Contents permission, and G-9 forbids it. The board keeps its own owner map (§3.10.1). ONB-7 (task 1872) owns the file.
- **A merge and the review stage agree (SW-55).** With "Agent work needs review" on, a merge moves the task to the review lane. It moves to Done only when the merging login maps to the named reviewer or the lead. No map from a GitHub login to a member exists, so v1 always lands in review under the setting.
- **A pull request from a person with no Metorite access (SW-58).** It shows in Needs attention as "Work with no task". The lead makes the task in one click.

#### 3.10.9 Agents that are not Claude Code (SW-56, SW-57)

**Already planned.** MCP-G16 (task 1803): the server instructions are 4597 characters, and Claude Code cuts them at 4096. So the stopping rule and `record_outside_decision` never reach a session.

SW-34a adds resources. SW-31 adds guidance. SW-30b and WS-56 plan cloud agents. `AGENTS.md` is the shared rule file (task 1872, rule 4).

**New.**

- **MCP-G16 comes before the second contributor.** The budget is stricter than the cut: each safety sentence starts before character 3500.
- **A protocol block (SW-56).** The results of `claim_task` and `my_work` carry up to six short lines. A client that drops instructions and prompts still gets the rules at the moment it needs them.
- **A rule that must bind every agent runs in GitHub.** A hook binds Claude Code only. SW-57 adds a pr-check step for the evidence of a pull request.
- **A path for an agent with no Metorite access.** It opens a draft pull request with the task URL, the id `mt-xxxxxxxx`, or the line `No task: <reason>`. SW-28 lists a pull request with none of them. SW-58 lets the lead make the task. GitHub issues would need the Issues permission, so v1 does not take them (Q12).

#### 3.10.10 Cleanup and the clean-context check (SW-59)

**Already planned.** CIV-16 (task 1828, PR 1763): the clean-up rule, a Stop hook and a reaper. `CLAUDE.md` §4: the verifier is never the implementer, and the reviewer hunts for the case where the work is wrong. D145. The evidence block of the pull request template (task 1872, ONB-6).

**New.**

- **Both stay mandatory for every contributor.** The enforcement is in GitHub (SW-57), because a local hook binds one tool only.
- **A run role (SW-59).** A run may declare `implementer`, `verifier` or `reviewer`. The task shows "Verified by <run>". A self-declared role is evidence for a reviewer and never a gate. This part is advisory (R7).

#### 3.10.11 The board of 2026-11-06 (task 1900)

**The facts.** The box coordinator audited the board at 17:12 UTC on 2026-11-06 (task 1900). 43 agent runs were open. 6 worked. 1 waited on the owner. 36 held a task with no live session.

Seven causes explain the 36. The owner asked that this plan fix the causes. Each cause below says what the spec already plans, then what is new.

**Cause 1: a CI wait outlived the session (12 tasks).** The pull request was open. GitHub checks waited 1 to 3 hours for a runner. Memory pressure on the box stopped the CI watchers. The lane stopped and the task stayed held.

- **Already planned.** The `waiting` state (1045a) names a wait on CI, but it caps at 120 minutes and needs a live session. 1045b lets a check event renew the lease. SW-10a, the sweep, is dark. Task 1731 is the answer watcher.
- **New (SW-60).** A run parks on its pull request and the session may end. The run stays held while the checks run, up to a cap (Q13). When the checks end or the pull request merges, the run shows in `my_work` as resumable. The box watcher starts a session. Without the GitHub connection, the watcher reads the checks with `gh`.

**Cause 2: a turn ended with no disposition (most of the 36).** Step 6 of the server instructions asks for exactly one disposition. Claude Code cuts the instructions at 4096 characters (task 1803), so the step is lost.

- **Already planned.** Task 1803 fixes the budget. SW-56 puts a protocol block in the claim result. CIV-16 (task 1828, PR 1763) adds a Stop hook that lists live child processes. It does not look at runs.
- **New (SW-61).** The Stop hook blocks the end of a turn once when the session holds a run with no disposition. SW-56 gains the same line.

**Cause 3: a question went only to the terminal (task 835, 16:34 UTC).** A background session has no reader.

- **Already planned.** `ask_on_task` exists. Step 6 of the server instructions and step 4b of `next-ticket.md` name it. The instructions are cut at 4096 characters (task 1803).
- **New (SW-61 and SW-56).** The Stop message and the protocol block both say that a question goes through `ask_on_task`, never only to the terminal.

**Cause 4: an answer woke nobody (10 tasks, some for over 10 hours).**

- **Already planned.** Task 1731 (no spec) wakes the session that asked, or starts one in its lane. Task 1730 flags a run that waits with no question. MCP-G4 makes an answer reach only the session that asked.
- **New.** No new slice. Task 1731 joins the group that must land before a second contributor (§5.0a). The answer is pull-only until it lands (D140.4).

**Cause 5: a merge closed nothing (9 tasks, 1 to 8 days old).**

- **Already planned.** GH-6 (G-4) moves a task to Done on merge for an agent-worked task. It is dark behind `github_app_enabled`. SW-53 adds the per-organization switch. GH-8 turns it on.
- **New.** SW-53 and GH-8 for the HathiLabs organization join the first group of §5.0a. SW-53 gains a backfill with a dry-run list for the pull requests that merged before the turn-on.

**Cause 6: dead claims stayed (up to 8 days).** The sweep is off. A power cut killed four sessions that held claims.

- **Already planned.** SW-10a and SW-10b. SW-47 sends the recovery record to the app lead.
- **New (SW-62).** The first sweep meets a backlog. It writes one digest row, not 36 rows. The owner gets a bulk action, "Release the runs of ended sessions".

**Cause 7: epic holds.** Runs held a parent task for days while the subtasks carried the work (tasks 537, 1585, 1796, 1820).

- **Already planned.** SW-16 fences what a run may write. Nothing warns about a claim on a parent.
- **New (SW-63).** `claim_task` of a task with open subtasks works and carries a warning: "claim the slice, not the epic". It is a warning and not a refusal.

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
8. **The `waiting` state is capped at 120 minutes.** After that time, with no new activity, the run reads `stale`. A waiting line renews the lease as any activity does, and the cap never passes one lease. One exception: SW-60 (D183, Q13) lets a run parked on its CI pass the lease up to a larger cap.
9. **The stranded sweep goes on in staging first.** The nudge verb (task 1054), the `waiting` state (1045a) and the linked events (1045b) land before the flip. Production follows after the staging test of SW-10b passes.
10. **The lease is two hours, from the newest activity of any author.** A question keeps the task for three leases. This replaces the 15-minute TTL of the first text of SW-4.

### 4.1 D183 — many people, each with their own agents (2026-11-06)

`work_plan.md` §3 holds it as **D183**. It extends D140. It changes one rule: SW-60 lets a parked run pass the lease of D140.8 (Q13).

**Owner calls (task 1872, 2026-11-06).**

1. **An app project is the unit of ownership.** A contributor takes the tasks of one app and works in one app at a time.
2. **GitHub stays the merge gate.** Local CI is for speed.
3. **The GitHub connection lives in Metorite** (WS-54).

**Agent defaults (task 1874).** They stand unless the owner objects. The owner accepted Q1 to Q12 of §8 on 2026-11-06. Q13 is open.

4. **The app lead is `pm_projects.lead`**, found up the project tree. A shared seam is a project with its own lead.
5. **A claim outside the member's apps warns.** A mode per organization can turn the warning into a refusal.
6. **A question has an addressee.** The agent sets a `purpose`. The gateway sets the person: the run's member, or the app lead, or the fallback of owner and admin. The lead joins the answer set of D-EA-14.
7. **Nothing frees a task by itself.** An unanswered question escalates to people and keeps the slot.
8. **A review cap per person** stops new work on that person's apps. It ships as a warning.
9. **The board mirrors GitHub and does not replace it.** A per-organization switch comes first. The App gets no new permission.
10. **A rule that must bind every agent runs in GitHub.** A local hook binds one tool only.

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

### 5.0a The order for P9

1. **Before a second contributor starts.** Build or turn on these:
   - SW-10b (the sweep), SW-7a (the session key) and SW-7b (the label).
   - Task 1803 (the instruction budget) and task 360 (MCP-G6).
   - SW-27 (numbers) and SW-44 (the owner map).
   - The Questions for me slices (tasks 1806 to 1808, 1832) and the Waiting slices AP-2 and AP-15 (task 1875).
   - SW-18 and SW-25, which SW-55 and SW-26 need.
   - The box token (task 1838), if the contributor uses a box.
   - Task 1731 (the answer watcher), SW-60 (park on CI), SW-61 (the Stop gate) and SW-62 (the first-sweep digest). SW-62 comes before SW-10b.
   - SW-53 with GH-8 for the HathiLabs organization, so a merge moves the task. GH-0 comes first: the owner registers the App at GitHub (OWNER-GATE). The install on the GitHub organization is OWNER-GATE too.
2. **Routing.** SW-45, SW-46, SW-48, SW-47, then SW-49.
3. **Seeing.** SW-50, then SW-51.
4. **GitHub.** SW-53, then GH-8 behind the per-organization switch, then SW-54, SW-28, SW-26, SW-52, SW-55 and SW-58.
5. **Other agents.** SW-56, SW-57 and SW-59.

**Why this order.** A second person doubles every clash, every stuck run and every question. The first group removes the silent failures. The second group sends each question to a person who can answer it. The rest adds sight and the GitHub link.

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
- **Digest first.** SW-62 lands before the flip, so the first pass meets a backlog and writes one row (§3.10.11).
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

- **Status.** **SW-31a is built, dark** (backend and MCP return, no UI), behind `AGENT_GUIDANCE_ORGS`. SW-31b (the Settings screen) is not built.
- **Split.** SW-31a: items 1, 3, 4 and 5 below, the routes `/agent-guidance`, and the flag. SW-31b: item 2, the Settings → Agents screen at the new route `/settings/agents`.
- **Gate for SW-31b.** D145 owner preview. It is a new route and a new screen, so the owner approves the preview before the merge.
- **Scope.**
  1. Migration, expand only: a new table `pm_agent_guidance` (migration 340, `infra/postgres/340_pm_agent_guidance.sql`, renumbered from 335 at the merge, R1). It has the shape of `project_alignment.md` §4.2 from the start. Its key columns are `id`, `organization_id`, `project_id`, `version`, `body`, `written_by` and `written_at`. It also holds `kind` (default `guidance`, the one CHECK), `rule_key`, `source_page_id`, `source_page_version`, `quote`, `level`, `state` and `proposed_by`. SW-31a writes `kind = 'guidance'` and NULL in the rule columns, and ALN-5 owns the rest. `project_id` is NULL for the org level. Else it names a `pm_projects` node (a space or a project), with cascade. A unique index on the org, `COALESCE(project_id, nil UUID)`, `kind`, `COALESCE(rule_key, nil UUID)` and the version keeps one row for each version. The COALESCE makes it NULL-safe on each Postgres version. Each save adds a row, so the history stays, and a trigger refuses an UPDATE of a guidance row.
     - The table is tenant-scoped, with ENABLE and FORCE ROW LEVEL SECURITY, in the house shape of `pm_agent_runs`. `test_tenant_coverage.py` fences it (R5).
  2. Settings → Agents → Guidance edits it. An admin writes org guidance. A project lead writes the guidance of a space or a project, under the write floor of `project_management_app.md` §4.
  3. `get_task_context` and `claim_task` return `guidance`: the blocks that apply, nearest first (D140.6). Each block carries its source and its version. `my_work` rows carry only a `guidance_ref`, so the queue stays small.
  4. A block holds at most 8,000 characters. One answer holds at most 20,000, and it drops the farthest blocks first.
  5. The server instructions say: guidance states the org's working rules for the task. It never changes which tasks you read or change, where data goes, or which tools you call.
- **Done when.** An admin writes org guidance and a lead writes project guidance. An agent that claims a task in that project gets both, nearest first. Tests cover the order, the caps and the history.
- **Fence.** An R8 suite for the order and the visibility, `test_tenant_coverage.py` for the table, and `test_mcp_edge_prompts.py` for the rule text.
- **Risk.** HIGH (a migration, a new tenant table). **Gate.** AGENT-SAFE for SW-31a. D145 owner preview for SW-31b.
- **As built (SW-31a).** These notes record the build.
  - **Routes.** They are in `gateway/routes/agent_guidance.py`, not under `/projects`: `POST /agent-guidance` (save), `GET /agent-guidance/history`, `GET /agent-guidance/tasks/{task_id}` and `GET /agent-guidance/refs`. The last two are in `MCP_ROUTES`. The router checks the flag first, then `feature:projects`, as the Projects router does.
  - **Who writes.** The save of a project or a space needs the write floor of a grant (`authority.assert_may_write_guidance`). The save of the org needs `admin:settings:manage` (`core.ORG_VOCABULARY_WRITE`), the house rule for an org-wide write. A `manager` holds `projects:settings:write` and not that permission, so a manager gets 403 on the org.
  - **Who reads.** A member who sees a task but not its space gets the space block. Its source is the stub `{"hidden": true}`, with no name and no project id, as EA-15 shows a hidden parent. The refs read each id through the visibility seam, so a hidden or an unknown project gets null.
  - **The answer.** The edge returns `guidance` as `{blocks, dropped}`. `dropped` counts the farthest blocks that the cap cut. `get_task_context` makes 13 gateway calls at most. The blocks name their level as `project`, `space` or `company`. The chat tool for guidance is a follow-up (SW-31c).

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

#### CIV-14c · `get_task_context` reads every comment

- **Scope.** As task 1728. The input `comments` takes `recent` (default, the newest 20) or `all`. With `all`, the edge pages the timeline route, 100 rows a page, up to 500 comments, and returns them oldest first. The result then has `comments_truncated`, true when older comments did not fit. That mode makes at most `CONTEXT_ALL_CALL_CEILING` gateway calls (the 13 of EA-15a and four more pages). The answer of a run is already a comment, because `agent_runs.py::_comment` writes one for each non-ephemeral activity.
- **Fence.** `uv run pytest tests/unit/test_mcp_edge_context.py -k civ14c`.
- **Status.** Built. **Risk.** STANDARD. **Gate.** AGENT-SAFE.

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

#### SW-43a · Withdraw an answer

- **Scope.** As §3.9. It adds the flag, `answer_changeable_sql` and the route `POST /projects/agent-runs/{run_id}/answer/withdraw`. It adds the `answer` field of the run wire and the hidden read. It adds the `member_withdraw` move, the edge schema, the agent sentence and the "Withdraw my answer" button.
- **Done when.** A withdraw reopens the question and the run reads `awaiting`. An agent activity after the answer gives 409. Another member and an agent caller get 403. A flag-off call gets 404. A replayed key writes once. A race ends in one outcome.
- **Risk.** HIGH (a write on a run). **Gate.** AGENT-SAFE to build dark. OWNER-GATE `enforcement-flip` to turn on.

#### SW-43b · Change an answer

- **Scope.** A replace route that withdraws and answers in one transaction. An option `confirm: true` asks "Are you sure?" before it writes. A Change button on the card.
- **Depends on.** SW-43a.

#### SW-43c · Chat tool and flag on

- **Scope.** The chat tools `withdraw_agent_answer` and a change tool. The agent text on by default. The flag on.
- **Depends on.** SW-43a and SW-43b.

#### CIV-14a · A claim and an ask carry the session link

- **Scope.** Metorite task 1726. `claim_task` and `ask_on_task` take an optional `external_link`: an https link, at most 2048 characters. It sets `pm_agent_runs.external_link` on the run that the call opens or finds. The run must belong to the caller, as the lease gate decides. A new link replaces the old link, and a call with no link keeps it. `POST /projects/tasks/{id}/claim` takes an optional body `{external_link}` with `extra="forbid"`. A link that is not https gets 422. `my_work` rows return `external_link`, and so does the run wire of `list_agent_runs` and `get_task_context`. The edge shows the link as it shows every URL of a run. The work steps of the prompt tell an agent to pass its session link, such as the Remote Control URL, at the claim.
- **Fence.** `test_mcp_edge_claim.py` (`ci14a` tests, including the real database), `test_mcp_edge_agent_runs.py`, `test_mcp_edge_context.py` and `test_mcp_edge_prompts.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. No migration: the column exists since the table.

#### CIV-14f · An answer watcher on the home box (Metorite task 1731, G2b)

- **Scope.** A systemd user timer on `metorite-home` runs `scripts/home_server/answer_watcher.py` every 2 minutes. The script calls `list_agent_runs` with `needs_agent` true. For each run that needs an agent, it starts ONE fresh headless session in a new worktree `wt-answer-<run id 8>-<wake 6>`, with the run id in the prompt. The owner chose a fresh session for each run (task 1731, 2026-11-06). The watcher never wakes an old session, because no tool can reach a session that ended its turn.
- **Which runs.** The list already holds only runs that wait for an agent. The state is not a filter, because an answered run reads `stale` five minutes after its answer, and the edge wire carries no reason. The watcher skips a run that is `stopping`, a `stranded` run (a nudge, not an answer), a run with a bad id, and a run with activity in the last 2 minutes, so a live session of the owner acts first. **The vet:** before a start it reads the run, and starts only when the newest person prompt is by the member of the run. A prompt by another member starts nothing.
- **One session for one wake.** A wake is a run id plus its `last_activity_at`. An attempt file with `O_CREAT | O_EXCL` per wake means a second tick starts nothing. A new answer changes the stamp, so it is a new wake. A wake that no session took up is tried again after 2 hours, at most 3 times.
- **Each session is a transient unit.** The watcher runs as a `oneshot` unit, and systemd kills what stays in its cgroup. So each session starts with `systemd-run --user --collect --unit=answer-<slug>`. The live count is the number of running `answer-*` units, and the cap is 2. It starts nothing when less than 5000 MB is free (lane rule 11). A sweep removes a `wt-answer-*` worktree that holds no change and no commit, after 24 hours, and only when no session runs.
- **The credential (decided by the owner, 2026-11-06, task 1731).** The watcher has its own read-only token: a box token (D-EA-5, `external_agent_access.md` §10.9) with the scope `projects:read` only. The owner makes it in Integrations, AI agents once EA-19c and EA-19d merge. The owner drops it in `~/.config/metorite/watcher.env` (mode 0600) through the secrets drop (`docs/secrets_drop.md`). The script reads it from the environment and never prints it, and no child gets it. A session that the watcher starts uses the login of its own lane. The default session command allows the Metorite tools only (`--allowedTools mcp__metorite__*`), and `WATCHER_CLAUDE_CMD` overrides it.
- **A known gap: other clients.** `list_agent_runs` lists the runs of every client of the member. The watcher cannot tell a run of the owner's PC from a run of the box, so the 2-minute quiet window is its only guard. A session that cannot claim the run (another connection holds it) ends with `release_task`.
- **OPEN, the answer rule (owner, task 1731).** The platform says a session acts on an answer only when `yours` is true and it asked the question itself. A fresh session did not ask. So the session the watcher starts reads the run and, under today's rule, must not act on the answer. The question is on the task.
- **Status.** BUILT 2026-11-06, dark, not merged (Metorite task 1731). `answer_watcher.sh install` copies the script and the units and enables nothing. `enable` refuses a token file that is not mode 0600. Enabling the timer is OWNER-GATE (`deploy`). Two Opus readers reviewed the first draft. They found the oneshot kill, the lost `stale` answers, a second answer that never started, a lock cap that did not cap, and the answer rule above. The text and the code now fix all but the last.
- **Fence.** `uv run pytest tests/unit/test_answer_watcher.py`, with a fake `list_agent_runs`, a fake starter and a fake live count. It covers one start for one wake, a new answer, a `stale` run, the skips, the live cap, the vet, the retries, a failed read, the transient unit, the token never in a child, and the sweep.
- **Risk.** HIGH (it starts sessions with credentials). **Gate.** AGENT-SAFE to build. OWNER-GATE to enable the timer (`deploy`) and to mint the token.

### 5.9 P8 — deferred, the owner decides

| Slice | What | Waits on | Gate |
|---|---|---|---|
| SW-20 | Cost for each agent, task and request. A self-reported `usage` first | H-42, the rate card | OWNER-GATE |
| SW-21 | Goals linked to projects, given to agents as context | A product-shape call. Q4 of §8 | OWNER-GATE |
| SW-22 | A watchdog: a verifier agent for a finished subtree | P3 live, then SW-10, SW-15 and SW-16 | OWNER-GATE |

### 5.10 P9 — many people, each with their own agents

**Source.** §3.10 and D183. Task 1874 holds the brief. Each slice below has a task in Backlog under epic 321.

Slices of other specs keep their own owners. P9 depends on them. They are MCP-G6 (task 360), MCP-G16 (task 1803), EA-18 (tasks 1806 to 1808 and 1832), the box token (task 1819) and AP-1 to AP-14. Others are SW-7a, SW-10b, GH-8, SW-18, SW-26, SW-27 and SW-28.

They keep their own gates.

#### SW-44 · The app-owner map on the board

- **Metorite task.** 1877.
- **Scope.**
  1. Make a CRM project. Move the 187 tasks that sit directly in "Metorite Application" into an app or a seam project. Use `preview_move_tasks` first. Count them again at dispatch.
  2. Make the seam projects of §3.10.1 that do not exist yet.
  3. Set `lead` on each app and seam project. This is a grant write (`authority.py`). The owner names the leads and does the writes on production, or opens a dated grant (Q6).
  4. Add a Needs attention kind, "App project with no lead". It lists a child of "Metorite Application" that has open tasks and no lead of its own.
- **Done when.** No task sits directly in "Metorite Application". Each app and seam project has a lead. A test app project with open tasks and no lead shows the row.
- **Fence.** `test_needs_attention_r8.py` holds the kind. The board moves are advisory, because no test reads production data (R7).
- **Risk.** HIGH (a grant write). **Gate.** AGENT-SAFE for the code. OWNER-GATE for the lead writes on production (CLAUDE.md §3a rule 3).

#### SW-45 · Own apps in `my_work` and `claim_task`

- **Metorite task.** 1878.
- **Scope.**
  1. Read "own apps" as §3.10.1 says, through `authority.py`. No migration.
  2. `my_work` gains the group `own_app_free` (§3.10.1). Each row carries `app_project`. Assigned rows keep their order.
  3. In mode `warn`, `claim_task` of an unassigned task outside the member's apps works. It returns a `warning` that names the lead. It writes one timeline line. In mode `refuse` it answers 403 `outside_apps`. A task assigned to the member always passes.
  4. The flag `AGENT_CLAIM_SCOPE_MODES`, a map from organization to `off`, `warn` or `refuse`, default `off` (`docs/flag_reload.md`).
- **Done when.** R8: a member with one own app gets that app's free tasks in `own_app_free`. A claim outside it warns in `warn`, gets 403 in `refuse`, and passes in `off`. An assigned task passes in all modes.
- **Fence.** `test_agent_claim_scope_r8.py` (new). `test_app_ownership_seam.py` (new) fails when a module other than `authority.py` names `pm_projects.lead` or `lead =`. Its allowlist holds the public walk SQL symbol of `authority.py`. An edge test for the warning text.
- **Risk.** HIGH (a refusal on a write). **Gate.** AGENT-SAFE dark. The flip to `refuse` is OWNER-GATE `enforcement-flip`. **Depends on.** SW-44. Q6.

#### SW-46 · The lead answers, and a preview goes to the lead first

- **Metorite task.** 1879.
- **Scope.** No migration. The ladder of AP-15 resolves the addressee at read.
  1. `ask_on_task` takes `purpose`: `question`, `preview` or `review`. An unknown `purpose` value gets 422. The value goes in the activity `content`.
  2. For `preview` and `review`, the ladder of AP-15 starts at the nearest lead (§3.10.2). Edit the ladder function of AP-15. Add no second ladder.
  3. `person_may_answer_sql` gains the app lead, through the public lead-walk SQL of `authority.py`. This is the one text. `AGENT_QUESTIONS_ORGS` stays the only flag.
  4. The invariant of §3.10.2 holds. The ladder drops a person outside the answer set. It does not widen the set to watchers or to `data:org:read` holders.
  5. The `my_work` row of an answered run carries `answered_by`.
  6. The window rule of SW-43a reads "written by the person who answered".
  7. The `work_on_task` steps tell the agent to use `preview` and `review`, and to put `confirm: true` on a costly option. The text does not go in the server instructions.
- **Done when.** R8: a contributor's agent asks with `purpose: preview` on an app with lead L. L sees it in the list and answers it. The contributor's row says "Waiting for L". The run reads `answered`, and the contributor's `my_work` shows `answered_by` L. A wait with no lead and no assignee reaches an owner, admin or manager. That person answers with 200. A member outside the answer set gets 403.
- **Fence.** `test_agent_questions_r8.py` (task 1806) and `test_waiting_seam_r8.py` (AP-15) extended, and `test_mcp_edge_prompts.py` for the `work_on_task` text.
- **Risk.** HIGH (identity, who may answer). **Gate.** AGENT-SAFE, dark behind `AGENT_QUESTIONS_ORGS`. **Depends on.** Task 1806 merged, AP-15 (task 1875), SW-44, SW-43b (task 1801), task 1803. Q7.

#### SW-47 · Stuck work reaches the app lead

- **Metorite task.** 1880.
- **Scope.**
  1. The recovery record of a stranded run goes to the run's member and the app lead. The first nudge stays with the run's member.
  2. An `awaiting` run past three leases gets one fingerprint with the reason `question_unanswered`, and no `agent_quiet` fingerprint (§3.10.5). The reason CHECK of `pm_agent_run_strands` allows only `agent_quiet` and `answer_unread`, and it has no name. Look up its name at dispatch. Drop it, add the wider one as `NOT VALID`, then `VALIDATE` (migration, expand only, the pattern of migration 342).
  3. The Needs attention row offers Answer and Release the run. The slot stays held.
- **Done when.** R8 with a moved clock: at three leases one bell and one row appear, and the task is still held. A repeated sweep writes once. The app lead sees the recovery record.
- **Fence.** `test_agent_run_sweep_r8.py` and `test_needs_attention_r8.py`.
- **Risk.** HIGH (a migration, the sweep). **Gate.** AGENT-SAFE dark. The sweep itself is OWNER-GATE `enforcement-flip` through SW-10b. **Depends on.** SW-10b, SW-46, AP-15, task 1730.

#### SW-48 · The lead acts on the runs of their apps

- **Metorite task.** 1881.
- **Scope.** Edit `person_may_end_sql` in `agent_runs.py`, the one text. `may_end_run` and `needs_attention.py` read it. So the button and the route agree. Add one branch with the public lead-walk SQL of `authority.py`. The app lead of the run's task may Release and Resume the run. Today the org-wide case uses `RELEASE_ADMIN_PERMISSION = "admin:members:read"`. Q5 recommends `admin:members:manage` and is still open. The lead does not answer by this rule (SW-46).
- **Done when.** R8: the lead of the app gets 200 on Release and Resume. A member who is not the run's member and not the lead gets 403. A lead of another app gets 403. `can_act` on Needs attention agrees with the route.
- **Fence.** `test_agent_runs_r8.py`, `test_agent_run_exclusive.py` and `test_needs_attention_r8.py`.
- **Risk.** HIGH (a write authority rule). **Gate.** AGENT-SAFE. **Depends on.** SW-44. Q5 and Q9.

#### SW-49 · Review capacity

- **Metorite task.** 1882.
- **Scope.**
  1. One SQL text, `filters.review_queue_sql`, counts the items that wait on a member (§3.10.3).
  2. The setting `review_cap` for each member, default 5. MCP-G6 (task 360, no spec, not built) caps open runs for each connection and has no settings row yet. SW-49 fixes the store of `review_cap` at dispatch, and it adds a table only if no settings store fits.
  3. A task whose app lead is at the cap gets 429 `review_capacity` on `claim_task` and `start_agent_run`. The answer holds the count, the cap and the age of the oldest item. In mode `warn` the claim works and carries a warning. `my_work` leaves out the `own_app_free` rows and says why.
  4. The flag `AGENT_REVIEW_CAP_MODES`, default `off`.
- **Done when.** R8: a lead with 5 waiting items. A contributor's sixth claim gets 429 in `refuse` and a warning in `warn`. The lead answers one, and the claim passes. A run that exists is never stopped.
- **Fence.** `test_review_capacity_r8.py` (new).
- **Risk.** HIGH (a refusal on a write). **Gate.** AGENT-SAFE dark. The flip to `refuse` is OWNER-GATE `enforcement-flip`. **Depends on.** SW-46. Task 360 helps but is not needed. Q8.

#### SW-50 · The run shows the human and the box

- **Metorite task.** 1883.
- **Scope.** The run wire gains `for_member` and `box`. `box` is the name of the connection when the connection is a box token, else null. Read it through `pm_agent_runs.connection_id`. Add a column only if the join is not enough. The card, the run panel, the bell, the fleet view and `get_agent_run` show "Claude Code for Ravi · on metorite-home".
- **Done when.** A run of a box token shows the box. A run of a revoked connection shows `box` null and does not fail.
- **Fence.** `agentCardLook.test.ts` and a route test.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview. **Depends on.** The box token slices of task 1819 (PR 1764, task 1838), SW-7b.

#### SW-51 · The fleet view

- **Metorite task.** 1884.
- **Scope.**
  1. Extend `GET /projects/{project_id}/agent-summary` (`agent_summary.py`) with `group_by=person`. The view loops over the roots that the viewer can see (spaces, personal trees and "Metorite Application"). Add no second route.
  2. Group by person, box, agent and session. Fields as §3.10.6 lists. Cost reads "unknown".
  3. A Fleet tab in the Agents panel (SW-8). No new nav pane.
  4. A class A chat read in `skill_projects/manifest.py`.
- **Done when.** R8 with two members and two apps under two different roots: the owner sees both apps and the lead sees only theirs. No row shows a task the viewer cannot see. `visual-review` passes at four widths.
- **Fence.** `test_projects_agent_focus_r8.py`, `test_projects_chat_coverage.py`, and a vitest for the tab.
- **Risk.** HIGH (visibility across the organization, as AP-12). **Gate.** AGENT-SAFE. D145 owner preview, because the tab is a new screen. **Depends on.** SW-8, SW-45, SW-50.

#### SW-52 · Collision notes reach the owners

- **Metorite task.** 1885.
- **Scope.**
  1. Migration, expand only: `pm_projects.owned_paths TEXT[] NULL` on a seam project.
  2. The notes of SW-26 and SW-28 go to both run members and the lead of each app. A change on an owned path goes to the seam lead too.
- **Done when.** Two pull requests add the same migration number: both run members and the leads get one note each. A note never names a task that its reader cannot see.
- **Fence.** `test_conflict_radar_r8.py` and `test_needs_attention_r8.py`. The match between `owned_paths` and CODEOWNERS is advisory (R7).
- **Risk.** HIGH (a migration, visibility). **Gate.** AGENT-SAFE. **Depends on.** SW-26, SW-28, SW-44.

#### SW-53 · GitHub per organization

- **Metorite task.** 1886.
- **Scope.** The setting `GITHUB_APP_ORGS`, a list of organizations that reloads with no restart. `github_app_enabled` stays the master. The webhook receiver, the connect flow and the reconcile poll check the list. A delivery for an organization not on the list gets 202 and writes nothing.
- **Backfill.** After the turn-on, one pass applies the merge rule (GH-6) to tasks whose pull request merged earlier. GH-6 runs only when a row moves into `merged`. A reference that `add_task_reference` wrote with no GitHub row does not trigger it. The backfill must read those too. A dry run lists the tasks first and writes nothing. The owner approves the list (OWNER-GATE, a production one-off). The apply moves only the listed agent-worked tasks. Check at dispatch that the poll reads merged references. R8 holds both: the dry run writes nothing, and the apply moves only listed tasks.
- **Done when.** R8: with the master on and organization B off the list, a delivery for B writes nothing, and one for A links its pull request.
- **Fence.** `test_github_webhook_r8.py` (new cases) and `test_github_reconcile_r8.py`.
- **Risk.** HIGH (webhook ingress, tenancy). **Gate.** AGENT-SAFE dark. The turn-on is GH-8. **Depends on.** GH-4 to GH-6.

#### SW-54 · Required checks on the task

- **Metorite task.** 1887.
- **Scope.** An admin lists the required check names for the organization. The default is empty, and the 7 names of `ci_velocity.md` §4 seed HathiLabs only. The fold of GH-5 reports `required_passed` and `required_total`. A required check that has not run counts as pending. The task and the card show "5 of 7 required".
- **Done when.** A reference with 7 required names and 5 passed reads "5 of 7". A missing name reads pending.
- **Fence.** `test_github_checks.py` (new cases).
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview. **Depends on.** GH-5 live (GH-8), SW-53. Q10.

#### SW-55 · A merge and the review stage agree

- **Metorite task.** 1888.
- **Scope.** The G-4 rule gains a step. When the project setting "Agent work needs review" (SW-18) is on, a merge moves the task to the review lane. No map from a GitHub login to a member exists. So under the setting every merge lands in review. The timeline names the merger. A login map is later work and has no slice.
- **Done when.** R8: with the setting on, a merge lands in review and the timeline names the merger. With the setting off, GH-6 is unchanged.
- **Fence.** `test_github_merge_r8.py` (new cases).
- **Risk.** HIGH (a write authority rule). **Gate.** AGENT-SAFE. **Depends on.** GH-6, SW-18.

#### SW-56 · A protocol block in the results

- **Metorite task.** 1892.
- **Scope.** The results of `claim_task` and `my_work` carry `protocol: {version, lines}`. It holds at most 6 lines of 120 characters. The lines say: claim the slice, not the epic. Post a thought each minute. Ask with `ask_on_task` when blocked, and never only in the terminal. End each turn with one disposition: review or Done, a question, a release, or (where SW-60 is on) a park on CI. Hand off with evidence. The text is fixed server text.
- **Done when.** A client that ignores instructions and prompts still reads the block in the claim result.
- **Fence.** `test_mcp_edge_context.py` and `test_mcp_edge_prompts.py`. The 3500-character budget fence belongs to task 1803.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** Task 1803 (no spec).

#### SW-57 · The evidence of a pull request is a GitHub check

- **Metorite task.** 1889.
- **Scope.** `scripts/check_pr_evidence.py` reads the pull request body. It needs a task URL, an id `mt-<8 hex>`, or the line `No task: <reason>`. It also needs a `Verifier:` line and a `Reviewer:` line. The template of ONB-6 (task 1872) names the headings. A pr-check step runs the script. The step reports only for one week. The owner then makes it a required check.
- **Done when.** Unit tests cover each body case. The step runs on a pull request from any author.
- **Fence.** `tests/unit/test_check_pr_evidence.py` (new).
- **Risk.** STANDARD. **Gate.** AGENT-SAFE to build. OWNER-GATE to make it required, because branch protection is the owner's. **Depends on.** ONB-6 (task 1872, no spec yet).

#### SW-58 · Make a task from a pull request

- **Metorite task.** 1890.
- **Scope.** The row "Work with no task" (SW-28) gains the act "Make a task". The lead picks a project. The act makes the task, adds the `pull_request` reference and removes the row. A chat tool does the same (class B, one confirm card).
- **Done when.** R8: the act makes one task with one reference, and the row leaves. A second press makes no second task.
- **Fence.** `test_needs_attention_r8.py`, a route test and `test_projects_chat_coverage.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview. **Depends on.** SW-28, SW-53.

#### SW-59 · A run declares its role

- **Metorite task.** 1891.
- **Scope.** Migration, expand only: `pm_agent_runs.role TEXT NULL`, with a CHECK of `implementer`, `verifier` and `reviewer`, added as `NOT VALID` and then `VALIDATE` (R6). `claim_task` and `start_agent_run` take an optional `role`. The task shows "Verified by <run>" when a verifier run of another run id finished on the head of the task's pull request. The role is display data.
- **Done when.** Two runs with two roles show two chips. The role reaches no authorization check.
- **Fence.** The structural test of §3.7 (extended), an edge schema test and a vitest for the chip. The chip is advisory (R7).
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE. D145 owner preview for the chip. **Depends on.** SW-7b, GH-5 (the head of the pull request) and SW-53.

#### SW-60 · A CI wait is handed to Metorite

- **Metorite task.** 1902.
- **Scope.**
  1. `post_agent_activity` `waiting` takes `on: "ci"` with a `pr`, a pull request that is already a reference of the task. A park of this kind has the cap `AGENT_CI_PARK_MAX_MINUTES`, 240 by default (Q13), and not the 120 of 1045a. Migration, expand only: `pm_agent_runs.waiting_ref_id UUID NULL`, the reference that the run waits on.
  2. A parked run has a live path (§3.3). Its checks have not ended, and the cap has not passed. `lease_held_sql` gains a parked arm up to the cap. It is the one lease text, so the takeover rule, the `unheld` filter and `held_by` all follow. The stranded clause follows too. The session may end.
  3. The checks may end, or the pull request may merge or close. Then the run reads `needs_agent` with the reason `ci_done`. The result is passed, failed or merged. The GitHub path (1045b, GH-5) writes it once for each `(run, head commit, conclusion)`. It renews the lease once. The event moves `last_signal_at` only and never `last_activity_at`, so the run does not read `answered` (1045b item 4). The event also clears the `session_key`, as Resume does, so the next session can take the run.
  4. Without the GitHub connection, the box watcher (task 1731) reads the parked runs. It runs `gh pr checks` for each. It starts a session in the task's lane when the checks end. It needs no write scope. It lists parked runs with `list_agent_runs` filtered to `needs_agent` and reason `ci_done`. Its session must use the connection of the parked run, or a connection of the same member. Check this against the box token at dispatch (task 1838).
  5. At the cap the run is an ordinary stranded run (SW-10a).
  6. The `work_on_task` steps tell the agent what to do. With a pull request open and checks pending, post `waiting` on `ci` with the `pr`. Then end the turn. The text applies only to the organizations on the flag. Elsewhere a park strands at 120 minutes.
- **Done when.** R8 with a moved clock: a parked run reads `waiting` past 120 minutes, up to the cap. The stranded clause does not match it. A check end writes one `ci_done` and one lease renewal. A replayed event writes nothing. A second claim at minute 130 of a parked run gets 409. After the cap, the run is stranded. A watcher test with a fake `gh` starts one session for one run.
- **Fence.** `test_agent_runs_r8.py`, `test_agent_run_sweep_r8.py`, a webhook test beside the GitHub fences, and a test of the watcher in `scripts/home_server/`.
- **Risk.** HIGH (a migration, the lease). **Gate.** AGENT-SAFE dark behind `AGENT_CI_PARK_ORGS`, a flag that follows `docs/flag_reload.md`. **Depends on.** 1045a, 1045b, GH-5 for the GitHub path, task 1731 for the restart. Q13.

#### SW-61 · A turn does not end with a run and no disposition

- **Metorite task.** 1903.
- **Scope.**
  1. A Stop hook for a background session, in `stop-children.mjs` of CIV-16 (task 1828, PR 1763). It reads `transcript_path` from its input. It needs no token and calls no MCP. A background session is one that the lane launcher marks with an environment variable. Name the variable at dispatch. An interactive session passes.
  2. The hook matches tool names by their suffix, for example `mcp__metorite__claim_task`. It counts a claim only when its tool result is not an error, so a 409 does not block. It maps the run id of the result to the task. A disposition for that run is `finish_agent_run`, `release_task`, `ask_on_task`, a `waiting` post with a `pr` (SW-60), or a `set_task_status` call. The hook cannot read the status of a call with no token, so any `set_task_status` on the task counts.
  3. The hook blocks the stop once, with the decision `block`. It passes when `stop_hook_active` is true. The message names the four dispositions: review or Done, `ask_on_task`, park on CI, or `release_task`. It says that a question goes through `ask_on_task`, never only to the terminal. A second stop passes.
- **Done when.** Hook tests with transcript fixtures: a claim with no disposition blocks once and then passes. A claim with a release passes. A claim with a 409 passes. A session with no claim passes. An interactive session passes. The message holds the four names.
- **Fence.** `node .claude/hooks/stop-children.test.mjs` (the test file of the hook, extended). `test_mcp_edge_context.py` holds the protocol line of SW-56. A hook that cannot read the transcript passes and logs, so it never traps a session.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE to build. OWNER-GATE `guard-write` for the entry in `.claude/settings.json`. The dated grant opens it until 2026-11-30. **Depends on.** PR 1763 (task 1828), SW-56.

#### SW-62 · The first sweep writes one digest, and the owner can release the runs of ended sessions

- **Metorite task.** 1904.
- **Scope.**
  1. A sweep pass may find more than 5 new strands in one organization. Then it writes one digest. The store is `pm_agent_run_strands`, with a new kind `digest` (migration, expand only: widen the CHECK as `NOT VALID`, then `VALIDATE`). Needs attention reads it as one row, "N runs have no live session". The row lists the runs and groups epic holds. The pass sends no single nudge.
  2. The second miss rolls up too. One lease later the sweep updates the digest and writes no row for each run.
  3. The reader of the row is a person who may end the runs (item 6). The list holds only the tasks that the reader can see (D12).
  4. A bulk act, `POST /projects/agent-runs/release-ended`. It shows the count first. After one confirm it releases each eligible run. Eligible means stranded or past the lease, and not working. A run in `awaiting` within three leases and a run parked under SW-60 are not eligible. The act never reassigns. It writes one run activity for each run, as the release route does.
  5. A chat tool does the same (class B, one confirm card).
  6. The act uses both halves of the end rule. `person_may_end_sql` covers the member and the assignees. `may_end_run` with `RELEASE_ADMIN_PERMISSION` covers the admin. Q5 changes that permission to `admin:members:manage`. A caller releases only the runs that the caller may end.
- **Done when.** R8: 36 candidate runs give 1 row and 0 nudges in the first pass. The bulk act releases the eligible runs only. A working run is never released. A second pass writes nothing.
- **Fence.** `test_agent_run_sweep_r8.py`, `test_needs_attention_r8.py`, `test_projects_chat_coverage.py`. A named mutation: a release of a working run turns the suite red.
- **Risk.** HIGH (a migration, a write on many runs, the sweep). **Gate.** AGENT-SAFE dark. The click on production is the owner's. D145 owner preview for the row. **Depends on.** SW-10a, SW-48. SW-10b waits for it.

#### SW-63 · A claim on an epic warns

- **Metorite task.** 1905.
- **Scope.** `claim_task` of a task with open subtasks claims it and returns a `warning`. An open subtask is one that `filters.task_closed_sql` does not match. The warning reads: "This task has N open subtasks. Claim the slice, not the epic." The result lists up to 5 subtask ids. It is a warning and not a refusal. It adds one optional field to the result. This slice widens rule 3 of §2.3 in the same edit. An optional result field may go live under `agent_runs_enabled`, as an optional argument may. The `work_on_task` steps say the same.
- **Done when.** R8: a claim of a parent with 2 open subtasks carries the warning and works. A claim of a leaf has no warning.
- **Fence.** `test_mcp_edge_claim.py` and `test_agent_run_exclusive.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE.

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

**SW-43a** (find each symbol by name): `withdraw_agent_answer` and `_MOVES["member_withdraw"]` in `agent_runs.py`. `answer_changeable_sql` and `live_answer_sql` in `filters.py`. `answer_change_enabled` in `agent_oauth.py`. `agent_answer_change_enabled` in `settings.py`. `ANSWER_RULE` in `agent_edge/prompts.py`. `withdrawPayload` in `agentRuns.ts`.

**P9** (read on 2026-11-06 at `f504698bd`). Find each symbol by name. `pm_projects.lead` in `infra/postgres/146_projects.sql`. `may_end_run` in `routes/projects/agent_runs.py`. `pm_agent_runs.connection_id` in `267_pm_agent_runs.sql`.

`people_absences` in `174_people_absences.sql`. `github_app_enabled` in `settings.py`.

The sweep is in `gateway/agent_run_sweep.py`. The instruction text in `agent_edge/mcp.py` (`_ROUTINE_INSTRUCTIONS`, `_RUN_INSTRUCTIONS`). The mode-flag idiom in `settings.py` (`CRM_ANSWER_CHECK_MODES`) and `docs/flag_reload.md`. No `CODEOWNERS` file exists on main.

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
| SW-43a | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_runs_r8.py tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_ui_actions.py tests/unit/test_mcp_edge_agent_runs.py`, and `npx vitest run src/app/projects/lib/agentRuns.test.ts` |
| SW-44 | `uv run pytest -q -p no:cacheprovider tests/unit/test_needs_attention_r8.py` |
| SW-45 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_claim_scope_r8.py` (new) `tests/unit/test_agent_label_fence.py` |
| SW-46 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_questions_r8.py tests/unit/test_agent_approver_r8.py` (new) `tests/unit/test_mcp_edge_prompts.py tests/unit/test_tenant_coverage.py` |
| SW-47 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_run_sweep_r8.py tests/unit/test_needs_attention_r8.py` |
| SW-48 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_runs_r8.py tests/unit/test_agent_run_exclusive.py` |
| SW-49 | `uv run pytest -q -p no:cacheprovider tests/unit/test_review_capacity_r8.py` (new) |
| SW-50 | `npx vitest run src/app/projects/lib/agentCardLook.test.ts`, and the route test (new) |
| SW-51 | `uv run pytest -q -p no:cacheprovider tests/unit/test_projects_agent_focus_r8.py tests/unit/test_projects_chat_coverage.py`, and the vitest of the tab (new) |
| SW-52 | `uv run pytest -q -p no:cacheprovider tests/unit/test_conflict_radar_r8.py tests/unit/test_needs_attention_r8.py tests/unit/test_tenant_coverage.py` |
| SW-53 | `uv run pytest -q -p no:cacheprovider tests/unit/test_github_webhook_r8.py tests/unit/test_github_reconcile_r8.py` |
| SW-54 | `uv run pytest -q -p no:cacheprovider tests/unit/test_github_checks.py` |
| SW-55 | `uv run pytest -q -p no:cacheprovider tests/unit/test_github_merge_r8.py` |
| SW-56 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_context.py tests/unit/test_mcp_edge_prompts.py` |
| SW-57 | `uv run pytest -q -p no:cacheprovider tests/unit/test_check_pr_evidence.py` (new) |
| SW-58 | `uv run pytest -q -p no:cacheprovider tests/unit/test_needs_attention_r8.py tests/unit/test_projects_chat_coverage.py` |
| SW-59 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_label_fence.py tests/unit/test_mcp_edge_coverage.py`, and the vitest of the chip (new) |
| SW-60 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_runs_r8.py tests/unit/test_agent_run_sweep_r8.py`, the webhook test (new), and the watcher test in `scripts/home_server/` (new) |
| SW-61 | `node .claude/hooks/stop-children.test.mjs` (extended), and `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_context.py` |
| SW-62 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_run_sweep_r8.py tests/unit/test_needs_attention_r8.py tests/unit/test_projects_chat_coverage.py` |
| SW-63 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_claim.py tests/unit/test_agent_run_exclusive.py` |

---

## 8. Open owner questions

**Decided 2026-11-06 at 10:33 UTC (owner, task 1874, "Take all recommendations (Q1 to Q12)").** Q1 to Q12 hold the recommended answer, with the Q7 correction of the same task. Q13 is open. A slice that waits on a question says so.

| # | Question | Recommendation | Blocks |
|---|---|---|---|
| Q1 | Does an `agent:<name>` assignee move into the delegate slot? | **Yes, by expand and contract.** SW-29a reads an `agent:<name>` assignee as the delegate. A new delegation writes the delegate, and the dispatch sink listens to both for one release. A later slice stops new `agent:<name>` assignee writes. D-PM-4 keeps one actor vocabulary | SW-29a |
| Q2 | May a member hand a task to another member's agent? | **Yes, to a shared agent only.** An agent is private to its member by default. Its member, or an admin, can share it with the org in its profile. A shared agent still acts as its own member, so it sees only what that member sees. The timeline says "delegated by Priya" | SW-29a item 5, SW-29b, SW-30a |
| Q3 | Which cloud agents get a connector in SW-30b? | **The GitHub Copilot coding agent first.** It has a documented member-credential API (`external_agent_dispatch.md` §3), and the GitHub App already exists. The others wait for the WS-56 decision | SW-30b |
| Q4 | Do cost attribution and goals wait for H-42? (The open question of epic 321) | **Cost waits for H-42. Goals fold into WM-5** (task 984, goals and key results), so Projects gets one goal object | SW-20, SW-21 |
| Q5 | May a person with `admin:members:read` end another member's run? (H-409 item 2) | **No. Use `admin:members:manage`.** Ending a run is a write | H-409, SW-29b Stop all |
| Q6 | May a claim outside the member's apps be refused? (P9, §3.10.1) | **Warn first, per organization.** Refuse only after one week of data. The owner names the lead of each app (SW-44) | SW-44, SW-45 |
| Q7 | Who answers a preview or a review, and when does an unanswered question escalate? | **The app lead first, by the ladder of the Waiting spec (AP-15).** The circle opens at 24 hours (AP-Q9). P9 adds no second clock. The slot stays held | SW-46, SW-47 |
| Q8 | What is the review cap for each person? | **5 items, in mode `warn` first.** The age of the queue tells when to change it | SW-49 |
| Q9 | May the app lead Release and Resume a teammate's run? | **Yes, for the lead of the run's task project.** Q5 stays as it is for the org-wide case | SW-48 |
| Q10 | How does GitHub reach the board? | **A per-organization switch (`GITHUB_APP_ORGS`). No new App permission.** An admin types the required check names. The board keeps its own owner map and does not read CODEOWNERS | SW-53, SW-54 |
| Q11 | Does the evidence check of a pull request become a required check? | **Report only for one week. Then the owner makes it required**, because branch protection is the owner's | SW-57 |
| Q12 | Do GitHub issues from contributors with no access become tasks? | **No, not in v1.** A draft pull request with a task URL or `No task:` is the path. Issues need the Issues permission | SW-58 |
| Q13 | How long may a run stay parked on CI (SW-60)? | **240 minutes.** GitHub checks waited 1 to 3 hours for a runner on 2026-11-06. At the cap the run is an ordinary stranded run | SW-60 |

---

## 9. What this spec could not settle

- **The column names of the delegate.** SW-29a fixes them at dispatch, under the rules of §3.7 and Q1.
- **The `push` event.** It is not known whether the GitHub App subscribes to `push` (1045b).
- **The board half of SW-5.** EA-13g may already draw part of the holder line. Read it at dispatch.
- **The production flags.** This spec uses the reading of 2026-10-29. Read the env files of the box again before SW-7a and SW-10b.
- **The read-only door.** The edge already hides write tools from a connection with read scopes only, and it refuses their call (SW-34b, "What exists"). It is not known yet how a client keeps a separate token for the path `/mcp/readonly`. SW-34b settles it.
- **Stale status lines in `external_agent_access.md`.** Its header still calls SW-7, SW-10a and SW-11a "not merged". They merged as #1312, #1333 and #1345. The next slice that touches that file fixes the lines.
- **The owner map.** CODEOWNERS (task 1872, ONB-7) and the leads on the board are two maps. A pr-check test cannot read the board. SW-52 keeps them in step by hand, and it is advisory.
- **The login-to-member map.** SW-55 needs to know which GitHub login is which member. Read at dispatch whether such a map exists.
- **Own apps.** The rule "lead or write grant" needs a check of the grant tables at dispatch (SW-45).
- **Staging is retired (D144).** SW-7a, SW-10b and GH-8 still name a staging step. P9 adds no staging step. The slice that turns each one on must restate its order.
- **Dependencies with no spec.** ONB-6 and ONB-7 (task 1872), the answer watcher (task 1731), MCP-G6 (task 360) and MCP-G16 (task 1803) live in tasks only. Build them as their tasks say.
- **Slice names in two branches.** The box token (PR 1764) and "Questions for me" (branch `mt-181a56e0-questions-for-me`) both name slices EA-18a and up. One branch renumbers at merge. This spec cites the tasks (1806 to 1808, 1832 and 1838) so the names are not needed.
- **The D number.** D183 was the next free number on 2026-11-06. Check it again at merge (R1).

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
- **2026-11-06.** P9 amendment for task 1874. It adds §3.10, D183 (§4.1), §5.0a, the slices SW-44 to SW-63 (§5.10), Q6 to Q12 and five open points in §9. It adds no code.
- **2026-11-06, evening.** The owner accepted Q1 to Q12. §3.10.11 and SW-60 to SW-63 answer the board audit of task 1900. Q13 is new.
