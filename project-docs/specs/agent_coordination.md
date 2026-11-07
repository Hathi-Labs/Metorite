# Agent coordination — many agents and many people on one board

**Status: ACTIVE (SW-0, 2026-10-30). SW-43a (withdraw an answer) is built and dark, 2026-11-05. SW-43b and SW-43c are open.**

**CIV-14g (the watcher hand-off of the answer rule, §5.8) is built, not merged, 2026-11-07.**

**P10 (§3.11, §5.11, D186) was planned on 2026-11-06. No P10 slice is built.** The know-how for other organizations is in `orchestration_playbook.md`.

**P9 (§3.10, §5.10, D183) was planned on 2026-11-06.** **SW-63 is built. No other P9 slice is built.**

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
- The slices SW-0 to SW-36, the two liveness bugs of tasks 1045 and 1054, the P9 slices SW-44 to SW-63, and the P10 slices SW-64 to SW-99 (§5).
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

### 3.11 The fleet that Metorite runs and teaches (P10, D186)

**The ask.** The owner asked on 2026-11-06 (task 1906) for two things. First, "brainstorm all the other capabilities and features that the Metorite MCP for multi-agent orchestration should have". This covers sessions in VS Code and the Metorite platform. Second, Metorite "contains the know-how and understanding" to help set up orchestration "for whoever connects the Metorite MCP". The reference article is https://niteagent.com/blog/multi-agent-production-2026/. Its numbers are not checked. One figure has an arithmetic error. P10 takes its patterns and none of its numbers.

**Order.** P10 builds on the P9 text (§3.10, D183, §5.0a, Q13). It merges after PR 1777.

**How to read this section.** Each table row names an item of the brief. "Already planned" names the slice or the task that holds it. "New" is what P10 adds. The slices are in §5.11. The playbook is the know-how for other organizations. It has its own spec, `orchestration_playbook.md`, with the slices PB-1 to PB-10. §3.11.8 says why.

**The decisions** are **D186** (§4.2). **The price:** every P10 capability and the playbook are part of the flat seat (`launch_surface.md` §4). No SKU and no add-on.

**Evidence from 2026-11-06 that shaped P10.**

- The coordinator could not reach the session `lane-l6p` without a second copy of that session.
- The coordinator grepped transcripts to find the session of task 1650.
- 9 merged tasks stayed In progress for up to 8 days (task 1900).
- The box lost its sign-in at 01:23 UTC, and six answers went unread (task 1819).

**Rules that bind P10.**

1. **One channel to a running agent.** A steer, a watch event and a request from a peer all travel as a notice (SW-64). Nothing adds a second channel.
2. **A notice grants nothing** (D140.2). It is untrusted text with the name of its sender. An agent follows a steer from its own member or from the app lead, as it follows an answer. It treats any other notice as information. No notice changes a scope, a tool or a rule of the server.
3. **Pull stays the base** (D140.4). A notice also waits in the `my_work` row of its run. So an agent that never saw it live still finds it at its next session.
4. **Declared data only warns or narrows.** A lane, a role, a label and a path claim are what the session says about itself. They show on the board and they raise warnings. They never widen what a session may do (D186.5). A rule that binds comes from the connection, the policy store (SW-94) or the member.
5. **Warn first** (rule 3 of §3.10). A rule that refuses or stops a run ships as `off`, then `warn`, then `refuse` or `stop`. The flip to the last mode is an `enforcement-flip`.
6. **No credential in a hook.** The runner and the plugin hold no secret beyond the box tokens (EA-19, task 1819, and the runner token of Q18). A hook never reads the OAuth token of a session (owner call, task 1731).
7. **No HathiLabs rule in a product default** (D186.2). `orchestration_playbook.md` §4 carries the fence.

#### 3.11.1 The engine (section A of the brief)

| Item | Already planned | New |
|---|---|---|
| **Steer a running agent** | A member's `prompt` on an open run makes it `answered`, and `my_work` lists it (§3.2). Stop reaches a run as `stopping`. SW-30a makes an `@agent` on an open run a `prompt`. SW-43 gives the member a window to take an answer back. All of these reach an agent that calls `my_work` or `get_agent_run`. None reaches an agent in the middle of a turn | **SW-64.** A notice of kind `steer`. It rides on the next result of a Metorite tool that already reads the run. A fast path through the runner and the plugin (SW-78, SW-82) gives near-instant delivery on a box |
| **Park and wake** | The `waiting` state (1045a, up to 120 minutes). SW-60 parks a run on its CI. SW-15 wakes a run when its last blocker closes, and SW-16 sets the blocker. SW-9 is the wait record. SW-17 (push) is deferred. Task 1731 restarts a session for an answer | **SW-66.** Park on a pull request merge, on another run, or on a time, beside the CI park. One cap for all (Q13). One wake path. Blocker wakes stay with SW-15 |
| **A notice to a coordinator** | The first nudge goes to the run's member, and the recovery record goes to the app lead (SW-10a, SW-47). SW-62 writes one digest. All of them reach people. Nothing reaches an agent that coordinates other agents | **SW-65.** A coordinator run watches runs. Idle, ended, question and merged events arrive as notices |
| **Lanes as records** | SW-8 is a roster by member, agent and session. SW-29b is an agent profile. SW-45 gives a member own apps. SW-50 shows the box. MCP-G6 (task 360) caps open runs for each connection. SW-31b plans the Settings, Agents screen. A lane today is a text file that a person assembles by hand | **SW-67.** A lane is a record: purpose, app project, model, rules, box and concurrency. It is display and scheduling data and grants nothing. Its cap binds only a session that declares the lane, so it is advisory (R7) |
| **A queue that knows capacity** | SW-30a hands a task to an agent. SW-45 lists free tasks in own apps. SW-49 caps what waits on a person. Task 1731 starts a session for an answered or queued run | **SW-68.** One queue route ranks the candidates for a box and a lane. It names the reason for each task that cannot start: lane full, review cap, box memory, account limit, CI lane busy, release cut-off |
| **Path and area claims below the task** | SW-26 reads the changed files of open pull requests. SW-27 reserves numbers. SW-52 adds `owned_paths` for seams. SW-45 warns on a claim outside an app | **SW-69.** An agent declares the paths it will touch before it edits. Overlap warns at once and writes an affects note (SW-25). SW-26 confirms later from the pull request |
| **Agent-to-agent requests through the task** | SW-25 is a one-way affects note. SW-30a hands a task over. SW-16 lets an agent make tasks inside a fence. AP-8 and AP-16 are asks between people, and from an agent to one person | **SW-65.** A typed request from the task of one agent to the task of another, with a reply. The exchange is a comment on both tasks. No free chat exists |
| **Each sub-agent as a child run** | Task 1065 (SW-39) plans child run rows under the parent task. It has no spec text yet, and SW-0c (task 1069) copies the research | No new slice. P10 adds one constraint to SW-39: a child run is excluded from the index `pm_agent_runs_one_open_per_task` (migration 289), or the child cannot start. See §3.11.9 |
| **A delivery pipeline on each task** | SW-23 (a Check), SW-54 (required checks), SW-57 (evidence of a pull request), SW-59 (run role), SW-18 and SW-55 (review), GH-5 and GH-6 (checks and merge). Each is one signal. No one view joins them | **SW-71.** Nine stages with evidence, joined from those signals. The risk tier is a field. **SW-70** adds the brief, the input contract |
| **A live check after the release** | GH-6 moves an agent-worked task to Done on merge. It is dark. CLAUDE.md rule 8 says to verify delivery by evidence | **SW-72.** The release step reports the deployed commit and the merged pull requests. The `live` stage passes when the deployed commit holds the merge. Q14 sets whether Done waits for it |
| **A production act record** | CLAUDE.md §3a rule 2 asks for a report of each production act in the message. The gateway writes one audit row for each door call (#150) | **SW-73.** A tool writes the act, the box, the evidence and the rollback to the task |
| **Question policies** | SW-33 (structured questions), SW-43 (withdraw and change), `confirm: true` on a costly option, AP-5 (every agent question is an interrupt, so quiet hours add nothing), AP-15 (the circle opens after 24 hours) | **SW-74** merges duplicates and answers similar questions in one act. **SW-75** takes the recommended option after a deadline, for reversible questions only, on LOW-tier tasks that a person tiered |
| **Recover the fleet in one step** | SW-62 releases the runs of ended sessions in one act. Task 1731 restarts a session for an answer. EA-19 removes the shared sign-out. SW-60 keeps a CI park alive across a restart | **SW-79.** After a box restart or a sign-out, one command and one button (SW-79, the button SW-81) restart the sessions of the box, lane by lane |
| **Pull a request into review when it opens** (from section B) | GH-4 links a pull request to its task. GH-6 acts on merge only | **SW-76.** An opened pull request moves its task to the review lane on the server, under its own policy key that is off by default. It works for every agent type. It amends the non-goal "A status move other than to Done" of `github_integration.md` and the routine of EA3-39 |

##### The notice channel (SW-64, SW-65)

A notice is a row of a new tenant table, `pm_agent_run_notices`. It holds the run and the kind (`steer`, `pause`, `watch` or `request`). It holds a text of at most 1,000 characters and the sender (a member or a run). It holds the times of delivery and of reading.

- **Who may steer.** The run's member and the app lead of the task (SW-48). A manager who is neither may not. Ten steers for each run in one hour at most. A confused deputy is the reason: the agent acts with its own member's grants, so only that member and the lead may direct it.
- **Delivery.** The edge adds `notices` to the result of each tool that already reads the run: `post_agent_activity`, `claim_task`, `my_work`, `get_task_context` and `ask_on_task`. At most 3 notices, oldest first. This costs no extra gateway call. The agent posts a thought each minute (rule of the protocol), so a steer arrives within about one minute.
- **A fast path on a box.** The runner (SW-78) writes the unread notices of a session to a local file. A hook of the plugin (SW-82) adds that file to the context after each tool call. The hook reads a file and no secret.
- **Reading.** The agent reads a notice when it posts a thought that names the notice id. An unread steer shows on the card after 10 minutes as "Steer not read". The sender gets one bell.
- **Pause** is a steer of kind `pause`. The protocol tells the agent to post `waiting` on `member` and stop. Resume (SW-11a) ends it. No new run state exists.
- **Watch.** A coordinator run calls `watch_runs` with task ids, a lane, or `children`. It gets a `watch` notice when a watched run turns idle, ends, asks, is answered or merges. The notice holds fixed words and the task title. It never holds the text of an activity, so it cannot relay an injection. A run watches only runs of tasks that it may see. At most 50 watches for each run, and a watch ends with its watcher.
- **Request.** `ask_peer(task_id, to_task_id, kind, text)` sends a `request` notice to the open run of the other task. With no open run, it answers 409 and writes nothing, because an agent may not ask a person (AP-Q4). It needs comment authority on the target task, and not only the right to see it. `kind` is `need` or `offer`. `reply_peer(notice_id, text)` answers. At most 5 requests for each run in one hour, and a chain stops at depth 3. Each request and reply is also a comment on both tasks.
- **Fence.** `test_agent_notices_r8.py` (new) for the order, the caps, the visibility and the untrusted text. `test_mcp_edge_coverage.py` for the new tools.

##### Park on any condition (SW-66)

SW-60 parks a run on its CI. SW-66 gives the park three more kinds, in one new tenant table, `pm_agent_run_wakes`.

| Kind | Fires when | Written by |
|---|---|---|
| `ci` | The checks of the pull request end (SW-60) | The GitHub path (1045b, GH-5) |
| `pr_merged` | The pull request merges or closes | The GitHub path (GH-6) |
| `run_done` | Another run ends | `_move_run` on a terminal status, and `_take_over`, which ends a run as `abandoned` |
| `at` | A time passes | The sweep tick of SW-10a. No new loop |

- **One cap.** Every kind obeys `AGENT_CI_PARK_MAX_MINUTES`, 240 by default (Q13). An `at` beyond the cap gets 422. The flag is `AGENT_PARK_ORGS`, apart from the flag of SW-60. This extends the one exception of D140.8 and D183 (D186.12).
- **A live path.** A parked run with a pending condition has a live path (§3.3). `lease_held_sql` and its Python twins `lease_expired` and `holder_free` read it through one function, `run_wake_conditions`. The `ci` park of SW-60 keeps its column `waiting_ref_id`, and the function reads it as a built-in condition. So one reader serves both stores.
- **A wake** is idempotent on `(run, kind, ref, due)`. It reads `needs_agent` with the reason `wake`, and it holds the kind and the result. It renews the lease once through `last_signal_at`. It clears the session key, so the next session can take the run (§3.4 and SW-60).
- **A park is a disposition.** SW-66 amends SW-61, SW-56 and SW-62: any `waiting` post with a pending wake condition counts as a disposition for the Stop hook, is named in the protocol line, and is not eligible for the bulk release.
- **A blocker wake** stays with SW-15. A run that waits on a task adds a `blocks` link (SW-16) and parks.

#### 3.11.2 Sessions (section B of the brief)

| Item | Already planned | New |
|---|---|---|
| **One Metorite plugin for Claude Code** | SW-19a (the session-start hook), SW-19c (repo hooks), `work_on_task` (the one MCP prompt), and the contributor tasks ONB-1 to ONB-9 (task 1872). They all serve this one repository | **SW-82.** A plugin that any organization installs: the MCP config, protocol hooks, commands, and generic verifier and reviewer agents. HathiLabs repo rules stay in the repo (D186.2) |
| **A status line** | Nothing | **SW-83.** Task, stage, lease left, CI and waiting answers, from a local cache. It makes no network call and holds no secret |
| **Commands: /claim, /park, /ask, /handoff, /done, /fleet** | `/next-ticket` (SW-19) and `/handoff` serve this repository | **SW-82 and PB-2.** One registry of server prompts. The plugin commands are built from it. A fence keeps the two lists equal |
| **Hooks** | SW-19c reminds an agent when `gh pr create` has no task URL. SW-61 blocks a stop with no disposition. SW-57 checks the pull request body in GitHub | **SW-82** adds a context hook for the fast notice path, and a warning on a push with no task id in the branch. **SW-76** moves the link and the status to the server, where every agent type reaches it |
| **A question in two places** | `ask_on_task`. `record_outside_decision` closes the task question when a person answers in chat. SW-61 forbids a question that goes only to the terminal | **SW-82.** The `/ask` command asks on the task and waits. An answer in the terminal goes through `record_outside_decision`. The first answer wins |
| **A push into an idle live session** | The edge is stateless and answers JSON only (`external_agent_access.md` §9.6). The pull model is D140.4. Task 1731 starts a session for an answer | **SW-84.** A spike. It checks what Claude Code supports and writes the finding. No build |
| **A session pinned to one app project** | A connection has scopes and pins (§3.1). SW-45 warns on a claim outside own apps | **SW-67.** A lane names an app. A claim from that lane outside the app warns. A hard pin is a connection with a pinned project, and it already exists |
| **Attach from the card** | CIV-14a (task 1726): `claim_task` and `ask_on_task` take an `external_link` | **SW-86.** The card shows Open session. A runner-started session sets its own link, because the runner knows the session id. The card also shows a resume command |
| **A worktree for each claim** | `scripts/worktree.sh`, CIV-14j (the reaper) and CIV-16 (the clean-up rule) serve this repository | **SW-85.** The runner makes a worktree when a claim starts and removes it at Done or release. The project repo setting is the one of DX-4. A VS Code panel is deferred (§5.11) |

#### 3.11.3 The platform (section C of the brief)

| Item | Already planned | New |
|---|---|---|
| **Mission control** | Needs attention (SW-11a) shows what is stuck. `/waiting` (AP-2) shows what needs a person. The Fleet tab (SW-51) shows what runs. SW-62 digests stuck runs. The nine-pane allowlist forbids a new pane (§2.2) | **SW-88.** No new page. The Fleet tab gains a release train (what is merged, what is live, what waits for 21:30 UTC) and a stuck list |
| **Card controls** | Stop and Release (built). Resume (SW-11a). Delegate (SW-30a). SW-48 lets the lead Release and Resume | **SW-86.** Steer, Pause, Take over and Hand to another agent. Each is a composition of existing routes with one confirm. No new authority |
| **An evidence panel** | CIV-14b (task 1727) shows images. SW-54 shows "5 of 7 required". SW-59 shows a verifier chip | **SW-87.** One panel that draws the stages of SW-71 with their evidence |
| **"Run with an agent"** | `external_agent_dispatch.md` DX-2 (the Run with… menu) and SW-30a (Delegate to…, also on the bulk bar) | **SW-89.** Select tasks, pick a lane, set an order or an overnight window. The runs chain through `run_done` wakes (SW-66) |
| **A planning view** | WS-27t built a timeline with `blocks` links, cycle refusal and start and due dates. SW-16 lets agents set blocks | **SW-90.** A ready set and a critical path, as an overlay on that timeline and as one read tool |
| **Digests** | SW-36 drafts a weekly project update. WS-BRIEF (`core_brief.md`) holds the brief and its 18:30 close-out | **SW-91.** Two brief sections: a morning fleet digest, and a release-eve digest before 21:30 UTC. Plain counts, with no AI credit |
| **Phone-first approvals** | AP-2 and §5.7 of the Waiting spec give the phone layout. AP-6 answers in chat. SW-46 routes a preview to the lead | **SW-92.** "Approve the LOW-tier items" in one act. Each item still gets its own answer that names the member |
| **Delivery analytics** | RA-2 and RA-4 measure outcome, run time and silent runs. SW-20 holds cost, deferred behind H-42 | **SW-93.** Time in each stage, idle-hold hours, answer latency, merge rate, rework and cost per merged slice. Cost reads "unknown" until SW-20 |

#### 3.11.4 Machines (section D of the brief)

| Item | Already planned | New |
|---|---|---|
| **A Metorite runner on each box** | Task 1731 plans a timer that starts a session for an answered or queued run. The owner's decision of 2026-11-06 says it never wakes an old session. `external_agent_dispatch.md` DX-11 plans a local daemon. SW-17 plans a push and is deferred. SW-60 item 4 reads parked runs on the box. EA-19 (task 1819) gives the box token | **SW-78.** One runner. Task 1731 is its first release, and SW-78 grows it. It starts sessions for queued, answered and woken runs, and it enforces the caps of SW-68 |
| **A Machines page** | The Fleet tab shows runs (SW-51). Integrations, AI agents lists connections (EA-4b) | **SW-77** stores the heartbeat of a box. **SW-81** draws it: memory, load, sessions, sign-in, account state and CI queue |
| **Resource leases** | CIV-14j reaps orphan dev stacks. CIV-16 adds a clean-up rule, a Stop hook and a reaper | **SW-80.** A scratch database, a port or a preview server is lent to a run and freed when the run ends. The runner keeps the register |

##### The runner (SW-77, SW-78, SW-79)

The runner is a small program that the member installs on the member's own box. It fits kind C of `external_agent_dispatch.md` §4 ("Local"): the member's login never leaves the member's machine. It is not a server feature, and D-DX-4 stays true.

- **Credential.** The box token of EA-19. The first release keeps the owner's decision on task 1731: the scope `projects:read` only. It starts a local session for an answered or queued run, and it never wakes an old session. The heartbeat, the server-side start of a run and the wake of a live session need a second token with the scope `box:runner` (decided, Q18). Minting it on a live box is an owner act. SW-77 defines `box:runner` and adds a pointer in `external_agent_access.md` §10.9. Never the OAuth token of a session. The session that the runner starts logs in with the member's own Claude login on the box.
- **Loop.** Every 2 minutes it reads `needs_agent` runs of its lanes and the queue of SW-68. For each run it starts a session in the lane, with the run id in the prompt. Or it wakes the live session that holds the link (CIV-14a). It never starts two sessions for one run.
- **Heartbeat.** Every minute it posts memory, load, sessions, the sign-in state, the account state and whether the CI lane is busy (SW-77). The server treats the numbers as untrusted display data.
- **Caps.** It starts no session when the lane is full, the box lacks memory, or the account is rate-limited. Memory and account state come from the box, so those caps are advisory (R7). The lane count binds only a session that declares its lane, so it is advisory too. The review cap comes from the server and binds.
- **Recover.** `metorite-runner recover`, and the button on the Machines tab (SW-79, the button SW-81), list the open runs of the box that have no live process. They restart each in its lane, with the reason `box_restarted`.
- **Language and place.** Node, as the hooks of this repository are. The first home is `scripts/home_server/`. A package for other organizations follows in the plugin (SW-82). Task 1731 holds the first release.
- **Gate.** Building it is AGENT-SAFE. Installing it on a box is a `deploy` act (§3a of CLAUDE.md) under a dated grant. A box of a customer is the customer's act.

#### 3.11.5 Governance and cost (section E of the brief)

| Item | Already planned | New |
|---|---|---|
| **Policies as data** | Settings that exist or that P9 plans, each in its own store: "Agent work needs review" (SW-18), `review_cap` (SW-49, store open), required checks (SW-54), and flags for each organization (`AGENT_CLAIM_SCOPE_MODES`, `GITHUB_APP_ORGS`). The dated grants of `OWNER_GRANTS.md` are the HathiLabs window | **SW-94.** One policy store, resolved by app project and risk tier. The later settings use it. Moving the grant window into it is an owner option and is not planned (Q17) |
| **Budgets** | Task 1064 (SW-38) caps run time and credits for each agent and sends a stop signal. SW-20 holds cost behind H-42. MCP-G6 caps open runs for each connection. The write limit is 120 in 600 seconds | **SW-95.** Budgets for each lane and each day, and loop guards (the same call repeated, too many writes). They use measured run time first. They widen SW-38 and do not copy it |
| **A kill switch** | A connection can be revoked (EA-4b). A box token is revocable (EA-19). DX-9 revokes a removed member's connections. PKG-9 disposes of a package install, which is a different thing | **SW-96.** Pause every agent of a box, a person or an organization in one act, and resume later |
| **An audit export** | One audit row for each door call (#150). The Overview of Reports measures runs | **SW-97.** An export of agent writes for a date range, with the member, the client stamp, the run and the task |

##### One policy store (SW-94)

A policy is a row. It holds the scope (organization, project, lane, member or box), a risk tier or `any`, a key from a fixed list, a value, the author and a version. Each save adds a row, as guidance does (SW-31). Two functions resolve it: `policy_for(task, key)` for a task key, and `policy_for_actor` for an actor key (SW-94). `policy_for` reads the nearest project first, then each parent, then the organization, then the shipped default. A tier row wins over an `any` row at the same level.

The first keys: `stages_required`, `brief_required`, `claim_scope_mode`, `review_cap`, `park_max_minutes`, `overnight_allowed`, `done_on`, `question_auto_after_minutes`, `lane_mode`, `pr_open_to_review`, `release_cutoff`, `review_required` (SW-18), `required_checks` (SW-54), `budget_mode`, `budget_minutes_day`, `budget_writes_day`, `loop_guard_repeats` and `agents_paused`. A key outside the list gets 422. The writer rules are in SW-94: an admin writes any key, and a member writes `agents_paused` for self. A row has a scope kind: organization, project, lane, member or box. A task key resolves project, then each parent, then organization, then the default. An actor key (`agents_paused`, the lane and day budgets) resolves box, then member, then lane, then organization, then the default. The edge reads the actor form at token lookup, where no task exists. Each key accepts some kinds only. A key on the auth path needs `admin:settings:manage`, except as SW-94 says. A policy does not grant. It only sets how strict a warning or a refusal is (rule 5). The environment flags stay as the default of the organization. Where a flag has ordered modes, the flag is the ceiling. A policy may choose a mode at or below the flag and never above it. The stage list, the tier names and the release cut-off are policy defaults. The product holds no HathiLabs constant (D186.2).

#### 3.11.6 Learning (section F of the brief)

| Item | Already planned | New |
|---|---|---|
| **Shared lessons for each project** | SW-31 (guidance with history; SW-31a is built, SW-31b is not). Task 1068 (SW-42): an agent proposes a guidance edit and a lead approves. The guidance table holds rule rows with `state`, `proposed_by`, a source and a quote, and lead acceptance (ALN-5, ALN-5b, ALN-11) | **SW-98.** A lesson is a rule row. SW-98 adds `verify_by` and provenance, so a lesson expires unless someone verifies it again. An import path takes local memory files as proposals |
| **A cause tag on each stalled run** | §3.10.11 names seven causes in prose. `pm_agent_run_strands` holds a `reason` (`agent_quiet`, `answer_unread`, `question_unanswered`) that says which sweep rule fired | **SW-99.** A fixed `cause` vocabulary that says why the run stalled, on the strand and on the release activity of a run. Reports group by cause |
| **Versioned lane and prompt templates** | Nothing | **PB-9** (`orchestration_playbook.md`). The templates are playbook content. A lane (SW-67) names the template version it came from |

##### The lessons (SW-98)

The article's strongest warning is that one false fact at the hub spreads to every agent. So a lesson is not a free note.

- **A lesson is a rule row** of the guidance table (kind `rule`), with the states and the lead's acceptance of `project_alignment.md` §4.2 (ALN-5, ALN-5b). ALN-11 owns `needs_review`. SW-98 adds `verify_by` and a provenance record (the task, run or pull request it came from, the member who saw it, the date seen). The change is one migration, expand only.
- **Age shows.** An agent reads the age beside the text: "seen 2026-10-12, from task 1585". A lesson past `verify_by` leaves the active set until a lead verifies it again.
- **Approval first.** An agent or a person proposes. A lead of the project approves. Only active lessons reach `get_task_context`. This is the rule of task 1068 (SW-42), made stricter by provenance and age.
- **Import.** `propose_lessons` takes a batch of notes, for example the local memory files of a coordinator session. Each becomes a proposal with the source "imported". A lead triages the queue: approve, merge or reject. No imported note is active before that.
- **The verifier re-derives.** The playbook tells a verifier to check a lesson against the code and not to trust it (`orchestration_playbook.md` §3).
- **Fence.** `test_agent_lessons_r8.py` (new): a proposed lesson never reaches `get_task_context`, an expired one leaves, a lesson of another project never shows.
- **Owner.** WS-ALN owns the table. P10 and WS-ALN share this slice, and `project_alignment.md` gets a pointer when SW-98 dispatches.

#### 3.11.7 The delivery pipeline and the live check (SW-70 to SW-73)

**The brief (SW-70).** A task may carry `brief`, a JSON object with fixed keys. The keys are `objective`, `deliverable`, `done_when`, `boundaries`, `tools` and `context`. Each text is at most 500 characters. This is the input contract of a sub-agent. It holds the goal, the output, the tools and the limits. `work_plan.md` §1 is the long form of this contract for a HathiLabs task. It stays the HathiLabs layer. `get_task_context` returns the brief after the Check (SW-23). `check_text` stays the command that proves the task. `done_when` is the sentence a person reads.

**The stages (SW-71).** The stages are `audit`, `build`, `verify`, `review`, `preview`, `ci`, `merge`, `release` and `live`. Each has the state `pending`, `passed`, `failed`, `waived` or `na`. The task field `risk_tier` is `LOW`, `STANDARD` or `HIGH`. Only a person sets it, through the app. No MCP tool writes it. A task with no tier is not LOW.

| Stage | Read from | Reported by |
|---|---|---|
| `audit` | Nothing | `report_stage` |
| `build` | A run that finished with a pull request reference | The system |
| `verify` | A verifier run on the head commit (SW-59) | The system, or `report_stage` |
| `review` | The reviewer's decision (SW-18) | The system |
| `preview` | An answered question with `purpose: preview` (SW-46) | The system |
| `ci` | The required checks (SW-54, GH-5) | The system |
| `merge` | The merged reference (GH-6) | The system |
| `release` and `live` | The release record (SW-72) | The signed record, or a person in the app. A `report_stage` is a claim |

The system stages are computed when the task is read. Only an explicit report is stored, in `pm_task_stages`. The policy `stages_required` lists the stages for a tier. The card shows "4 of 6 stages". A `verify`, `release` or `live` that an MCP caller reports is shown as a claim and never counts as passed. In v1 the pipeline informs and warns, and it refuses nothing. A refusal to move a task to Done with a required stage open is a later flip.

**The live check (SW-72).** The release step of the deploy workflow posts a signed record. The record holds the deployed commit, the merged pull request numbers since the last release, and the version line that the live service reports. The organization holds the signing secret. The store is `pm_release_events`. When the merged pull request of a task is in a record, its `release` and `live` stages pass. With the policy `done_on = live`, the live check moves the task to Done and closes its run. It writes the evidence as a comment. A task merged for more than 36 hours and not live shows in Needs attention as "Merged, not live". That also catches a release that failed.

**Why this does not change GH-6.** With `done_on = merge`, the default, GH-6 acts as it does today. The live check then only fills the stages. HathiLabs sets `done_on = live` (Q14). The merge-to-Done rule moves to the live check for that organization.

**Gate.** Posting from the deploy workflow needs an edit of `.github/workflows/deploy.yml` and a repository secret. The edit is a HIGH-tier change because it is the deploy path. Adding the secret is an owner act. The gate id `deploy-write` covers `deploy/` only, and not this file.

#### 3.11.8 The playbook, and why it has its own spec

Section G of the brief is product content: patterns, a brief contract, gates, failure causes, a cost model and templates, shipped to every organization. It is not a mechanism of coordination.

- **It has its own audience.** An organization admin reads it, and an agent of another company.
- **It has its own versioning.** Content changes with no migration, and an organization forks a template.
- **It has its own fence.** A test refuses any HathiLabs rule in the default layer.
- **This file is long already.** It is more than 1,400 lines.

So `orchestration_playbook.md` is a new ACTIVE spec (INDEX row in the same pull request). It holds D186 items 1 and 2 in detail. It holds the limit of 4,096 characters, the six places the know-how lives, the layering rule and the lessons from the article. It holds the slices PB-1 to PB-10. This spec owns the mechanisms the playbook uses: resources (SW-34a), guidance (SW-31), the protocol block (SW-56), notices (SW-64) and the policy store (SW-94).

#### 3.11.9 Notes on slices of other lanes

- **SW-39 (task 1065), child runs.** The index `pm_agent_runs_one_open_per_task` allows one open run for each task. A child run of a sub-agent needs a `parent_run_id` and a `kind`. The index must exclude `kind = 'child'`, in the same migration. Dispatch SW-39 with that constraint. A child holds no lease and no claim, and it shows under its parent only.
- **SW-38 (task 1064) and SW-42 (task 1068)** have no spec text. SW-95 uses the stop signal of SW-38, and SW-98 builds on SW-42. Both wait until SW-0c (task 1069) or their own slice writes the text.
- **SW-0c (task 1069)** copies the Hyperagent research into the section that task 1069 names. P10 does not do that work.
- **SW-60 (task 1902).** SW-66 reads its column `waiting_ref_id` as a built-in condition. SW-60 needs no change. SW-66 has its own flag.
- **DX-11** of `external_agent_dispatch.md` is the local runner. One line there now points to SW-78.

#### 3.11.10 What P10 could not settle

- **A push into an idle live session** (SW-84). The docs of 2026-11-06 show two facts. Channels push a message into a running session, but only from a local stdio server, in research preview, with an opt-in flag and an allowlist. A remote HTTP server cannot be a channel. A background session wakes with `claude --resume <session> "prompt"` from version 2.1.285. So SW-84 decides between a local channel server in the plugin and the resume command of the runner. The fast path of SW-64 uses neither.
- **What a plugin can carry.** A plugin bundles commands, agents, skills, hooks, MCP servers and more. It cannot set the main `statusLine`. So SW-83 ships a setup command that writes the user's settings. SW-82 checks the bearer header form of `.mcp.json` against the mcp page at dispatch.
- **How the release step knows the pull request numbers.** This repository squashes with `(#N)` in the subject. Another organization may not. The endpoint takes a list, and the script that builds the list is the organization's.
- **The account state of a box.** The runner reads it from the box's own client. The way may change by Claude Code version. SW-77 marks the field as best effort.
- **The edge budget.** The edge has one database connection (`external_agent_access.md` pooler budget). SW-64 adds no extra gateway call, but measure it at dispatch.

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

### 4.2 D186 — Metorite runs the fleet and teaches it (2026-11-06)

`work_plan.md` §3 holds it as **D186**. It extends D140 and D183. It amends earlier rules in five points (items 10 to 14).

**Owner calls (task 1906, comment of 2026-11-06 18:05 UTC).**

1. **Metorite ships orchestration know-how to every organization that connects its MCP.** The know-how reaches an organization in seven ways: MCP prompts, MCP resources, short tips in tool results, guidance layers, setup tools, an orchestration doctor and chat. The server instructions keep the safety rules only, because Claude Code cuts them at 4,096 characters (task 1803).
2. **HathiLabs rules are the HathiLabs layer, never the product default.** STE, the R rules, D numbers, gate labels and the guard hooks bind HathiLabs work. The default layer has none of them. `orchestration_playbook.md` §4 names the fence.
3. **The price is the flat seat.** The playbook and every P10 capability are part of ₹500 per user per month (`launch_surface.md` §4). No SKU and no add-on. AI credits still meter AI work, as they do today.

**Agent defaults (task 1906).** The coordinator accepted the recommended options of the brief. They stand unless the owner objects. Q14 to Q18 are decided (§8.1).

4. **One channel to a running agent: the notice** (SW-64). A steer, a watch event and a request from a peer all use it.
5. **Declared data only warns or narrows.** A lane, a role, a label and a path claim never widen authority (extends D140.2).
6. **The runner is local.** It runs on the member's own box with the box token. It adds no server feature that holds a member's login (D-DX-4 stays).
7. **A fact that spreads needs a source, an age and an approval.** A lesson and a guidance block carry provenance and a `verify_by` date. A lead approves before another agent reads it.
8. **Policies are data.** One store, resolved by app project and risk tier (SW-94). The environment flags stay as master switches.
9. **Done may wait for the live service.** A setting, `done_on`, picks `merge` (the default, GH-6 unchanged) or `live` (SW-72). Q14 sets HathiLabs.

**What D186 amends.** It amends these earlier rules, each in one point. The slice that builds each change adds the pointer to the amended text:

10. **D140.2 and D-EA-14, by SW-75 only.** A policy may answer a reversible question on a LOW task that a person tiered. Q16 is decided: build it dark, off by default.
11. **The non-goal "A status move other than to Done" of `github_integration.md` §2 and EA3-39 item 5, by SW-76.** A pull request that opens may move its task to review, under a policy that is off by default.
12. **D140.8 and D183, by SW-66.** A run may park past the lease on a merge, on another run or on a time, as it may on CI. The same cap applies (Q13).
13. **EA3-39 item 1 ("one prompt") and item 8 (prompts list only with `projects:write`), by PB-2.** The edge serves more prompts. Playbook prompts list with `projects:read`. Prompts that write list with `projects:write`.
14. **The owner decision on task 1731, by SW-78.** A later release uses a second token with a narrow scope (Q18, decided). Minting it on a live box is an owner act.

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

### 5.0b The order for P10

P10 starts after the first group of §5.0a is live. Each slice also needs its own P9 and earlier slices. SW-64 needs SW-48 and SW-56. SW-67 needs SW-45. SW-70 needs SW-23. SW-71 needs SW-23 and SW-59. SW-81 and SW-88 need SW-51. Slices in one step may run in parallel.

1. **Fix what leaks today.** Task 1731 (SW-78 release 1) and EA-19 (task 1819), then SW-77, which defines the runner scope. SW-99 follows SW-62.
2. **Reach a running agent.** SW-64, then SW-66, then SW-65.
3. **One store for settings, then the stages.** SW-31b first, then SW-94, before SW-49 and SW-54 where the order allows. Then SW-70, SW-71, SW-76, SW-73, then SW-72. SW-70 and SW-71 use a fixed default until SW-94 has merged.
4. **Tabs.** The tabs of SW-67 and SW-98 follow SW-31b and SW-94.
5. **Lanes and machines.** SW-67, SW-68, the later releases of SW-78 (it then reads lanes and caps), SW-79, SW-80, SW-85, SW-81.
6. **Sessions.** SW-82, SW-83, and SW-84 at any time.
7. **The platform.** SW-86, SW-87, SW-88, SW-91, SW-93.
8. **Limits.** SW-95, SW-96, SW-97.
9. **Learning.** SW-98.
10. **Later, the owner decides.** SW-69, SW-74, SW-75, SW-89, SW-90, SW-92 and the deferred table of §5.11.

The playbook (`orchestration_playbook.md` §7) runs beside this list. PB-1 first. PB-3 needs SW-34a. PB-4 needs SW-56. PB-5 needs SW-31.

**Why this order.** The incidents of 2026-11-06 were a lost sign-in, a CI wait that outlived its session, an answer that woke nobody and a merge that closed nothing. Steps 1 and 2 remove those. The stages (step 3) make the next failure visible. The limits come last. They stop work, and a stop needs evidence first.

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
  - **The answer.** The edge returns `guidance` as `{blocks, dropped}`. `dropped` counts the farthest blocks that the cap cut. `get_task_context` makes 14 gateway calls at most (MCP-G18 added the attachments read). The blocks name their level as `project`, `space` or `company`. The chat tool for guidance is a follow-up (SW-31c).

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
- **As built (2026-11-07, first part).** `edge/resources.py` serves `resources/list`, `resources/templates/list` and `resources/read` for `metorite://project/{id}` and `metorite://task/{id}`. Each read runs the tool `get_project` or `get_task`, so the authority is the tool's. `resources/list` holds no concrete row. A hidden or unknown resource answers `-32002`. It is dark behind `agent_resources_orgs`. `metorite://company/context` is not built: it needs the tool `get_company_context` (EA-5, D-EA-6). Fence: `tests/unit/test_mcp_edge_resources.py`.

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
- **DECIDED, the answer rule (owner, option A, task 1731, 2026-11-07).** A session that the watcher started may act on an answer when three things are true. The answer is the newest person prompt on the run, and its `member` is the member of the run. The run is the one that the first prompt of the session names. No later activity withdrew the answer. A restart, erase or stop answer still needs a session that asked it itself. CIV-14g builds this exception.
- **Status.** BUILT 2026-11-06, dark, not merged (Metorite task 1731). `answer_watcher.sh install` copies the script and the units and enables nothing. `enable` refuses a token file that is not mode 0600. Enabling the timer is OWNER-GATE (`deploy`). Two Opus readers reviewed the first draft. They found the oneshot kill, the lost `stale` answers, a second answer that never started, a lock cap that did not cap, and the answer rule above. The text and the code now fix all but the last.
- **Fence.** `uv run pytest tests/unit/test_answer_watcher.py`, with a fake `list_agent_runs`, a fake starter and a fake live count. It covers one start for one wake, a new answer, a `stale` run, the skips, the live cap, the vet, the retries, a failed read, the transient unit, the token never in a child, and the sweep.
- **Risk.** HIGH (it starts sessions with credentials). **Gate.** AGENT-SAFE to build. OWNER-GATE to enable the timer (`deploy`) and to mint the token.

#### CIV-14g · The answer rule names the watcher hand-off (Metorite task 2197)

- **Scope.** The edge text of the answer rule names one exception, `prompts.WATCHER_EXCEPTION`. It shows in `ANSWER_BODY_ON` and `ANSWER_BODY_OFF`, so `my_work`, `list_agent_runs` and `work_on_task` carry it. Each short pointer of the instructions names "a watcher hand-off". The watcher prompt starts with the fixed text `ANSWER WATCHER HAND-OFF for run`, then the run id. It holds ids only.
- **The exception is narrow.** It holds only for the run that the first prompt names. It holds only for the newest person prompt, when its member is the member of the run and no later activity withdrew it. It never allows a restart, an erase or a stop.
- **The cut.** Claude Code cuts the instructions at 4096 characters. With sessions off, the cut left 3 characters, so the off pointer is shorter now. The last kept sentence ends at 4093 (off) and 4091 (on).
- **Status.** BUILT 2026-11-07, not merged (Metorite task 2197). It changes text only.
- **Fence.** `tests/unit/test_mcp_edge_prompts.py` `TestTheAnswerRule`: the watcher tests and the cut test. `tests/unit/test_answer_watcher.py`: the prompt starts with the marker, and the marker of the watcher equals the marker of the edge.
- **Risk.** HIGH (the answer rule, MCP-G4). **Gate.** AGENT-SAFE. Enabling the watcher timer stays OWNER-GATE (`deploy`).

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
- **As built (2026-11-07).** `tools.PROTOCOL_LINES` holds five lines. The key `protocol` is optional in the output schema of both tools. It shows only while the runs flag is on, because each line names a run tool, and only for an organization on `agent_protocol_orgs` (`agent_oauth.protocol_allowed`). The park-on-CI line waits for SW-60. Fence: `test_mcp_edge_context.py` `TestTheProtocolBlock` and `test_mcp_edge_claim.py` `TestTheClaimProtocolBlock`.

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
- **Built 2026-11-06.** SW-63 is built and live under `agent_runs_enabled`, because it only adds the optional result fields `warning` and `open_subtasks`. The gateway counts only the subtasks that the caller can see. The fence is `TestAClaimOfAnEpicWarns` in `test_mcp_edge_claim.py`.

### 5.11 P10 — Metorite runs the fleet and teaches it

**Source.** §3.11 and D186. Task 1906 holds the brief. Each slice below has a task in Backlog under epic 321. The slices PB-1 to PB-10 are in `orchestration_playbook.md` §7. Slices of other specs keep their owners and gates. P10 depends on these slices: SW-7a, SW-46, SW-48, SW-56, SW-60, SW-31 (SW-31a is built, SW-31b is not), SW-34a and EA-19 (task 1819). It also depends on task 1731. P10 merges after PR 1777, which holds P9.

**Common rules.** Every slice ships dark behind a flag or a setting that is off, unless it says otherwise. A new table is tenant-scoped under FORCE ROW LEVEL SECURITY (R5). A migration is expand only (R6), and its number is taken at build time (R1). Each new route joins `skill_projects/manifest.py`. Each new tool joins `MCP_WRITE_ROUTES` or `MCP_ROUTES` with a named scope (§2.3 rule 6). A box token authenticates at the edge, so a runner call is an MCP tool and not a gateway route. A migration makes a slice HIGH.

#### SW-64 · A steer reaches a running agent

- **Metorite task.** 1910.
- **Scope.** The table `pm_agent_run_notices`. The route `POST /projects/agent-runs/{run_id}/notices` for `steer` and `pause`. Only the run's member and the app lead of the task (SW-48) may steer. The edge adds `notices` to the results named in §3.11.1. A notice leaves as `untrusted_text` with the name of the sender. The steer rule goes in the `work_on_task` steps and in a tip (PB-4), because the protocol block of SW-56 is capped at 6 lines. It tells the agent to follow a steer from its own member or from the app lead, and to treat a steer from any other sender as information. The agent reads a notice by a thought that names its id. The card shows "Steer not read" after 10 minutes. `my_work` rows carry the unread count.
- **Done when.** R8: the run's member posts a steer, and the next `post_agent_activity` result holds it once. A manager who is neither the member nor the lead gets 403. A run that ended gets 409. The 11th steer in an hour gets 429. A replayed key writes once.
- **Fence.** `test_agent_notices_r8.py` (new), `test_mcp_edge_agent_runs.py`, `test_mcp_edge_prompts.py` for the protocol line, `test_tenant_coverage.py`.
- **Risk.** HIGH (a migration, who may write to a run). **Gate.** AGENT-SAFE, dark behind `AGENT_NOTICES_ORGS`. **Depends on.** SW-7a, SW-48, SW-56.

#### SW-65 · Watch runs, and ask a peer through the task

- **Metorite task.** 1911.
- **Scope.** Tools `watch_runs`, `ask_peer` and `reply_peer`, on the notice table of SW-64. The limits of §3.11.1. `ask_peer` needs comment authority on the target task, through the existing seam, and not only the right to see it. With no open run on the target it answers 409 and writes nothing, because an agent may not ask a person (AP-Q4). Each request and reply is also a comment on both tasks. A watch event holds fixed words and the task title.
- **Done when.** R8: a coordinator that watches two runs gets one `watch` notice when one turns idle, and none for a run it may not see. A request reaches the open run of the other task once. A request to a task with no open run gets 409. A caller with no comment authority on the target gets 403. A chain stops at depth 3. The sixth request in an hour gets 429. No notice holds the text of an activity.
- **Fence.** `test_agent_notices_r8.py`, `test_mcp_edge_coverage.py`, and a test that walks every watch event for leaked text.
- **Risk.** HIGH (visibility, an agent-to-agent channel). **Gate.** AGENT-SAFE, dark. **Depends on.** SW-64, SW-25.

#### SW-66 · Park on a merge, a run or a time

- **Metorite task.** 1912.
- **Scope.** The table `pm_agent_run_wakes`. `waiting` takes `wake_on`, a list of `{kind, ref, at}` with the kinds `pr_merged`, `run_done` and `at`. One function, `run_wake_conditions`, reads these and the `ci` column of SW-60. The Python twins `lease_expired` and `holder_free`, and `lease_held_sql` and the stranded clause in `filters.py`, all read it, so the gate and the SQL filter agree. The Stop hook of SW-61 counts a `waiting` post with `wake_on` as a disposition (item 2 of SW-61 changes). A `run_done` wake has two writers: `_move_run` on a terminal status, and `_take_over`, which ends a run as `abandoned`. The one cap is `AGENT_CI_PARK_MAX_MINUTES` of SW-60 (Q13), for every kind of park. The policy `park_max_minutes` may lower it and never raise it. The flag is `AGENT_PARK_ORGS`, apart from the CI flag of SW-60. This slice extends the one lease exception of D140.8 and D183 (D186.12).
- **Done when.** R8 with a moved clock: a run parked on a merge reads `waiting` past 120 minutes up to the cap, and is stranded after it. A merge writes one wake and one lease renewal. A replayed event writes nothing. A run parked on another run wakes when that run ends, also when a takeover ends it. An `at` beyond the cap gets 422. A wake clears the session key. The Python twin and the SQL agree on a moved clock. The Stop hook of SW-61 passes a session that holds a run with a pending `wake_on`. The protocol line of SW-56 names a park on any condition. `release-ended` (SW-62) skips a run with a pending wake.
- **Fence.** `test_agent_runs_r8.py`, `test_agent_run_sweep_r8.py`, `test_agent_run_exclusive.py`, a webhook test beside the GitHub fences, `test_tenant_coverage.py`, `node .claude/hooks/stop-children.test.mjs`, `test_mcp_edge_context.py`, `test_projects_chat_coverage.py`.
- **Risk.** HIGH (a migration, the lease). **Gate.** AGENT-SAFE, dark behind `AGENT_PARK_ORGS`. **Depends on.** SW-60, 1045b, GH-6, Q13.

#### SW-67 · Lanes as records

- **Metorite task.** 1913.
- **Scope.** The table `pm_agent_lanes`. Its columns are the name, the purpose (required, the bottleneck this lane removes), the app project, the model and a rules note of 2,000 characters. It also holds the box connection, `max_open_runs` (default 1), the owner member, a shared flag. PB-9 adds the template column. The column `pm_agent_runs.lane_id`. `claim_task` and `start_agent_run` take an optional `lane`. A claim outside the lane's app returns a `warning`. A session that declares a lane is capped by it. A session that declares none is not, so the lane cap is advisory (R7). The hard cap stays the cap of each connection (MCP-G6). The flag `AGENT_LANE_MODES` adds `refuse` for a full declared lane (429 `lane_full`). The screen is a tab "Lanes" of the Settings, Agents screen of SW-31b. The Fleet tab groups by lane.
- **Done when.** R8: two lanes of two members. A claim from a lane outside its app warns and writes one timeline line. In `refuse` the second open run of a declared lane with cap 1 gets 429. A claim with no lane is unchanged. A lane of another organization is invisible. A lane never reaches an authorization check.
- **Fence.** `test_agent_lanes_r8.py` (new), the structural test of §3.7 extended to lane fields, `test_tenant_coverage.py`, a vitest for the tab.
- **Risk.** HIGH (a migration, a refusal on a write). **Gate.** AGENT-SAFE dark. D145 owner preview for the tab. **Depends on.** SW-7b, SW-45, SW-31b.

#### SW-68 · A queue that knows capacity

- **Metorite task.** 1914.
- **Scope.** The MCP tool `list_agent_queue(lane)` ranks the tasks a box may start next: answered and queued runs first, then tasks that fit the release cut-off, then priority. The tool is a read with a named scope in `MCP_ROUTES` and the scope list of `external_agent_access.md` §10.9. Each task that cannot start carries a reason from a fixed list. The reasons are `lane_full`, `review_cap`, `box_memory`, `account_limit`, `ci_lane_busy` and `cut_off`. The last four come from the heartbeat (SW-77) or from the organization's `release_cutoff` policy (SW-94, default none), and are advisory. `lane_full` binds only for a declared lane (SW-67). `review_cap` binds.
- **Done when.** R8: a full lane lists its tasks with `lane_full`. A lead at the review cap lists `review_cap` for the tasks of the lead's apps. With no `release_cutoff` no task carries `cut_off`. A stale heartbeat adds no reason and writes a note. The tool lists no task the caller cannot see.
- **Fence.** `test_agent_queue_r8.py` (new), `test_mcp_edge_coverage.py`. The advisory reasons are labelled so (R7).
- **Risk.** HIGH (visibility, a refusal reason on a write). **Gate.** AGENT-SAFE dark. **Depends on.** SW-67, SW-49, SW-77, SW-94.

#### SW-69 · Path claims below the task

- **Metorite task.** 1915.
- **Scope.** The table `pm_agent_path_claims` and the tool `declare_paths(task_id, paths)`. At most 50 paths for each run. An overlap with another open run returns the other task when the caller may see it. It writes an affects note both ways once (SW-25). The note needs comment authority on the other task, through the existing seam. A path under an `owned_paths` entry (SW-52) warns with the seam lead. Claims end with the run. It only warns.
- **Done when.** R8: two runs declare one path, and each gets one warning. A declared path never blocks a write. A claim ends when the run ends. A note never names a task its reader cannot see. A caller with no comment authority on the other task writes no note.
- **Fence.** `test_agent_path_claims_r8.py` (new), `test_conflict_radar_r8.py` (new, from SW-26) for the confirm step. The feature is advisory (R7).
- **Risk.** HIGH (a migration, visibility). **Gate.** AGENT-SAFE. **Depends on.** SW-25, SW-52.

#### SW-70 · A brief on the task

- **Metorite task.** 1916.
- **Scope.** `pm_tasks.brief JSONB NULL` with the six keys of §3.11.7, each text at most 500 characters, a schema check at write. `create_task` and `update_task` take `brief`. `get_task_context` returns it after the Check. The policy `brief_required` (SW-94) warns or refuses a delegation with no brief. The shape is the schema of PB-1.
- **Done when.** R8: a brief with an unknown key gets 422. A delegated task with no brief and the policy on in `warn` carries a warning. The brief leaves as `untrusted_text`.
- **Fence.** `test_task_brief_r8.py` (new), `test_mcp_edge_context.py`, `test_mcp_edge_coverage.py`.
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE. **Depends on.** SW-23.

#### SW-71 · The delivery pipeline on the task

- **Metorite task.** 1917.
- **Scope.** `pm_tasks.risk_tier` with a CHECK of `LOW`, `STANDARD`, `HIGH`, added `NOT VALID` then `VALIDATE`. The three tier names are product vocabulary and outside the neutral rule of D186.2. Only a person sets it, through the app. No MCP tool writes it, and a NULL tier is not LOW. The table `pm_task_stages` for reported stages. One read function joins the system stages (§3.11.7). The tool `report_stage(task_id, stage, result, evidence)`. A report of `verify` by the run that built the task is stored as a claim and never counts as `passed`. The task wire gains `stages`. The policy `stages_required` (SW-94) lists the stages for a tier. Its default list is a policy default and not a code constant. It warns and refuses nothing. Until SW-94 merges, a fixed default list serves.
- **Done when.** R8: a task with a finished run, a merged reference and passed checks reads `build`, `ci` and `merge` as passed with no stored row. `report_stage` stores `audit`. A caller that may not write the task gets 403. An MCP call that sets `risk_tier` gets 422. A `verify` reported by the building run reads `claimed`. A task that moves to Done with a required stage open carries a warning.
- **Fence.** `test_task_stages_r8.py` (new), `test_tenant_coverage.py`, `test_mcp_edge_coverage.py`.
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE dark. **Depends on.** SW-23, SW-59, SW-18, GH-6. It reads SW-54 when it has merged, else the checks of GH-5 with no required list.

#### SW-72 · The live check closes the task

- **Metorite task.** 1918.
- **Scope.** The table `pm_release_events`. The route `POST /releases` is public and signed with HMAC over the raw body. The body names the organization. That id only selects the secret, and the HMAC proves it. The lookup follows GH-E5 (a `SECURITY DEFINER` function), and the route joins `PUBLIC_ROUTES` with `test_default_deny_auth.py`. The body holds the deployed commit, the merged pull request numbers and the live version line. Where the secret is stored is fixed at dispatch, by the pattern of GH-1. The route marks `release` and `live` on each task whose merged reference is in the list. With `done_on = live` it moves the task to Done, closes the run, and writes the evidence. "Merged, not live" shows after 36 hours, only for an organization with `done_on = live`. A person may set `live` with evidence in the app where no release step exists. A `release` or `live` that an MCP caller reports is a claim, like `verify`. It never passes the stage and never fires `done_on = live`. Only the signed record or a person in the app does. This repository's `deploy.yml` gets the last step.
- **Done when.** R8: a signed record for a merged task passes `live` and, in `live` mode, closes it once. A bad signature gets 401 and writes nothing. A replay writes nothing. A record for another organization's pull request touches no task. An MCP `report_stage` of `live` reads `claimed` and closes nothing. A task merged 37 hours ago with no record shows the row in `live` mode and not in `merge` mode.
- **Fence.** `test_release_events_r8.py` (new), `test_default_deny_auth.py`, `test_needs_attention_r8.py`, `test_github_merge_r8.py` (GH-6 unchanged in `merge` mode).
- **Risk.** HIGH (webhook ingress, a status write, the deploy path). **Gate.** AGENT-SAFE dark. The edit of `.github/workflows/deploy.yml` is HIGH tier, because it is the deploy path. Adding the repository secret is an owner act. **Depends on.** SW-71, GH-6. Q14.

#### SW-73 · A production act record

- **Metorite task.** 1919.
- **Scope.** The tool `record_act(task_id, act, box, evidence, rollback)`. It writes a `response` activity with `content.act`. No migration. `evidence` must not be empty. The evidence panel (SW-87) lists the acts. The `work_on_task` steps tell an agent to call it after each production act.
- **Done when.** An act with no evidence gets 422. The act shows on the task. The text leaves as `untrusted_text`. The record is self-reported and says so.
- **Fence.** `test_mcp_edge_agent_runs.py`, `test_mcp_edge_coverage.py`. Advisory (R7).
- **Risk.** STANDARD. **Gate.** AGENT-SAFE.

#### SW-74 · Merged and shared questions

- **Metorite task.** 1920.
- **Scope.** Two parts, one flag. (1) Two open questions on one task with the same purpose and the same option ids show as one card, and an answer closes both. (2) "Answer the N similar" on a card: the same project and the same option ids. It writes one answer for each question and names the member. A question with a `confirm: true` option is never in the set. The surfaces are `/waiting` and `WaitCard`, which belong to AP-2 and AP-4. SW-74 adds its acts through them and files the request in `waiting_on_me.md`. Quiet hours are dropped: AP-5 makes every agent question an interrupt, so a quiet-hours rule would change nothing.
- **Done when.** R8: duplicates merge and one answer closes both. A bulk answer writes N answers and skips the `confirm` question.
- **Fence.** `test_agent_questions_r8.py`, `test_waiting_seam_r8.py`, a vitest for the card.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE dark. D145 owner preview. **Depends on.** Task 1806, AP-2, AP-4, SW-46.

#### SW-75 · A question taken by policy

- **Metorite task.** 1921.
- **Scope.** The policy `question_auto_after_minutes` (SW-94), null by default. After the deadline, a question that the agent marked `reversible` and `recommended` gets the recommended option. The answer writes `content.by_policy` and the card reads "Taken by policy after 4 hours: Wait for the shards". The Change button of SW-43b stays. It never applies to `purpose` `preview` or `review`, to an auth question, to a `confirm: true` option, or to a task whose tier is not LOW. A task with no tier (NULL) is excluded. Quiet hours do not apply (SW-74). The minimum is 240 minutes. This slice amends D140.2 and the answer set of D-EA-14 (D186.10). Q16 is decided: build it dark, off by default.
- **Done when.** R8 with a moved clock: a reversible question on a LOW task is taken at the deadline. Each excluded case is not, and a NULL tier is one of them. The timeline names the policy and not a member. The policy off writes nothing.
- **Fence.** `test_agent_auto_answer_r8.py` (new), one named mutation for each exclusion.
- **Risk.** HIGH (an answer without a person). **Gate.** AGENT-SAFE dark. The master flag is `AGENT_AUTO_ANSWER_ORGS`, default off, and a policy can only act under it. The turn-on is OWNER-GATE `enforcement-flip`, and the slice registers the flag in `work_plan.md` §6. **Depends on.** SW-43b, SW-94, SW-71.

#### SW-76 · An opened pull request moves its task to review

- **Metorite task.** 1922.
- **Scope.** The webhook path of GH-4 moves a linked task to the review lane. This happens when its pull request opens as ready for review and the task has an open run. A draft does not move it. A task already in review or Done does not move. The timeline names the pull request. The setting is its own policy key, `pr_open_to_review`, default off. It does not ride on `GITHUB_APP_ORGS`. This slice amends the non-goal "A status move other than to Done" of `github_integration.md` §2 and the routine of EA3-39 (D186.11). The agent routine still moves the task itself where the policy is off.
- **Done when.** R8: with the policy on, an opened pull request moves a linked task once. A draft, a closed task and an organization with the policy off do not. A replayed delivery writes nothing.
- **Fence.** `test_github_webhook_r8.py` (new cases).
- **Risk.** HIGH (a status write). **Gate.** AGENT-SAFE dark. **Depends on.** SW-53, GH-4, SW-94.

#### SW-77 · The box heartbeat and the machine record

- **Metorite task.** 1923.
- **Scope.** The table `agent_box_health`: connection, time, free memory, load, sessions, sign-in state, account state, CI lane busy, lease counts. The MCP tool `report_box_health`, for a box token only. It joins `MCP_WRITE_ROUTES`, with a named scope in the scope list of `external_agent_access.md` §10.9. SW-77 defines the scope `box:runner` for a box token. It covers `report_box_health`, `list_agent_queue` and the start of a run, and nothing else. It is added to the scope list of `external_agent_access.md` §10.9 with a one-line pointer. A box token authenticates at the edge (`agent_edge/bearer.py`), so there is no gateway REST route for it. The values are untrusted display data. A heartbeat older than 3 minutes reads "offline". Account state is best effort.
- **Done when.** R8: a box token writes a heartbeat. An OAuth connection gets 403. A member reads only the boxes of the member's organization that the member may see. A revoked token writes nothing.
- **Fence.** `test_agent_box_health_r8.py` (new), `test_mcp_edge_coverage.py`, `test_tenant_coverage.py`.
- **Risk.** HIGH (a tenant table, token auth). **Gate.** AGENT-SAFE dark. **Depends on.** EA-19.

#### SW-78 · The runner

- **Metorite task.** 1924.
- **Scope.** §3.11.4. The first release is task 1731 as the owner decided: a box token with the scope `projects:read` only, which never wakes an old session. Later releases add three things. They post the heartbeat (SW-77), they start a run on the server for a queued or woken run, and they wake a live session with `claude --resume <session> "prompt"` where the version allows (2.1.285). Those need a second token with a narrow scope (decided, Q18). SW-77 defines the scope. Release 2 depends on SW-77. Minting a token on a live box stays an owner act. The runner reads lanes and caps (SW-67, SW-68) once they exist. It writes the notice file for the fast path. It never starts two sessions for one run. It keeps the secret in the box's secret store and reads no OAuth token.
- **Done when, release 1 (step 1 of §5.0b).** This is task 1731: a fake `list_agent_runs` shows one start for one run, with the read-only token, and no wake of an old session. **Release 2 (step 5).** It needs SW-67, SW-68, SW-64 and the runner scope of Q18. A fake `list_agent_runs` and a fake `claude` show one start for one run, a wake for a run with a live link, and no start for a full lane. The notice file holds an unread steer within 10 seconds of the poll.
- **Fence.** A test in `scripts/home_server/`. `test_runner_no_secret.py` (new) refuses a read of an OAuth token path.
- **Risk.** HIGH (it starts sessions with credentials). **Gate.** AGENT-SAFE to build. Minting the second token on a live box is an owner act. The install on the home box is `deploy`. **Depends on.** Release 1: task 1731 and EA-19. Release 2: also SW-77, SW-64, SW-67 and SW-68.

#### SW-79 · Recover the fleet in one step

- **Metorite task.** 1925.
- **Scope.** `metorite-runner recover` and an MCP tool with the same effect. The button belongs to SW-81. It lists the open runs of the box that have no live process, and restarts each in its lane with the reason `box_restarted`. The run keeps its task and its handoff. It shows the count first and asks one confirm.
- **Done when.** A test box with 3 dead sessions restarts 3 runs once. A run that is parked or awaiting is not restarted. A second call restarts none.
- **Fence.** The runner test, and a test of the MCP tool that restarts a run with the reason `box_restarted`. The tool joins `MCP_WRITE_ROUTES` with the scope `box:runner`, and `test_mcp_edge_coverage.py` holds it.
- **Risk.** HIGH (it starts sessions). **Gate.** AGENT-SAFE to build. **Depends on.** SW-78, SW-62.

#### SW-80 · Resource leases on the box

- **Metorite task.** 1932.
- **Scope.** A register in the box's state folder. `metorite-lease acquire port|db|preview` gives a free resource named for a run, and the runner frees all leases of a run when it ends. The heartbeat carries the counts. It generalizes CIV-14j and CIV-16.
- **Done when.** Two runs get two ports. A run that ends frees its leases. A lease of a dead run is freed within one poll.
- **Fence.** A runner test. Advisory (R7).
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** SW-78, CIV-16.

#### SW-81 · The Machines tab

- **Metorite task.** 1926.
- **Scope.** A Machines tab beside Fleet in the Agents panel (SW-51). One card for each box: memory, load, sessions, sign-in, account state, CI lane, leases, and the runs it holds. The Recover button (SW-79). A link to Integrations, AI agents for revoke.
- **Done when.** `visual-review` passes at four widths. An offline box reads "offline" with its last time.
- **Fence.** A vitest for the tab and `test_projects_agent_focus_r8.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview. **Depends on.** SW-77, SW-51.

#### SW-82 · The Metorite plugin for Claude Code

- **Metorite task.** 1927.
- **Scope.** A plugin that bundles four things. First, the MCP config with a bearer header from an environment variable. Second, the protocol hooks: the Stop gate of SW-61, a context hook for the notice file, and a warning on a push with no task id in the branch. Third, commands built from the prompt registry. Fourth, generic verifier and reviewer agents. The commands are `/claim`, `/park`, `/ask`, `/handoff`, `/done` and `/fleet`. `/ask` asks on the task and waits. It holds no HathiLabs rule. A fence keeps the command list equal to the registry of PB-2.
- **Done when.** A fresh Claude Code installs the plugin and `/metorite:claim 42` claims a task. The hooks pass their tests. The package holds no token and no HathiLabs token (the denylist of PB-5).
- **Fence.** `node` tests for each hook, `test_plugin_commands_match_prompts.py` (new), `test_playbook_org_neutral.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE to build. OWNER-GATE to publish (Q15). **Depends on.** SW-61, PB-2, and PB-1 for the neutral test.

#### SW-83 · A status line

- **Metorite task.** 1928.
- **Scope.** A script that prints the task, the stage, the lease left, the CI state and the waiting answers from a local cache. Hooks fill the cache from the results of Metorite tools. The runner fills the CI and waiting parts. The script makes no network call and holds no secret. A plugin cannot set the status line, so a setup command writes the user's settings.
- **Done when.** The line reads from the cache in under 50 ms. With no cache it prints nothing. The setup command asks before it writes.
- **Fence.** A node test with cache fixtures.
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** SW-82.

#### SW-84 · Spike: a push into an idle session

- **Metorite task.** 1929.
- **Scope.** No build. Test two ways on a real Claude Code: a local channel server in the plugin, and the resume command of the runner. Write the finding, the version and the limits into §3.11.10. Recommend one.
- **Done when.** The finding is in this file and on the task. An owner decision follows if it needs a build.
- **Fence.** The finding names the Claude Code version it was tested on. The spike is advisory (R7).
- **Risk.** LOW. **Gate.** AGENT-SAFE.

#### SW-85 · A worktree for each claim

- **Metorite task.** 1930.
- **Scope.** When the runner starts a session for a claim, it makes a worktree from the project `repo` and `base_ref` (DX-4). It removes the worktree at Done or release, and it keeps one with uncommitted work. A VS Code panel is deferred.
- **Done when.** A claim makes one worktree. Done removes it. A dirty worktree stays and shows in the heartbeat.
- **Fence.** A runner test.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** SW-78. It reads the `repo` and `base_ref` setting of DX-4. WS-56 is still PROPOSED, so this slice owns a minimal setting until WS-56 decides.

#### SW-86 · Card controls

- **Metorite task.** 1931.
- **Scope.** On the card and the panel: Steer (SW-64), Pause, Take over and Hand to another agent. Take over releases the run, adds the member as assignee, and asks for a handoff note. Hand over releases and delegates (SW-30a). Each act uses an existing route and one confirm. Open session and a resume command show when the run carries a link or a session id.
- **Done when.** Each act works for a person who may do it and shows no button to others. `visual-review` passes.
- **Fence.** A vitest, `test_projects_ui_actions.py`, `test_agent_runs_r8.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview. **Depends on.** SW-64, SW-30a, SW-48.

#### SW-87 · The evidence panel

- **Metorite task.** 1933.
- **Scope.** One panel on the task. It draws the stages of SW-71 with their evidence. It also shows the checks, the verifier chip, the production acts (SW-73) and the images of task 1727.
- **Done when.** A task with a full pipeline shows every stage and its link. `visual-review` passes.
- **Fence.** A vitest.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview. **Depends on.** SW-71, SW-73, task 1727.

#### SW-88 · The release train and the stuck list

- **Metorite task.** 1934.
- **Scope.** Two sections on the Fleet tab. The release train lists merged, live, waiting for the release cut-off (the `release_cutoff` policy, none by default), and "Merged, not live". The stuck list lists stranded runs, digests and unanswered questions. No new pane.
- **Done when.** R8: the sections list only tasks the viewer may see. With no `release_cutoff` the train shows no cut-off. `visual-review` passes at four widths.
- **Fence.** `test_projects_agent_focus_r8.py`, a vitest.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview. **Depends on.** SW-51, SW-72, SW-62.

#### SW-89 · A run queue for a selection

- **Metorite task.** 1935.
- **Scope.** Select tasks, pick a lane, set an order, and optionally a window such as overnight. The columns are `pm_agent_runs.queue_id` and `queue_pos`. The runs are `created`. The next run wakes when the one before it ends (`run_done`, SW-66). The policy `overnight_allowed` (SW-94) limits the tiers. The review cap stops the queue.
- **Done when.** R8: three tasks run one after the other. A HIGH task in an overnight queue is refused by default. A cancelled run does not stall the queue.
- **Fence.** `test_agent_run_queue_r8.py` (new).
- **Risk.** HIGH (a migration, runs started for others). **Gate.** AGENT-SAFE dark. D145 owner preview. **Depends on.** SW-66, SW-30a, SW-67, SW-94.

#### SW-90 · A ready set and a critical path

- **Metorite task.** 1936.
- **Scope.** A read tool and a timeline overlay (WS-27t). The ready set is the open tasks of an epic with no open blocker. The critical path is the longest chain of `blocks` by estimate. No new table.
- **Done when.** A test epic with a known chain returns the right ready set and path. A task the caller cannot see is left out.
- **Fence.** `test_plan_view_r8.py` (new).
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview for the overlay. **Depends on.** SW-16.

#### SW-91 · Fleet digests

- **Metorite task.** 1937.
- **Scope.** Two sections for the brief (WS-BRIEF): a morning fleet digest and a release-eve digest. The second runs only where the `release_cutoff` policy is set. Counts and links only. No AI credit.
- **Done when.** The digest lists what needs the reader, what runs, what is stuck and what ships. A reader sees only their own scope.
- **Fence.** The brief fences of `core_brief.md`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE dark. **Depends on.** BR-2b, SW-88.

#### SW-92 · Approve many

- **Metorite task.** 1938.
- **Scope.** On `/waiting`, "Approve the LOW-tier items". It lists the questions with `purpose` `preview` or `review` on tasks whose tier a person set to LOW. A NULL tier is excluded. The `verify` and `ci` stages must have passed, and `verify` must come from a run of another session than the builder (SW-59), never from `report_stage`. One confirm. It writes one answer for each, naming the member. It chooses the option the agent marked recommended. A `confirm: true` option is never in the list. The surface belongs to AP-2 and AP-4, and the request is filed in `waiting_on_me.md`.
- **Done when.** R8: a batch of 3 writes 3 answers. A task with a failed stage, a NULL tier or a HIGH tier is not in the list. A `verify` claimed by the building run does not count. A member outside the answer set gets 403.
- **Fence.** `test_agent_questions_r8.py`, a vitest.
- **Risk.** HIGH (answers on behalf of the member). **Gate.** AGENT-SAFE dark. D145 owner preview. **Depends on.** AP-2, AP-4, SW-46, SW-71, SW-59.

#### SW-93 · Delivery analytics

- **Metorite task.** 1939.
- **Scope.** Reports, Agents: time in each stage, idle-hold hours, answer latency, merge rate, rework (changes requested), and cost per merged slice. Cost reads "unknown" until SW-20. A cause filter (SW-99).
- **Done when.** The panels match a fixture. No cost shows as zero.
- **Fence.** The analytics tests beside `analytics_agent_runs.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview. **Depends on.** SW-71, RA-4.

#### SW-94 · One policy store

- **Metorite task.** 1940.
- **Scope.** §3.11.5. The table `pm_agent_policies`, with history. The functions `policy_for(task, key)` and `policy_for_actor(org, member, box, lane, key)` reuse the project-tree walk of SW-31a (`routes/agent_guidance.py`) and add no walk of their own. A row has a scope kind: organization, project, lane, member or box. A key lists the scope kinds it accepts. A key that sits on the auth path (`agents_paused`, `budget_mode`, `required_checks`, `claim_scope_mode`, `lane_mode`, `question_auto_after_minutes`, `pr_open_to_review`) needs the permission `admin:settings:manage`, except as the writer rules below say. An admin writes any key at any scope. A member writes `agents_paused` at the scope kind member, for self only (R8: a member pauses own agents and gets 403 on another member). A project lead writes a key that is not on the auth path, for the lead's own project, except `required_checks`, which stays admin only as SW-54 says. Where a flag has ordered modes, the flag is the ceiling. A policy may choose a mode at or below the flag and never above it. The flag stays the default of the organization. The first keys are in §3.11.5, with `release_cutoff` (default none). The screen is a tab "Policies" of the Settings, Agents screen (SW-31b). `review_cap` moves from the Integrations setting of §3.10.3 to this tab, and SW-49 reads it from here. If SW-18, SW-49 or SW-54 has merged before this slice, SW-94 copies their stored values into policy rows (expand only), with an R8 case. After that, they read through it.
- **Done when.** R8: the nearest project wins over the organization, and a tier row wins over `any`. An unknown key gets 422. A key on a scope kind it does not accept gets 422. A project lead writing an auth-path key gets 403. A policy cannot turn on what a flag turns off. With the flag at `warn` and the policy at `refuse`, the result is `warn`. Each save adds a row.
- **Fence.** `test_agent_policies_r8.py` (new), `test_tenant_coverage.py`.
- **Risk.** HIGH (a tenant table, a write authority rule). **Gate.** AGENT-SAFE dark. D145 owner preview for the tab. Build it first in step 3 of §5.0b. **Depends on.** SW-31b.

#### SW-95 · Day budgets and loop guards

- **Metorite task.** 1941.
- **Scope.** Budgets for each lane and each day (minutes, writes). Loop guards catch the same tool call with the same arguments repeated too many times, or too many writes in a window. The modes `off`, `warn` and `stop`. `stop` uses the stop signal of SW-38. Values come from the policy store.
- **Done when.** R8: a run that repeats one call 20 times gets a notice in `warn` and `stopping` in `stop`. A lane over its day budget starts no new run. Cost is not needed.
- **Fence.** `test_agent_budgets_r8.py` (new).
- **Risk.** HIGH (it stops runs). **Gate.** AGENT-SAFE dark. The master flag is `AGENT_BUDGET_MODES`, default off, and the policy `budget_mode` is at or below it. The flip to `stop` is OWNER-GATE `enforcement-flip`, and the slice registers the flag in `work_plan.md` §6. **Depends on.** SW-38 (task 1064), SW-94, SW-67.

#### SW-96 · A kill switch

- **Metorite task.** 1942.
- **Scope.** The policy `agents_paused` for a box, a member or an organization. While it is on, the edge answers each write tool with 423 `agents_paused`, and reads work. The edge reads the pause in the same transaction as the token lookup (`read_live_connection`), as EA-19b will, so it adds no read to a call. Open runs get `stopping`. A box also stops its runner. An admin pauses any scope. A member pauses their own. Only an admin resumes an organization. **The split.** This pauses the agent door only. The tenant kill switch of `saas_operations_doctrine.md` (WS-31, CP-8) stops the tenant. EA-19 revokes a box token for good. SW-96 pauses and resumes and revokes nothing.
- **Done when.** R8: a paused member's write tool gets 423 and a read works. A pause of one organization touches no other. Resume restores writes. The audit row names who paused.
- **Fence.** `test_agent_pause_r8.py` (new), `test_mcp_edge_coverage.py`.
- **Risk.** HIGH (auth path). **Gate.** AGENT-SAFE dark. **Depends on.** SW-94.

#### SW-97 · An audit export

- **Metorite task.** 1943.
- **Scope.** An admin exports the agent writes of a date range as CSV or JSON: member, client stamp, run, task, tool, time. The source is the activity table with `meta.via`, and the door audit row (#150). The export is itself an audit row.
- **Done when.** R8: an admin gets the rows of the organization only. A non-admin gets 403.
- **Fence.** `test_agent_audit_export_r8.py` (new).
- **Risk.** STANDARD. **Gate.** AGENT-SAFE.

#### SW-98 · Lessons with provenance and age

- **Metorite task.** 1944.
- **Scope.** §3.11.6. A lesson is a rule row of the guidance table (kind `rule`, `project_alignment.md` §4.2, ALN-5, ALN-5b). It uses their states and the lead's acceptance, and ALN-11 owns `needs_review`. SW-98 adds a migration, expand only: `verify_by` and a provenance JSON (run, task, pull request, date seen) on the rule rows. It adds `propose_lessons` for an import, and the expiry rule: a lesson past `verify_by` leaves the active set. The queue is a tab of the Settings, Agents screen (SW-31b). It is owned jointly with WS-ALN, and ALN is told in `project_alignment.md`.
- **Done when.** A proposed lesson never reaches `get_task_context`. A lesson past `verify_by` leaves the active set. A lesson of another project never shows. An imported note is not active before a lead approves it.
- **Fence.** `test_agent_lessons_r8.py` (new), `test_tenant_coverage.py`, `test_mcp_edge_prompts.py`.
- **Risk.** HIGH (a migration, a channel that reaches every agent). **Gate.** AGENT-SAFE dark. D145 owner preview for the queue. **Depends on.** SW-31b, ALN-5, task 1068 (SW-42, no spec text yet).

#### SW-99 · A cause on each stalled run

- **Metorite task.** 1945.
- **Scope.** The list: `ci_wait`, `no_disposition`, `question_terminal_only`, `answer_unread`, `merge_not_linked`, `session_dead`, `epic_hold`, `box_down`, `signed_out`, `capacity`, `human_wait` and `other`. A column `cause` on `pm_agent_run_strands`, and on the release activity of a run. The strand `reason` says which sweep rule fired. The `cause` says why the run stalled. They differ, and a `reason` of `answer_unread` maps to the `cause` `answer_unread`. The sweep sets `cause` from its evidence. A person picks it on Release. An agent may send it in a handoff.
- **Done when.** R8: the sweep tags a parked run `ci_wait`, an epic claim `epic_hold` and a revoked connection `signed_out`. A value outside the list gets 422.
- **Fence.** `test_agent_run_sweep_r8.py`, `test_needs_attention_r8.py`.
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE. **Depends on.** SW-10a, SW-62.

#### Deferred in P10

| Item | What | Waits on |
|---|---|---|
| A VS Code panel | The fleet beside the editor | SW-82 and SW-86 in use |
| Moving the grant window into the policy store | `OWNER_GRANTS.md` as data | An owner decision (Q17) |
| A login-to-member map for GitHub | Needed to let a merge by the lead land in Done (SW-55) | Nothing plans it |

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

### 6.1 Anchors read for P10

Read at `ef6960f8d` of the P10 branch on 2026-11-06. Line numbers move. Find each symbol by name at dispatch.

| Anchor | What |
|---|---|
| `agent_runs.py` `_move_run`, `_take_over`, `person_may_answer_sql` | The terminal-status writers and the answer set (SW-64, SW-66) |
| `filters.py` `lease_held_sql`, `lease_expired`, `holder_free` | The lease SQL and its Python twins (SW-66) |
| `infra/postgres/289_agent_run_exclusive_claim.sql` | The index `pm_agent_runs_one_open_per_task` (SW-39 note) |
| `infra/postgres/340_pm_agent_guidance.sql`, `343_pm_agent_guidance_rules.sql` | The guidance table and its rule rows (SW-98) |
| `routes/agent_guidance.py` | The project-tree walk that `policy_for` reuses (SW-94) |
| `packages/acb_auth/acb_auth/agent_oauth.py` `MCP_ROUTES`, `MCP_WRITE_ROUTES` | The tool allowlist (SW-68, SW-77) |
| `apps/services/agent_edge/agent_edge/bearer.py` | Where a box token authenticates (SW-77) |
| `apps/services/agent_edge/agent_edge/prompts.py` | The one prompt `work_on_task`. PB-2 builds the registry |
| `apps/services/gateway/gateway/agent_run_sweep.py` | The sweep tick that fires an `at` wake (SW-66) |

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

### 7.1 Verify commands for P10

| Slice | Command |
|---|---|
| SW-64, SW-65 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_notices_r8.py tests/unit/test_mcp_edge_agent_runs.py tests/unit/test_mcp_edge_prompts.py tests/unit/test_mcp_edge_coverage.py tests/unit/test_tenant_coverage.py` (new file first) |
| SW-66 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_runs_r8.py tests/unit/test_agent_run_sweep_r8.py tests/unit/test_agent_run_exclusive.py tests/unit/test_tenant_coverage.py tests/unit/test_mcp_edge_context.py tests/unit/test_projects_chat_coverage.py`, `node .claude/hooks/stop-children.test.mjs`, and the webhook test (new) |
| SW-67 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_lanes_r8.py tests/unit/test_agent_label_fence.py tests/unit/test_tenant_coverage.py` (new file first) |
| SW-68 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_queue_r8.py tests/unit/test_mcp_edge_coverage.py` (new file first) |
| SW-69 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_path_claims_r8.py tests/unit/test_conflict_radar_r8.py` (new files first) |
| SW-70 | `uv run pytest -q -p no:cacheprovider tests/unit/test_task_brief_r8.py tests/unit/test_mcp_edge_context.py tests/unit/test_mcp_edge_coverage.py` (new file first) |
| SW-71 | `uv run pytest -q -p no:cacheprovider tests/unit/test_task_stages_r8.py tests/unit/test_tenant_coverage.py tests/unit/test_mcp_edge_coverage.py` (new file first) |
| SW-72 | `uv run pytest -q -p no:cacheprovider tests/unit/test_release_events_r8.py tests/unit/test_default_deny_auth.py tests/unit/test_needs_attention_r8.py tests/unit/test_github_merge_r8.py` (new file first) |
| SW-73 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_agent_runs.py tests/unit/test_mcp_edge_coverage.py` |
| SW-74 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_questions_r8.py tests/unit/test_waiting_seam_r8.py` |
| SW-75 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_auto_answer_r8.py` (new) |
| SW-76 | `uv run pytest -q -p no:cacheprovider tests/unit/test_github_webhook_r8.py` |
| SW-77 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_box_health_r8.py tests/unit/test_tenant_coverage.py tests/unit/test_mcp_edge_coverage.py` (new file first) |
| SW-78, SW-79, SW-80, SW-85 | The runner tests in `scripts/home_server/` (new), and `uv run pytest -q -p no:cacheprovider tests/unit/test_runner_no_secret.py tests/unit/test_mcp_edge_coverage.py` (new file first) |
| SW-81, SW-86, SW-87, SW-88 | `npx vitest run` on the new component tests, and `uv run pytest -q -p no:cacheprovider tests/unit/test_projects_agent_focus_r8.py tests/unit/test_projects_ui_actions.py tests/unit/test_agent_runs_r8.py` |
| SW-82, SW-83 | The node hook tests of the plugin (new), and `uv run pytest -q -p no:cacheprovider tests/unit/test_plugin_commands_match_prompts.py tests/unit/test_playbook_org_neutral.py` (new file first) |
| SW-89 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_run_queue_r8.py` (new) |
| SW-90 | `uv run pytest -q -p no:cacheprovider tests/unit/test_plan_view_r8.py` (new) |
| SW-91 | The brief fences of `core_brief.md` |
| SW-92 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_questions_r8.py` |
| SW-93 | The analytics tests beside `analytics_agent_runs.py` |
| SW-94 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_policies_r8.py tests/unit/test_tenant_coverage.py` (new file first) |
| SW-95 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_budgets_r8.py` (new) |
| SW-96 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_pause_r8.py tests/unit/test_mcp_edge_coverage.py` (new file first) |
| SW-97 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_audit_export_r8.py` (new) |
| SW-98 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_lessons_r8.py tests/unit/test_mcp_edge_prompts.py tests/unit/test_tenant_coverage.py` (new file first) |
| SW-99 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_run_sweep_r8.py tests/unit/test_needs_attention_r8.py` |

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

### 8.1 Owner questions of P10

**Decided 2026-11-06 at 18:29 and 18:37 UTC (owner, task 1906).** The owner accepted the recommended answer to Q14 to Q17, and chose "a second narrow token" for Q18. A slice that waits on a question says so.

| # | Question | Recommendation | Blocks |
|---|---|---|---|
| Q14 | Does a task become Done when it merges, or when the release is live? | **A setting, `done_on`.** The default is `merge`, so GH-6 is unchanged. HathiLabs sets `live`, because 9 merged tasks stayed open for up to 8 days and the owner wants the deployed commit as proof | SW-72 |
| Q15 | Where does the Claude Code plugin live, and who publishes it? | **A public git marketplace repository under the Hathi-Labs GitHub organization.** Claude Code installs from a git marketplace. Publishing is an OWNER-GATE act. Other options: a download from Metorite, or private for contributors only | SW-82 |
| Q16 | May a policy answer a question for a member after a deadline? | **Build it dark, off by default, for reversible questions only.** Never for a preview, a review, an auth question, a `confirm` option or a HIGH task. The minimum is 4 hours. The other option is never to build it | SW-75 |
| Q17 | Does the grant window move from `OWNER_GRANTS.md` into the policy store? | **No, not now.** The guard stays a file that only the owner writes. It is listed as an option for later | SW-94 |
| Q18 | May the runner use a wider scope, or a second token, to post the heartbeat and start sessions? | **Decided 2026-11-06: a second token with a narrow scope (`box:runner`) for the runner. The read-only token stays for the watcher.** Minting it on a live box is an owner act. The other option was: the runner stays a read-only watcher | SW-78, later releases |

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
- **2026-11-06, night.** P10 amendment for task 1906. It adds §3.11, D186 (§4.2), §5.0b, the slices SW-64 to SW-99 (§5.11), §7.1, Q14 to Q17 (§8.1), and the new spec `orchestration_playbook.md`. It adds no code. Three Opus audit rounds fixed 32, 34 and 16 defects.
