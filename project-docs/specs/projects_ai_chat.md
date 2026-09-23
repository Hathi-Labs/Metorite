# Projects · the AI chat — WS-27bm

**Status: ACTIVE. S1 (the reads) and S2 (the daily writes) built
2026-09-22. S2b (the rest of class B), S3 (the guarded acts) and S4 (the
workflows, the views and the forms) and S5 (the rest of the manifest)
built 2026-09-23. S6 (navigation and the frontend-tool dispatcher) built
2026-09-23. The visual review ran 2026-09-23 (§4.2). S7, the team
intelligence slices, was designed 2026-09-23 (§13). S7a (capacity) was built
2026-09-23. S7b to S7e are not built.** §10 says which slice each part belongs
to. §4.4 lists what the chat reuses, file by file.

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
3. The agent proposes phases, tasks, owners and dates as a `planCard`
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

All three are agent-proposed. The owner may overrule any of them.

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
| **S7b · Fit** | `GET /projects/tasks/{id}/candidates` and its draft form · `GET /projects/analytics/rebalance` · "Suggested" in the assignee picker · the chat tools `fit_for_task` and `rebalance` (§13.4) | AGENT-SAFE |
| **S7c · Conflicts** | `GET /projects/analytics/conflicts` with seven kinds · the Conflicts panel · the report section `conflicts` · the chat tool `find_conflicts` · the dependency rule moved to the server (§13.5) | AGENT-SAFE |
| **S7d · Plan with capacity** | `propose_plan` gains start dates, phases and dependencies, and shows each owner's fit on the plan card (§13.6) | AGENT-SAFE |
| **S7e · On-the-fly analysis** | The read tool `task_dataset` and the rule for numbers the chat computes itself (§13.7) | AGENT-SAFE |
| **Flip** | `NEXT_PUBLIC_PROJECTS_CHAT` on the box | `enforcement-flip`, granted until 2026-09-30 |
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

### 13.5 S7c — Conflicts

`GET /projects/analytics/conflicts?project_id=&horizon_days=` returns one list.
Each row carries its kind, the tasks and people it names, and one sentence
that says why.

| Kind | Rule | HR tier |
|---|---|---|
| `dependency_order` | A task starts or is due before a task that blocks it is due | no |
| `blocker_late` | A blocker is overdue, and the work it blocks is still open | no |
| `parallel_person` | One person holds tasks in different projects whose start-to-due spans overlap | no |
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

### 13.6 S7d — Plan with capacity

`propose_plan` gains three inputs and one check:
- A start date on each task.
- Phases, created as parent tasks with the phase's tasks as their subtasks.
- Dependencies, written as `blocks` links after the tasks exist, under the
  same one card.
- For each owner, the fit from §13.4 and the hours from §13.3, printed on the
  plan card row. The card marks a row whose owner lacks the hours, and the
  member edits it before confirming.

The plan still creates everything under one card, in one batch.

### 13.7 S7e — On-the-fly analysis

`task_dataset(project_id, filters, columns)` returns a compact table of up to
500 tasks in the viewer's scope. The columns are number, title, project, status
category, assignees, estimate, start, due, completed, created and blockers. The
agent computes an uncommon figure from it, for example cycle time by tag or
the share of work each phase holds.

**The rule for a number the chat computes.** The answer says that the chat
computed it, and from how many rows. If the table was truncated, the chat does
not compute a total from it. A figure that people ask for twice is a
candidate for a server read and a report section, and the chat says so.

### 13.8 What S7 does not do

- It does not write a person's skills, hours or absences. The People app owns
  those writes.
- It does not auto-assign. Every assignment is the class B `assign` with its
  card.
- It does not fix the outlook's capacity figure, which reads only the typed
  `capacity_hours_per_week`. That is H-169.
