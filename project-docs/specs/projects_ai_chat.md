# Projects · the AI chat — WS-27bm

**Status: ACTIVE. S1 (the reads) and S2 (the daily writes) built
2026-09-22. S2b (the rest of class B), S3 (the guarded acts) and S4 (the
workflows, the views and the forms) and S5 (the rest of the manifest)
built 2026-09-23. S6 (navigation and the frontend-tool dispatcher) built
2026-09-23. The visual review ran 2026-09-23 (§4.2). S7, the team
intelligence slices, was designed 2026-09-23 (§13). S7a (capacity) was built
2026-09-23. S7b (fit and rebalancing), S7c (conflicts) and S7d (plan with
capacity, no phases) were built 2026-09-24. S8 (documents and downloads
from the chat, §14) was built 2026-09-24. S9 (entity pills in the chat, §15)
was built 2026-09-24. S7e (on-the-fly analysis, §13.7) was built 2026-09-24.
S10 (chat follow-ups, §16) was built 2026-09-25. S11 (the Forecast reads the
schedule, §17) was built 2026-09-26. S12 (follow-ups, §18) was built
2026-09-28. S13 (message integrity, §19) was built 2026-09-28. S14 (no
forged agent rows, §20) was built 2026-09-28. S15 (chat is saved on
production, §21) was built 2026-09-29. S16 (every deploy proves that chat
saves, §21.10) was built 2026-09-29.** §10 says which slice each part belongs to. §4.4 lists what the chat reuses, file by file.

The design was verified against the tree on 2026-09-22. Every "already
there" claim was re-derived from the code, not from a write-up. Each anchor
carries a file name and a line number. A later reader can check them again.

**Owner directive:** 2026-09-22, "I want the AI chat fully functional for the
project app". In the same session: "continuously update the AI chat so
that we consider new features and abilities of the project app".

**Board row:** WS-27 · **ticket WS-27bm** · **owning spec:** this file.

**Parent spec:** `project_management_app.md` §6.4 names a `skill-projects`
tool family. That family does not exist. This spec builds it.

**Companions:** `generative_ui_2.md` (the HITL and UI doctrine) ·
`crm_app.md` WS-26d-write (the write-tool precedent this copies) ·
`org_access_control.md` §8d (why the chat cannot delete yet).

---

## 1. The answer, in one screen

**The chat is a thin surface over three seams that already exist.** It adds no
transport, no second chat component, no second authorization rule and no
second confirmation mechanism.

| It reuses | Where it is | What the chat adds |
|---|---|---|
| The chat component and its stream | `src/components/AgentChat.tsx` · `POST /agent/run/stream` (`routes/agent.py:1739`) | One rail, pinned to a new agent name |
| The confirmation card | `acb_skills/ask_tools.py:345` `request_confirmation`, fail-closed | A card before every write, with the counts read first |
| The Projects API and its rules | `routes/projects/*`, 130 routes, one visibility model | Tools that call those routes **as the acting member** |
| The agent shape | `apps/agents/agent-crm/agents.py` | `apps/agents/agent-projects/` + `apps/skills/skill-projects/` |
| The per-app cards | `src/components/tasks/TaskToolCards.tsx` | `src/components/projects/ProjectToolCards.tsx` |
| The sidebar slot | `src/app/projects/lib/projectApps.ts:65-72`, `launch: "preview"` | The slot goes `live` behind a flag |

**Three guards protect a write, and each one is a different thing.**

1. **The route decides authority.** The tool calls the gateway with the
   member's own identity, so the route's rule answers, and there is one rule.
2. **The tool class decides the ceremony.** A read has none. A reversible
   write shows one card. A hard-to-reverse act shows a card that carries the
   counts, and it can never be batched or pre-approved.
3. **The card decides consent.** No card, no write. A run with nobody there to
   answer writes nothing (`ask_tools.py:446-448`).

**The chat cannot hard-delete a project or a task.** The delete route has no
authority rule today (H-121, WS-40). A chat tool over it would let any reader
destroy a space, with a card as the only brake. Archive is the chat's remove
verb until WS-40 answers who may delete. §5.4 records this as D-PM-35.

**The chat keeps pace with the app by a fence, not by memory.** Every Projects
route is either mapped to a tool or excluded by name with a reason.
`tests/unit/test_projects_chat_coverage.py` fails on the first route that is
neither. §7 is the design.

---

## 2. Scope and non-goals

**In scope.** A member opens the Projects app, opens the chat, and can
understand, create, update and organise projects, tasks and their details by
talking. Five built-in workflows: plan a project from a goal, a status report,
the weekly report, a stuck review, and "my work" triage.

**Not in scope.**
- A second chat surface. The main chat app at `/chat` sees the same sessions,
  because the session store is shared by agent name.
- Hard delete of a project or a task. D-PM-35, until WS-40.
- Sending a report by email from the chat. Recipients and the schedule stay
  in the Reports app, and arming the schedule is owner-gated (§9.12.8).
- Assign-to-AI as a product feature. Parked by the owner (§9.12.10). This spec
  builds the `skill-projects` family that feature will later need, and nothing
  more.
- A second status vocabulary, a second grant mechanism, a second read cache.
- Product copy in STE. Owner decision 2026-08-26, `docs/style_ste.md` §1.

---

## 3. What a member can do — the workflow

The verbs below are grouped by what they cost to undo. That grouping is the
tool class in §5.2, so the ceremony a member sees follows from this table.

### 3.1 Understand — reads, no ceremony

| Ask | Tool | Route it calls |
|---|---|---|
| "What is in this space?" | `projects_tree` | `GET /projects/tree` |
| "How is Marketing doing?" | `project_summary` | `GET /projects/nodes/{id}/summary` |
| "Find the extruder task" | `find_tasks` | `GET /projects/search`, `GET /projects/tasks?q=` |
| "Show me open tasks due this week for Ayush" | `list_tasks` | `GET /projects/tasks` with the filter set the app uses |
| "Tell me about #142" | `task_detail` | `GET /projects/tasks/{id}` + `/timeline` + `/relations` |
| "What is mine?" | `my_work` | `GET /projects/assigned-to-me`, `GET /projects/my/inbox` |
| "Who could take this?" | `people_for` | `GET /projects/assignees`, `GET /projects/people/names` |
| "What statuses does this project use?" | `vocabulary` | `GET /nodes/{id}/statuses`, `/types`, `/tags`, `/fields` |
| "Where is work stuck?" | `analytics_stuck` | `GET /projects/analytics/stuck` |
| "Who is overloaded?" | `analytics_load` | `GET /projects/analytics/load` |
| "Are we getting faster?" | `analytics_throughput` | `GET /projects/analytics/throughput` |
| "What did we finish last week?" | `analytics_finished` | `GET /projects/analytics/finished` |
| "What is due?" | `analytics_outlook` | `GET /projects/analytics/outlook` |
| "Who has the hours for this?" (S7a) | `team_capacity` | `GET /projects/analytics/capacity` |
| "Render the weekly report" | `report_render` | `GET /projects/reports/{id}/render` |
| "Does this repeat?" | `recurrence` | `GET /projects/tasks/{id}/recurrence` |
| "Show me my view of #142" | `my_task` | `GET /projects/my/tasks/{id}` |
| "What is on the calendar this week?" | `calendar` | `GET /projects/calendar`, `GET /projects/my/calendar` |
| "What is waiting in intake?" | `intake_queue` | `GET /projects/intake` |
| "Anything for me?" | `notifications` | `GET /projects/notifications` |
| "Who watches this?" | `watchers` | `GET /projects/tasks/{id}/watchers`, `GET /projects/nodes/{id}/watchers` |
| "Which views does this project have?" | `project_views` | `GET /projects/nodes/{id}/views` |
| "Who can see this project?" | `project_access` | `GET /projects/nodes/{id}/grants` |
| "What contexts do I use?" | `my_contexts` | `GET /projects/my/contexts` |

Every number a read returns is a **server aggregate**. The tool never sums a
page of tasks in the agent. §9.12.7 gives the reason. The list is paginated,
and a count of one page looks right and is wrong.

### 3.2 Create and update — reversible writes, one card

| Ask | Tool | Route it calls | Why it is reversible |
|---|---|---|---|
| "Add a task: call the vendor about the quote" | `create_task` | `POST /projects/tasks` | Archive undoes it |
| "Rename it, set due Friday, priority high" | `update_task` | `PATCH /projects/tasks/{id}` | The timeline holds a revert |
| "Move it to In progress" | `set_status` | `PATCH /projects/tasks/{id}` (status by name) | Any status may be set again |
| "Assign it to Priya" | `assign` | `PUT /projects/tasks/{id}/assignees` | Reassign |
| "Comment: waiting on legal" | `comment` | `POST /projects/tasks/{id}/comments` | Soft delete by the author |
| "Break it into three steps" | `add_subtasks` | `POST /projects/tasks` × n, `parent_task_id` | Archive |
| "It is blocked by #140" | `link_tasks` | `POST /projects/tasks/{id}/links` | `DELETE …/links/{id}` |
| "Move it into the Q4 project" | `move_task` | `POST /projects/tasks/move/preview`, then `/move` | Move back |
| "Watch this task for me" | `watch` | `PUT /projects/tasks/{id}/watch` | Unwatch |
| "Defer it to Monday" | `defer` | `POST /projects/tasks/{id}/defer` | Per-member overlay, mine |
| "Make a project called Q4 launch under Marketing" | `create_project` | `POST /projects/nodes` | Archive |
| "Rename the project" | `update_project` | `PATCH /projects/nodes/{id}` | Rename back |
| "Bring the archived task back" | `unarchive_task` | `POST /projects/tasks/{id}/unarchive` | It is the undo |
| "Save this as a weekly report" | `report_save` | `POST /projects/reports` | `DELETE /projects/reports/{id}` |
| "Add a Blocked status" | `create_status` | `POST /projects/nodes/{id}/statuses` | An empty lane costs nothing. Delete is guarded |
| "Rename the lane to In review" | `update_status` | `PATCH /projects/statuses/{id}` | Rename back |
| "Add a Bug type" | `create_type` | `POST /projects/nodes/{id}/types` | Delete is guarded, and tasks keep existing |
| "Make Bug the default type" | `update_type` | `PATCH /projects/types/{id}` | Set another default |
| "Add a Customer field" | `create_field` | `POST /projects/nodes/{id}/fields` | Delete is guarded |
| "Add the option Enterprise" | `update_field` | `PATCH /projects/fields/{id}` | Drop the option while nothing holds it |
| "Register a tag called urgent" | `create_tag` | `POST /projects/nodes/{id}/tags` | Delete is guarded |
| "Rename the tag to p0" | `update_tag` | `PATCH /projects/tags/{id}` | Rename back. Every task follows, and the card says how many |
| "Fix the typo in my comment" | `edit_comment` | `PATCH /projects/comments/{id}` | Edit again. The author only |
| "Repeat this every Monday" | `set_recurrence` | `PUT /projects/tasks/{id}/recurrence` | `DELETE` stops it. The task stays |
| "Note to self: renew the domain" | `create_personal_task` | `POST /projects/my/tasks` | Archive. Nobody else sees it |
| "File this as Someday for me" | `set_my_overlay` | `PATCH /projects/tasks/{id}/personal` | Per-member overlay, mine |
| "Capture this into intake for Ops" | `capture_intake` | `POST /projects/intake` | Decline it. No project named means the member's own personal project, or a refusal |
| "Accept the vendor task" | `triage_intake` | `POST /projects/intake/{id}/accept`, `decline`, `duplicate`, `snooze` | Decline and duplicate archive, and the card says so. `unarchive_task` restores |
| "Save this as a view called Mine" | `save_view` | `POST /projects/nodes/{id}/views`, `PATCH /projects/views/{id}` | Rename back. Delete is guarded |
| "Clear my notifications" | `mark_notifications_read` | `POST /projects/notifications/read` | The bell refills |

**A vocabulary row is named, never numbered.** The member says "the Blocked
lane". The tool reads the project's own list and resolves the name the way a
status is resolved: every case-insensitive match, never the first. Two
matches is a question for the member. The card names where the row lands.
A type, a field or a tag lands on the tree's root, so the card names the root
and every project under it. A status lands on the node that owns the set,
because a subproject may inherit its lanes from a parent. An org-wide row is
named as such, and an org-wide tag rename carries no count, because the list's
count is scoped to one tree and the rename is not.

**A batch is one card.** A plan that creates one project and twelve tasks
shows one card that lists all thirteen rows. Twelve cards would train the
member to click through, which defeats the card.

**The card names the row.** Before a write that targets an existing row, the
tool reads the row so the card can say its title and its project. A card that
reads "update task 8f3c…" is a signature bought under a misdescription.
WS-26d-write decision 1 is the precedent, and its test shape is the fence:
every call before the card is a `GET`.

### 3.3 The guarded acts — hard to reverse, one card each, never batched

| Ask | Tool | Route it calls | What the card carries |
|---|---|---|---|
| "Archive this project" | `archive_project` | `POST /projects/nodes/{id}/archive` | Subtree count, open-task count, the name |
| "Archive the task" | `archive_task` | `POST /projects/tasks/{id}/archive` | Title, status, open subtask count |
| "Restore the project" | `unarchive_project` | `POST /projects/nodes/{id}/unarchive` | The rows it will clear |
| "Merge #12 into #9" | `merge_tasks` | `POST /projects/tasks/{id}/merge` | Both titles, what moves |
| "Set all of these to Done" | `bulk_update` | `POST /projects/tasks/bulk` | The exact ids and the change |
| "Move the folder into Ops" | `move_project` | `POST /projects/nodes/{id}/move` | The subtree, the new parent |
| "Delete the Blocked status" | `delete_status` | `DELETE /projects/statuses/{id}?move_to=` | Count in use (the statuses read's per-lane `counts`, the route's own number), the target |
| "Use the parent's statuses" | `set_status_set` | `POST …/status-set/preview`, then `…/status-set` | The preview's counts: moving, completing, reopening |
| "Delete the tag" | `delete_tag` | `DELETE /projects/tags/{id}` | `GET /tags/{id}/impact` first |
| "Merge the tag into p0" | `merge_tags` | `POST /projects/tags/{id}/merge` | The source's task count, both names |
| "Delete the type" | `delete_type` | `DELETE /projects/types/{id}` | The tree it clears from. No read counts the tasks, so the receipt carries the route's number |
| "Delete the field" | `delete_field` | `DELETE /projects/fields/{id}` | The key and the tree. No read counts the values, so the receipt carries the route's number |
| "Undo that change" | `revert_activity` | `POST /projects/activities/{id}/revert` | Each field as now → restored |
| "Delete my comment" | `delete_comment` | `DELETE /projects/comments/{id}` | The comment text. The author only |
| "Delete the view" | `delete_view` | `DELETE /projects/views/{id}` | The name and type. The receipt carries the cascade counts |
| "Delete the report" | `report_delete` | `DELETE /projects/reports/{id}` | The name and scope |
| "Detach the file" | `delete_attachment` | `DELETE /projects/tasks/{id}/attachments/{id}` | The file name. The bytes stay |

Three rules bind every row of this table.

1. **One act, one card.** The tool refuses a list. A member who wants five
   projects archived answers five cards.
2. **No pre-approval.** "Archive it without asking" is not an argument the
   tool accepts. Neither the agent, the persona, nor an earlier answer in the
   session can skip a class C card.
3. **The counts come from the route, before the write.** Archive and delete
   routes already read their counts first (`tree.py:934-936`, R7/R8). The card
   shows those numbers, so the member signs what the server will do.

**How S3 built it** (`skill_projects/guarded.py`). The card's first line after
the fixed note is `impact: …`. Where a read exists, the line carries its
numbers: the summary and the tree for an archive (what the member can see,
and the card says so), the statuses read's per-lane counts for a status, the
impact read for a tag and a tag merge, the preview for a status set. Where no read
exists (a type's tasks, a field's values, a view's positions), the line names
the scope and says the receipt carries the count the route reports. A tool
that takes one row refuses a comma-separated list before any read
(`_many`). `bulk_update` and `merge_tasks` take a selection because the route
does it in one transaction. Both cap it at 50 and name every task. A bulk
`delete` action is refused (D-PM-35). `test_projects_agent_writes.py` holds
the three rules: the list refusal, the impact line, and one card per call.

**Not on the chat surface, and the reason.** `DELETE /projects/nodes/{id}` and
`DELETE /projects/tasks/{id}` are hard deletes. `pm_projects` cascades over the
subtree, every task and every grant. Their only guard is read visibility
(`tree.py:938-940`). D-PM-35 keeps them out of the chat until WS-40 lands.

### 3.4 The built-in workflows

Each workflow is a **prompted sequence over the tools above**, not a new
endpoint. The agent's instructions carry the sequence. The tools carry the
guards. A workflow cannot reach a write that its tool class forbids.

**How S4 built it.** Three tools carry the steps a prompt alone would get
wrong. `propose_plan` (`forms.py`) validates every task for the four
fields, draws the plan as a `planCard` with `hitl`, and after the member's
submit creates the project and every task as one batch under one card.
`status_report` (`views.py`) reads the summary, the stuck, load and outlook
routes, flags each child project and draws a `statDashboard`. `edit_task`
and `edit_project` draw a `formCard` with the row's values and pass the
changed fields to `update_task` or `update_project`, so the card those tools
show is the consent. The views (`render_timeline`, `render_board`,
`render_tasks`, `render_report`) read through their composite's routes and
draw one template each. W4 and W5 stay instructions over the tools.

**W1 · Plan a project from a goal.** Carried forward from the CommandCenter
`agent-project-manager` satellite repo (`.github/skills/project-planning`).

1. The member states a goal and a deadline.
2. The agent reads the space (`projects_tree`), the vocabulary, and the
   people (`people_for`).
3. The agent proposes tasks, owners, dates and dependencies as a `planCard`
   (`emit_generative_ui`, `hitl: true`, inline — the rail has no panel host).
   Each task has a verb-plus-object title, an owner, an effort estimate and
   a date. A task that lacks one of the four is not proposed.
4. Priority score is `impact × urgency × effort`, each 1 to 5, shown per task
   and never stored. It is a sorting aid, not a field.
5. The agent names three ways the plan fails, before it asks for approval.
   That step was the satellite skill's inversion check, and it stays.
6. On approval, `create_project` and `create_task` run as **one class B
   batch**, one card, every row listed.

**W2 · Status report.** Carried forward from `.github/skills/project-tracking`.

- Scope is a node or the portfolio. The agent reads `analytics_stuck`,
  `analytics_load` and `analytics_outlook`.
- Every project gets one of three flags, from the server's reads. **Blocked**:
  a task in it has an open blocking link (the stuck read names the task's
  project). **At risk**: it has overdue work, or the stuck read's overdue
  list names it. **On track**: neither. The load read's top holder is named
  in the report's Load section, not folded into a flag. *(As built in S4.
  The draft said "due within 3 days with no status change in 7 days", which
  no route answers per project.)*
- The output is a `statDashboard` inline card and a Markdown artifact
  (`write_artifact`) the member can open in the side panel.
- The agent offers to comment on each at-risk task. Each comment is a class B
  write in one batch card.

**W3 · The weekly report.** The Reports app owns the numbers (§9.12.8). The
chat finds or saves a definition (`report_save`, class B) and renders it
(`report_render`). It never computes a second set of numbers, and it never
sends.

**W4 · Stuck review.** `analytics_stuck` for the scope, then one question per
stuck task: move it, reassign it, comment, or leave it. Each answer is a
class B write, batched into one card at the end of the review.

**W5 · My work triage.** `my_work`, then the member's own overlay only:
`defer`, `watch`, `complete`. No shared field changes without a card.

---

## 4. Where it runs — the seams, and what is new

### 4.1 Backend — one agent, one skill package

```
apps/agents/agent-projects/
  config.json        runtime "maf" · skill_repos ["skill-projects"] · tool_scope
  instructions.md    the workflows in §3.4, the rules in §5, the data fence
apps/skills/skill-projects/
  skill_projects/
    manifest.py      every /projects route → tool, class, or an exclusion
    client.py        the gateway client, copied from agent-crm (identity, verbs, paths)
    reads.py         class A tools
    views.py         class A tools that draw a template (S4)
    writes.py        class B tools
    forms.py         class B tools over an editable card (S4)
    guarded.py       class C tools
```

**Identity is the acting member, and nothing else.** The client sends the
internal bearer and `X-User-Email` from the per-run ContextVar the executor
binds (`agent-crm/agents.py:93-104`). A run with no attributed user gets no
header and the gateway refuses. That answers the one design question §6.4
left open. The chat path runs as the member, and the route's rule is the
authority.

`EffectiveAccess.intersect()` is for the assigned-agent path
(`agent_dispatch.py`), which runs under `agent:<name>`. The two paths share
the skill package and differ only in who they act as.

**The verb is bounded in the client.** `_ALLOWED_METHODS` holds `GET`, `POST`,
`PATCH`, `PUT` and `DELETE`, and every `DELETE` path is listed by literal in
`manifest.py`. The two hard-delete routes are not in that list, so no tool can
reach them even by mistake. The path is bounded the way `_record_uuid` and
`_entity_slug` bound it in the CRM agent (`agents.py:23-30`).

**Names, not ids, where a person speaks names.** A status, a type, a tag and a
person are given by name. The tool resolves the name against the project's
own vocabulary, the way `update_deal_status` resolves a stage. Two rows with
one spoken name is a question for the member, never a first-match pick
(WS-26d-write decision 3).

**Every tool prints `full_id: <uuid>`** for each row it returns, the
`skill-task-gtd` convention the cards read.

### 4.2 Frontend — one rail, one card file, one persona

```
src/app/projects/components/AssistantRail.tsx     thin wrapper over <AgentChat>
src/app/projects/lib/assistantPersona.ts          the live context, from code
src/components/projects/ProjectToolCards.tsx      the cards, two lines in MessageBubble
src/app/projects/lib/projectApps.ts               "ai-chat" goes live behind the flag
```

**The rail is the Tasks rail, re-pointed.** `AssistantRail.tsx:1-33` in the
Tasks app does four things. The Projects rail does the same four and nothing
else.

- A session list scoped by agent name.
- A persona from the live app state.
- Quick actions into the composer.
- Memory parity with the main chat app.

**Where it mounts.** Two places, one component. The `ai-chat` sidebar slot
(`page.tsx:3024-3029`) shows it full-width. **S1 builds this one.**

An "Assistant" toggle in the board's action row shows it docked, so a member
can talk while the board is open. On a phone the chat is a full scene, like
the email assistant. **BUILT, 2026-09-23.** `lib/chatDock.ts` owns the rules,
and `chatDock.test.ts` is their fence:

- The dock is a 26rem column while `DOCK_QUERY` (`80rem`, Tailwind's `xl`)
  matches. Below that width the page mounts no rail, and the toggle opens the
  full slot. On a phone, the sidebar's AI chat entry opens the slot as a full
  scene.
- The dock and the docked task panel share one right-hand column. While a task
  holds it, the chat hides and stays mounted, so a streaming reply keeps
  streaming. A full-width task panel is an overlay and hides nothing.
- The toggle is pressed only while the column is on screen. While a task holds
  the column, a press closes the task and shows the chat.
- A space, a folder, Analytics and Reports keep the dock, because the chat's
  own navigation goes there in the middle of a reply. Only the `ai-chat` slot
  removes it, so the member never sees two chats.
- Each browser remembers the choice in `localStorage`, as `panelMode.ts` does.
- A write receipt reloads the board only if it finished in the last minute
  (`isFreshReceipt`). A receipt replayed from history does not reload it.

While the slot is open the tree highlights no node. So the rail header names
the scope itself, and the persona says "current scope", not "looking at".
The persona carries no `view` in the slot, because the member sees the chat
and no canvas.

**The persona carries the member's place, as data.** The selected node and
its level, the view and its filters, the open task id, the bulk selection ids,
today's date and the member's timezone. Every title is quoted as data with the
fence sentence the Tasks persona uses (`taskAssistantPersona.ts:47-55`). The
persona also carries whether the member holds `projects:settings:write`, so the
agent can say "ask your admin" instead of trying and failing.

**The cards.** `ProjectToolCards` is inert unless a message carries a
`skill-projects` tool, so the same cards render in the main chat app. Five
kinds. **S1 builds `TaskListCard` and a titled text card for every other
read.** `PlanCard` and `ReportCard` are S4, `ActionResultCard` is S2.

| Card | For | Action |
|---|---|---|
| `TaskListCard` | `list_tasks`, `find_tasks`, `my_work` | A row opens the task panel by `?task=` |
| `SummaryCard` | `project_summary`, the five analytics reads | Opens the node, or the Analytics app |
| `planCard` (template) | W1's proposal, editable, before approval | Submits the edited plan back to `propose_plan` |
| `timeline`, `taskBoard`, `dataGrid`, `reportCard` (templates) | `render_timeline`, `render_board`, `render_tasks`, `render_report` | The timeline's title opens its task. A board card and a table row open their task. The report's title opens the Reports app |
| `formCard` (template) | `edit_task`, `edit_project` | Submits the edited fields back to the tool |
| `ActionResultCard` | Every write | Says what changed, links the row. A done class C act wears the warning tone, and every other done write wears success. `GUARDED_TOOLS` names the class C tools, and `test_the_cards_know_every_guarded_tool` holds it equal to the manifest. Built 2026-09-23 |

**The generic card is the default.** A tool the card file does not know
renders as `ActionResultCard` from its class. A new tool never renders as
raw text, and it never needs a card file change to ship. §7.3 depends on this.

**Frontend tools, navigation only.** Through `useFrontendTool`
(`src/hooks/useFrontendTool.ts`): `open_task(id)`, `open_project(id)`,
`open_app("analytics" | "reports")`, `set_filter(...)`. None writes data.
**Built in S6 (2026-09-23).** The platform had the registry and no way
to reach it: the model's tools are server-side. `acb_skills.frontend_tools`
is the dispatcher. A skill tool pushes one CUSTOM `frontend_tool` event onto
the run's stream, and `AgentChat` runs the registered handler once per event
id. The Projects page registers `projects.open_task`, `projects.open_project`
and `projects.open_app`. The model reaches them through `open_in_app`, which
reads the row first and always returns the link as well. The result says
"asked", not "opened": the run cannot see whether a Projects page consumed
the event. A dispatch is kept off the stored message and runs once per event
id, across a reload. `set_filter` is not
built. The page's filter state has no stable shape to hand a model yet.

**The visual review (2026-09-23).** The rail and every card were rendered in
a browser, in dark and light mode, at compact density, under a changed accent
and at phone width. It found five defects, all fixed. The board's view tabs
sat above the chat, because the chat pane was missing from the rule that
hides them for Analytics and Reports. Two headers both said "AI chat", so the
rail's header now names the scope it answers about. Two sets of suggestions
competed, so the four prompts are now the shared chat's own "Try asking"
pills, the pattern the main chat and the email assistant use. The receipts
showed the model's «guillemet» fence, the `full_id:` lines and raw routes,
so they now show plain words. The timeline coloured a comment with the
member's accent, so its dots now use the categorical ramp.

**The second UX review (2026-09-23, owner report).** A browser rig replayed
the events the real view tools emit, with a Markdown answer, a file, and a
class B and a class C receipt. It found five defects, and all are fixed.

- **The chat was bound to one project.** It now reaches everything the member
  can see. The header's "Chat about" picker (`lib/chatScope.ts`) sets a focus,
  which is a hint and never a boundary. The focus follows the tree until the
  member picks, and then it holds. The persona tells the model to find any
  other project the member names, and to answer a general question across
  everything.
- **"Open task" stayed in the chat.** A card's link opened the task panel
  beside a full-width chat, and the project never showed. Now the page leaves
  the chat slot, selects the task's project, opens the task, and docks the
  conversation.
- **Each view showed twice.** The template drew, and then a text card showed
  the same facts. For the status report that text was raw Markdown source. A
  view tool's text is for the model, so `VIEW_TOOLS` hides its card once the
  tool is done.
- **A file had no side panel.** "Open in side panel" on a Markdown file
  wrote to a store that only the chat page drew. The Projects page now mounts
  `SidePanelEditor`, which draws nothing until a file is open.
- **Raw data leaked.** The "Interactive view" fold repeated the file event as
  JSON, and the cards showed `project_id` values and activity ids. The fold
  now hides file events, and the cards strip both kinds of id.

**The board follows the chat.** A receipt card that reports a done write
fires `cc-projects-changed` once, and the Projects page reloads the selected
project. The chat never reaches the page's state. That event is the one
seam, and it is the reason an edit from the chat shows on the board without
a click.

**Quick actions.** Four composer suggestions, scoped to the selected node.
They read "What is stuck here?", "Summarise this task", "Plan a project from
a goal" and "Weekly report for this space".

### 4.3 The flag

`NEXT_PUBLIC_PROJECTS_CHAT` on the frontend flips the `ai-chat` slot from
`preview` to `live` and shows the rail toggle. Default OFF. The agent is
registered on the backend whether or not the flag is on. The main chat app can
reach any registered agent, so a member with `feature:projects` and
`feature:chat` may already talk to it there. There is no backend flag, because
the routes the tools call are the routes the app already serves.

### 4.4 What the chat reuses, file by file

The owner's rule for this build: extend the chat and AG-UI stack that exists,
never a second one. This table is the audit. Every row names the file the
chat reuses and the file that reuses it. A new seam would be a new row with
nothing in the first column.

| Existing piece | Where it lives | What reuses it |
|---|---|---|
| The chat component, streaming, reconnect, persistence | `src/components/AgentChat.tsx` · `src/hooks/useAgentChat.ts` | `AssistantRail.tsx` mounts it, as the Tasks and email rails do |
| The AG-UI stream | `POST /agent/run/stream` (`routes/agent.py`) · `src/app/api/agent/chat/route.ts` | Untouched. The agent is one more name on it |
| The confirmation card | `acb_skills/ask_tools.py::request_confirmation` · `src/components/ConfirmationCard.tsx` | Every class B tool, through `writes.py::_confirm` |
| The per-app card slot | `src/components/MessageBubble.tsx` (beside `EmailToolCards`, `TaskToolCards`) | `ProjectToolCards.tsx` |
| The tool-card chrome and dismiss | `src/components/ToolCardShell.tsx` · `src/lib/dismissedTools.ts` | Every Projects card |
| The session store | `src/lib/sessions.ts` | The rail's session list, scoped by agent name |
| The memory hook | `src/hooks/useChatMemories.ts` | The rail, for parity with the other assistants |
| The risk annotations and the permission policy | `acb_skills/tool_annotations.py` · `permission_policy.py` | Every tool is annotated. The policy defers to the card |
| The acting-member identity | The run ContextVar the executor binds (`memory_tools._get_memory_user_id`) | `client.py::current_user_email` |
| The gateway client shape and its refusals | `apps/agents/agent-crm/agents.py` | `client.py` copies the verb bound, the path bound and the identity rule |
| The write-tool shape | `agent-crm` WS-26d-write | `writes.py` copies read-then-card-then-write, name resolution, the card budget |
| The registries and the loader | `agent_registry.json` · `routes/agent.py::_AGENT_REGISTRY` · `acb_skills/loader.py` | The agent is registered the way the CRM agent is |
| The activity writer | `routes/projects/core.py::record_activity` | D-PM-36 adds one field to its `meta`, not a second writer |
| The generative UI templates | `emit_generative_ui`, `genUITemplates.tsx` | The agent's `tool_scope` carries it. W1's plan panel is a `formCard` (S4) |
| The design system | `src/components/ui/Button.tsx`, the `--success` and `--destructive` tokens | The rail's controls and the result cards |

**Read alongside.** `generative_ui_2.md` §4 lists the chat as a consumer.
`learning-resources/13-ag-ui-and-generative-ui.md` §4(e) is the worked
example. `apps/services/gateway/AGENTS.md` carries the second-consumer note
on `routes/projects/`.

---

## 5. The three guards

### 5.1 Authority — the route's, and only the route's

The tool never opens a database session and never holds SQL. It asks the
gateway, as the member. So every refusal the app already makes is the chat's
refusal too. The agent relays the refusal in plain words and does not try the
same write again.

- 404 for a row the member may not see (R5).
- 403 naming the permission the member lacks (`core.py:512-529`).
- 422 for a shape or privacy rule (`core.py:1258`, `:1318`).

The permissions that exist today, and the routes they guard, are the whole
authority model. The table in `project_management_app.md` §4 and the map in
`org_access_control.md` §8d.1 hold them. This spec adds **none**.

### 5.2 The tool class — the ceremony a write earns

| Class | Meaning | Card | Batch | `non_interactive_default` |
|---|---|---|---|---|
| **A** | Reads | None | — | — |
| **B** | A write the app can undo | One card, may list many rows | Yes | `"deny"` |
| **C** | A write that is hard to undo | One card per act, with counts | **No** | `"deny"` |
| **X** | Excluded from the chat | — | — | — |

`manifest.py` assigns the class. `@annotate` from
`acb_skills.tool_annotations` carries it to the permission layer, which defers
to the card (`permission_policy.py:197`). Annotation is not enforcement. The
tool awaits the card itself, and the fence in §12 asserts both.

No class B or C tool passes `non_interactive_default="approve"`. A test asserts
the absence structurally, the way `test_crm_agent_write.py` does.

### 5.3 Consent — the card

`request_confirmation(title, detail, context)` at the top of every class B and
C tool, before any mutating request is built. The `context` block is budgeted
under 4000 characters with the warning line first (WS-26d-write, "the card
stopped matching the wire"). A class C card starts with the counts.

### 5.4 D-PM-35 — the chat cannot hard-delete until WS-40 answers who may

**Decision (agent-proposed, owner may overrule).** `delete_project` and
`delete_task` are class X. The chat's remove verb is archive.

**Why.** H-121 measured that no Projects route carries a permission check, and
that `DELETE /projects/nodes/{id}` cascades over a subtree on read visibility
alone. The owner deferred the fix as WS-40 and asked for a record. A chat tool
over that route would put the cascade one sentence away from any reader, with
a card as the only brake. A card guards against a mistake. It is not
authority, and §8d.3 says it must never be described as authority.

**What changes when WS-40 lands.** The two routes gain a permission. The two
tools move from class X to class C in `manifest.py`, and their cards carry the
counts the route already reads. Nothing else changes.

**The interim the owner may choose instead.** Gate the two tools on
`projects:settings:write`, the one permission Projects already has. This spec
does not recommend it, because that permission means "may edit the
vocabulary", and reusing it for delete is the near-miss §8d.1 warns about.

---

## 6. Data and attribution

**No new table, and no new column in slice 1.** Every write lands through a
route that already records a `pm_activities` row with `created_by` set from
the authenticated context (`core.py:2585`, R3). A chat write is attributed to
the member, because the member approved it.

**D-PM-36 — a chat write says it came from the chat. Built in S2.** The
skill sends one request header, `X-Actor-Via: chat:projects-assistant`. A
router-level dependency (`core.py::capture_actor_via`) binds it for the
request, and `record_activity` copies it into `meta.via`. `created_by` stays
the member, so authorship rules such as comment edit (`activities.py:306`)
keep working.

The timeline can then show "by Priya, through the assistant". A member who
reverts an assistant edit can see which ones those were. One seam, one
field. A header outside the pattern binds nothing, so the human path is
unchanged. The fences are `tests/unit/test_projects_actor_via.py` (the
dict, the dependency and the router) and `tests/live/live_actor_via.py`,
which reads the JSONB back from a real Postgres (R8).

---

## 7. Keeping pace — the chat follows the app by a fence

The owner's second directive is the hard one. The app gains features every
week, and a chat that lists its abilities in prose is stale by the next merge.
The Tasks persona proved that: it sent members to a "Connect workspace" button
for two weeks after the button was deleted (`taskAssistantPersona.ts:33-40`).

### 7.1 D-PM-37 — every Projects route is mapped or excluded, and a test says so

`manifest.py` is a table. One row per route template, in the shape the router
reports it: method, path, tool name, class. A row may instead say `excluded`
and carry a reason. `tests/unit/test_projects_chat_coverage.py` does three
things.

1. It imports `gateway.routes.projects.router` and walks `router.routes`.
2. It fails on any route that has no manifest row. The message names the
   route and says: map it to a tool, or exclude it with a reason.
3. It fails on any manifest row that names a route the router no longer
   serves. A stale tool is a lie the agent will tell.

So a pull request that adds a Projects endpoint fails CI until its author
decides what the chat does with it. The decision costs one line. The fence
is R7's answer to "continuously update".

### 7.2 The persona and the instructions come from code

The agent's instruction file names workflows and rules. It does not list
tools. The executor already injects the tool docstrings, and the tools come
from the manifest, so the list the model sees is the list that runs. A tool's
docstring is the one place its behaviour is described, and it is beside the
code that does it.

### 7.3 The card file never blocks a ship

§4.2's generic card renders any unknown tool from its class. So a new tool
reaches the member with a correct card on the day it merges. A bespoke card
is a later polish, not a gate.

### 7.4 What still needs a person

A new **workflow** (§3.4) needs a paragraph in `instructions.md`. A new
**guarded act** needs its counts on the card, and a reviewer must check that
the route reads those counts before it writes. The fence catches the route.
It cannot judge the card.

---

## 8. Cost

The agent declares `tier-balanced` as its default (D-AI-4). The rail passes the
member's chat model through `AgentChat model=… lockModel`, and a Projects
settings entry for it follows the Tasks shape (`TaskSettingsModal.tsx:40-77`)
in slice 5. Every completion passes the gateway's `/v1` chokepoint
(`orchestrator/agents.py:412-438`), so local metering and the Router hop both
apply without a change here. ⚠️ `ROUTER_SERVING_ENABLED` is off, and H-42 says
the rate card is unpriced. So the chat spends no credits today, and a customer
sees no AI usage for it. This spec does not change that.

---

## 9. Decisions

- **D-PM-35** — the chat cannot hard-delete until WS-40 answers who may. §5.4.
- **D-PM-36** — a chat write carries `meta.via` on its activity row. §6.
- **D-PM-37** — every Projects route is mapped or excluded in
  `manifest.py`, and `test_projects_chat_coverage.py` fails otherwise. §7.1.
- **D-PM-39** — only the server creates an agent row or a system row. A
  LiteLLM reply and a compaction summary stay in the browser until a later
  slice gives them a server writer. §20.5.

D-PM-35, D-PM-36 and D-PM-37 are agent-proposed. The owner may overrule any
of them. The owner decided D-PM-39 on 2026-09-28, with the option "Accept it
now, fix later".

---

## 10. Slices

Each slice is one pull request. Each one is useful alone.

| Slice | Builds | Gate |
|---|---|---|
| **S1 · Read** — ✅ **BUILT 2026-09-22** | `skill-projects` class A tools · `manifest.py` with every route classified · the coverage fence · `agent-projects` registered · the rail behind the flag · the persona · `TaskListCard` and a titled card for every other read | AGENT-SAFE |
| **S2 · Write** — ✅ **BUILT 2026-09-22** | The fifteen daily class B tools with the card (create, update, assign, comment, subtasks, link, unlink, move, watch, complete, defer, restore a task, create and update a project, save a report) · `ActionResultCard` · `X-Actor-Via` and `meta.via` (D-PM-36) | AGENT-SAFE |
| **S2b · The rest of class B** — ✅ **BUILT 2026-09-23** | The vocabulary writes (status, type, field, tag create and update) · edit a comment · recurrence · the personal task and overlay · the two reads they need (`recurrence`, `my_task`) · the agent instructions now describe the writes (S2 left them saying "reads only") | AGENT-SAFE |
| **S3 · Guarded** — ✅ **BUILT 2026-09-23** | The seventeen class C tools in `guarded.py` with impact-first cards · the one-act-one-card rule and its test · the agent instructions name the guarded acts | AGENT-SAFE |
| **S4 · Workflows** — ✅ **BUILT 2026-09-23** | W1 plan (`propose_plan` over a `planCard`), W2 status report (`status_report`), W3 weekly (`render_report`), W4 stuck and W5 triage (instructions over the tools) · five templates in the shared catalog (`timeline`, `taskBoard`, `dataGrid`, `reportCard`, `planCard`) with a lockstep fence · `edit_task` and `edit_project` over a `formCard` · the board reloads after a chat write (`cc-projects-changed`) · the chat model setting reused | AGENT-SAFE |
| **S5 · The rest** — ✅ **BUILT 2026-09-23** | The eleven reads and writes that were still in `PLANNED` (`inbox.py`: views, calendar, contexts, watchers, intake, notifications, grants). `PLANNED` is empty of WS-27bm names: every `/projects` route is built or excluded by name | AGENT-SAFE |
| **S6 · Navigation** — ✅ **BUILT 2026-09-23** | The frontend-tool dispatcher (`acb_skills.frontend_tools`, `runFrontendToolEvent`) · `open_in_app` and the page's three handlers · the two S2 follow-ups (an unknown address named on the card, the subtasks receipt opens the parent) | AGENT-SAFE |
| **Visual review** — ✅ **DONE 2026-09-23** | The rail and the cards seen in eight contexts. The defects it found are fixed (§4.2) | AGENT-SAFE |
| **S7a · Capacity** — ✅ **BUILT 2026-09-23** | `GET /projects/analytics/capacity` · the Analytics app's Capacity panel · the report section `capacity` · the chat tool `team_capacity` (§13.3) | AGENT-SAFE |
| **S7b · Fit** — ✅ **BUILT 2026-09-24** | `GET /projects/tasks/{id}/candidates` and its draft form · `GET /projects/analytics/rebalance` · "Suggested" in the assignee picker · the chat tools `fit_for_task` and `rebalance` (§13.4) | AGENT-SAFE |
| **S7c · Conflicts** — ✅ **BUILT 2026-09-24** | `GET /projects/analytics/conflicts` with seven kinds · the Conflicts panel · the report section `conflicts` · the chat tool `find_conflicts` · the dependency rule moved to the server with one fixture for both sides (§13.5, §10.5) | AGENT-SAFE |
| **S7d · Plan with capacity** — ✅ **BUILT 2026-09-24** | `propose_plan` gains start dates and dependencies, and shows each owner's fit and hours across the plan on the card, through `POST /projects/plan/preview` (§13.6, §10.6). No phases | AGENT-SAFE |
| **S8 · Documents and downloads** — ✅ **BUILT 2026-09-24** | A written document opens beside the board, as in `/chat` · the panel follows the session · Download PDF for a Markdown or HTML file · Download and Download PDF for a saved report, in the Reports app and on the chat's report card · the Files section of the instructions (§14) | AGENT-SAFE |
| **S9 · Entity pills** — ✅ **BUILT 2026-09-24** | A task, a project, a person, a status and a tag in a chat answer draw as a pill. A task or a project opens in this tab. A pill links only on exactly one match in the same message's tool results. The link colour, the made-up stat delta, the missing space before bold and the date that wrapped (§15) | AGENT-SAFE |
| **S7e · On-the-fly analysis** — ✅ **BUILT 2026-09-24** | `GET /projects/analytics/dataset` and the read tool `task_dataset`: a capped table, or the server's groups over the full set. The rule for numbers the chat computes itself (§13.7, §10.7) | AGENT-SAFE |
| **S10 · Chat follow-ups** — ✅ **BUILT 2026-09-25** | The checkpoint names its agent · the plan card edits after · a lost project write gives a receipt · the Throughput pin, if S7e is on main (§16) | AGENT-SAFE |
| **S11 · Outlook schedules** — ✅ **BUILT 2026-09-26** | The outlook's capacity reads each person's schedule. Absences apply only for an admin viewer. The report section passes the reader's grant. The panel says when it does not count leave (§17) | AGENT-SAFE |
| **S12 · Follow-ups** — ✅ **BUILT 2026-09-28** | The end-date count follows the grant (H-188) · one broken panel does not blank Analytics · an @name turn keeps the agent that ran (§18) | AGENT-SAFE |
| **S13 · Message integrity** — ✅ **BUILT 2026-09-28** | Only the run changes an agent reply, and the fold seals it · no client updates a system row · a declined write names its ids in unchanged · one migration, run_member_email and run_final_at (§19) | AGENT-SAFE |
| **S14 · No forged agent rows** — ✅ **BUILT 2026-09-28** | The server mints the agent row of a run when the run starts · no client inserts an agent row or a system row · an empty minted row stays hidden · a creator owns only a room with no rows, and migration 221 backfills every creator's owner row (§20) | AGENT-SAFE. D-PM-39 decided by the owner 2026-09-28 |
| **S15 · Chat is saved on production** — ✅ **BUILT 2026-09-29** | A JSON body to the gateway names its content type, so a save gets no 422 · each chat, room, fold, mint, run-trace and blob write binds the tenant · `_load_room` binds it too, so no member owns a room that they cannot see · an empty server answer keeps the browser cache · an R8 suite, a smoke check and an alarm (§21) | AGENT-SAFE. A live defect, audited by the diagnosis of 2026-09-29 |
| **Flip** | `NEXT_PUBLIC_PROJECTS_CHAT` on the box | `enforcement-flip`, granted until 2026-11-30 (`.claude/OWNER_GRANTS.md`, PR #487) |
| **Delete** | `delete_project`, `delete_task` from class X to C | Blocked on WS-40 |

### 10.1 Acceptance — S1

**Done when:**
1. `agent_registry.json` lists `projects-assistant`, and `GET /agent/list`
   returns it for a member with `feature:projects`.
2. Every class A tool calls the gateway with `X-User-Email` from the run
   ContextVar. A run with no user makes zero HTTP calls.
3. `manifest.py` classifies every route `router.routes` reports, and
   `test_projects_chat_coverage.py` passes. Removing one row makes it fail.
4. The rail renders in the `ai-chat` slot when the flag is on. The slot stays
   `preview` when it is off. `projectApps.test.ts` covers both.
5. "What is stuck in Marketing?" in the rail returns the server's numbers,
   and the card opens the Analytics app.

**Tests:** `tests/unit/test_projects_chat_coverage.py` ·
`tests/unit/test_projects_agent.py` (the recording fake client, copied from
`tests/unit/_crm_agent_fakes.py`) · `src/app/projects/lib/projectApps.test.ts`
· `src/app/projects/lib/assistantPersona.test.ts`.

### 10.2 Acceptance — S2 and S3

**Done when:**
1. Every class B and C tool awaits `request_confirmation` before any mutating
   request is built. A denied card makes zero mutating calls, and every call
   before the card is a `GET` or a preview the manifest records as writing
   nothing (`READ_ONLY_POSTS`, D-PM-29).
2. No class B or C tool passes `non_interactive_default="approve"`. The test
   asserts the absence in the source.
3. A class C tool given a list refuses before the card.
4. The class C card's first line carries the counts the route reads.
5. A write with `X-Actor-Via` lands `meta.via` on its activity row, against a
   real database (R8).

### 10.3 Acceptance — S7a

**Done when:**
1. The route returns one row for each assignee of open work in scope, and one
   Unassigned row. For the same scope, `open_tasks` on each row equals the
   Load route's count for that person. An R8 test proves this on a real
   database.
2. For a caller without `admin:members:read`, the response says
   `hr_visible: false`. It has no hours, absence, `end_date`,
   `max_concurrent_tasks` or at-risk keys. A test proves the keys are absent,
   not null.
3. If no open task on a row has an estimate, the row has no spare or committed
   hours, and it says why.
4. `horizon_days` changes the window. The default is 14, and the route
   refuses a value outside 1 to 90. The response prints both windows.
5. The at-risk list and the pill equal `workload.at_risk_tasks` and
   `workload.classify` for the same inputs. The People dashboard reads the
   same leaf module, and its tests still pass.
6. `capacity` is in `SECTIONS`, `_REPORT_SECTIONS`, `RenderedBody` and
   `reportEmail.ts`. One lockstep test fails when one of the four lacks it.
   `capacity` is not in `_DEFAULTS`.
7. `team_capacity` is class A and in `manifest.py`, and the coverage fence
   passes. A run with no user makes zero HTTP calls.
8. The panel draws from the route only, and counts nothing in the browser.
   Somebody looks at it in light mode, at compact density, under a changed
   accent, and beside Load.

### 10.4 Acceptance — S7b

**Done when:**
1. `GET /projects/tasks/{id}/candidates` returns at most 3 candidates, ranked
   by `rank_candidates` and by no second ranker. A source test asserts that
   the new modules import `rank_candidates` and do not call `score_skills`.
2. The match text is the title, the tag names and the capped description. A
   test shows that a skill named only in a tag ranks a person.
3. The pool follows §13.4 rule 4. A test shows a person with no open task as a
   candidate, and no assignee or agent in the list.
4. The window follows §13.4 rule 4, and the response prints it.
5. With no estimate for any candidate, every rank follows §13.4 rule 2. A
   test with a mixed list proves that `spare_hours` is absent on EVERY
   candidate, not zero, and `test_people_suggestions.py` passes unchanged.
6. Each of the three warnings in §13.4 rule 5 has its own test.
7. A caller without `admin:members:read` gets 200 with `hr_visible: false` and
   no `candidates` key. A test proves that the key is absent.
8. `GET /projects/candidates?title=&tags=&due=` returns the same body as the
   task route for the same text and due date. The test uses a task with no
   assignees, because the task route leaves its assignees out. A title shorter than 2
   characters gets 422.
9. `GET /projects/analytics/rebalance?project_id=&include_subtree=&horizon_days=`
   lists the at-risk tasks in scope with their helpers, and the idle people
   with the unassigned tasks in scope. The helpers and the idle people come
   from the rule 4 pool, so an idle person with no work in scope appears.
   Both suggester routes call the one leaf join. An R8 test proves that no task outside the viewer's grant
   appears. A caller without the grant gets no `at_risk` and no `pickups` key.
10. "Suggested" renders above the name search in `TaskBody` only, and hides
    when `hr_visible` is false. A pick goes through the existing `onPick`,
    which writes `PUT /projects/tasks/{id}/assignees`. A pure lib function
    holds the decisions, and a vitest covers it.
11. `fit_for_task` and `rebalance` are class A, and `manifest.py` maps the
    three routes. The coverage fence passes. A run with no user makes zero
    HTTP calls, and no tool sends a request that is not a GET.
12. The status header, the §10 row, the board row and the INDEX line say that
    S7b is built (R4).

### 10.5 Acceptance — S7c

**Done when:**
1. One fixture file drives both `dependency_conflict` (pytest) and
   `conflicts()` (vitest). If one case changes, both suites fail.
2. The route returns only the seven kinds. An R8 test builds each kind once
   on a real database.
3. A `blocks` link whose blocker the viewer cannot see gives no row. An R8
   test proves that the blocker's id and title are absent from the body.
4. A `relates_to` or `duplicates` link never gives a row. A closed blocker
   never gives `dependency_order` or `blocker_late`. A pair that is late and
   misordered gives one `blocker_late` row and no `dependency_order` row.
5. A blocker in a stopped project still gives a row. An R8 test proves it.
6. Without the HR grant, the body says `hr_visible: false` and carries none of
   the four HR kinds. A test proves that they are absent.
7. `absent_on_due`, `leaving` and `over_concurrency` agree with
   `candidate_warnings` for the same inputs, and `test_projects_candidates.py`
   passes unchanged.
8. The `overcommitted` rows equal `at_risk_tasks` for the same person and
   inputs, filtered to the scope.
9. `parallel_person` follows rule 6. Tests show no row for two tasks, for
   three tasks in one top-level project, for tasks whose spans share no day,
   or for a task with one date. Three tasks that are due today give a row. In a project scope, a person with one in-scope task and two
   visible tasks in other top-level projects on the same day gives one row. A
   test proves the cap of 5.
10. `horizon_days` outside 1 to 90 gets 422, and the response prints its
    window.
11. `conflicts` is in `SECTIONS` and not in `DEFAULT_SECTIONS`. The lockstep
    test passes, and a new opt-in test fails if `conflicts` enters the
    defaults.
12. `find_conflicts` is class A and in `manifest.py`, and the coverage fence
    passes. A run with no user makes zero HTTP calls.
13. The panel draws from the route only, and a lib test scans it for
    arithmetic. Somebody looks at it in light mode, at compact density, under
    a changed accent, and beside Capacity.
14. The status header, the §10 row, the board row and the INDEX line say that
    S7c is built (R4).

### 10.6 Acceptance — S7d

**Done when:**
1. `propose_plan` accepts `key`, `start` and `after`. It refuses each rule 7
   case before any card, and a test proves each case makes zero calls that are
   not GET.
2. A recording fake shows the rule 8 order, exactly one `_confirm`, and no
   call that is not GET outside the routes the manifest gives the tool.
3. Every link is on the confirm card. A card over the limit refuses.
4. When the fake refuses the fifth `POST /projects/tasks`, the receipt lists
   the created ids, names the failed row and counts the rows not tried. No
   delete or archive call runs.
5. Every `due_at` and `start_date` that the tool posts equals the submitted
   value, also for a pair that conflicts. The card carries the warning
   sentence (rule 3).
6. The preview route. Without the HR grant, the body says `hr_visible: false`
   and carries no fit or hours keys, and a test proves they are absent. With
   the grant, an owner with no skill match shows "no match", not an absent
   fit. Two plan rows for one owner mark the second when the hours run out.
   An R8 test shows that the hours equal `person_capacity` plus the plan rows,
   and that work outside the viewer's grant has no effect.
7. A source test asserts that the preview imports `dependency_conflict` and
   `rank_candidates`, and never calls `score_skills`.
8. A vitest over a pure lib function for the PlanCard: the submit carries
   `start`, `after` and the three scores, the fit and the hours are read-only,
   and a marked row shows its mark.
9. MANIFEST, `READ_ONLY_POSTS` and COMPOSITE (with `link_tasks` added) are
   updated, and `test_projects_chat_coverage.py` passes.
10. `instructions.md` W1 no longer asks for phases, and a test fails if the
    word comes back into W1.
11. The status header, the §10 row, the board row and the INDEX line say that
    S7d is built (R4).

### 10.7 Acceptance — S7e

Each item maps to the §13.7 rule with the same number.

**Done when:**
1. `GET /projects/analytics/dataset` lives in `analytics_dataset.py` and
   writes nothing. `task_dataset` is class A in `reads.py`, and it issues only
   that GET. `instructions.md` forbids `write_artifact`, `run_script` and
   `code_task` over the rows, and a test pins the sentence. The spec calls
   the fence advisory.
2. `state=open` uses `load_open_where` and `load_params`, and `closed` and
   `all` use Throughput's scope with `archived_at IS NULL`. An R8 test shows
   that the open total equals the `analytics/load` total for one scope. An
   unreadable project gives 404, and a hidden project's tasks are absent from
   the portfolio.
3. A source test asserts that the module imports `cycle_cte_sql` and has no
   `completed_at` subtraction. An R8 test shows that the median and the p90
   equal Throughput's figures for the same tasks.
4. An unknown column gives 422. `columns=` returns only the named columns, and
   the full set carries the tag names and the status names.
5. An R8 test proves that the id and the title of a hidden blocker are absent
   from the body, and that a visible blocker is present.
6. The default limit is 200. A limit above 500 or below 1 gives 422. An R8
   test shows `total`, `truncated` and the stable order at the cap.
7. An unknown `group_by`, an unknown `measure` or an unknown filter key gives
   422. R8 tests check the group values against hand-computed figures, the
   median and the p90 over the full set, and the HR gate. Without the grant,
   the value keys are absent for `assignee` with `estimate_sum` or a cycle
   measure. An agent group carries `agent: true`. Without the grant, no row
   has a `cycle_hours` or an `estimate_mins` key, also for `full_id,cycle_hours`
   alone, and `hidden_columns` names them. With the grant, every column comes
   back. Without the grant, a tag that only one person uses hides its value
   and keeps `n`. A group of 3 people shows its value, and the HR viewer
   sees every value. Mutations that restore the per-request rule, set K to 1
   or drop the K rule each fail an R8 test.
8. The tool trailer tells the model not to compute a figure over the whole
   set when `truncated=yes`. `instructions.md` carries the two labels, and a
   test pins them.
9. The three old sentences are gone from `instructions.md`, and tests pin the
   new ones. The S9 tests still pass.
10. Hermetic tests pin the header line, the pipe cells, the 80-character cut,
    the fence and the trailer, in both modes.
11. `__all__`, `own_tool_scope` and MANIFEST carry `task_dataset`. The
    `agents.py` description names capabilities, not tools. So it names the
    capability ("a table of tasks or the server's exact groups over them")
    and not the tool name (corrected in S10, §16).
    `test_projects_chat_coverage.py` and `test_projects_agent.py` pass.
12. The status header, the §10 row, the board row and the INDEX line say that
    S7e is built (R4).
13. Amended 2026-09-28 (`projects_reports.md` §9, Q10). The groups by
    assignee follow §7.1 of `projects_reports.md`. R5d of that spec holds
    the rules and the tests.

---

## 11. Verification

Run from the repo root, with a database up. R8: with no database, 843 tests
skip and the run still reads green.

```
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_agent.py
uv run pytest tests/unit/test_tenant_coverage.py
uv run python tests/live/live_actor_via.py   # D-PM-36 on a real row
```

For S7a, with `TENANT_LADDER_DATABASE_URL` set, because the R8 tests skip
without it and the run still reads green:

```
uv run pytest tests/unit/test_projects_analytics_capacity.py tests/unit/test_people_dashboard.py tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_agent.py tests/unit/test_projects_reports.py tests/unit/test_projects_report_sections_lockstep.py tests/unit/test_projects_reportable_reports.py
```

For S7b, with the same database settings:

```
uv run pytest tests/unit/test_projects_candidates.py tests/unit/test_projects_analytics_rebalance.py tests/unit/test_people_suggestions.py tests/unit/test_people_dashboard.py tests/unit/test_projects_assignees.py tests/unit/test_projects_analytics_capacity.py tests/unit/test_projects_routes.py tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_agent.py
```

In `workbench/control_plane`, run `npx tsc --noEmit` and
`npx vitest run src/app/projects/lib/assignees.test.ts src/app/projects/lib/candidates.test.ts`.
Then look at the task panel's picker in light mode, at compact density, under
a changed accent, and beside the bulk bar's picker. The slice creates the two
new pytest files and `candidates.test.ts`.

For S7c, with the same database settings:

```
uv run pytest tests/unit/test_projects_analytics_conflicts.py tests/unit/test_projects_candidates.py tests/unit/test_projects_analytics_capacity.py tests/unit/test_projects_relations.py tests/unit/test_projects_report_sections_lockstep.py tests/unit/test_projects_reports.py tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_agent.py
```

In `workbench/control_plane`, run `npx tsc --noEmit` and
`npx vitest run src/app/projects/lib/timeline.test.ts src/app/projects/components/TimelineView.test.ts src/app/projects/lib/conflicts.test.ts src/lib/reportEmail.test.ts`.
The timeline's copy of the rule must not change. The slice creates `test_projects_analytics_conflicts.py`, `gateway/conflicts.py`,
`lib/conflicts.ts`, `lib/conflicts.test.ts` and the fixture file.

For S7d, with the same database settings:

```
uv run pytest tests/unit/test_projects_agent_writes.py tests/unit/test_projects_agent.py tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_candidates.py tests/unit/test_projects_analytics_capacity.py tests/unit/test_projects_analytics_conflicts.py tests/unit/test_projects_relations.py tests/unit/test_projects_plan_preview.py
```

In `workbench/control_plane`, run `npx tsc --noEmit` and
`npx vitest run src/components/genUITemplates.test.ts src/app/projects/lib/planCard.test.ts`.
Then look at the plan card in light mode, at compact density, under a changed
accent, and beside the board. The slice creates `test_projects_plan_preview.py`
and `planCard.test.ts`.

For S8, with the same database settings:

```
uv run pytest tests/unit/test_pdf_render.py tests/unit/test_documents_pdf_route.py tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_agent.py tests/unit/test_genui_catalog_lockstep.py tests/unit/test_projects_reports.py tests/unit/test_projects_report_sections_lockstep.py tests/unit/test_projects_reportable_reports.py
```

In `workbench/control_plane`, run `npx tsc --noEmit` and
`npx vitest run src/lib/autoOpenArtifact.test.ts src/lib/artifactKind.test.ts src/lib/reportEmail.test.ts src/app/projects/lib/reportFiles.test.ts src/app/api/documents/pdf/pdf.test.ts`.
Then look at the report card in the rail, and at the Reports app, in light
mode, at compact density and under a changed accent. The slice creates the
two pytest files, the three new vitest files, `gateway/pdf_render.py`,
`routes/documents.py` and `lib/autoOpenArtifact.ts`.

For S9, with the same database settings:

```
uv run pytest tests/unit/test_projects_agent.py tests/unit/test_projects_chat_coverage.py tests/unit/test_genui_catalog_lockstep.py tests/unit/test_projects_agent_writes.py
```

In `workbench/control_plane`, run `npx tsc --noEmit` and
`npx vitest run src/lib/remarkEntityPills.test.ts src/lib/entityIndex.test.ts src/components/ui/EntityPill.test.ts src/components/markdownPills.test.ts src/components/genUITemplates.test.ts src/lib/theme/`.
Then look at an answer with pills in the rail and in `/chat`. Look in light
mode, at compact density, under a changed accent and at 390px. The slice
creates the four new vitest files, `lib/remarkEntityPills.ts`,
`lib/entityIndex.ts`, `lib/projectToolRows.ts`, `ui/EntityPill.tsx` and
`ChatEntityPill.tsx`.

For S7e, with `TENANT_LADDER_DATABASE_URL` set:

```
uv run pytest tests/unit/test_projects_analytics_dataset.py tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_agent.py tests/unit/test_projects_agent_writes.py
```

S7e changes no frontend file, so it has no vitest command. If a later change
touches the chart templates, run `npx tsc --noEmit` and
`npx vitest run src/components/genUITemplates.test.ts` in
`workbench/control_plane`. The slice creates
`routes/projects/analytics_dataset.py` and
`tests/unit/test_projects_analytics_dataset.py`. The tool lives in
`reads.py`.

For S10, with `DATABASE_URL` and `TENANT_LADDER_DATABASE_URL` set to the
same tenant database. `test_rooms.py` reads `DATABASE_URL`, and its R8 tests
skip without it:

```
uv run pytest tests/unit/test_projects_agent_writes.py tests/unit/test_projects_agent.py tests/unit/test_projects_chat_coverage.py tests/unit/test_genui_catalog_lockstep.py tests/unit/test_projects_plan_preview.py tests/unit/test_rooms.py tests/unit/test_chat_hardening.py tests/unit/test_projects_analytics_throughput.py
uv run ruff check apps/skills/skill-projects/skill_projects/forms.py
```

In `workbench/control_plane`, run `npx tsc --noEmit` and
`npx vitest run src/lib/assistantCheckpoint.test.ts src/app/projects/lib/planCard.test.ts src/components/genUITemplates.test.ts src/components/entityPillsAuthor.test.ts src/components/entityPillsGate.test.ts src/lib/theme/`.
Then look at the "Waits on" control on the plan card in light mode, at
compact density, under a changed accent, and beside the board. The slice
creates `lib/assistantCheckpoint.ts` and `lib/assistantCheckpoint.test.ts`.

`test_projects_report_sections_lockstep.py` is the lockstep test of §10.3
item 6. It is a pytest that reads the two TypeScript files as text, so one test
covers the Python and the TypeScript halves.

In `workbench/control_plane`:

```
npx tsc --noEmit
npx vitest run src/app/projects/lib/projectApps.test.ts src/app/projects/lib/assistantPersona.test.ts
npx vitest run src/app/projects/lib/capacity.test.ts src/lib/reportEmail.test.ts src/app/projects/lib/analyticsRead.test.ts
```

`capacity.test.ts` is the Capacity panel's own test. The panel cannot render
in this node-env suite, so its decisions live in `lib/capacity.ts`. The test
also scans the panel and that module for arithmetic (§10.3 item 8).

Then the check no test makes (`DESIGN_SYSTEM.md` §8). Look at the rail in
light mode, at compact density, under a changed accent, and beside the board.

---

## 12. Open questions for the owner

1. **Delete.** Accept D-PM-35, or take the interim gate on
   `projects:settings:write`. §5.4 gives the case against the interim.
2. **Grants.** `POST /projects/nodes/{id}/grants` writes visibility. CLAUDE.md
   §3a rule 3 stops an agent from writing a live organization's membership.
   A grant is narrower than membership, and this spec puts it in class X
   until the owner says otherwise.
3. **The tier.** `tier-balanced` by default, or `tier-powerful` as the Tasks
   rail chose. The cost difference is real once H-42 prices the card.

---

## 13. Team intelligence — plan, assign, capacity, conflicts (S7)

**Owner directive, 2026-09-23.** The chat must plan projects and work out who
should take a task. It must know what each person on the team can do. It must
find where work overlaps and interferes.

Every reusable insight must also appear in the
Analytics and Reports apps. The server computes what is common. The agent
computes only what is uncommon, on the fly.

### 13.1 The answer

**Most of the arithmetic exists, and the chat reaches almost none of it.**
The People app owns it. The chat's manifest allows only `/projects/*`. One
route already crosses: `people_for` reads `GET /projects/assignees`, which
gives an HR-tier caller contracted hours, load, top skills and end-date
warnings (`assignees.py:22-26`). But it matches people by name, it ranks
nobody, it has no window, and it sees no conflicts. S7 follows its precedent:
the HR gate, and a function-local import of the People helpers.

| Question | The seam that already answers it | Where it lives today |
|---|---|---|
| How many hours does a person have? | `person_schedule`, `contracted_hours_per_week`, `working_hours_between` (absences subtracted) | `gateway/work_schedule.py` |
| Does their dated work fit before each due date? | `at_risk_tasks` (cumulative), `classify` (the five pills) | `gateway/workload.py` |
| Who fits this task? | `score_skills` (level × recency), `rank_candidates` (skill × spare hours × away) | `routes/people/search.py`, `routes/people/suggestions.py` |
| Who should help whom? | The rebalancing suggester | `routes/people/suggestions.py` |
| Does a task start before its blocker ends? | `conflicts()` | `src/app/projects/lib/timeline.ts:670`, the browser only |

**S7 exposes these seams under `/projects/*` and adds no second arithmetic.**
Each new route calls the functions above. The Analytics panel, the report
section and the chat tool all read the same route, so the three cannot
disagree.

### 13.2 Rules that bind every S7 route

1. **One arithmetic.** Hours come from `work_schedule.py`. Fit comes from
   `score_skills` and `rank_candidates`. Pills come from `workload.classify`.
   A route that computes any of these again is a defect.
2. **The viewer's scope.** Task counts use `task_visibility_clause` and
   `reportable_with_ancestors_clause`, as `analytics.py` does. A rollup is not
   a licence to see work the viewer cannot open.
3. **The HR tier is gated.** Skills, contracted hours, spare hours, absences,
   end dates and `max_concurrent_tasks` need `admin:members:read`
   (`can_read_hr_fields`, `people_center_app.md` §4.2). Without it, a route
   returns the task half only and says `hr_visible: false`. Then the chat
   says that an admin can see capacity. It does not guess.
4. **No estimate, no hours.** If no open task carries an estimate, the
   route omits the hours-based figures and says so. `classify` already does
   this. A zero is never shown as "free".
5. **The window is explicit.** Each route takes `horizon_days` (default 14,
   the People dashboard's `HORIZON_DAYS`) and prints the dates it covered.

### 13.3 S7a — Capacity

`GET /projects/analytics/capacity?project_id=&include_subtree=&horizon_days=`

The route returns one row for each person who holds open work in scope, and
one row for unassigned work. A row carries these figures:
- Open tasks, overdue tasks, and estimated hours left.
- Contracted hours in a week, and working hours in the window with absences
  subtracted.
- Committed hours in the window, and spare hours.
- Absences in the window, and an end date before the window closes.
- The at-risk tasks, with the shortfall on each.
- The pill and its reason, from `classify`.
- Tasks in progress against `max_concurrent_tasks`.

**Four rules for the build.**
1. **Counts use the Load predicate.** The route builds its task counts from
   `analytics.py`'s `scope_clause` and the `open_where` of `load`. It does not
   use the People dashboard's `_OPEN` and `project_clause`. Otherwise the
   Capacity panel and the Load panel beside it disagree about open work.
2. **The row arithmetic moves to a leaf module.** `dashboard.py::_row` fixes
   its window and reports spare hours when nothing is estimated. Its
   arithmetic moves to `gateway/capacity.py`, outside both route packages,
   with a `horizon_days` input. The People dashboard and this route both call
   it. A `/projects` route imports People helpers inside the function, as
   `assignees.py` does, to avoid the import cycle.
3. **Two windows, named.** The pill is the People dashboard's pill:
   `classify` compares this Monday-to-Sunday week with contracted hours.
   Spare hours and at-risk tasks use the `horizon_days` window. The response
   carries both windows.
4. **`capacity` is an opt-in report section.** It goes into `SECTIONS` and
   not into `_DEFAULTS`. Otherwise every saved report with no `sections` key
   starts to show capacity.

**Surfaces.** The Analytics app gets a Capacity panel beside Load. Reports get
the section kind `capacity`, in `reports.py` `SECTIONS`, `RenderedBody`,
`reportEmail.ts` and the chat's `_REPORT_SECTIONS`. The chat gets
`team_capacity`, class A.

**As built, 2026-09-23.** Six facts that the rules above do not say.
- **Two scopes on one row.** The row's task half is this scope, and it is
  Load's count. The hours read every open task the caller can see, in any
  project, so a person busy elsewhere shows no spare hours here. Owner
  direction. `all_work` carries the counts that the hours measure.
- **The People dashboard keeps its output.** It calls `person_capacity`, and
  it still reports spare hours when no task has an estimate, with
  `hours_basis` beside them. Only the capacity route drops those keys (§13.2 rule 4).
- **The HR half also carries the top skills** from `people_skills`, strongest
  level first. Owner direction.
- **`skill_projects/writes.py` `REPORT_SECTIONS` is a fifth list.** The chat
  saves a report through it, so the lockstep test holds it too.
- **Load leads the Analytics panel grid**, so Capacity sits beside it. The
  node dashboards already lead with Load. The route lives in
  `routes/projects/analytics_capacity.py`, and Load's predicate is the named
  `analytics.load_open_where`, which both routes call.
- **One dated bound for two callers.** `gateway/capacity.py` `dated_until`
  reaches this Sunday for the pill and includes the horizon's last day. The
  People dashboard and the capacity route both read it. Review round 1 found
  a one-day horizon that hid Friday's work from the pill.

### 13.4 S7b — Fit and rebalancing

`GET /projects/tasks/{id}/candidates` ranks people for one task by
`rank_candidates`. The draft form is
`GET /projects/candidates?title=&tags=&due=`.
It ranks people for a task that does not exist yet, which planning needs. Each
candidate carries the skills that matched and the spare hours before the due
date. It also carries the warnings: away on the due date, leaving before it,
or over `max_concurrent_tasks`.

`GET /projects/analytics/rebalance` is the People suggester scoped to a
project subtree. It lists the at-risk tasks with the helpers who fit them, and
the idle people with the unassigned work that fits them.

**Surfaces.** The assignee picker in the task panel shows "Suggested" above the
name search. The chat gets `fit_for_task` and `rebalance`, both class A.
Assigning stays the class B `assign` with its card.

**Rules for the build.** The S7b audit (2026-09-23) found six decisions that
the text above does not make. These are the decisions.

1. **No HR grant, no candidates.** A caller without `admin:members:read` gets
   200, `hr_visible: false`, and no `candidates` key. A ranked list of names
   still says who holds which skill, so names alone are an oracle
   (`people_center_app.md` §4.2). The picker then hides "Suggested". The
   rebalance route leaves out `at_risk` and `pickups` in the same way.
2. **No estimate, rank by skill and availability — for the whole list.** If
   `hours_basis` is false for ANY candidate, the route calls
   `rank_candidates` with a neutral spare figure of 1 for EVERY candidate. So
   every rank is skill × away, and the ranks stay comparable. A mixed list
   would put a person with no basis 30 times below a person with 30 spare
   hours, from a figure the reader cannot see. The response then leaves out
   `spare_hours` for every candidate and carries one `hours_note`. `rank_candidates` itself does not change, and
   `test_people_suggestions.py` passes unchanged.
3. **The match text.** It is the title, the tag names, and the first 500
   characters of the description. A tag often names the skill that the title
   does not. A long description mentions skills in passing, so it is capped.
4. **The pool and the window.** The pool is every active person in `people`
   with an email. The task's assignees and agents are left out. Spare hours
   come from `person_capacity` over all the work the caller can see. The
   horizon is the days from today to the due date, clamped to 1 to 90, or 14
   days if the task has no due date. The response prints the window.
5. **Warnings wrap the candidate.** The route wraps each `Candidate` with a
   `warnings` list: away on the due date, an end date before the due date,
   and more tasks in progress than `max_concurrent_tasks`. The `Candidate`
   model in `suggestions.py` does not change.
6. **One join for the suggester.** The join of at-risk tasks to helpers, and
   of idle people to unassigned work, moves into a leaf function in
   `gateway/capacity.py`. `/people/dashboard/suggestions` and
   `/projects/analytics/rebalance` both call it. The rebalance route filters
   the at-risk tasks to its scope, and takes the unassigned tasks from the
   Load `scope_clause`. The helpers and the idle people come from the rule 4
   pool. A person is idle when `person_capacity` gives the pill `idle`, over
   all the work the caller can see.

**Names.** The chat tool is `fit_for_task`, because `suggest_assignees` is
already the name of the picker's route function in `assignees.py`. "Suggested"
renders in `TaskBody` only, never in `BulkBar` or `MoveTasksDialog`, because
it needs one task.

**As built, 2026-09-24.** Seven facts that the rules above do not say.
- **Where each part lives.** The two candidates routes are in
  `routes/projects/candidates.py`, and the rebalance route is in
  `routes/projects/analytics_rebalance.py`. `pool_capacity` reads the rule 4
  pool with the S7a capacity SQL. `rank_for_text` applies rule 2 over
  `rank_candidates`, and both routes rank through it.
- **Who "any candidate" is (rule 2).** It is a pool person whose skills match
  the text. `rank_for_text` finds them with `rank_candidates` itself, one
  person at a time with the neutral figure. So neither new route module
  scores a skill. The pickup match inside `rebalance_join` still calls
  `score_skills`, because it is the People suggester's own loop, moved.
- **The pool excludes alumni and keeps active people.** A contractor or an
  invited person is not in the pool. The rebalance route still reads the
  at-risk work of every directory person who is not alumni.
- **Availability.** For one task, `away` is an absence on the due date. For
  an overdue task, or a task with no due date, it is an absence today. For the rebalance route it is today, as on
  the People dashboard. The picker prints one absence once.
- **The rebalance route follows rules 2 and 3 too.** Each at-risk task ranks
  its helpers through `rank_for_text` over the rule 3 match text. A task whose
  helpers lack an hours basis shows no `spare_hours` and one `hours_note`.
- **The People suggester keeps its output, except for a shared task.** It
  calls `rebalance_join` with plain `rank_candidates` over the title. A
  comparison of 400 random boards gave the same JSON before and after the
  move, and `test_projects_analytics_rebalance.py` pins one board by hand.
  One change is on purpose (review round 1). The join lists a task with two
  holders ONCE, and no holder helps on it, as rule 4 says. Before, the task
  appeared once for each holder, and each holder helped the other.
- **The first two warnings need a due date.** With no due date there is no day
  to be away on or to leave before. The concurrency warning needs none.

### 13.5 S7c — Conflicts

`GET /projects/analytics/conflicts?project_id=&include_subtree=&horizon_days=`
returns one list.
Each row carries its kind, the tasks and people it names, and one sentence
that says why.

| Kind | Rule | HR tier |
|---|---|---|
| `dependency_order` | A task starts or is due before a task that blocks it is due | no |
| `blocker_late` | A blocker is overdue, and the work it blocks is still open | no |
| `parallel_person` | On one day, one person holds 3 or more open tasks whose start-to-due spans all cover that day, in 2 or more top-level projects | no |
| `overcommitted` | A person's dated work does not fit before a due date (`at_risk_tasks`) | yes |
| `absent_on_due` | A task is due on a day its assignee is away (`absent_on`) | yes |
| `over_concurrency` | A person has more tasks in progress than `max_concurrent_tasks` | yes |
| `leaving` | An assignee's end date is before the task's due date | yes |

**The dependency rule moves to the server.** Today it exists only in
`timeline.ts`. The route owns it, and the timeline keeps its copy for live
feedback while the member drags. One fixture file of cases runs against both,
so the two cannot drift.

**Surfaces.** A Conflicts panel in the Analytics app. The report section kind
`conflicts`. The chat tool `find_conflicts`, class A.

**Rules for the build.** The S7c audit (2026-09-24) found twelve decisions
that the text above does not make. The owner took four decisions on
2026-09-24: one in rule 4, one in rule 5 and two in rule 6. The rest follow
the S7a and S7b precedent.

1. **The query.** The route takes `project_id`, `include_subtree` and
   `horizon_days`. It uses `check_horizon` (1 to 90, default 14) and
   `scope_clause`, as S7a does.
2. **One pure predicate, one fixture.** `gateway/conflicts.py` holds a pure
   `dependency_conflict(blocker, blocked)`. It is the rule of
   `timeline.ts:670`: a conflict is `interval(blocker).to` strictly after
   `interval(blocked).from`, so a shared day is the normal handover. A task
   with one date uses it for both ends, a due date before the start date is
   swapped, and a completed blocker never conflicts. Only `blocks` links
   count, from `source_task_id` to `target_task_id`.
   `tests/fixtures/projects_dependency_conflicts.json` pins these cases, and
   both the pytest and `timeline.test.ts` read it. Every `due_at` in the
   fixture is noon UTC, so no case depends on the machine's time zone. The
   timeline's own behaviour does not change.
3. **The server's day.** The day of `due_at` is its UTC date, as `_due` and
   `workload.as_date` read it. "Today" is `date.today()`, as S7a and S7b use.
   The UI writes due days at noon local time, so the browser and the server
   agree within 11 hours. S7c makes no other change to this.
4. **Dependency scope and visibility.** The blocked task is open and in scope
   (`load_open_where`). The blocker passes `task_visibility_clause` and is not
   archived, in any project. A blocker the viewer cannot see gives no row, and
   no count says that a row was dropped. **A blocker in a stopped project
   still counts** (owner, 2026-09-24): the blocked task still waits on work
   that nobody is doing.
5. **A late blocker gives one row.** `blocker_late` means the blocker is open
   and its `due_at` is before `now()`, the predicate of Load's overdue. **If a
   pair is both late and misordered, it gives only `blocker_late`** (owner,
   2026-09-24). That is the fact to act on, and it implies the order problem.
6. **`parallel_person` needs three.** Only open, visible tasks with both a
   start date and a due date count. A span covers the days from its start to
   its due day, and both days count. **The row fires when, on one day, a person holds 3 or more such
   tasks, in 2 or more top-level projects** (owner, 2026-09-24). A sub-project
   counts as its root project, so the key is `root_project_id`. The route
   counts a person's tasks over ALL the work the caller can see, as rule 8
   does, because a project's own scope has only one root and the kind could
   never fire there. The row fires only when at least one task on that day is
   in scope, and it lists the in-scope tasks first. Agents are left out. A
   person gives at most one row. It names the busiest day, the tasks on that
   day up to 5, and `tasks_total`.
7. **The HR kinds reuse S7b.** `candidate_warnings` splits into predicates
   that return a kind, and its string output stays byte-identical, so
   `test_projects_candidates.py` passes unchanged. `absent_on_due` uses
   `absent_on` on `availability_day`, so a partial absence counts and an
   overdue task checks today. `leaving` means `end_date` before the due day.
   `over_concurrency` uses the S7a `in_progress` count over all visible work.
8. **`overcommitted`.** The route calls `person_capacity` over all the work
   the caller can see, as S7a does. It gives one row for each at-risk task in
   scope, with its shortfall. The sentence says that the hours include work in
   other projects.
9. **The HR gate.** Without `admin:members:read`, the four HR kinds are absent
   and the response says `hr_visible: false`, as S7a and S7b do.
10. **The window.** `horizon_days` bounds the kinds that depend on a date:
    `absent_on_due`, `leaving`, `overcommitted`, and `parallel_person` days in
    the window. An overdue task is always in the window, because rule 7 checks
    it today, and that is the row that matters most. `dependency_order` and `blocker_late` ignore it, because a
    wrong order is wrong whenever it falls.
11. **Shape and cap.** Each row carries `kind`, `task_ids`, `people`,
    `sentence` and `severity`. `severity` is `high` when a due date is at
    stake (`blocker_late`, `overcommitted`, `absent_on_due`, `leaving`), and
    `medium` for the rest (`dependency_order`, `over_concurrency`,
    `parallel_person`). No other value exists. The response keeps at most 200 rows, carries
    `total` and `by_kind`, and sorts by kind, then by due day, with a stable
    sort. The report section caps like `load`.
12. **Report and chat.** `conflicts` goes into `SECTIONS`, not into
    `DEFAULT_SECTIONS`. `find_conflicts` is class A and GET only. The manifest
    row is `Route("GET", "/projects/analytics/conflicts", "find_conflicts", "A")`.

**Two known weak spots the server must not copy.** `TimelineView.tsx:1496`
checks only the first blocker of a row, and `conflicts()` reads the browser's
local day. The route checks every visible blocker, and reads the UTC day.

**As built, 2026-09-24.** Eight facts that the rules above do not say.
- **Where each part lives.** The route is
  `routes/projects/analytics_conflicts.py`. The pure rules are in the leaf
  module `gateway/conflicts.py`: the dependency rule, the parallel day, the
  severity, the sort and the cap. The S7b warnings split into
  `away_warning`, `leaving_warning` and `concurrency_warning`, and
  `warning_kinds` returns each one with its kind.
- **A parallel span includes its due day** (owner, review round 1). Work
  that is due today is the parallel work that matters most. The first build
  stopped each span one day early, and then three tasks due today gave no
  row. The dependency rule keeps its handover day, because that rule is about
  order and this one is about load. The rule is in one function,
  `parallel_days`.
- **The row carries more than rule 11 names.** `due_on` is the day that the
  sort reads. For `dependency_order` it is the first day of the blocked task.
  For `blocker_late` it is the due day of the blocker. `parallel_person` adds
  `day` and `tasks_total`, and `overcommitted` adds the three hours figures.
  `people` holds an address and a directory name for each person.
- **The response names its own vocabulary.** `kinds` lists the kinds that
  this caller may see, and `by_kind` has one key for each of them. Without
  the HR grant, no HR kind name is in the body at all.
  `window.ignored_by` names the two dependency kinds.
- **The dependency kinds hide what no board shows** (review round 1). A
  blocker in triage gives no row, because a parked task is on no board. A
  row names no agent address. When a blocker's due date is before its start,
  the sentence names its start date as the end, and says so.
- **The HR kinds read the holders that the directory knows.** A holder with
  no `people` row has no schedule, absences or end date, so that holder
  gives no HR row. `over_concurrency` names the tasks in progress in scope.
- **The report section caps at 20 rows**, the cap of `load`, and it carries
  `total` and `by_kind`. The chat fences each sentence, because a sentence
  carries task titles.
- **The panel draws 12 rows**, then says how many the server sent. Severity
  uses the status vocabulary: `high` is the destructive token and `medium`
  is the warning token. The dot carries the severity, and the label stays in
  the foreground colour. The visual review found warning text too faint on a
  light card.

### 13.6 S7d — Plan with capacity

`propose_plan` gains two inputs and one check:
- A start date on each task, optional.
- Dependencies, written as `blocks` links after the tasks exist, under the
  same one card.
- For each named owner, the fit from §13.4 and the hours from §13.3, printed
  on the plan card row. The card marks a row whose owner lacks the skills or
  the hours.

The plan still creates everything under one card, in one batch.

**No phases (owner, 2026-09-24).** The first draft of this section created
phases as parent tasks. The owner declined them. The Projects app already has
stages, which are the workflow steps a task moves through (Backlog, To do, In
progress, Done). For a team of this size, start dates and dependencies carry
the order of a plan, and the Timeline draws it. A plan big enough to need
grouping uses sub-projects, which exist already. A phase would also add a
parent task that needs an owner and that the analytics would count as work.

**Rules for the build.** The S7d audit (2026-09-24) found these decisions. The
owner took rules 1, 2 and 3 on 2026-09-24.

1. **Fit for the named owner** (owner). Each row shows the fit of the owner
   the plan names: the matched skills, the spare hours and the warnings. If
   the owner matches no skill, the row says so. It does not fall back to the
   top 3 of §13.4.
2. **Hours across the plan** (owner). For each owner, the route adds the
   owner's plan rows to their existing visible work, in due-date order, and
   walks them with `at_risk_tasks`. So five rows that each fit alone, and
   together do not, mark the rows where the hours run out.
3. **A mark warns and never blocks** (owner), as D-PM-12's arrow does. The
   member can still create the plan. The confirm card repeats every mark,
   after the server computes the marks again for the owners the member
   edited.
4. **No HR grant, no fit.** A planner without `admin:members:read` sees no
   fit, no hours and no mark. The card says in one line that an admin can see
   capacity (§13.2 rule 3).
5. **One read: `POST /projects/plan/preview`.** It takes the rows and returns,
   for each row, the fit and the hours across the plan, and the dependency
   warnings from `gateway/conflicts.dependency_conflict`. It writes nothing,
   so it goes into `READ_ONLY_POSTS`. One call serves the whole plan, which
   avoids one full-pool read per row, and the skill never carries its own copy
   of the dependency rule.
6. **Stable row keys.** Each row carries a `key`, and `after` lists the keys
   of the rows that block it. If the member drops a row, its links drop too,
   and the confirm card lists the dropped links.
7. **Refuse before the card, with zero writes,** for a start date after the
   due date, an unknown key in `after`, a row that blocks itself, or a
   `blocks` cycle (a pure topological check). The server's
   `assert_no_block_cycle` stays as the fence behind it.
8. **The write order.** The project node, then the tasks with `start_date`,
   then the assignees, then the links. `MAX_BATCH` (50) counts the tasks. If
   the confirm card body would pass the card's 4000-character limit, the tool
   refuses, because a cut card is not consent.
9. **A partial failure stops and says what exists.** At the first refusal the
   tool stops. The receipt lists every task it created with its `full_id`,
   names the row that failed, and counts the rows it did not try. It archives
   nothing by itself, because that would be a class C act with no card.
10. **The same checks as `create_task`.** Owners pass `_unknown_addresses`
    (H-162). Impact, urgency and effort go back and forth through the submit,
    so the sort score after the submit is the score the card showed.
11. **Lockstep.** The planCard catalog line changes together with the
    component (`genUITemplates.test.ts`). The receipt in `ProjectToolCards.tsx`
    and step 3 of W1 in `instructions.md` change in the same slice.

**As built, 2026-09-24.** Fourteen facts that the rules above do not say. The
dispatch named five gaps, and three of them change what a member sees.
- **Where each part lives.** The read is
  `routes/projects/plan_preview.py`. The tool is `forms.py` `propose_plan`.
  The card's decisions are in `lib/planCard.ts`, and `PlanCard` in
  `genUITemplates.tsx` draws them.
- **A full owner keeps the skill match** (gap 1, member-visible).
  `rank_candidates` drops a person with no spare hours. So the preview
  ranks the named owner with the neutral spare figure 1, as `rank_for_text`
  does. The row shows the skills, and the hours tell the member separately
  that the owner is full.
- **No estimate on the owner's other work** (gap 2, member-visible). The
  plan rows carry an effort, so the walk always has a basis. When none of
  the owner's other open work carries an estimate, the row keeps its hours
  and carries one note. That row shows no spare hours, because a spare
  figure there reads as free time.
- **The request** (gap 3). The body is
  `{rows: [{key, title, owner, effort_mins, start?, due, after?}]}`. The
  route refuses these with 422 before a session opens: more than 50 rows, a
  bad or repeated key, a start after the due date, and an `after` key that
  names no row. A body key
  that the model does not declare gets 422, so a body cannot name a member
  or a tenant (R5).
- **The window** runs from today to the plan's last due date, clamped to 1
  to 90 days, and the body prints it. A row due after the window, or before
  today, says that the walk did not check it.
- **Two reads.** The tool reads the preview before the plan card, and again
  after the submit. The second read covers the whole submitted plan, not
  only the owners the member edited. A dropped row changes the hours of the
  rows beside it.
- **Owners resolve twice.** Before the plan card, a name that does not
  resolve is not a refusal. The row says "owner not resolved", and the
  member can correct it. After the submit, the strict resolution of
  `create_task` applies.
- **A refused preview is not a refusal.** The plan card then says that
  the tool could not check capacity, and the member can still create the plan.
  A lost connection on the preview does the same (review round 1).
- **An owner that did not resolve is marked for every planner** (review
  round 1). A name is picker data that every member reads, so the mark
  "owner not resolved" does not need the HR grant or a preview.
- **A lost connection mid-batch gives the partial receipt too** (review
  round 1). The `stopped:` line then says that the write may or may not
  have landed, and asks the member to read the project before a retry.
- **The effort of a row is 0 to 129600 minutes**, the bound of the preview
  route. The tool refuses a row outside it before the card (review round 1).
- **The partial receipt.** A `stopped:` line makes the receipt card read
  "stopped part way", in the warning tone. It still opens the first task.
- **The card lays out a block for each task**, not a table (visual review).
  The rail beside the board is narrow, and a five-column table cut the title
  and the owner to a few letters. The card prints each warning about order
  in plain words. The confirm card keeps the fence.

### 13.7 S7e — On-the-fly analysis

`task_dataset(project_id, filters, columns)` returns a compact table of up to
500 tasks in the viewer's scope. The columns are in rule 4. The agent uses it
for an uncommon question, for example cycle time by tag or the share of work
each stage holds. For a figure over the whole set, the server groups the rows
and computes the figure (rule 7). The chat explains the figure. It does not add
rows up itself.

**The rule for a number the chat computes.** The answer says that the chat
computed it, and from how many rows. If the table was truncated, the chat does
not compute a total from it. A figure that people ask for twice is a
candidate for a server read and a report section, and the chat says so.

**The owner's answers, 2026-09-24.** The S7e audit returned NO-GO, because
three product decisions were open. The owner answered all three on
2026-09-24.

- **O1 · No code execution over member data.** The chat writes no dataset file
  and runs no script. The server computes. The instructions forbid
  `write_artifact`, `run_script` and `code_task` over the dataset rows. **This
  fence is ADVISORY** (R7). Those three are floor tools, and a tool scope
  cannot remove them. No test can stop the model from calling them.
- **O2 · The server groups the data.** The route takes an optional `group_by`
  from an allowlist and a `measure` from an allowlist. The server returns
  exact figures. The model picks figures and explains them. It never adds
  rows up itself.
- **O3 · Per-person counts for every member, per-person speed for admins
  only.** A member may group by assignee to count tasks. `estimate_sum` and
  the two cycle measures, grouped by assignee, need `can_read_hr_fields`
  (`admin:members:read`). Without the grant, those keys are ABSENT, not null,
  and the response says `hr_visible: false`. This is §13.2 rule 3.
  Amended 2026-09-28 (`projects_reports.md` §9, Q10). A member counts tasks
  by assignee only for the people that §7.1 allows.
- **O4 · Token cost** is advisory. A full table of 500 rows costs many tokens.
  The tool asks for a short default column set, and a grouped read costs one
  line for each group. H-42 prices the tiers. No slice measures this cost.

**Rules for the build.** The spec-auditor wrote these on 2026-09-24, after the
owner's answers. They follow the S7c and S7d precedent.

1. **One read and no file.** The route is
   `GET /projects/analytics/dataset?project_id=&include_subtree=&state=&columns=&limit=&group_by=&measure=&<filters>`,
   in `routes/projects/analytics_dataset.py`. It is the class A tool
   `task_dataset` in `reads.py`, with the manifest row
   `Route("GET", "/projects/analytics/dataset", "task_dataset", "A")`. The
   tool writes nothing. The instructions forbid `write_artifact`,
   `run_script` and `code_task` over its rows (ADVISORY, O1).
2. **The scope is Load's or Throughput's, never a third.** `state=open` is
   exactly `load_open_where` plus `load_params`. That carries the D-PM-32(b)
   stopped-project exclusion. `closed` and `all` use Throughput's scope
   (`scope_clause`, `task_visibility_clause` and the triage exclusion), with
   `archived_at IS NULL`. A missing `project_id` means the portfolio. An
   unreadable project gives 404. An R8 test checks that the open count
   equals the `analytics/load` total for the same scope.
3. **One cycle time.** `cycle_hours` and the completion come from
   `cycle_cte_sql`: the first `in_progress` to `done` on the activity spine.
   `completed_at` on the task row clears when a task opens again, so the route
   never reads it. A source test asserts the import, and that the module has
   no `completed_at` subtraction. A median and a p90 never become a mean.
4. **Columns come from an allowlist of 17.** The first nine are `number`,
   `full_id`, `title`, `project`, `root_project`, `status`,
   `status_category`, `type` and `tags`. The other eight are `assignees`,
   `estimate_mins`, `start`, `due`, `completed_at`, `created_at`,
   `cycle_hours` and `blockers`. `columns=` picks a subset. An
   unknown name gives 422. The table includes the tag names and the status
   names, because the examples above need them.
5. **Blockers follow §13.5 rule 4.** Only `blocks` links count. The blocker
   must pass visibility and must not be archived. A blocker that the viewer
   cannot see gives nothing. An R8 test proves that the id and the title of a
   hidden blocker are absent.
6. **The cap is visible.** The default limit is 200 and the maximum is 500. A
   limit above 500 gives 422. The sort is stable on `created_at`, then `id`.
   `total` and `truncated` are always present. There is no second page.
7. **Group on the server when the chat asks (O2 and O3).** `group_by` is one
   of `tag`, `status`, `status_category`, `project`, `assignee`, `type`,
   `created_week` and `completed_week`. `measure` is one of `count`,
   `estimate_sum`, `cycle_hours_median` and `cycle_hours_p90`. An unknown
   value gives 422. With `group_by` set, the response returns groups
   (`key`, `label`, `value`, `n`) and no rows. The server computes the median
   and the p90 over the full filtered set, not over the capped rows. Take
   `assignee` with `estimate_sum` or a cycle measure, and a caller without
   the HR grant. Then the value keys are absent and `hr_visible` is false. A
   test proves that they are absent. The server marks an agent in an assignee
   group. **The rows follow O3 too** (fix rounds 1 and 2, 2026-09-24). A
   row for a caller without the HR grant NEVER carries `cycle_hours` or
   `estimate_mins`, whatever else the request names. The server drops them
   and lists them in `hidden_columns`, and the body says `hr_visible: false`.
   A narrower rule, which dropped them only beside `assignees`, failed to one
   join. A call for `full_id,cycle_hours` and a call for `full_id,assignees`,
   joined on the task, give each person's speed. `assignees` stays, and so
   does `group_by=assignee&measure=count`, because a count per person is
   for every member. A grouped measure behind an assignee filter hides its
   value in the same way.
   **A group of fewer than K = 3 people hides its value** (fix round 2). The
   server counts the distinct people in each group, and an agent is not a
   person. It counts only the people whose tasks FEED the measure (round 3).
   A task with no cycle time adds nothing to a median, and a task with no
   estimate adds nothing to a sum, so its owner does not count toward K.
   The server trims and lower-cases each address first. Without the grant, a group with fewer than 3 people carries no
   `value` and no `measured`, it says `measure_hidden: true`, and it keeps
   `n`. A tag that only Ana uses, or a project where only Ana works, is
   Ana's speed under another name. With 3 people, no one member of the
   group reads another's figure by taking their own out. Cycle time by tag
   or by stage stays available as a server group under this rule.
8. **The label rule.** Every figure that the chat derives carries "computed by
   the assistant from N of M tasks, not an Analytics figure". A
   `statDashboard` tile title begins "Computed from N tasks". With
   `truncated=yes`, the chat computes no total, share or median over the
   whole set, and the tool trailer says so. A grouped figure from the server
   is labelled "from the server, N tasks". It is exact, so the chat may
   summarise it.
9. **The instructions change in the same slice.** Three places forbid or
   contradict a computed number: the analytics line, the chart line and the
   numbers rule. The slice writes them again around rules 1, 7 and 8. Tests
   pin the new
   sentences, in the `_w1` and `_files_section` style. The S9 «» rules stay.
10. **The tool output shape.** A header line, then one line for each row,
    with the cells separated by pipes. Titles and names pass through
    `client.data()` and are cut to 80 characters. The trailer is
    `rows=N total=M truncated=yes|no scope=…`. Grouped output is one line for
    each group, `key · value · n`, and a trailer.
11. **Lockstep.** `skill_projects.__all__`, `config.json` `own_tool_scope`,
    the `agents.py` description and MANIFEST change together.
    `test_projects_chat_coverage.py` and `test_projects_agent.py` pass.
12. **R4.** The status header, the §10 row, the board row (`work_plan.md`
    WS-27) and the INDEX line say that S7e is built.

**The filters.** The route takes the `build_task_filters` names, and three
more: `created_after`, `completed_after` and `completed_before`. Any other key
gives 422. S7e has no HR column on a row. Hours, skills and absences stay in
`team_capacity` and `fit_for_task`.

**As built, 2026-09-24.** Nine facts that the rules above do not say.
- **Where each part lives.** The route is
  `routes/projects/analytics_dataset.py`, and the tool is `reads.py`
  `task_dataset`. Throughput's scope now has a name,
  `analytics.history_where`, and Throughput calls it. That is the S7a
  precedent for `load_open_where`, and it keeps the scope in one place.
- **The completion window is 26 weeks**, the widest Throughput window. The
  route calls `cycle_cte_sql` with that window. A completion that is older
  has no `completed_at` and no `cycle_hours`, and the response prints
  `cycle_window`. `completed_at` is the first `done` on the spine. A
  cancellation is not a completion, so a cancelled task has neither value.
- **The completion filters read the spine too.** `completed_after` and
  `completed_before` compare the same `done` that `completed_at` prints.
- **A blocker is open and not in triage**, as in
  `analytics_conflicts.dependency_sql`, because a closed blocker blocks
  nothing. Each blocker carries its id, its number and its title.
- **`measure` needs `group_by`.** A measure alone gets 422, and the detail
  names Throughput for one figure over the whole scope.
- **A group carries `measured` beside `value`**, which is the count of tasks
  that had a figure. A task with two tags or two assignees is in two groups,
  so the tool tells the model not to add groups up. The route keeps at most
  100 groups and prints `groups_total`. A week group reads in date order.
- **Without the HR grant the server does not compute the hidden value**, and
  the body says `measure_hidden: true`. The row rule is
  `analytics_dataset.hr_gate`, and the K rule is `MIN_GROUP_PEOPLE`.
- **One grouped statement.** The groups, the count of people in each group,
  `groups_total` and the task total come from ONE statement, so the cycle
  CTE runs once. The group cap of 100 is a SQL `LIMIT`.
- **A project name follows the caller's grants** (fix round 2). A task can
  be visible through its assignee while its project is not. Such a row and
  such a group print no project name.
- **The lead-time proxy is accepted** (owner, 2026-09-24). `completed_at`
  and `created_at` stay on the row for every member, also beside
  `assignees`. A member can read a lead time for each person from them. The
  instructions sentence that forbids a person's lead time is the only fence,
  and it is ADVISORY.
- **The tool asks for eleven columns by default** (O4). Without the grant
  the table loses `cycle_hours` and `estimate_mins`, and the tool prints a
  "Hidden columns" line that says why. It clamps the limit to 1 to 500 before the call. A `|` in member text becomes `/`, so a
  title cannot add a cell.
- **The S9 pill index does not read these rows.** A dataset row is not the
  `- #<n> «title»` card line, so its names draw as plain marked text.
- **Deferred from review round 2** (the owner put S7e on production first).
  Nobody has measured the token size of a full table. An estimate is 15k
  to 20k tokens for 200 rows of eleven columns. The `agents.py` description
  does not name `task_dataset` literally. No test pins Throughput's
  rendered SQL, so a change to `history_where` fails only the dataset
  tests. S10 adds that pin (§16.2 rule 10).

### 13.8 What S7 does not do

- It does not write a person's skills, hours or absences. The People app owns
  those writes.
- It does not auto-assign. Every assignment is the class B `assign` with its
  card.
- It does not fix the outlook's capacity figure, which reads only the typed
  `capacity_hours_per_week`. That was H-169, and S11 fixes it (§17).

---

## 14. Documents and downloads from the chat (S8)

**Owner request, 2026-09-24.** The Projects chat must show a document, and
let the member view and download a Markdown file. It must also let the member
download a report as a PDF.

**Status: BUILT 2026-09-24.** A follow-up of the same date bounds a long
run of break characters, and it wraps a long line in a code block. It
also lets a colleague wait for a render (rules 6 and 7).

### 14.1 The answer

A member gets a file from the Projects chat in two ways.

1. **A document the assistant writes.** The agent writes Markdown or HTML
   with `write_artifact` into `outputs/`. The file opens beside the board, as
   it does in `/chat`. Its card has Open, Download and Download PDF.
2. **A saved report.** `render_report` draws the report card. The card has
   Download (Markdown) and Download PDF. The Reports app has the same two
   buttons.

No path adds a second formatter or a second PDF renderer. Each is one seam.

### 14.2 What was already there

The audit of 2026-09-24 read these facts from the code.

- `write_artifact` writes `outputs/<path>` and emits `artifact_created`.
  The gateway serves the file at `GET /agent/workspace/{sid}/file`.
- The rail draws `ArtifactCard` through the shared `MessageBubble`. Open
  goes to the side panel on a desktop, and to `ArtifactViewerModal` on a
  phone. So Markdown view and download worked before S8.
- `/chat` opened a written document in the side panel by itself, and pruned
  the panel to the active session. The rail did neither.
- No PDF generator existed. `pymupdf` was already a gateway dependency.
- A report was JSON only. `lib/reportEmail.ts` formatted a render for email
  and for nothing else. The report card ignored its `reportId`.

### 14.3 What S8 builds

1. **One auto-open rule.** `src/lib/autoOpenArtifact.ts` holds the rule that
   `/chat` held inline. `/chat` and the rail both call it. A Markdown file, an
   HTML file or a React artifact under `outputs/` opens live. Nothing opens on
   a phone, because a phone has no side panel. The card stays the way in.
2. **The panel follows the session.** The rail calls `syncPanelToSession`
   when its session changes, as `/chat` does. A tab from another conversation
   never renders beside the board.
3. **One PDF seam.** `gateway/pdf_render.py` lays out HTML as an A4 PDF with
   `fitz.Story`. Markdown goes through `markdown-it-py` with raw HTML off.
   `markdown-it-py` was already in `uv.lock` through `rich`. S8 makes it a
   direct gateway dependency.
4. **A workspace file as a PDF.** `GET /agent/workspace/{sid}/file` takes
   `format=pdf`. The route makes the same workspace, blocked-path and
   containment checks as a raw read. It converts `md`, `markdown`, `mdx`,
   `html` and `htm`, and it answers any other type with 415. The answer is an
   attachment with a `.pdf` name. The Next proxy passes `format` through.
   `ArtifactCard`, `DocumentPane` and `ArtifactViewerModal` show Download PDF
   for Markdown and HTML only. `artifactKind.ts` builds both links.
5. **A saved report as a file.** `reportEmail.ts` now builds one layout and
   draws it three ways: the email's text, its HTML, and the Markdown file.
   `reportDocument` gives the file every row, where the email keeps ten. The
   email output is the same, with one exception. An overdue section with no
   rows no longer draws an empty `<ul>`. The PDF is the same HTML, posted as
   `text/html` to `POST /documents/pdf`. `app/projects/lib/reportFiles.ts`
   owns the download path, and `ReportFileButtons` draws it in the Reports
   app and in the chat's report card. The card draws no button without a real
   report id.
6. **The instructions.** `instructions.md` has a Files section. It tells the
   model to write a document into `outputs/` with a clear name. It tells the
   model to point at the card's buttons. The model must never claim a PDF
   that it did not make.

### 14.4 Rules

1. **The numbers come from the render route only.** A file renders the
   report again on the click. Nothing in the path computes a figure.
2. **One formatter.** `lib/reportEmail.ts` formats a report for the email and
   for both files. The gateway formats nothing. It lays out the HTML that
   the browser sends.
3. **The PDF endpoint cannot fetch.** `pdf_render.sanitize_html` keeps an
   allowlist of text tags and drops every tag and attribute that can name a
   resource. The renderer gets no archive. A test serves an image on a local
   port and proves that a render never requests it.
4. **Caps.** A source above 1,000,000 bytes gets 413. A layout past 300 pages
   gets 413. `POST /documents/pdf` and its Next proxy check the declared
   length and the bytes as they arrive.
5. **Identity.** Both routes sit under the app-wide `require_authenticated`.
   `POST /documents/pdf` also refuses an anonymous context itself. Neither
   route reads a table or writes one (R5).
6. **MuPDF never runs in the gateway process** (fix round 1). MuPDF is C
   code. 199,000 nested `<div>` overflowed its stack and killed the gateway,
   and one word of 900,000 letters held the GIL for more than two minutes.
   So `render_pdf` lays out in a child process. The parent kills the child
   after 13 seconds (fix round 5). The slowest legitimate render measured
   was a 286-page table report, at 4.35 s, and 13 s is three times that.
   At most four children run at one time, and never
   more than the CPU count, so two on the production box (fix round 4).
   One member has at most one render in flight, and a second one gets
   429 at once. One organization also has at most one render in flight.
   A colleague's render waits up to 8 seconds for it to end, and then
   gets 429. A 286-page report takes 5 to 6 s on the 2-CPU box, so two
   people who download at the same moment both get a file. The keys are
   the authenticated email and the tenant the request bound. A request
   with no bound tenant shares one key. A render
   waits at most 2 seconds for a slot, with an `await` that holds no
   thread, and then gets 503 (fix round 3). A cancelled request kills its
   child. The child inherits only `CHILD_ENV_KEYS`, never the gateway's
   keys.
7. **Two bounds apply before layout.** The sanitizer refuses nesting deeper
   than 64 elements. A run of more than 2,000 characters that MuPDF cannot
   break is also refused. Both get 422, and both bind Markdown too. The
   break characters were measured (fix rounds 3 to 5). **The default is
   "does not break".** A character breaks a run only if it is assigned and
   it is in the measured set: a space, a hyphen, the Unicode spaces except
   U+00A0 and U+3000, and most kana, ideographs, Hangul syllables and
   full-width Latin. Everything else counts toward a run, so an unassigned
   or private-use code point is refused. `_CJK_NO_BREAK` in `pdf_render.py`
   lists the measured exceptions inside the kept ranges.
   **A run of break characters is refused too** (follow-up to fix round 5).
   MuPDF breaks at each one, but it lays out a long run of them as the
   square of its length. With 300,000 characters, hyphens took 38.8 s and
   U+202F took 81.9 s. So the check refuses more than 2,000 copies of one
   character, of any class. It also refuses more than 2,000 break spaces
   in any mix, such as tabs in a `<pre>` or U+2003 beside U+2002. The
   exceptions are the line feed and the carriage return. The break-space
   count leaves them out on purpose, because each one ends a line in a
   `<pre>`, and 300,000 of them stop at the page cap in under 1 s. Outside a
   `<pre>`, HTML collapses a run of ASCII spaces, tabs or line feeds to one
   space. So the check collapses that run first and does not refuse it.
   Later reviews of the follow-up found more cases. A combining mark or a
   format character does not end a run, so the check removes the `Mn`,
   `Me` and `Cf` characters first. It keeps a `Cf` character that is also
   a break space, such as U+2060, so a run of those is still refused. A
   form feed counts toward a run, because MuPDF does not collapse it.
   **MuPDF does not wrap a line inside `<pre>`**, and it lays out a long
   line as the square of its length. 1 MB of 20,000-character lines took
   38.3 s. So `wrap_pre_lines` breaks every `<pre>` line at 80 columns
   before layout. On A4 with this CSS, 83 columns fit inside the margin.
   The same 1 MB now renders in 0.66 s.
8. **Every failure has a status.** A size refusal is 413. A document that
   MuPDF cannot read, or a child that crashes, is 422. A second render for
   the same member is 429. A colleague's render that waited 8 seconds is
   429. A timeout or a full renderer is 503. No MuPDF
   error reaches the member as a 500.

### 14.5 Acceptance — S8

**Done when:**

1. `/chat` and the rail call `autoOpenArtifact`, and `/chat` keeps no
   extension list. A vitest shows that Markdown opens, a PNG does not, and a
   phone opens nothing.
2. The rail calls `syncPanelToSession(activeId)` in an effect on `activeId`.
   A vitest reads the source and fails without it.
3. `pdf_render` returns bytes that start with `%PDF`. A render never
   requests a URL in the HTML. The size cap, the page cap and an unknown type
   each refuse.
4. `?format=pdf` returns an attachment for Markdown and HTML, and 415 for a
   PNG. An anonymous caller gets 401. A traversal or a blocked path gets no
   PDF.
5. `POST /documents/pdf` returns a PDF for `text/html`, 415 for another
   type, 413 over the cap and 401 without an identity.
6. `reportDocument` writes Markdown from a render fixture, carries every
   row, and draws the email's own HTML. The tests of the email pass with no
   change.
7. The report card's buttons use the card's `reportId`, and a card without a
   real id draws none. A vitest drives the Markdown and the PDF path.
8. `instructions.md` has the Files section, and a test fails if it goes.
9. The status header, the §10 row, the board row and the INDEX line say that
   S8 is built (R4).
10. Rules 6 to 8 each have a bounded test. Deep nesting and a long word get
    422 through `render_pdf`. A child that aborts gets 422, and a child that
    hangs is killed at the timeout with 503. A MuPDF error gets 422. The
    tests run the hostile input in a child or stop it in Python, so the suite
    cannot crash.
11. Deleting the route's own size cap fails a test, because the test
    replaces the renderer with a trap.
12. Two golden tests pin the email HTML byte for byte. The strings came from
    the formatter on `main` before S8.
13. The PDF extensions in `artifactKind.ts` equal the gateway's
    `SOURCE_KINDS`, and a vitest reads the Python file to prove it.

### 14.6 What S8 does not do

- It adds no Files tree to the rail. The card and the side panel are the way
  in.
- It does not change how the app emails or schedules a report.
- It flips no flag. The rail stays behind `NEXT_PUBLIC_PROJECTS_CHAT`.
- A Markdown image does not reach the PDF. The sanitizer drops every `<img>`
  on purpose, because an image is a fetch.
- **Known limit: Devanagari text cannot be copied out of the PDF.** MuPDF
  draws it in its built-in Noto Serif Devanagari, and the page shows the
  shaped text correctly. But the PDF maps a conjunct glyph to the wrong
  character, so copy, search and a screen reader get wrong text. No font in
  the tree or in `pymupdf` fixes that mapping.
- A word longer than 2,000 characters is refused, not broken. A long hash or
  a base64 block in a document therefore gets 422. So does a Thai paragraph
  of more than 2,000 characters with no space, because MuPDF does not break
  inside Thai and its layout time grows as the square of the run.
- A line of more than 2,000 copies of one character is refused, not laid
  out. That includes a line of hyphens or of spaces in a code block.
- A `<pre>` line longer than 80 columns wraps onto the next line. It is
  not refused. So a one-line JSON dump of 4 KB or of 900 KB renders whole.
  Before the wrap, a line past about 83 columns was clipped at the page
  edge.
- **How the 13 s limit was measured.** The input is a Markdown table report
  with one row for each task. On the dev box, 6,000 rows (608 KB) made 286
  pages in 4.35 s. On the production box, srv1914284 with 2 CPUs, the S8
  review measured 6,000 rows (501 KB) in 3.7 to 3.9 s. It measured 7,300
  rows (609 KB) in 5.49 s. The 300-page cap stops a normal document near
  7,600 rows, at about 6 s. A 1 MB HTML table gets 413 at the page cap, in
  8.5 s. `test_r5_the_timeout_is_the_measured_backstop` records the same
  figures.

### 14.7 The visual review (fix round 2)

A Playwright review on 2026-09-24 looked at the rail, the side panel, the
viewer and the Reports app in four contexts. It found eight defects. The fixes
set four rules.

1. **The board keeps 32rem.** `src/lib/sidePanelFit.ts` decides whether a
   document may open in the side panel beside the board. If the row cannot
   hold the tree, the panel, 32rem of board and the dock, the document opens
   in the full-screen viewer. Nothing opens by itself then. The page's
   `<main>` clips, so the board never draws over the chat.
2. **Prose follows the theme.** `.cc-prose` in `globals.css` points every
   prose colour at a token. `prose-invert` is gone from the tree.
3. **One Markdown renderer.** `MarkdownBody` in `MarkdownMessage.tsx` draws
   the chat's answer and the generative-UI `markdown` node.
4. **The primitives use the status vocabulary.** A badge, a callout and an
   icon take their hue from `statusAccent.ts`, and a button is the `Button`
   primitive.

The report card takes its section titles from the Reports app
(`REPORT_CARD_SECTIONS` in `skill_projects/views.py`). A test reads
`ReportsView.tsx` and fails if a title is not there. The fences are
`src/components/chatVisualReview.test.ts`, `sidePanelFit.test.ts`,
`scrollCue.test.ts` and `test_projects_agent.py`.

**These changes reach `/chat` too, on purpose.** `MarkdownBody` is the chat's
one Markdown renderer, so its table cells and its task-list icons changed in
`/chat` and in the Projects rail. A task-list item is an icon with
`role="img"` and a label. `sidePanelFitWiring.test.ts` renders
`MessageBubble` under each value of `SidePanelFitContext`, and drives the
rail's `artifactHandler`, so both places are proven to act on the fit.

The re-check found four more defects (fix round 4). A chat table cell may
break a long word, so the table fits the rail. Code blocks take their
colours from `lib/codeTheme.ts`, which uses tokens only. Code in `.cc-prose`
is foreground ink. A badge draws its words in foreground ink and its hue as
a tint and a dot, because `--warning` ink measures 1.57:1 on a light card.

Fix round 5 replaced `wrap-anywhere` on table cells with `break-words`, which
keeps each word whole. A table wider than its box scrolls, and the bar shows.
Inline code in `.cc-prose` has no quote marks, and it looks like inline code in
the chat. A real browser run on 2026-09-24 measured the rail at compact density,
`/chat` at 1440 and a phone at 390. No word broke inside itself, and each wide
table and long code line scrolled with a 10px bar. **A capture rig must show
scrollbars.** Playwright's headless Chromium starts with `--hide-scrollbars`,
so a capture shows no bar even where the box scrolls. Pass
`ignoreDefaultArgs: ["--hide-scrollbars"]` before you judge a scroll box.

---

## 15. Entity pills in the chat (S9)

**Owner request, 2026-09-24.** The owner sent a screenshot of a Projects chat
answer and asked for pills "rather than just text". The answer showed four
defects:

- `**«Projects/Tasks App»** — 15 tasks`: a bold name, with the marks.
- `#5 **«Notification engine for projects»**`: the same, after a task number.
- `assigned to vjvarada@hathilabs.com`, drawn as a blue `mailto:` link.
- A `statDashboard` tile "Overdue 2" with a red "▼ 2". No tool printed that
  change. The model copied the value into `delta`.

The text `today.**Early stages` also had no space before the bold. That is a
model error, not a renderer error.

**Status: BUILT 2026-09-24.**

### 15.1 The answer

The model keeps the «guillemets» that the tools print around a name. The chat
draws each marked name as a pill. A pill for a task or a project opens the
row in this tab. A pill resolves only against the tool results of the same
message, and it links only on exactly one match.

The skill output does not change. The cards read the «» in the tool output
(`ProjectToolCards.tsx`), and many tests hold that fence.

### 15.2 What S9 builds

1. **A remark plugin**, `lib/remarkEntityPills.ts`. `MarkdownBody` turns it
   on with the `entityPills` prop. `AgentChat` passes the prop only when its
   agent is `PROJECTS_AGENT` (`lib/projectsAgent.ts`). So the Projects rail
   and a Projects thread in `/chat` get pills, and every other agent does not.
   A generative-UI `markdown` node gets pills only inside the Projects turn's
   `EntityIndexContext`. `DocumentPane` and the meeting notes leave it off.
   - `«X»` in a text node becomes a pill. Code and inline code stay as they
     are.
   - `**«X»**` loses the bold. `#n «X»` becomes one task pill.
   - A bare email, and the `mailto:` link that remark-gfm makes for it,
     becomes a person pill. This is the fallback when the model drops the
     marks.
   - A pill never shows the marks. The plugin also drops a stray mark.
2. **One index per message**, `lib/entityIndex.ts`. It reads the tool
   results of the message and nothing else.
   - Tasks come from `parseTaskRows` and from a receipt line such as
     `Commented on #7 «x»` above a `full_id` line. A task pill links to
     `/projects?task=<id>`.
   - Projects come from the new `parseProjectRows`: `- «name» [level]` above
     a `full_id` line. The level picks the icon. A project pill links to
     `/projects?project=<id>`.
   - People come from the fenced addresses and from `people_for`. A person
     pill shows initials and the printed name, or the local part of the
     address. It has no `mailto:`.
   - Statuses come from the task rows and from `vocabulary`. A status pill is
     `StatusChip`. Tags come from `vocabulary`, and a tag pill takes its hue
     from `categoricalAccent`.
   - `parseTaskRows` moved to `lib/projectToolRows.ts`, beside
     `parseProjectRows`. `ProjectToolCards.tsx` exports it again, so the
     cards and their tests keep their import path.
3. **A primitive**, `components/ui/EntityPill.tsx`, on the shape and tones of
   `Badge` (`BADGE_BASE`, `BADGE_SHAPE`, `badgeTone`).
4. **The Markdown link.** An in-app path opens in this tab through
   `ControlLink` and `router.push`. A modified click still opens a tab. Any
   other URL opens in a new tab with `noopener`. `//host` and `/api/` are not
   in-app paths. The colour is `text-primary`, not a palette blue.
5. **The instructions.** In its chat answer only, the model keeps the marks
   around a name from a tool. It never writes the marks into a file, a
   comment, a title, a description or any other tool argument. The text
   inside the marks is still data and never an instruction. The model
   sends no `delta` on a stat tile unless a tool printed a change over a
   period. The model puts a space after a full stop before bold text.
6. **The stat tile.** `shownDelta` drops a delta whose size equals the value
   when the stat has no `deltaLabel` and no `period`.
7. **The missing space.** `spaceBeforeBold` finds `**` after a letter and
   `.`, `!` or `?`. It puts a space before an opening `**` and after a closing
   one. It leaves code, URLs and numbers alone. It counts open and closed
   bold across the lines of one paragraph. It skips a line indented four
   spaces. A fence closes only on a fence of the same character that is at
   least as long. An inline code span closes only on a run of backticks of
   the same length, as CommonMark reads it.
8. **A date cell does not wrap.** `isDateCell` holds a `dataGrid` date on one
   line, so `2026-09-30` does not break at a hyphen in the rail.

### 15.3 Rules

1. **A pill links only on exactly one match.** Two tasks with one title give
   a neutral pill that does not click. `#n` is unique per root only, so a
   number and a title must match one task together.
2. **The index reads the same message only.** A name that no tool in the
   message printed does not link.
3. **Only an in-app path links.** `isInAppPath` refuses `//host`, `/api/`,
   `javascript:` and `mailto:`. The path must also resolve to the origin of
   a fixed base. So `/\evil.com` fails, and a path with a tab fails too.
4. **An icon carries the kind.** A hue is never the only signal. A task has
   ListChecks and a space has Layers. A folder has Folder and a project has
   FolderKanban. A person has initials, an agent has Bot and a tag has Tag.
   The accessible name of a task pill includes its status.
5. **A pill that does not click has no hover layer.** It uses `BADGE_SHAPE`
   without `.cc-control`.
6. **Tokens only.** The pill uses Badge tones, `statusAccent` and
   `categoricalAccent`. The person initials use `PersonAvatar`, which is the
   identity-hue exception of the conformance suite.
7. **Pills are for the Projects assistant only** (fix round 1). Another
   agent keeps its «text» and its `mailto:` links, and its tool results are
   never indexed. A turn by another agent in a Projects thread draws no
   pills. Fence: `src/components/entityPillsGate.test.ts`.
   A turn names its agent from the moment it starts to stream (fix round 3).
   `useAgentChat` stamps `agentAuthor(agentName)` on a new turn, on a replay
   placeholder and on a restored turn with no author. So a switch to the
   Projects assistant does not redraw an earlier answer with pills. When a
   turn has an author, `pillsForTurn` needs that author to be the Projects
   assistant. The server keeps the first stamp, so a reload reads the same
   name. Fence: `src/components/entityPillsAuthor.test.ts`.
8. **The marks stay in the chat answer** (fix round 1). The model never
   writes them into a tool argument, because nothing removes them there.
   Fence: `test_the_marks_stay_out_of_every_tool_argument`.
9. **A linked pill reads as ink, not as a blue word** (visual review). Its
   label is foreground ink on a `bg-primary/10` tint. Its icon and its `#n`
   take `text-primary`. A hover makes the tint stronger and underlines the label. Fence:
   `src/components/ui/EntityPill.test.ts`.
10. **No space before the punctuation after a pill** (visual review). The
    markup holds none. The gap that the review saw was the right padding of
    the pill, so a pill now takes `px-1`. The plugin keeps a pill and its
    punctuation on one line. Fence: `src/components/markdownPills.test.ts`.
    The group also takes an opening bracket or quote that touches the pill,
    so "Scope (" does not end a line alone (visual re-check). Fence:
    `src/lib/remarkEntityPills.test.ts`.
    The pill text is `0.9em` of the text around it, as inline code is
    `0.82em`. At compact density it no longer shrinks more than the prose.
    A measured line with a pill is as tall as a line without one. Fence:
    `src/components/ui/EntityPill.test.ts`.
11. **A person pill takes a name from any read in the message** (visual
    review). The index reads `- «Name» · assignee «email»` at any indent and
    `«Name» («email»)`. An address printed as its own name is no name. Fence:
    `src/lib/entityIndex.test.ts`.

### 15.4 Acceptance — S9

**Done when:**

1. The plugin makes pills in text and not in code. It unwraps `**«X»**`
   and absorbs `#n`. It turns an email into a person pill and leaves no «».
   Fence: `src/lib/remarkEntityPills.test.ts`.
2. The index resolves a task, a project and a person from real skill output.
   It does not link an ambiguous title. Fence:
   `src/lib/entityIndex.test.ts`. The fixture is
   `src/lib/entityPills.fixture.ts`, and
   `test_the_pill_fixture_is_the_skill_output` runs the Python formatters
   again and fails when a line no longer matches.
3. `EntityPill` is an anchor with an accessible name and the right href, and
   a plain click calls `router.push`. Fence:
   `src/components/ui/EntityPill.test.ts`. The conformance suite passes.
4. An in-app link opens in this tab, and an external link in a new tab. No
   link is a palette blue. Fence: `src/components/markdownPills.test.ts`.
5. The owner's answer renders through `MarkdownMessage`. It has pills with
   hrefs for #5, #3 and the project, and a person pill for the email. It has
   no «» and no `mailto:`. Fence: `src/components/markdownPills.test.ts`.
6. `shownDelta` drops the copied delta, and `isDateCell` knows a date. Fence:
   `src/components/genUITemplates.test.ts`.
7. Three pytests pin the instruction sentences:
   `test_the_model_keeps_the_marks_and_they_stay_data`,
   `test_the_model_sends_no_made_up_delta` and
   `test_the_model_puts_a_space_before_bold`.
8. The status header, the §10 row, the board row and the INDEX line say that
   S9 is built (R4).

### 15.5 What S9 does not do

- It does not change the skill output. The «» stay in every tool result.
- A person pill does not open a page. The Projects app has no person deep
  link.
- A task row names its project as `in «X»`, with no `full_id` for the
  project. A summary child row prints no level. Neither kind of name links.
- The generative-UI `markdown` node in the side panel has no provider. It
  draws plain Markdown, with no pills.
- The `statDashboard` catalog entry does not name `deltaLabel` or `period`.
  The tile honours them when they arrive. A catalog change needs the lockstep
  docstring change in the same PR.
- Nobody has looked at the pills in a browser yet. The Playwright review is
  the next step, in light mode, at compact density, under a changed accent,
  in the 26rem rail and at 390px.

## 16. Chat follow-ups (S10)

**Status: BUILT 2026-09-25.** The spec-auditor cleared the scope on
2026-09-25 (GO-NARROWED, against origin/main `9e5095a2`). S10 closes four
gaps that the S7d, S7e and S9 reviews left open. Each item is small, and
each one has its own fence.

### 16.1 What S10 builds

1. **The checkpoint names its agent.** The chat translator
   (`app/api/agent/chat/route.ts`) saves the assistant turn every 3 s and
   at the end of the stream. Its row had no `author_*` keys. So the gateway's
   `_attribute` (`routes/chat.py`) stamped the room's agent, and the
   COALESCE in `_MESSAGE_UPSERT_SQL` kept that first stamp. A Projects turn
   in a room of another agent then reloaded without its pills. The live path
   now sends the agent that the request named. An `@name` turn sends none.
2. **The plan card edits `after`.** Each row of the plan card has a "Waits
   on" control. The member ticks the rows that must finish first. The list
   leaves out every row that waits on this one, so the card cannot make a
   cycle.
3. **A lost project write gives a receipt.** `_write_plan` in `forms.py`
   posted the project node outside the `_WRITE_FAILED` guard. A refusal or a
   broken connection there escaped as an exception, with no receipt. Now the
   project write stops the batch like every other write.
4. **Throughput pins its scope.** S7e named `analytics.history_where` and
   made the dataset read share it. No test held Throughput to it. S10 adds
   two hermetic tests. S7e merged before S10 started, so this item is built.

### 16.2 Rules

1. **The live path sends the named agent.** `translateAndPersistStream`
   takes `checkpointAgent(resolvedAgentName, message)`. Each checkpoint row
   carries `author_kind: "agent"` and `author_email` set to that name.
2. **The reconnect path and an `@name` turn send no author.** Neither knows
   which agent runs. In a room, `_address_agent` (`routes/agent.py`) can send
   an `@name` turn to another agent. `isRoomAddress` copies the gateway's
   `_MENTION_RE`, and a test fails when the two differ. The server then
   stamps the room's agent, as before S10 (fix round 1).
3. **The row shape lives in `src/lib/assistantCheckpoint.ts`.** A route file
   may export only route names, so a test cannot reach a function there.
   `persistAssistantMessage` calls `assistantCheckpointRow` and builds no row
   of its own.
4. **The server does not change.** `_attribute` keeps a client's
   `author_email` for an agent turn. The COALESCE keeps the first stamp, so
   a later save with no author does not rename the turn.
5. **`after` is an input.** `PLAN_READ_ONLY` no longer holds `after`. The
   control is a `CollapsibleSection` with one `Checkbox` for each other row,
   labelled with its title. Both come from `components/ui`. S10 adds no new
   primitive and no raw input. `CollapsibleSection` gains one prop,
   `ariaLabel`. Each control is named `Waits on (for <title>)`, and its count
   leaves out the key of a dropped row (`afterCount`, fix round 1).
6. **The card cannot make a cycle.** `afterOptions(row, rows)` leaves out the
   row itself and every row that waits on it, directly or through other
   rows. A row with no option shows the line "Waits on: none. Every other
   task waits on this one." in place of the control.
7. **The server stays the fence for §13.6 rule 7.** `_submitted_rows` and
   `_plan_rows` refuse an unknown key, a self-block and a cycle before
   `_confirm`, with zero writes. S10 adds no refusal text in TypeScript.
8. **Lockstep.** The `planCard` catalog summary in `genUITemplates.tsx` says
   that the member edits `after`. The `planCard` bullet in `write_artifact.py`
   already lists `after?:[key]`, so it does not change. A longer bullet put
   the `emit_generative_ui` schema past its ceiling in
   `test_tool_schema_diet.py`.
9. **The project write stops the batch.** On a `GatewayRefusal` the receipt
   says `stopped: the project «X» was refused.`, the reason, and `Nothing was
   created.` On an `httpx.TransportError` it says that the write may or may
   not have landed. It tells the member to read the tree before a retry.
   Both receipts count every task, owner and link as not tried. No other
   write runs.
10. **The Throughput pin.** One test asserts that `throughput` passes the
    output of `history_where` into `weekly_sql` and `cycle_summary_sql`. One
    test pins the string that `history_where` gives for a fixed scope and a
    fake visibility.
11. **No migration, no flag and no new route.**

**As built, 2026-09-25.** Four facts that the rules do not say.
- **An `@name` turn can still reload under the wrong author.** With no
  author, the first checkpoint stamps the room's agent. The gateway's fold
  knows the addressed agent, but it writes after that first checkpoint, and
  the COALESCE keeps the first stamp. `test_rooms.py` holds the gap as a
  strict xfail, `test_an_addressed_turn_is_stamped_with_the_agent_that_ran`.
  The fix is a server change (§16.4).
- **`test_rooms.py` needed a fixture repair to run at all.** Its `_seed_user`
  wrote `ON CONFLICT (email)`, and `app_user` has only a unique index on
  `lower(email)`. So every R8 test in the file failed in its fixture against
  a real ladder. CI never saw it, because CI does not set `DATABASE_URL`. The
  fixture now writes `ON CONFLICT (lower(email))`.
- **The Throughput suite skips in its `db` fixture, not at module level.** A
  module-wide skip hid the two hermetic pins on every run without a
  database.
- **The "Waits on" control starts folded** and shows the count of ticked
  rows. The `After:` line under the row still names the blockers by title.

### 16.3 Acceptance — S10

**Done when:**

1. `src/lib/assistantCheckpoint.test.ts` passes. It checks the row with an
   agent and without one, and scans `route.ts`. The live call passes
   `resolvedAgentName` through `checkpointAgent`, the reconnect call does
   not, and both checkpoints pass the agent. An `@name` message claims no
   author, and `ROOM_ADDRESS_RE` equals the gateway's `_MENTION_RE` (rules 1
   to 3).
2. The R8 test `test_a_checkpoint_author_wins_over_the_room_agent` in
   `tests/unit/test_rooms.py` passes. A checkpoint with `author_email`
   `projects-assistant` in a room whose agent is `orchestrator` stores
   `projects-assistant`. A second save with no author keeps it (rule 4).
3. `entityPillsAuthor.test.ts` and `entityPillsGate.test.ts` pass unchanged.
4. `planCard.test.ts` passes. `PLAN_READ_ONLY` holds no `after`. For the
   chain t1, t2 after t1 and t3 after t2, the options of t1 hold neither t2
   nor t3. The options of t3 hold t1 and t2. `planSubmit` sends the edited
   `after` and strips the key of a dropped row (rules 5 and 6).
5. `genUITemplates.test.ts` passes. It draws the control on each row that
   has an option, and draws none on a plan of one task. Each control's name
   holds its row's title. The count leaves out a dropped row's key. A row
   with no option shows the "Waits on: none" line.
6. `test_projects_agent_writes.py` passes (rules 7 and 9).
   - An edited `after` puts the link on the confirm card and posts it.
   - A cycle and a self-block each give the refusal, with no write and no
     `_confirm` call.
   - A `ConnectError` on `POST /projects/nodes` gives `stopped: the project
     «Steps» lost its connection`, `may or may not` and `not tried: 7 tasks`.
   - A `GatewayRefusal` there gives `was refused` and `Nothing was created.`
   - In both cases there is no task POST, no assignee PUT and no link POST.
7. `test_genui_catalog_lockstep.py` passes (rule 8).
8. The two Throughput tests in `test_projects_analytics_throughput.py` pass
   (rule 10).
9. The status header, the §10 row, the board row and the INDEX line say that
   S10 is built (R4).

### 16.4 What S10 does not do

- **H-169.** The owner answered the HR question on 2026-09-25, and the fix
  now waits for its own slice. S11 (§17) is that slice.
- It does not put a literal tool name in the `agents.py` description.
- It does not measure the token size of a `task_dataset` table.
- It does not change the main agent of a room, or the route that sets it.
- **It does not stamp an `@name` turn with the agent that ran.** That needs
  a server change. One way: the fold overwrites the author of an agent turn.
  Another way: the run stream names its agent, and the translator sends it.
  The strict xfail in `test_rooms.py` fails when either lands, so remove its
  mark in that change.
  S12 (§18) built the first way and removed the mark.

---

## 17. The Forecast reads the schedule (S11)

**Status: BUILT 2026-09-26.** The spec-auditor cleared the scope on
2026-09-25 (GO-NARROWED, against origin/main `b0814b2d`). S11 closes
HANDOFF H-169.

### 17.1 The answer

The Forecast panel divides the hours left by the team's weekly hours. Until
S11, those hours were the typed `people.capacity_hours_per_week` only. Now
each holder's hours come from their working schedule, the one arithmetic
of `work_schedule.py`.

**The owner's answer, 2026-09-25.** The Forecast uses each person's working
schedule for every viewer. Absences reduce the hours only for a viewer with
`admin:members:read`. A viewer without that grant sees a forecast that
ignores leave, and the panel says so.

**The visible name is "Forecast". The key stays `outlook`.** Owner
direction, 2026-09-26. Members read "Outlook" as the Microsoft mail
product. So every member-facing string now says "Forecast". The route
`/projects/analytics/outlook`, the report section key `outlook`,
`outlook_body`, the file names, the API types and the chat tool name
`analytics_outlook` keep their names. A rename of a key breaks saved
reports and the chat manifest, and a member never sees a key.

### 17.2 What exists

- `analytics.py` `team_capacity_sql` summed `capacity_hours_per_week` and
  counted `working_hours`, but it never read the schedule.
- `capacity_forecast` divides the hours left by one team rate:
  `weeks_left = ceil(hours_left / hours_per_week)`.
- `outlook` and `outlook_body` return the Forecast. The report section
  `outlook` in `reports.py` `render_body` calls `outlook_body`.
- `work_schedule.py` has `load_policy`, `person_schedule` and
  `working_hours_between`. `capacity.py` has `horizon_window` and
  `absences_for`.
- `routes/tasks/core.py` `can_read_hr_fields` decides the HR tier.
  `analytics_capacity.py` takes `hr_visible` as an argument, and S11 copies
  that precedent.

### 17.3 Rules

1. **The schedule wins.** Each holder's hours come from
   `person_schedule(load_policy(db), row)`. The typed
   `capacity_hours_per_week` never enters the forecast (D-PC-18, S7a).
   `capacity_disagreement` still reports a typed figure that differs.
2. **Every directory row has a schedule.** The org policy applies first,
   then `DEFAULT_POLICY`. A holder with no `people` row adds zero hours. The
   code never falls back to the typed figure.
3. **One weekly rate, from a fixed forward window.** Sum
   `working_hours_between(schedule, today, end, spans)` over the holders.
   Divide by `OUTLOOK_RATE_WEEKS` = 12. `end` is the last day of
   `horizon_window(today, 83)`, so the window holds 84 days. Call
   `working_hours_between` directly, not `person_capacity`. Its horizon is
   inclusive and adds one day, which is a divisor trap. `capacity_forecast`
   does not change.
4. **Absences follow the viewer's grant.** `outlook_body` takes
   `hr_visible: bool` as a keyword with no default. When it is true, the
   body reads `absences_for` and passes the spans. When it is false, the body
   passes `None` and runs no `people_absences` statement.
5. **Who decides `hr_visible`.** The route passes
   `can_read_hr_fields(user)`. The report section passes
   `can_read_hr_fields(user)` for the reader of the render. A send renders
   for each recipient, so the recipient's grant applies. The chat calls the
   route.
6. **The same people.** The rate sums exactly the holders CTE of
   `team_capacity_sql`. That is open, visible and reportable work in scope,
   with no agents. S11 changes the columns only, never the holders CTE.
7. **No figure for one person leaves the route.** The payload carries team
   totals only.
8. **The payload says what it counted.**
   - It adds `hr_visible`, `people.in_directory`, `people.absences_applied`
     and `capacity.window` (`starts_on`, `ends_on`, `weeks`).
   - It drops `people.with_stated_capacity` and `people.with_schedule_only`.
   - `people.hours_per_week` equals `capacity.hours_per_week`. Both round to
     one decimal.
9. **This narrows §13.2 rule 3 for the Forecast only.** The owner decided
   this on 2026-09-25. `working_hours` is directory tier
   (`people_center_app.md` §3.4a), and absences stay HR tier.
   `projects_reports.md` §7 rule 2 carries the same note.
10. **The panel says when it does not count leave.** When
    `absences_applied` is false, the capacity line adds this text: "This
    forecast does not count leave. An admin sees it with leave." The text
    says "working hours" and never "stated hours".

### 17.4 Acceptance — S11

1. A holder with typed capacity 10 and the default policy gives
   `capacity.hours_per_week` 40.0.
2. A holder with no `people` row adds 0. If no holder has a row, the verdict
   is `no_capacity`.
3. With no absence, the admin figure and the member figure are equal.
4. For 5 days of 8 hours, with one full working week away inside the
   window, an admin gets 36.7 and a member gets 40.0.
5. When `hr_visible` is false, no statement reads `people_absences`. A fake
   database records each statement.
6. A signature test shows that `outlook_body` has no default for
   `hr_visible`.
7. The report section equals the route for an admin and for a member. The
   R8 test `test_the_outlook_section_equals_the_outlook_route` holds both.
8. A person who holds only work that the viewer cannot see adds no hours
   (R8).
9. No payload key names a person or an absence. A key walk checks it.
10. `outlook.test.ts`: the leave sentence shows when `absences_applied` is
    false and not when it is true. No string in `outlook.ts` says "stated".
11. The H-169 Check finds no hit, and the same change deletes the H-169
    entry.
12. Every member-facing "Outlook" in the Projects app says "Forecast". The
    keys in §17.1 keep their names.

### 17.5 What S11 does not do

- It does not subtract hours that a holder gives to other projects.
- It has no `end_date` cutoff. A holder whose engagement ends inside the
  window still adds the full window.
- It does not change `leaving_within_90d`. S12 (§18) sends that count to
  an admin only, and deleted HANDOFF H-188.
- It does not change the velocity forecast or `capacity_forecast`.
- It adds no migration.
- It does not rename the three "coming" report templates in `reports.py`
  that name "the outlook section". The reports session owns that file.

### 17.6 Verification

```bash
# R8: the scratch Postgres. A TENANT_LADDER skip is not a pass.
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_projects_analytics_outlook.py \
  tests/unit/test_projects_report_sections_r3.py \
  tests/unit/test_projects_report_sections_lockstep.py \
  tests/unit/test_projects_analytics_capacity.py \
  tests/unit/test_people_schedule.py tests/unit/test_people_absences.py \
  tests/unit/test_projects_agent.py tests/unit/test_tenant_coverage.py -q -rs
uv run ruff check apps/services/gateway/gateway/routes/projects/analytics.py
uv run mypy apps/services/gateway/gateway/routes/projects/analytics.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

Five mutations each turn a test red: absences for every viewer, the typed
figure as a fallback, an inclusive 85-day window, a changed holders CTE,
and no leave sentence.

---

## 18. Follow-ups (S12)

**Status: BUILT 2026-09-28.** The spec-auditor cleared the scope on
2026-09-28 (GO-NARROWED, against origin/main `f62d5f70`). S12 closes
HANDOFF H-188 and the open gap in §16.4.

**The owner's answer, 2026-09-28.** The owner chose **"Admins only
(Recommended)"** for this question:

> "The Forecast panel shows every member how many people on the work leave
> within 90 days… What should non-admins see?"

The option text was:

> "Only admins see the 'leaving within 90 days' count. Everyone else sees no
> count. This matches how the Capacity panel treats end dates."

### 18.1 What S12 builds

1. **The end-date count follows the grant (H-188).** `outlook_body` in
   `analytics.py` puts `people.leaving_within_90d` in the payload only when
   `hr_visible` is true. Without the grant the key is absent. It is not
   null and it is not 0. `OUTLOOK_HR_KEYS` names the key. In `api.ts` the
   key is optional, and `outlook.ts` `peopleLine` reads a missing count as 0.
2. **One broken panel does not blank Analytics.** The analytics branch of
   `page.tsx` has no error boundary above it. So a panel that throws on a
   missing date blanked the whole page. `AnalyticsView.tsx` now puts each of
   its seven panels inside its own `LayoutBoundary`, with a `layout` prop that
   names the panel. The fallback says "the {layout} view", so a label is a
   plain name such as `forecast`, not `forecast panel`.
3. **An @name turn keeps the agent that ran.** `_upsert_messages` in
   `routes/chat.py` takes a keyword `author_from_run`, and its default is
   False. Only `chat_fold.persist_final_assistant_message` passes True. With
   it, the upsert sets `author_email` again when the stored row is an agent
   turn. A stored NULL kind counts as an agent turn only when the `role` is
   `assistant`. The checkpoint of an `@sales` turn writes the room's agent
   first, and the fold then writes the agent that ran.
4. **One member cannot write in the turn of another member (fix round 1).**
   The review found a live forgery. The row id comes from the client, and the
   upsert replaced `content` on any `(session_id, id)` that the caller named.
   So member A could put A's words in a turn by B, and the turn kept the name
   of B. The fold could do the same through a client-chosen
   `assistant_message_id`. The `DO UPDATE` in `_MESSAGE_UPSERT_SQL` now has a
   `WHERE`. A human row changes only when its own author sends a human write.
   The fold never changes a human row. A conflict that fails the `WHERE`
   leaves the row alone and raises no error.

### 18.2 Rules

1. `hr_visible` decides the count. When it is false, the key is absent.
2. `OUTLOOK_HR_KEYS` is the one tuple of HR keys in the outlook payload.
3. The holders CTE of `team_capacity_sql` does not change. The SQL still
   counts `leaving_soon` for every reader, and only the payload drops it.
4. The panel reads a missing count as no warning.
5. The chat and the email follow the payload and do not change. The chat
   prints the keys it gets, and the email does not print the count.
6. Each panel has its own boundary. There is no second boundary class.
7. Only the fold passes `author_from_run`. The route handler
   `POST /sessions/{id}/messages` never passes it.
8. The fold changes the author of an agent turn only. A stored NULL kind is
   an agent turn only when the `role` is `assistant`. A human turn keeps its
   author. `_persist_message_id` comes from the client, so the fold can reach
   a row that the client chose.
9. Every client write keeps the COALESCE. `authority` keeps its COALESCE for
   every writer.
10. S12 updates the rule in the two places that state it: the comment above
    `_MESSAGE_UPSERT_SQL` and item 3 of `apps/services/gateway/AGENTS.md`.
11. S12 adds no migration, no flag and no route.
12. **A human row changes only by its own author.** The `WHERE` on the
    `DO UPDATE` lets a write touch a human row only when three things are
    true. The write is not the fold. The incoming kind is `human`. The stored
    `author_email` equals the incoming one, with case ignored.
13. **The incoming author of a human write is the authenticated caller.**
    `_attribute` takes it from `actor_email`, never from the body. A body that
    claims an agent turn can name any `author_email`. So the `WHERE` also
    requires an incoming kind of `human`, and a claimed agent turn cannot pass
    as the author of a human row.
14. **A legacy row with a NULL kind is a human row unless its `role` is
    `assistant` or `system`.** It gets the same protection as a human row.

### 18.3 Acceptance — S12

1. `test_projects_analytics_outlook.py`: the key-set test holds both sets. A
   key walk finds no `OUTLOOK_HR_KEYS` key in the payload for a reader
   without the grant.
2. R8: `test_the_outlook_section_equals_the_outlook_route` passes for an
   admin and for a member. It also checks that the count is present for the
   admin only.
3. `outlook.test.ts`: with the key absent, the line has no "engagement" text
   and the tone is not warn. The test for a count of 1 still passes.
4. `layoutBoundary.test.ts`: each `<…Panel` tag in `AnalyticsView.tsx`
   opens straight after a `<LayoutBoundary` tag with a `layout` prop. Each
   panel closes straight into `</LayoutBoundary>`. The "declared once" test
   still passes.
5. R8, in `test_rooms.py`: the strict xfail is gone, and
   `test_an_addressed_turn_is_stamped_with_the_agent_that_ran` passes.
6. R8: after the fold stamps `sales-assistant`, a client save that names
   `projects-assistant` keeps `sales-assistant`.
7. R8: a fold write on the id of a human turn keeps `author_kind` human and
   keeps its `author_email`.
8. A source test shows that `chat_fold` passes `author_from_run=True`, and
   that the route handler does not name it.
9. `test_a_checkpoint_author_wins_over_the_room_agent` passes with no change.
10. **A person checks the fallback.** Make `FinishedPanel` get a report with
    no `period_start`, and look. The Finished panel shows the fallback, and
    the other six panels still show. No test in this tree can render a
    throwing child, because vitest runs in node and does not collect `.tsx`
    tests.
11. The same change deletes the H-188 entry and updates the status (R4).
12. R8, fix round 1, in `test_rooms.py`. Each test calls the real
    `save_messages` handler or the fold call:
    - member B saves a turn over the id of a turn by member A. The row keeps
      the content and the author of A.
    - B claims an agent turn with the `author_email` of A. The row does not
      change.
    - the fold writes on the id of a turn by A. The row does not change.
    - A can still update the turn of A.
    - an agent row still updates by a checkpoint from another sender and by
      the fold.
    - a legacy `user` row with a NULL kind does not change by a save from
      another member or by the fold.
    - a legacy `assistant` row with a NULL kind still takes the fold.
13. `layoutBoundary.test.ts`: no `layout` label ends in "panel" or "view".

### 18.4 What S12 does not do

- **No `end_date` cap.** A cap changes `person_capacity` for five routes. It
  would also show the end date through the rate. So it needs its own slice,
  and that slice must gate it on `hr_visible`.
- It adds no boundary in `ReportsView` or `NodeDashboard`.
- It does not change the stream or the translator for the author.
- **Residual, CLOSED by S13 (§19): any room sender could write an agent row
  or a system row.** The translator checkpoints as the sending member, so the
  S12 `WHERE` could not tell the sender of a run from another member. An
  overwrite kept the stored `author_email` and `author_kind`, so a forged
  agent reply kept the agent's name. A client write carries no `authority`,
  so the clearance label that a forged overwrite kept was the run's label,
  which the fold writes. A tab that held an old copy of an agent reply could
  also overwrite the final text on a save of the whole array. S13 scopes an
  agent row to the member who started the run, and the fold seals it.
- **Residual, CLOSED by S13 (§19): the fold could write a legacy system
  row.** A legacy row with a NULL kind and the role `system` passed the S12
  `WHERE`, so the fold replaced its `content`. S13's `WHERE` lets no write
  update a system row. So S13 removed the kind guard from the author `CASE`,
  and `test_the_fold_keeps_a_legacy_system_row` replaces
  `test_the_fold_keeps_the_author_of_a_legacy_system_row`.
- **Residual, CLOSED by S13 (§19): a declined write reported success with no
  detail.** `POST /chat/sessions/{id}/messages` now answers
  `{"ok": true, "saved": n, "unchanged": [ids]}`.
- `_attribute` still takes the claim of an agent turn from the body when it
  INSERTS a new row. So a member can make a new row that names any agent.
  That is not a change to an existing turn, and S12 does not change it.
- **As built, it DOES guard `day()` and `period()`** (commit 2af0a8ce). In
  the dev build, a throw in `FinishedPanel` froze the whole Analytics page
  even inside its `LayoutBoundary`. So the helpers now take any input and
  give `""` or "this period". `analyticsDates.test.ts` is the fence.
- It does not measure tokens, and it adds no tool name in `agents.py`.
- It does not rename the "coming" report templates.

### 18.5 Verification

```bash
# R8: a real Postgres, with the ladder applied. Set both variables.
export DATABASE_URL=... TENANT_LADDER_DATABASE_URL=...
uv run pytest tests/unit/test_projects_analytics_outlook.py \
  tests/unit/test_projects_report_sections_r3.py \
  tests/unit/test_projects_report_sections_lockstep.py \
  tests/unit/test_projects_sql_asyncpg.py tests/unit/test_projects_agent.py \
  tests/unit/test_rooms.py tests/unit/test_chat_hardening.py -q -rs
G=apps/services/gateway/gateway
uv run ruff check $G/routes/projects/analytics.py $G/routes/chat.py $G/chat_fold.py
uv run mypy $G/routes/projects/analytics.py $G/routes/chat.py $G/chat_fold.py
cd workbench/control_plane && npx tsc --noEmit
npx vitest run src/app/projects/lib/outlook.test.ts \
  src/lib/layoutBoundary.test.ts \
  src/app/projects/components/reportVisuals.test.ts \
  src/lib/assistantCheckpoint.test.ts
npx vitest run
```

Four mutations each turn a test red:

- the count sent to every reader
- `author_from_run` passed on the route
- a fold that changes a human turn
- a panel moved outside its boundary.

Fix round 1 adds four more, and each turns a test red:

- the `WHERE` removed from the `DO UPDATE`
- the check for an incoming kind of `human` removed
- a legacy NULL-kind `user` row read as an agent row
- a `layout` label that ends in "panel".

## 19. Message integrity (S13)

**Status: BUILT 2026-09-28.** The spec-auditor cleared the scope on
2026-09-28 (GO-NARROWED, against origin/main `32063289`). S13 closes three
residuals in §18.4. `groups_sessions_authority.md` §4 links here.

### 19.1 The answer

Only the run may change an agent reply. The run is the member who started it,
until the fold seals the row. After the seal, only the fold may change it. No
client may change a system row. The response names each write that the
server declined.

### 19.2 What exists

1. `_upsert_messages` in `routes/chat.py` is the only runtime writer of
   `chat_message`. It runs `_MESSAGE_UPSERT_SQL` once for each row.
2. The row id comes from the client. The translator (`route.ts`
   `persistAssistantMessage`) and the browser (`sessions.ts` `saveMessages`)
   both save through `POST /chat/sessions/{id}/messages`, as the member who
   sent the turn. On a reconnect the translator saves as the member who
   reconnects, who may not be the member who sent the turn.
3. The fold (`chat_fold.persist_final_assistant_message`) writes the same row
   with `author_from_run=True`. Its `user_id` is the email of the member who
   started `/agent/run/stream` (`_mem_user` in `routes/agent.py`).
4. Before S13, the `WHERE` guarded human rows only. Any room sender could
   change an agent row or a system row, and a declined write answered
   `{"ok": true}` with no detail.

### 19.3 What S13 builds

1. **One migration**, `infra/postgres/220_chat_message_run_integrity.sql`. It
   adds `chat_message.run_member_email TEXT` and
   `chat_message.run_final_at TIMESTAMPTZ` with `ADD COLUMN IF NOT EXISTS`.
   Both are nullable, with no default and no backfill.
2. **The run member.** `_upsert_messages` sets `run_member_email` on an agent
   row only. A client write gives the caller (`actor_email`). The fold gives
   its `user_id`. The value is lower-cased, and an empty value is NULL. The
   upsert keeps a stored value with `COALESCE`, so the first writer sets it.
3. **The seal.** A fold write sets `run_final_at = now()` on an insert and on
   an update. A client write never sets it.
4. **The `WHERE`.** The stored kind is `author_kind`. When it is NULL, the
   role `assistant` is an agent row, the role `system` is a system row, and
   every other role is a human row.
   - Human row: the S12 rule, unchanged.
   - Agent row, a fold write: it passes when the stored `run_member_email` is
     NULL or equals the incoming value, with case ignored.
   - Agent row, a client write: it passes only when `EXCLUDED.author_kind` is
     `agent`, `run_final_at` is NULL, and the stored `run_member_email` equals
     the incoming value, with case ignored. A NULL run member lets only the
     fold update the row.
   - System row: no write passes.
5. **The author `CASE`.** The fold arm has no kind test now, because the
   `WHERE` lets the fold reach agent rows only. It keeps
   `EXCLUDED.author_email IS NOT NULL`, and the `ELSE` arm is unchanged.
6. **The declined ids.** The SQL ends in `RETURNING id`. `_upsert_messages`
   returns the ids that got no row back, in request order.
7. **The response.** `save_messages` answers
   `{"ok": true, "saved": <rows written>, "unchanged": [<ids>]}` with status
   200.
8. **The fold.** When the server declines its write, the fold logs
   `chat_fold.persist_declined`. It still returns the folded message, and it
   never raises.

### 19.4 Rules

1. Only the run changes an agent row. A member who did not start the run
   cannot change it. That includes a room owner.
2. The seal is final for clients.
3. An agent row with a NULL run member takes the fold only. That covers
   legacy rows and rows that old code writes during the deploy.
4. No writer may update a system row. S13 let a client INSERT one. S14
   takes that away (§20.4 rule 8).
5. A declined write is not an error. The status is 200, and `ok` is true.
6. `authority` keeps its `COALESCE`.
7. S13 adds no flag and no route, and one migration.
8. S13 updates the rule in the comment above `_MESSAGE_UPSERT_SQL`, in item
   3 of `apps/services/gateway/AGENTS.md` and in §18.4.
9. **The fold id of a run with no `assistant_message_id` is server-only**
   (fix round 1). `/agent/run/stream` gets it from `fold_message_id` in
   `routes/agent.py`, once for each run, as `assistant-{thread}-{uuid4}`.
   No event and no response carries it. The old fallback was
   `assistant-{thread}-{run_id}`, and RUN_STARTED publishes `runId` to the
   room. So a member could insert that id first, become its run member, and
   make the `WHERE` decline the real fold for good. The Next chat always
   sends its own id, so the fallback serves API and automation callers only.

### 19.5 Acceptance — S13

1. R8 tests in `test_rooms.py`, through the real `_save_as` handler, the
   upsert as the fold calls it, or the real fold call:
   - Alice starts a run, and Bob saves the agent row. The content does not
     change, and `unchanged` holds its id. Bob owns the room.
   - Alice saves her own agent row before the fold. The content changes.
   - After the fold, a save by Alice with older content leaves the fold's
     content.
   - A fold with the starter Bob on a row whose run member is Alice does not
     change it. The real fold call logs `chat_fold.persist_declined` and
     returns the folded message.
   - A client save does not change an agent row with a NULL run member. The
     fold does, and it sets `run_member_email`.
   - A human save by Alice on the id of her own agent row does not change it.
   - A client save on a system row does not change it. A new system row
     still inserts.
   - The fold on a legacy system row keeps its content and its
     `author_email`. `test_the_fold_keeps_a_legacy_system_row` replaces
     `test_the_fold_keeps_the_author_of_a_legacy_system_row`.
   - `test_an_agent_turn_still_updates_by_checkpoint_and_by_fold`: the server
     declines the checkpoint from Bob, and the fold by Alice takes.
2. The `_save_as` response holds `ok` true, `saved`, and `unchanged` with the
   right ids in request order.
3. `test_chat_message_upsert.py` passes, with `actor_email` on both writes.
4. The other S12 tests pass unchanged, and
   `test_only_the_fold_passes_author_from_run` still passes.
5. `entityPillsAuthor.test.ts` and `assistantCheckpoint.test.ts` pass
   unchanged.
6. `test_migration_prefixes.py` passes. The ladder applies the migration,
   and two more replays of the file give no error.
7. Five mutations each turn a test red: no run-member check, no seal check,
   the fold let through on a system row, the fold let through with another
   starter, and an `unchanged` that is always empty.
8. Fix round 1, R8: a member inserts a reply under the old
   `assistant-{thread}-{run_id}` id before the fold. The fold then writes
   the id from `fold_message_id`, and the real reply is stored.
   `test_a_run_with_no_message_id_cannot_be_preempted` is the fence. A
   `fold_message_id` that returns a predictable id turns it red. A source
   test checks that the route calls the helper once.
9. The migration sets `lock_timeout` to `5s` before its `ALTER`.

### 19.6 What S13 does not do

- `_attribute` still takes the claim of an agent turn from the body when it
  INSERTS a new row. So a member can make a new row that names an agent or a
  system summary. S14 closes this (§20): no client inserts an agent row or a
  system row now.
- The server mints no row id when the caller sends one. A member who knows
  that id before the first checkpoint of the run can insert first. The fold
  then declines, and the reply of the real run is not stored. The Next chat
  mints its id with `nanoid()` in the browser of the sender, so another
  member does not see it before the run. A caller that sends no id gets a
  server-only id (§19.4 rule 9).
- Before the fold, a second tab of the same member can write older content.
- The LiteLLM and batch paths have no fold, so their rows never seal. Since
  S14 the browser save of a LiteLLM reply is declined (§20.5, D-PM-39).
- S13 does not change the translator, the browser or the stream. Nothing in
  the browser reads `unchanged` yet.

### 19.7 Verification

```bash
# R8: a real Postgres, with the ladder applied. Set both variables.
export DATABASE_URL=... TENANT_LADDER_DATABASE_URL=...
uv run pytest tests/unit/test_rooms.py tests/unit/test_chat_message_upsert.py \
  tests/unit/test_chat_hardening.py tests/unit/test_migration_prefixes.py \
  tests/unit/test_tenant_coverage.py -q -rs
uv run pytest evals/trajectories/test_chat_fold_trajectory.py -q
G=apps/services/gateway/gateway
uv run ruff check $G/routes/chat.py $G/chat_fold.py
uv run mypy $G/routes/chat.py $G/chat_fold.py
cd workbench/control_plane && npx tsc --noEmit
npx vitest run src/components/entityPillsAuthor.test.ts \
  src/lib/assistantCheckpoint.test.ts
npx vitest run
```

The `-rs` output must show no R8 skip. With `DATABASE_URL` set,
`test_tenant_coverage.py` has two tests that fail by construction on a fresh
ladder. `.github/workflows/pr-check.yml` records why, and they are not S13's.

---

## 20. No forged agent rows (S14)

**Status: BUILT 2026-09-28.** The spec-auditor cleared the scope on
2026-09-28 (GO-NARROWED with option A, against origin/main `2020af8d`). S14
closes the first residual in §19.6. `groups_sessions_authority.md` §4 links
here.

### 20.1 The answer

Only the server creates an agent row or a system row. A client may update an
agent row that the server created, under the S13 rules. A client may not
insert one. The gateway creates the agent row of a run before it opens the
stream.

### 20.2 What exists

1. **`_attribute` takes an agent author from the body on an insert.** Before
   S14, a client write with the role `assistant` made a new agent row. The
   kind `agent` did the same. That row named any agent (`_attribute`).
2. **Any sender could insert an agent row or a system row.** Both reach the
   model context of every other member. `acb_llm/context.py`
   `assemble_run_context` keeps the roles `user`, `assistant` and `system`
   in the history that it gives the model.
3. **Three writers reached the row.** They were the translator checkpoints
   (`route.ts` `persistAssistantMessage`), the fold
   (`chat_fold.persist_final_assistant_message`) and the browser
   (`sessions.ts` `saveMessages`).
4. **The browser never saves a streaming agent row.** `saveMessages`
   filters out an `assistant` row while `streaming` is true. So during a run
   the translator and the fold are the only writers of the agent row.
5. **No server writer of system rows exists.** The browser saves the
   compaction summary (`compact/route.ts`, the id `compact-<ms>`) and other
   system rows. The gateway writes none.

### 20.3 What S14 builds

1. **The mint.** `run_agent_stream_endpoint` in `routes/agent.py` calls
   `_mint_run_row` once. The call comes after
   `_refuse_if_another_run_is_active`, so a steered or refused turn mints
   nothing. It comes before `StreamingResponse`. `_mint_run_row` calls
   `_ensure_session`, then `_upsert_messages` with `mint=True`. The row id
   is `_persist_message_id`, and the content is empty. The author is the
   agent that runs. The run member is the starter, in lower case.
2. **The mint never updates.** The `WHERE` of the update starts with
   `NOT CAST(:mint AS boolean)`. When a row with that id exists, the mint
   changes nothing, even the reply of the caller.
3. **The mint is best effort.** On a failure it logs `agent.mint_failed`,
   and the run goes on. The fold then inserts the row at the end.
4. **The insert guard.** The `INSERT` is a `SELECT` with
   `WHERE CAST(:may_insert AS boolean) OR EXISTS (...)`. `_upsert_messages`
   sets `may_insert` for a human row, for the fold (`author_from_run`) and
   for the mint. `unchanged` names a declined insert. `VALUES` coerced
   each parameter to its column type and a `SELECT` does not, so each
   parameter has a `CAST`. The R8 tests prove the types.
5. **One new keyword, `mint`.** Only `routes/agent.py` passes it. The fold
   keeps `author_from_run=True`, and it may still insert.
6. **An empty row stays hidden.** `_get_messages` omits an agent row with no
   content, no tool events, no custom events and no reasoning. The filter is
   in the SQL, so a `LIMIT` does not count the hidden row.
7. **Three roles only** (fix round 1). `MessageRecord.role` is
   `Literal["user", "assistant", "system"]`, so the route answers 422 for
   any other value. `_attribute` treats only the role `user` as a human
   turn. The browser draws every other role as an agent reply. So a role
   such as `tool` or `Assistant` would pass as one. The
   `chat_message` CHECK already refused these values, but as a 500 for the
   whole batch.
8. **`_ensure_session` raises no role** (fix round 1). The mint calls it at
   every run start. It adds the owner row only for the creator of the
   session (`chat_session.user_id`). Since round 2 it also needs a room
   with no participant row yet. An owner may remove the creator, and a
   later run of the creator must not give the role back. It adds the `primary`
   agent row only when the room has no `primary` agent. A browser-created
   session
   still gets both on its first run, because `_upsert_session` makes
   neither. The fold calls the same helper, so the fix covers it too.
9. **The first content sets the time** (fix round 1). The mint stamps the
   server clock at request time, and the prompt carries the browser clock.
   While the stored content is empty, a write that passes the `WHERE` moves
   `timestamp_ms` forward to its own value. The first checkpoint then sets
   the time, as it did before S14.
10. **The mint is bounded** (fix round 1). `_mint_run_row_bounded` waits
    `_MINT_TIMEOUT_S` (2 seconds) at most. On a timeout it logs
    `agent.mint_failed` with the reason `timeout`, and the run goes on.

### 20.4 Rules

1. **Only the server inserts an agent row or a system row.** The mint and
   the fold are the server. A client inserts a human row only.
2. **The mint names the agent.** Its author is the agent that runs, from
   `_address_agent`. Its run member is the member who started the run.
3. **Client updates keep S13.** A client may update an agent row only as the
   run member and before the seal. §19.4 rules 1 to 3 are unchanged.
4. **A decline is not an error.** The status is 200, and `ok` is true. The
   id is in `unchanged`.
5. **One data migration, no flag and no route.** S14 changes SQL
   statements and adds one call on an existing route. Round 4 adds one data
   migration, 221 (§20.3 rule 14). It adds no column and no table.
6. **No change in Next.** The Next chat already sends the id that the
   gateway mints, as `assistant_message_id`. The only Next edit is the
   LiteLLM comment in `sessions.ts` `saveMessages`.
7. **The fold id rule stays.** §19.4 rule 9 is unchanged.
8. **§19.4 rule 4 changes.** A client may not insert a system row now. No
   writer updates one.
9. **The docs.** S14 updates §19.6, item 3 of
   `apps/services/gateway/AGENTS.md`, `groups_sessions_authority.md` §4, the
   comment above `_MESSAGE_UPSERT_SQL`, and the `MessageRecord` and
   `_attribute` docstrings.
10. **A run start never changes the roles of the room.** Only the creator of
    a session gets an owner row from `_ensure_session`, and a room keeps one
    `primary` agent. A member who reaches the room through a group or an org
    grant stays at that role when she starts a run.
11. **The creator owns only a room with no participant row** (round 3).
    `resolve_room_access` gives the owner role to `chat_session.user_id`
    only when the session has no participant row at all. That covers a
    legacy session and a browser-created session before its first run. A
    room with any membership resolves from its rows and grants only. So
    when an owner removes the creator, the removal holds.
12. **The first add keeps the creator.** In a session with no participant
    row, the creator can invite only through the fallback in rule 11. So
    `_add_participant` writes her owner row first, in the same transaction.
13. **The other creator checks follow rule 11.** `SESSION_VISIBLE_SQL`
    (the session list, a rename, the history read and the active list),
    `_delete_session` and `_upsert_session` take the creator arm only while
    the room has no participant row. `_thread_owner_ok` and the cancel
    check go through `resolve_room_access`, so they follow it with no
    change.
14. **Migration 221 makes rule 11 safe to ship** (round 4). Before S14,
    every creator was an owner with no row. Live sessions can hold rows for
    guests and none for the creator:
    - a chat shared before its first fold, because the old
      `_add_participant` inserted only the guest
    - a LiteLLM chat that never folds, which the creator then shared
    - a session whose fold failed, which the creator then shared

    Rule 11 alone would lock each of those creators out on the deploy.
    `infra/postgres/221_chat_session_creator_owner_backfill.sql` inserts
    (session, creator, `owner`) for every session whose `user_id` is an
    email, with `ON CONFLICT DO NOTHING`. The deploy applies it before the
    restart (R6). So every creator keeps the access that main gives today.
    That includes a creator whom an owner "removed", because main never
    enforced a removal. From the deploy on, a removal holds. A demoted
    creator keeps her role, and a `user_id` that is not an email gets no
    row. Under FORCE RLS the file binds each tenant in turn and copies the
    `organization_id` of the session.
15. **`isOwner` is the caller's own role** (round 4). The session list sets
    it when the caller's participant row says `owner`, or when the caller
    created a session that has no participant row. One `LEFT JOIN` on the
    participant key gives the role, so the list makes no query for each
    row.
16. **A creator whose `user_id` is not an email keeps the fallback in every
    room** (round 5). Such an id is `'default'`, which the chat uses when a
    caller has no email. It is outside the participant grammar:
    `routes/rooms._valid_subject` takes an email, `group:<slug>` or `org`,
    and the authority fold skips any other subject. So no owner row can
    stand for it, and no owner can remove it. Rules 11, 13 and 15 apply the
    "no participant row" test to an email creator only. Migration 221 and
    the first-add owner row keep their filter on `@`, and `_ensure_session`
    keeps its own. So a non-email creator keeps the owner role that main
    gives it, before and after the deploy.

### 20.5 What stops working (D-PM-39)

The owner decided this on 2026-09-28: ship the security fix now, and let a
later slice give these rows a server writer.

1. **A LiteLLM reply stays in the browser.** A reply from a Tier model or a
   Gemini model on the general chat has no server writer. The browser save
   of it is now declined. The default model, `auto`, runs through
   `/agent/run/stream`, so it is not affected.
2. **A compaction summary stays in the browser.** It is a system row, and
   the browser save of it is now declined. So a second device has no
   compaction checkpoint, and it compacts the conversation again.
3. **A reconnect with no local row writes no duplicate.** `useAgentChat.ts`
   makes a new placeholder id when no agent row survived a reload. Before
   S14 the browser saved that row beside the row of the fold. Now the
   server declines it.
4. **The fallback id of the translator writes no duplicate.** When the
   browser sends no id, `route.ts` makes `assistant-{thread}-{ms}` for its
   checkpoints, and the gateway folds a different id. Before S14 the server
   stored both rows. Now the server declines the checkpoint insert.
5. **The batch path is not reachable from the chat.** The legacy batch path
   in `route.ts` (`/agent/run`) has no fold and no mint. The chat does not
   send a turn to it.

### 20.6 Acceptance — S14

1. R8 tests in `test_rooms.py`, through the real `_save_as`, the real mint
   (`_mint_run_row`) and the upsert as the fold calls it:
   - Bob saves a new `assistant` row that names `projects-assistant`. No
     row exists, and `unchanged` holds its id.
   - Bob saves a new row with the role `user` and the kind `agent`. No row
     exists.
   - Bob saves a new system row. No row exists, and `unchanged` holds its
     id.
   - Alice starts a run. The mint makes the row with empty content, the
     agent as its author, and Alice as its run member.
   - A checkpoint by Alice on the minted id changes the content. A
     checkpoint by Bob does not.
   - The fold on the minted row takes it and seals it.
   - When the mint failed, the fold inserts the row.
   - The mint on an existing id changes nothing, for a human row, a system
     row, another member's agent row and Alice's own reply.
   - A reader does not see an empty minted row. A reader sees it after a
     checkpoint.
   - A new human row still inserts, and Alice still updates her own human
     row.
   - Fix round 1: the roles `tool`, `Assistant`, `system ` and `developer`
     each get 422 and no row, through the real handler behind FastAPI. A
     `user` row still inserts.
   - Fix round 1: a member who reaches the room through a group starts a
     run and gets no owner row. The fold gives none either. A run of a
     second agent adds no second `primary` agent row.
   - Fix round 1: the mint on a new session id creates the session, the
     owner row and the `primary` agent row. A checkpoint by the starter
     lands. A browser-created session gets both rows on its first run.
   - Fix round 1: a prompt at T, with the server clock two seconds behind,
     sorts before its reply after the first checkpoint.
   - Fix round 1: a slow mint returns after the timeout and logs the reason
     `timeout`.
2. The S13 tests pass, with the seeding moved to the mint.
   `test_no_client_inserts_or_updates_a_system_row` replaces
   `test_a_client_inserts_a_system_row_and_never_updates_one`.
3. A source test: only `routes/agent.py` passes `mint=True`, and the route
   mints once, after `_refuse_if_another_run_is_active`.
   `test_only_the_run_route_mints_and_only_after_the_refusal` is the fence.
4. `test_chat_message_upsert.py` seeds through the mint and passes.
   `test_chat_fold_trajectory.py` passes, except
   `test_detached_run_persists_final_message`, which also fails on main for
   an environmental reason. `entityPillsAuthor.test.ts` passes.
5. Five mutations each turn a test red:
   - the guard lets a client insert an agent row
   - the guard lets a client insert a system row
   - a mint updates a row
   - the mint comes before the steer or refusal check
   - the reader sees an empty minted row

   Fix round 1 adds six more, and each turns a test red:
   - `role` goes back to `str`
   - the owner row goes in on an existing session
   - a second `primary` agent row goes in
   - the timestamp rule is removed
   - the mint has no timeout
   - the mint does not call `_ensure_session`

   Round 2 adds one more. The owner insert without its `NOT EXISTS` turns
   `test_a_removed_creator_does_not_win_owner_back_by_a_run` red. In that
   test Bob removes Alice, the creator. Alice then runs through a group,
   and she gets no owner row.

   Round 3 adds these R8 tests:
   - Bob removes Alice, and she has no group grant. She resolves with no
     role and no access. The session list, a rename, a session upsert and
     a delete by her all do nothing.
   - Bob removes Alice, and she is in a group of the room. She resolves as
     `member`.
   - A legacy session with no participant row is still its creator's.
   - A browser-created session that Alice shares before her first run gets
     her owner row first, and she keeps `owner`.
   - The verifier's case: Bob saves a new `assistant` row with the kind
     `human`. No row exists, and `unchanged` holds its id.

   Round 3 adds six mutations, and each turns a test red:
   - the unconditional creator fallback
   - the creator arm of `SESSION_VISIBLE_SQL` with no `NOT EXISTS`
   - the creator arm of `_delete_session` with no `NOT EXISTS`
   - the creator arm of `_upsert_session` with no `NOT EXISTS`
   - no creator owner row before the first add
   - `_attribute` treats an `assistant` row with the kind `human` as human

   Round 4 adds these R8 tests:
   - A session holds a row for Bob and none for Alice, its creator. After
     221, Alice resolves as owner and sees the session in her list.
   - A creator demoted to `member` stays `member`.
   - A `user_id` that is not an email gets no row.
   - 221 runs twice with no error and no change. The ladder applies it too,
     and `test_migration_prefixes.py` passes.
   - `test_chat_creator_owner_backfill.py` builds the four `generated/`
     phases and runs 221 as a role that cannot bypass RLS. Two orgs each
     get their creator row, with the right `organization_id`.
   - `isOwner` is false for a demoted creator, true for an owner who did not
     create the room, and true for the creator of a room with no rows.

   Round 4 adds three mutations, and each turns a test red:
   - 221 inserts no creator row
   - 221 does not bind the tenant
   - `isOwner` goes back to `r.user_id == user_id`

   Round 5 adds two R8 tests:
   - The verifier's case C: `'default'` creates a session with no row and
     adds Bob. `'default'` stays owner, and Bob is a member.
   - 221 on a session with rows [bob] and the `user_id` `'default'`:
     `'default'` still resolves as owner, and 221 writes no row for it.

   Round 5 adds three mutations, and each turns a test red. Each one drops
   the exemption for a non-email creator, which puts the `@` filter back:
   - in `resolve_room_access`
   - in `SESSION_VISIBLE_SQL`
   - in `isOwner`

### 20.7 What S14 does not do

- The run member can still write any content in her own reply until the
  seal. A per-run token for the translator is a later slice.
- No server writer exists for a LiteLLM reply or a compaction summary yet.
- The first checkpoint takes its timestamp from the clock of the Next
  server, and the prompt takes the browser clock. A browser clock that runs
  more than the time to the first checkpoint fast can still sort the reply
  before its prompt. That was also true before S14.
- Two members can start their first runs at the same instant on a session
  with no `primary` agent. Each run can then insert a `primary` row. The
  reviewer accepted this (round 2, P3).
- `isOwner` reads the caller's own participant row. An owner role that
  comes only through a group subject does not set it. It is a display flag,
  and the server checks every act.
- A mint that times out can still finish later in its thread. It only
  inserts, so it changes no row that the fold wrote first.
- A run that `SupersedeRefused` stops inside the stream leaves an empty
  minted row. The reader does not see it.
- Nothing in the browser reads `unchanged` yet.

### 20.8 Verification

The §19.7 set, with both database variables, plus these:

```bash
uv run pytest tests/unit/test_run_agent_stream_e2e.py \
  tests/unit/test_agent_run_identity.py \
  tests/unit/test_org_access_control.py \
  tests/unit/test_chat_creator_owner_backfill.py -q -rs
G=apps/services/gateway/gateway
uv run ruff check $G/routes/chat.py $G/chat_fold.py $G/routes/agent.py
uv run mypy $G/routes/chat.py $G/chat_fold.py $G/routes/agent.py
```

The `-rs` output must show no R8 skip.

## 21. Chat is saved on production (S15)

**Status: BUILT 2026-09-29, with fix round 1 (§21.11, §21.12).** This slice
repairs a live defect in production.
A read-only diagnosis of production on 2026-09-29 is the audit
(GO-NARROWED). `chat_message` had never held a row in production, so every
member's chat history lived only in one browser.

### 21.1 The answer

Three defects stopped every chat save. Each one alone was enough.

1. The BFF sent a JSON body as `text/plain`, and the gateway answered 422.
2. Every chat write opened an unbound session, and FORCE RLS refused it.
3. The browser then wrote the empty server answer over its own cache.

S15 repairs all three. The tenant bind also closes a hole in room access,
so the bind of `_load_room` ships in the same change as the write fix.

### 21.2 Defect 1 — the 422 on every browser save

- **What.** The nine BFF routes below sent `headers: await gatewayHeaders()`
  with a string body and no content type. For a string body, Node's `fetch`
  sends `text/plain;charset=UTF-8`. FastAPI does not parse that body as
  JSON. An endpoint that takes a list answers 422 with `list_type`.
- **Evidence.** On production, `POST /chat/sessions` answered 422 twelve
  times in twelve. The messages save answered 422 on every call.
- **The routes.** `api/chat/sessions/route.ts`, and under
  `api/chat/sessions/[sessionId]/` the routes `messages`, `agents`,
  `participants`, `participants/[subject]`, `presence` and `room`. Also
  `api/observability/avatars/generate` and `api/observability/avatars/[name]`.
- **Since when.** Commit `e92d0620` (2026-07-30, "A bearer never leaves
  without an identity") removed the local `"Content-Type": "application/json"`
  from these routes.
- **The repair.** `gatewayFetch` now supplies `Content-Type:
  application/json` for a string body that names no content type
  (`withJsonContentType`, rule 10 in `lib/gatewayFetch.ts`). An explicit
  content type always wins. A FormData body stays as it was, so `fetch` still
  sets the multipart boundary. The nine routes also set the header
  themselves.

### 21.3 Defect 2 — an unbound tenant on every chat read and write

- **What.** Every helper in `gateway/routes/chat.py` opened
  `acb_graph.get_session()`. `acb_graph/db.py` documents it as "Unbound —
  binds NO `app.tenant_id`". `gateway/rooms.py` `_load_room`, the room
  routes, the fold, the mint, `run_trace.record_run_trace` and
  `blob_store.put_file` did the same.
- **Why it fails.** In production the chat tables are FORCE RLS, and
  `organization_id` is `NOT NULL DEFAULT
  current_setting('app.tenant_id', true)::uuid`. A fresh backend has no
  setting, so the default is NULL and the row is refused. A pooled backend
  that once ran `set_config(..., true)` keeps `''`, and the write fails with
  `invalid input syntax for type uuid: ""`.
- **Evidence.** `chat_message` held zero rows, so every GET answered `[]`.
  `run_trace.record_failed` fired 52 times and `agent_run` held zero rows.
  `agent_blob` held zero rows. `ACB_GRAPH_TENANT_BIND=true` was set on the
  box, but no chat path read it.
- **The repair.** Each helper takes an `organization_id` argument and opens
  `acb_graph.tenant_session(organization_id)`. A route handler passes
  `user.organization_id`, which the server resolves from the authenticated
  identity. `routes/agent.py` passes the tenant that it already resolves for
  the detached run to the mint, the fold, the room checks and the history
  loader. `main.py` passes it to its fold. `blob_store` takes an explicit
  tenant, or else the tenant bound on the caller's frame.

### 21.4 The security case — the room answer

Under the unbound read, `_load_room` found no row for any session id. "No
row" resolves to `_unsaved_thread()`, and that is owner access. So once the
writes worked, any member could read and write any room in their own org.

So the bind of `_load_room` is part of this slice and not a
follow-up. Every caller of `resolve_room_access` passes the tenant. The
callers are `routes/chat.py`, `routes/rooms.py`, `routes/memory.py` and, in
`routes/agent.py`, `_resolve_room`, `_thread_owner_ok` and
`_thread_control_ok`.

### 21.5 Defect 3 — the browser cache is wiped

`fetchMessagesFromDb` in `lib/sessions.ts` wrote the server answer over the
localStorage cache on every full fetch. The server answered `[]`, so each
page load erased the member's only copy. An empty server answer no longer
replaces a non-empty cache. The function then gives back the cache.

### 21.6 Why no test caught it

- The test database is the ladder only. It has no generated tenancy phases,
  so it has no RLS, and an unbound write succeeds there. The R8 suites for
  chat ran green against a shape that production does not have.
- Both clients hide every error. `sessions.ts` sends each save with
  `.catch(() => {})` and never reads the status, so a 422 looks like
  success. The fold, the mint and the run trace log a warning and go on.
- No check read production after a deploy. Nothing asked "is there a row".

### 21.7 Rules

1. A chat, room, run or blob helper opens `acb_graph.tenant_session`. It
   never opens `get_session()`. The fence is
   `test_rooms.py::test_no_chat_or_room_path_opens_an_unbound_session`, which
   reads the source and cannot skip.
2. The tenant is a required argument with no default. Seven functions
   take it so: `resolve_room_access`, `_upsert_messages`, `_ensure_session`,
   `_get_messages`, `persist_final_assistant_message`, `record_run_trace`
   and `_mint_run_row`. The same test checks each signature.
3. The tenant comes from the server-side identity or from the run. It never
   comes from the request body, a header or a query (R5).
4. With no tenant a helper fails closed. `tenant_session` raises
   `TenantUnbound`. The room answer is then `_undecidable()`, which denies.
   Nothing falls back to an unbound session.
5. A string body to the gateway goes out as JSON. The fences are
   `gatewayFetch.test.ts` (rule 10) and `gatewayBodies.test.ts`. The second
   one reads every `app/api/**/route.ts`. It fails on a call that sends
   `body: JSON.stringify(...)` with no JSON content type.
6. An empty server answer never replaces a non-empty chat cache. The fence
   is `lib/sessions.test.ts`.

### 21.8 Acceptance — S15

1. R8, in `tests/unit/test_chat_write_under_rls.py`, on the H3 phase-4
   catalog as its NOSUPERUSER NOBYPASSRLS role, with one fresh backend per
   session. It reuses the `promoted` and `app_engine` fixtures. It does not
   patch `get_session`.
   - An unbound write is refused, on a fresh backend and on a pooled one
     with the production error. These two tests record the cause.
   - A new session from `_upsert_session` and from `_ensure_session` lands,
     stamped with the member's org. No tenant raises and writes nothing.
   - The browser save through the real handlers lands and reads back.
   - The `sessions.ts` body sent as `text/plain` gets 422, and as JSON 200.
   - Org B does not see org A's session or message.
   - 🔴 Bob is in org A and is not in Alice's private room. He gets no role,
     cannot read, gets 403 on a save, and no row lands. Alice is the owner.
   - No tenant gives a refusal, never the owner answer.
   - The mint lands. The fold lands, seals the row and writes the
     `agent_run` row. A run trace lands. A blob put lands and reads back.
2. The ladder-shape suites still pass: `test_rooms.py`, the S13 and S14
   tests in it, `test_chat_message_upsert.py`,
   `test_resolve_agent_for_run.py` and the blob store suites.
3. Mutations. Each one fails at least one test:
   - one chat helper back to `get_session()`
   - `_load_room` back to `get_session()` (Bob becomes the owner)
   - the content-type default removed from `gatewayFetch`
   - the cache overwrite restored
4. The smoke check `scripts/smoke_chat_persist.py` and the alarm
   `scripts/alarm_chat_persist.sh` exist and are documented (§21.10).

### 21.9 What S15 does not do

- **The other unbound readers.** Some modules still open `get_session()` on
  a FORCE-RLS table outside the chat save path. They are `routes/workspace.py`,
  `acb_skills/history_tools.py`, the `dynamic_agents` and `pending_commit`
  reads in `routes/agent.py`, `routes/observability.py`,
  `routes/integrations.py`, `routes/integrations_skills.py`,
  `routes/debug.py`, `action_broker/broker.py` and `acb_skills/loader.py`.
  H-201 carries each one with its verdict.
- **The access-request knock.** `acb_auth.access` records an unprovisioned
  sign-in into `access_request`. It resolves the org from the email domain
  and has no tenant yet, so it is not the same one-line pattern. H-201 names
  it.
- **The save race.** Fix round 1 added one retry (§21.12). The upsert and
  the first save still leave the browser in parallel.
- **`/chat/active-sessions`.** Its Redis scan is not tenant-prefixed. A
  thread of another org has no visible row, so it can appear as "unknown".
  H-201 names it.
- **`record_message_feedback`.** Its audit event carries no organization,
  so `acb_audit` files it under the operator org.
- **`write_artifact`.** The agent's own blob mirror takes the tenant bound by
  the run. It passes no explicit one. The executor's shared
  `_WRITE_ARTIFACT_CONTEXT` dict is not per run, so it is the wrong place for
  a tenant.
- **Deploy wiring.** S16 wires the smoke check into `deploy.yml` (§21.10).
  The alarm stays manual, and §21.10 gives the reason.
- **A member with no organization.** Such a member now gets no chat
  persistence and no room access, where before the ladder shape let the
  write through. That is the fail-closed rule.

### 21.11 Fix round 1 — a session of another tenant is not a new thread

The reviewer found a P0 in the first build of S15. Under FORCE RLS, a
session of ANOTHER tenant reads as "no row". `_load_room` returned `None`
for it, and `resolve_room_access` turned `None` into `_unsaved_thread()`,
which is owner access.

The stream relay keys a run by the bare thread id
(`orchestrator/stream_relay.py`). So a member of org A who held an id of
org B could follow B's run, cancel it and steer it. That member could also
attach rows to B's session id. The unique index on `chat_session.id` and the
`chat_message` foreign key both ignore RLS.

**The repair.**

1. Migration 222 adds `public.chat_session_exists(p_id text) RETURNS
   boolean`. It is the first SECURITY DEFINER function in the ladder. It
   returns one bit across tenants and never a row or an org id. It sets
   `search_path = pg_catalog, pg_temp`, and it names `public.chat_session`
   in full.
2. FORCE RLS binds the owner too, unless the owner is a superuser or
   BYPASSRLS. So the function returns NULL when its owner cannot see through
   RLS on a table with RLS. The gateway reads NULL as "cannot tell" and
   denies. Measured on production on 2026-09-29: `postgres` owns
   `chat_session` with `rolbypassrls = t`.
3. The migration revokes EXECUTE from PUBLIC, and from `anon`,
   `authenticated` and `service_role` by name. Supabase gives a new function
   to those three, and PostgREST publishes it as an RPC. It grants EXECUTE to
   `acb_app` only.
4. `rooms.session_exists_elsewhere` calls the function in the caller's
   bound session. `_load_room` asks it when the bound read finds no row. An
   id that exists elsewhere, or that the function cannot place, gets no role
   and no capability. Only an id that exists nowhere is the caller's new
   thread.
5. `_ensure_session` and `_upsert_session` raise `SessionOfAnotherTenant`
   before any write. `POST /chat/sessions` then answers 404.
   `_upsert_messages` declines every id. So the mint and the fold also write
   nothing under the id.

**Acceptance, added to §21.8.** On the phase-4 catalog, as the app role:

- Org A's member gets `can_read`, `can_send` and `can_cancel` all false on
  an org B session. `_thread_owner_ok` and `_thread_control_ok` are false.
- A's save under B's id gets 403, and A's session upsert gets 404. No row
  of org A lands, and B's title does not change.
- `_ensure_session` raises, `_upsert_messages` declines, and the mint and
  the fold write no row of org A. B's rows stay as they were.
- A truly new id is still the member's own thread, with the owner role.
- With the function owned by a role that cannot bypass RLS, even a new id
  is refused. The check fails closed.
- The ACL of the function has no PUBLIC entry, and it names the app role.
- Mutation: `_load_room` without the check gives Alice owner on B's id and
  fails 3 tests. The write guard off fails 2 tests.

**Not in this round: the relay prefix.** Prefixing the stream relay keys
with the tenant is defence in depth. It reaches 10 importing modules and 16
client sites, and it moves the `stream_relay.py`, `steer.py` and
`room_stream.py` entries in the `test_tenant_redis.py` ratchet. That is not
small, so H-201 carries it. The room check above is the boundary, and every
reconnect, cancel and steer path goes through it.

### 21.12 Fix round 1 — the first save survives the race

The session upsert and the first save leave the browser at the same time.
When the save lands first, the `chat_message` foreign key refuses it, and the
answer is a 5xx. A member who closed the tab then lost the first prompt.
`postMessagesWithRetry` in `lib/sessions.ts` sends the save again once, after
about 1 s, on a 5xx or a network failure. It does not retry a 4xx, and it
never throws. The fence is `lib/sessions.test.ts`.

### 21.10 Verification

R8 needs a tenancy-shaped database: the ladder, and the four generated
phases, and a NOSUPERUSER NOBYPASSRLS role. The `promoted` fixture builds all
three from `TENANT_LADDER_DATABASE_URL`.

```bash
export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5550/acb_tenant
export DATABASE_URL=$TENANT_LADDER_DATABASE_URL
uv run pytest tests/unit/test_chat_write_under_rls.py \
  tests/unit/test_h3_rls_promotion_rehearsal.py \
  tests/unit/test_tenant_coverage.py tests/unit/test_migration_prefixes.py \
  tests/unit/test_tenancy_insert_fence.py -q -rs
uv run pytest tests/unit/test_rooms.py tests/unit/test_chat_message_upsert.py \
  tests/unit/test_resolve_agent_for_run.py tests/unit/test_chat_hardening.py \
  tests/unit/test_run_agent_stream_e2e.py tests/unit/test_org_access_control.py \
  tests/unit/test_chat_creator_owner_backfill.py \
  tests/unit/test_blob_store_durability.py tests/unit/test_blob_store_instance.py -q -rs
G=apps/services/gateway/gateway
uv run ruff check $G/routes/chat.py $G/rooms.py $G/routes/rooms.py $G/chat_fold.py \
  $G/run_trace.py packages/acb_memory/acb_memory/blob_store.py
uv run mypy $G/routes/chat.py $G/rooms.py $G/chat_fold.py $G/run_trace.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

The `-rs` output must show no R8 skip.

On the box, after a deploy:

```bash
# The smoke member and org exist already. This script creates neither.
cd /opt/acb/app && bash deploy/smoke_chat.sh   # as acb. Exit 0, 1 or 2.
bash scripts/alarm_chat_persist.sh     # exit 1 when a write failed in 10 min
```

**Every deploy runs the smoke (S16).** The `chat-smoke` job in `deploy.yml`
runs after the deploy is verified, on the push path and on the pull path. It
sends `deploy/smoke_chat.sh` to the box over ssh. The script mints a session
of 600 s in memory from the workbench `AUTH_SECRET`. It waits for
`/api/auth/me` to name the smoke member in the smoke org, 12 tries 10 s apart.
Then it runs `scripts/smoke_chat_persist.py`.

- Exit 1 (a step failed) and exit 2 (the environment is wrong) make the run
  red at once, with no retry. An ssh blip retries, 3 tries in total.
- Only a smoke that ran and failed is red. When ssh cannot reach the box for
  the whole connect budget, or drops on all 3 tries, the job gives a
  warning and stays green. That keeps the H-142 contract for the pull path:
  `pull-delivery` proved the SHA by HTTPS, and a runner that cannot reach
  the box must not turn it red.
- The run has no rollback. R6 says we only roll forward, so a red run means
  "the release is live, and chat does not save".
- The wait and the smoke hold a SHARED lock on `/opt/acb/acb-deploy.lock`.
  Every apply takes that file EXCLUSIVE, on both delivery paths. So a second
  deploy cannot restart the services in the middle of a smoke. When another
  deploy holds the lock for 240 s, the script exits 75, and the job gives a
  warning, not a red. That deploy runs its own smoke. A failure prints the
  commit that the box holds.
- The cookie goes to each child through the environment only. The script
  never writes it to a file, never puts it on a command line, and never
  prints it.
- Before step 1, the smoke deletes each session of the smoke member that is
  older than one hour, at most five a run. It uses the BFF list and the BFF
  delete, and no database access. A failed sweep prints `WARN 0` and does
  not fail the smoke.
- The fence is `tests/unit/test_deploy_smoke_wiring.py`.

**The alarm stays manual.** `alarm_chat_persist.sh` counts write failures in
the last 10 minutes of the gateway journal. After a deploy, that window holds
the failures of the old code too. So a deploy gate on it can go red for a
defect that the release fixed. A timer is the correct home, and no change has
added one.

The smoke check reads `/api/auth/me` first. It stops, and writes nothing,
when the cookie is not the smoke member in the smoke org.

**The smoke org and member.** These are the facts of record:

- The org slug is `smoke-chat`, and its id is
  `2df62642-751d-4ddd-a079-f643ea544c74`. An operator made it on 2026-09-29
  with `provision_local_organization`.
- Its `console_mirrored_at` is set. So the Console mirror reconciler never
  gives it a trial, seats or credits.
- Its `first_party` is false on purpose. When it is true, the org opens self-mutation PRs.
- The member is `smoke-chat@smoke.metorite.invalid`. The `.invalid` domain
  never receives mail.
- The script uses the host `app.metorite.com`, which is the `AUTH_URL` host.
  The host `metorite.com` is a different site, and its `/api/auth/me` gives 404.
- The cookie is `__Secure-authjs.session-token`. `deploy/smoke_chat.sh`
  mints a new one for each run. The static file `/home/acb/.smoke/cookie`
  was for the first manual run, and no step reads it now.

The first production run PASSED on 2026-09-29, all four steps, on
`https://app.metorite.com`.
