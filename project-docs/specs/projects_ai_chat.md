# Projects · the AI chat — WS-27bm

**Status: ACTIVE. S1 (the reads) and S2 (the daily writes) built
2026-09-22. S2b (the rest of class B), S3 (the guarded acts) and S4 (the
workflows, the views and the forms) and S5 (the rest of the manifest)
built 2026-09-23. Left: the visual review, and the frontend-tool
dispatcher (H-164).** §10 says which slice each part
belongs to. §4.4 lists what the chat reuses, file by file.

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

A rail toggle beside the triage rail (`page.tsx:3218`) shows it docked, so a
member can talk while the board is open. On a phone the chat is a full scene,
like the email assistant. **Those two are S5.**

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
| `ActionResultCard` | Every write | Says what changed, links the row, danger tone for class C |

**The generic card is the default.** A tool the card file does not know
renders as `ActionResultCard` from its class. A new tool never renders as
raw text, and it never needs a card file change to ship. §7.3 depends on this.

**Frontend tools, navigation only.** Through `useFrontendTool`
(`src/hooks/useFrontendTool.ts`): `open_task(id)`, `open_project(id)`,
`open_app("analytics" | "reports")`, `set_filter(...)`. None writes data.
⚠️ Not built. `executeFrontendTool` has no dispatcher in the platform yet
(S4 finding, H-164), so navigation is served by the cards' own links
(`?task=`, `?app=`) until the platform wires one.

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
| **Left** | The visual review in light mode, compact density and a changed accent (H-157, after the flag flip) · frontend navigation tools once the platform has a dispatcher (H-164) | AGENT-SAFE |
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

In `workbench/control_plane`:

```
npx tsc --noEmit
npx vitest run src/app/projects/lib/projectApps.test.ts src/app/projects/lib/assistantPersona.test.ts
```

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
