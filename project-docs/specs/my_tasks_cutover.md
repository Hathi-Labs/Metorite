# My Tasks — the cutover plan (WS-39, phase 2)

**Status: ACTIVE.** Owner directive, 2026-09-23. Board row **WS-39**.
Decisions this spec rests on: **D52 · D53 · D54 · D62 · D65**, and **D73**,
which this spec records. Owning spec for every slice named in §5.

Where this spec and `task_manager_app.md` §13 disagree, this spec wins. Where
this spec and `work_plan.md` §2 disagree, the board wins.

---

**Built so far.** S5, S6a to S6e and S8a are built and serving. S6f is built
on 2026-09-23 (D77, §4.10). S7 ran on
2026-09-23. S8 PR 1 merged on 2026-09-24 as `6e028aa6` (#411).

S9 merged as `ec979545` (#436). S6g, one inbox and one promote path, is built on
2026-09-24 and is the last slice open.

## 0. One paragraph

The Tasks app becomes **My Tasks**. It reads and writes the one task store
(`pm_tasks` + `pm_task_personal`) and nothing else. The old `gtd_*` store goes
away, table by table, and no table, route, flag or type keeps the name `gtd`.
The Getting Things Done method stays as the product. Only the name and the
store change. This spec turns the four open handoff entries (H-33, H-59, H-62,
H-151) and the two owner-gated runs (H-29, S3b and S3c) into one ordered plan.

## 1. What the owner asked for

Verbatim, 2026-09-23:

> "comprehensive review and update of the tasks app to migrate it towards
> using and ingesting the projects app instead of the external ClickUp API"

> "Rename the tasks app to My Tasks so as to remove ambiguity"

> "update all of the database naming entries also so that there is no
> confusion at all for the future"

> "Apart from this, everything else remains the same as the earlier tasks
> app."

> "We use the internal projects app for external projects"

> "in addition to whatever getting-things-done-related philosophy of setting up
> and managing personal tasks within the My Tasks app"

Three requirements, in the order the owner gave them:

1. **Finish the lens.** WS-39 slices 1 to 4 landed on 2026-08-25. The tail is
   open. It holds the CRUD tail, the local project tree, the promote door, and
   every AI and intake route. §3 measures it.
2. **Rename to My Tasks**, on the surface and in the schema.
3. **Change nothing else.** Capture, clarify, organise, engage, Waiting-For,
   the day planner, contexts, energy, the two-minute rule and Weekly Review
   keep their behaviour.

## 2. What does NOT change

- The GTD semantics in `task_manager_app.md` §13.4. Every disposition, the
  explicit-promise rule for `expected_by`, and the per-member overlay.
- The route `/tasks` and the feature slug `feature:tasks`. §4.2 says why.
- `pm_tasks` and `pm_task_personal`. D53.7 and D53.8 placed every field.
- `user_settings`, `calendar_day_state` and `calendar_rollover_log` (D53.6).
- The five `people*` tables (slice 1 of the rename, 2026-09-21).
- The HTTP paths the client calls. `test_client_route_contract.py` holds them.

## 3. What is true today (measured 2026-09-23)

### 3.1 The client

`workbench/control_plane/src/app/tasks/lib/api.ts` exports 47 functions.
15 consult `lensEnabled()`. 32 do not.

| Group | Functions | State |
|---|---|---|
| A · spine | `fetchItems`, `apiCapture`, `apiPatchItem`, `apiArchiveItem`, `apiDeleteItem`, `apiRestoreItem`, `apiPurgeItem`, `apiDelegateItem`, `apiPlanDay`, `apiRollover`, `apiReplan`, `apiEstimateStats` | lensed (slices 1 and 2) |
| B · promote | `apiMoveTask`, `fetchProjects`, `apiItemStageOptions` | lensed (slice 5a). **No component calls them.** |
| C · CRUD tail | `apiItemDetail`, `apiCaptureBatch`, `apiBulkDispose`, `apiBulkArchive`, `apiOrganize`, `apiListSubtasks`, `apiAddSubtasks`, `apiMergeInto`, `apiFileUnder`, `apiUploadAttachment` | not lensed |
| D · local tree | `fetchLocalHierarchy`, `apiCreateSpace`, `apiCreateFolder`, `apiCreateLocalProject` | not lensed. One consumer: the Clarify "Where" picker |
| E · AI | `apiAtomize`, `apiClarifyPropose`, `apiSuggestTitle`, `apiEnrichItem`, `apiBackfillContext`, `apiPlanProject`, `apiApplyPlan` | not lensed. The gateway side names `gtd_items` |
| F · settings, people, catalog | `fetchTaskSettings`, `updateTaskSettings`, `fetchStatusCatalog`, `fetchPeople`, `fetchOrgPeople`, `createPerson`, `updatePerson`, `uploadResume`, `apiGetDayState`, `apiSetDayState` | store-neutral, or read a table that survives |

Two functions in group A have no caller at all: `apiRollover` and
`apiPlanProject` with `apiApplyPlan`. `apiAgentPlanToday` and `apiPatchItem`
are the only two exports a file outside `app/tasks` and `app/calendar` imports
(`src/components/tasks/TaskToolCards.tsx`).

### 3.2 The gateway

Only `routes/tasks/calendar.py` asks `agent_source()`. Every other module in
`routes/tasks/` reads `gtd_*` by name.

| Family | Routes | The same thing under `pm_*` today |
|---|---|---|
| items CRUD tail | organize, subtasks, bulk, bulk-archive, merge-into, file-under, detail, stage-options, batch | Each piece exists under `/projects/tasks/*` and `/projects/my/*`, with three gaps: **batch capture**, **bulk overlay write**, and one atomic **organize** |
| local tree | `/hierarchy`, `/spaces`, `/folders`, `/local-projects` | `pm_projects` nests. **No route mints a personal child**: `personal_owner` is written only by `ensure_personal_project` |
| AI | clarify, enrich, suggest-title, backfill-context, insights, atomize (`ai.py`, 6 routes) | **None.** No LLM call exists in `routes/projects/` |
| intake | `capture_email.py` (5 routes), `whatsapp/transport/capture.py`, `email_link.py` | **None.** All key on `gtd_items.origin` JSONB |
| project planning | `tasks/planning.py` (2 routes) | **None.** `projects/planning.py` is the DAY planner |
| attachments | `/attachments` | Same registry. `projects/attachments.py` already writes `gtd_attachments` |
| background | `scheduler.py`, `sync.py`, `broker_handlers.py` | `sync.py` and the broker writers are dead since D52. `scheduler.py` reads `gtd_items` |

### 3.3 The database

| Table | Rows on production | Fate |
|---|---|---|
| `gtd_items` | 2, both unmigrated, one owner | dropped by the armed guard |
| `gtd_waiting` | 0 | dropped by the armed guard |
| `gtd_projects`, `gtd_spaces`, `gtd_folders` | 0 | dropped after the tree port |
| `gtd_contexts` | 0 | dropped. §4.5 |
| `gtd_attachments` | 2 | renamed. Shared with Projects |
| `gtd_horizons`, `gtd_reviews` | 0 | renamed. WS-21 and WS-18 keep them |
| `gtd_retirement_arm` | 0 | dropped after the guard fires |
| `pm_tasks` | 15 | the store |
| `pm_task_personal` | 0 | the overlay |

Production serves `2df07bf1` with `tasks_lens: false`. Neither flag is set in
the box `.env`. Migrations 187 to 192 are in the ledger.

⚠️ **Migration 190 is in the ledger as applied.** The runner skips a file whose
checksum has not changed. So "the next deploy applies 190" in
`docs/TASKS_LENS.md` is false under the ledger. The drop needs a **new**
migration that calls `gtd_retirement_drop()` again. §6 carries the corrected
order.

### 3.4 Two stale worktrees, one live branch

H-158 (the `process.env` inline fix in `lens.ts` and `nav.ts`) landed on
2026-09-23 in PR #382. S7 verifies the literal in the served bundle before it
trusts the flag.

## 4. Decisions — D73, recorded here

`work_plan.md` §3 carries the one-line entry. The reasoning lives here.

### 4.1 The name is **My Tasks**

The nav label, the page title, the empty states, the assistant persona and
every member-visible string say **My Tasks**. "Tasks" alone is what a Projects
board is full of, and one word for two things is the CLAUDE.md §5 defect on
the surface. The personal root project is already named "My Tasks"
(`ensure_personal_project`). The app and its root now agree.

### 4.2 The route and the slug do NOT change

`/tasks` stays. `feature:tasks` stays. A route rename breaks bookmarks, the
`access.ts` gate table, the launch-surface fence and every `href` in the tree
for no member-visible gain. A slug rename is a grant nobody holds, and minting
one ships the app dark to every member (the D54.1 argument). Both are
technical names. The label is what a person reads.

### 4.3 The table naming map

Prefix rule: `pm_` is the one task store. `my_tasks_` is a table that only the
My Tasks app reads. A shared registry gets a bare name.

| Today | After S8 | Why |
|---|---|---|
| `gtd_items`, `gtd_waiting` | dropped | S3b moved their rows |
| `gtd_projects`, `gtd_spaces`, `gtd_folders` | dropped | S6b ports the tree to `pm_projects` |
| `gtd_contexts` | dropped | §4.5 |
| `gtd_retirement_arm` | dropped | its one job is done |
| `gtd_attachments` | `attachments` | the file registry both apps write |
| `gtd_horizons` | `my_tasks_horizons` | D65 keeps the store |
| `gtd_reviews` | `my_tasks_reviews` | WS-18 keeps the store |
| `wa_commitments.gtd_item_id` | `wa_commitments.task_id` | it will hold a `pm_tasks` id |
| `pm_task_personal` | unchanged | D53.7 and D53.8 named it |

`pm_task_personal` keeps its name on purpose. It is keyed to `pm_tasks`, three
apps read it, and D53 recorded it. Renaming a table three lenses share to
carry one lens's name would add the confusion this rename removes.

⚠️ **Index, constraint and policy names keep their old spelling.** This is the
convention slices 1 and 2 set, and the reason is replay. A `CREATE INDEX IF
NOT EXISTS` in an old migration re-creates the old name when that file re-runs,
and then two indexes exist.

Renaming them safely means editing eight of the oldest migrations, which H-132
measured at 41 statements. No member, agent or route reads an index name. The
exception is deliberate and reversible.

### 4.4 `origin` lives on `pm_tasks`, as JSONB

Provenance is a fact about the work, not about one member's week. Email
capture, reply capture and WhatsApp capture key their idempotency on
`origin->>'email_id'`, `origin->>'thread_id'` and `origin->>'wa_message_id'`.
`pm_tasks.source` is a CHECKed TEXT and cannot carry them. Migration 211 adds
`pm_tasks.origin JSONB` (nullable, R6) with expression indexes on those three
keys. This closes H-62 (2).

### 4.5 Contexts are derived, not stored

`GET /projects/my/contexts` already derives a member's contexts from their
overlay rows, with counts. The Tasks UI has no "create context" control, and
`gtd_contexts` holds zero rows on production. The AI prompts read
`_user_contexts`, which falls back to `DEFAULT_CONTEXTS` in code. The table
goes. The default list stays in code.

### 4.6 `workflow_stage` writes resolve a status NAME against the task's project

`splitPatch` used to throw on `workflow_stage`, on purpose. The lens resolves
the name through `GET /projects/nodes/{project_id}/statuses` and writes
`status_id` through `PATCH /projects/tasks/{id}`. A name that matches nothing
is refused in the browser, before any request, with the list of valid names.
This closes H-62 (1).

### 4.7 Identifier hygiene follows the schema, in its own slice

`GtdItem`, `GtdProject`, `skill-task-gtd`, the 29 `gtd_*` tool names,
`test_tasks_gtd.py` and `test_gtd_quality_trajectory.py` carry the name in
code. They change in S9, after the schema, so one `tsc` run and one pytest run
verify the sweep. The tool family moves all at once (H-151).

### 4.8 Continuity with Projects — one product, two lenses (D73.8)

Owner directive, 2026-09-23, in two messages:

> "the tasks app works harmoniously with the projects app, and there's a
> continuity of integration between the tasks app and the projects app"

> "When a project is set up for a particular person and I am that person"

> "it should show up in my tasks in an appropriate way"

> "that helps me manage my day, calendar, and tasks list"

Projects is project management across the organization. My Tasks is personal
planning: my day, my calendar, my tasks, and my private projects. Both read
the one store, so continuity is a property to verify, not a sync to build.

**What already carries over (measured 2026-09-23).**

- A task assigned to me in Projects is in `/projects/my/inbox`, through the
  assignee arm of `MY_TASKS_FROM`. Its disposition is derived until I state
  one. NEXT when assigned to me. SOMEDAY in a backlog lane. DONE when closed.
- The day planner's candidate pool and the overdue strip compose the same
  fragment, so an assigned task enters my day and my calendar.
- The card vocabulary is shared: `TaskCardShell`, `TaskMeta`, `StatusChip`
  and `statusAccent` draw a task the same way in both apps.

**What does not carry over, and S6e builds.**

1. **A project where I am the lead** is invisible in My Tasks today. Only a
   task assigned to me in it shows. The lead is `pm_projects.lead`. The project
   must appear in the Projects view of My Tasks with its open tasks. The ones
   assigned to me come first.
2. **A task assigned to me that I have not looked at** has no overlay row. It
   lands straight in Next Actions. My Tasks must show it in a "From Projects"
   group at the top of the inbox until I triage it. Triage writes the overlay.
   The derivation rule of D53 does not change.
3. **The task detail is two panels.** My Tasks draws `ItemDetail.tsx`, a
   ClickUp-era composition. Projects draws `TaskPanel.tsx`. A member who opens
   a task in each app must meet the same fields in the same order. The fields
   are lane, priority, assignees, due date, custom fields, tags, comments,
   attachments and timeline. My Tasks adds its overlay strip above them. That
   strip holds disposition, context, energy, estimate and defer. One
   composition, imported, not copied.
4. **The way back.** A task card in My Tasks names its project and links to
   that board. The task panel in Projects shows the viewer's own disposition
   chip when they hold an overlay row. It shows nothing about anybody else's.
5. **The move dialog is the Projects one.** S6c reuses `move.py`'s preview and
   the Projects move dialog rather than drawing a second one.

### 4.9 Stages come from Projects, grouped by category (D73.9)

Owner request, verbatim, 2026-09-23:

> "if we do not have any ClickUp connection, then the status mapping also
> needs to be removed from the settings of My Tasks. Make sure we are properly
> mapping stages with the Projects app."


1. The ClickUp status mapping and the Kanban stages editor leave the My Tasks
   settings. One read-only note says where stages come from.
2. Next Actions groups a task by its lane CATEGORY
   (`pm_task_statuses.category`). The groups are To do (`todo`), In progress
   (`in_progress`) and Done (`done`), in that order. A task in a `backlog`
   or `triage` lane sits under To do, and a `cancelled` lane hides it
   (point 5).
3. A card keeps its own lane NAME as its pill. So "Building" in one project
   and "In progress" in another both sit under In progress.
4. A drag into a group resolves the category to one lane. It is the first
   lane by position with that category in the task's own project
   (`GET /projects/my/tasks/{id}/lanes`). Then the client PATCHes
   `/projects/tasks/{id}` with that `status_id`. A drag into Done goes
   through `POST /projects/tasks/{id}/complete` (§13.5a decision 1).
5. **A stated NEXT is never hidden by its lane.** Next Actions shows every
   task whose effective disposition is NEXT. A task in a `backlog` or
   `triage` lane sits under To do. Only a `cancelled` lane hides a task. The
   rule "backlog is Someday" is the derivation for a task with NO stated
   disposition, and `itemsForView("next")` already applies it. Without this
   rule, three kinds of task vanished from the list while the sidebar still
   counted them:
   * every task the S3b backfill moved, because migration 189 put each one in
     its root's Inbox lane (category `backlog`);
   * every capture clarified to Next, because an organize writes only the
     overlay;
   * every quick-add in the To do group.

   A drag to In progress resolves the lane as in point 4. A drag to To do on
   a task in a `backlog` lane moves it to the first `todo` lane.
6. A project with no lane of the category moves nothing. A toast names the
   project.
7. The client and `routes/tasks/settings.py` stop reading and writing
   `workflow_stages` and `status_stage_map`. The `user_settings` columns stay
   until a later contract (R6).

Fences: `app/tasks/lib/statusCategory.test.ts` (the grouping, the lane
resolution and the settings modal source) and `naming.test.ts` (no member
string says ClickUp).

### 4.10 One set of fields across My Tasks and Projects (D77)

Owner directive, verbatim, 2026-09-23, in six fragments:

> "I don't want to unnecessarily duplicate fields or have related fields in the
> Projects app and My Tasks app."

> "The My Tasks app might have specific fields for related context, from a
> more task-management perspective (personal and individual)"

> "but it should derive all the data or reuse all the field data from the
> Projects app itself."

> "For example, deadlines, priority" … "should be derived from the Projects
> app."

> "If you think that the Projects app fields are actually lacking and the My
> Tasks app has certain fields that are richer in nature,"

> "they should be included in the Projects app itself."

**The rule.** A fact about the WORK has one home: `pm_tasks`, or a side table
of it. Both apps read and write that home. My Tasks keeps an overlay only for
how one member holds the work. It never keeps a second copy of a work fact.

⚠️ **This AMENDS D53.8 for one field, by owner directive.** The overlay held
its own `time_estimate_mins`. It now reads the shared column. `work_plan.md`
§3 D77 records the decision.

⚠️ **D77 does not change priority. D76 owns it.** The member's `important`
stays on the overlay, and the shared `importance` only seeds it while it is
unstated. `task_manager_app.md` §13.4b owns that rule.

**The audit, measured on `main` at `74653868`.** Six pairs held one fact
twice, and each pair could disagree.

| # | Pair | What went wrong |
|---|---|---|
| 1 | `importance` vs the matrix cell | A task Projects called Urgent read `Low Priority · Eliminate?`. D76 answers this pair, with its seed |
| 2 | `estimate_mins` vs the overlay's `time_estimate_mins` | A My Tasks estimate never reached People capacity or analytics |
| 3 | the status vs the stated `disposition` | A teammate reopened a task, and it stayed DONE in my list. Projects closed it, and my NEXT stayed |
| 4 | the assignees vs the stored `waiting_on` | Projects reassigned it, and my Waiting-For and the Nudge named the old person |
| 5 | `start_date` vs `defer_until` | The start date was shared and My Tasks did not show it |
| 6 | `tags` vs `context` | The tags were shared and My Tasks lists did not show them |

Projects also lacked four editors that My Tasks had. The description was
read-only in the Projects body. The estimate had no editor. The start date was
not in the detail body. The watch toggle was in the Projects header only.

**Each field, decided.**

| Field | Decision | How |
|---|---|---|
| Priority | **Shared** | `pm_tasks.importance`, in D76's words (`IMPORTANCE_OPTIONS`). The shared body edits it. The My Tasks list column, the card chip, and a Priority sort, group and filter show it. My Tasks never writes it |
| Important | **Mine**, D76 | The member's own answer on the overlay. High and Highest seed it while it is unstated. D77 does not change it. Every My Tasks control over the member's matrix says "Your focus", the name D76 gives the private row in Projects |
| Estimate | **Shared** | `pm_tasks.estimate_mins`. My Tasks' Estimate writes it. The planner reads it. Migration 216 copies the overlay values once |
| Deadline | **Shared**, already `due_at` | A delegation never replaces a deadline the task has. The promised date is `expected_by` |
| Start date | **Shared** | `start_date`, in the body of both apps. My inbox hides the task until the later of it and my own `defer_until`. "Today" is the member's own date, from `user_settings.timezone`, on the server and in the client |
| Completion | **Derived** | `effective_disposition`: a closed lane reads DONE. A stated DONE on an open lane reads NEXT, and `is_triaged` stays true. Nothing is written |
| Waiting on | **Derived from the assignees** | The task's assignees minus me, first by `assigned_at`. The Nudge goes to the same people |
| Tags vs context | **Both kept** | Tags are the team's labels for the work. A context is how I batch MY time, and no work fact holds it |
| Description vs next action | **Both kept** | The body edits the description in both apps. The next action stays mine |
| Watchers | **Shared** | The watch toggle is in the shared body |
| Energy, two-minute, defer, block, flexible, hard date, actuals, rank, clarified, kept mine | **Mine**, unchanged | Each keeps its D53.5, D53.7 or D53.8 reason |

**One word, one meaning.** In My Tasks, "Priority" means only the shared
`importance`. Every control over the member's private matrix says "Your
focus". That covers the list column, the sort, the group, the filter, the
matrix view and the clarify card. A separate Priority sort, group and filter read the
shared `orgPriority`, so a member can rank their list by the company's
priority. The default sort stays the matrix rank, now named "Your focus".

The internal keys (`priority`, `priorities`) stay on the matrix, so a saved
sort or filter keeps its meaning. The matrix cells keep D76's names,
"Low Priority" among them. `focusWording.test.ts` is the fence.

**Promoted into Projects.** The Estimate editor, the description editor, the
start date and the watch toggle, all in the shared `TaskBody`. Also **Time
spent**, a read-only cell beside Estimate. It is the sum of every member's
actuals (`actual_end - actual_start`), bound to the task the caller can see.

**Three choices the directive did not settle, each an agent default:**

1. **The stored `waiting_on` is not ignored.** It is the label when it names
   the same address. It is the whole answer when no other person holds the
   task. A task in my own tree cannot carry a colleague (D62). So a chase
   there is an outside person, and the overlay is the only place that fact is.
2. **A stated TRASH stays TRASH on a closed lane.** Trash is my removal from
   my list, and a closed lane does not undo it.
3. **An actionable disposition on a closed task reopens it for the board.**
   Without this the lane wins, and "mark not done" snaps back to DONE. The
   gateway does it once, in `reopen_if_closed`. The PATCH, the bulk
   `personal` action and organize call it. The checkbox, Focus mode and Undo
   reach the bulk action. The task moves to the first `todo` lane of its own
   set. The move goes through `apply_status_transition`, so the timeline
   records the reopen.

   Only INBOX, NEXT and WAITING count (`OPEN_DISPOSITIONS`). Each says that
   somebody still has to do the work. SOMEDAY, REFERENCE and PROJECT do not
   reopen: filing a finished task is about my list, not about the team's
   work. The stated value is kept, and the closed lane still reads DONE.
   A defer writes SOMEDAY, so a defer never reopens either, and the defer
   card's "your inbox only" stays true.

   Changed 2026-09-24 (F4). The first build reopened on every disposition but DONE and TRASH. An Undo of a
   delete restores a closed task as DONE, so it never reopens it.

## 5. The slices

Each slice is one PR, merged and watched to a serving SHA (CLAUDE.md §4).
Each names its fence (R7). The order is load-bearing.

S5 and S6d do not depend on each other. They may run in parallel worktrees.
S6e follows S6a. It reads the lens seam S6a completes.

### S5 — the surface rename · AGENT-SAFE

**Scope.** Every member-visible string that names the app:

- `nav.ts` label and note, and the `centers.ts` tile.
- The in-app heading in `ListsSidebar.tsx`.
- The assistant persona (`taskAssistantPersona.ts`) and the agent
  `instructions.md`.
- Tooltips, hints and toasts in `app/tasks/`, `app/email/`, `app/notes/` and
  `components/tasks/` that say "Tasks" or "GTD" for this app.
- The one `gtd` tooltip in the inbox (H-132 measured it).
- The agent descriptions in `agent_registry.json`, `config.json` and
  `api/agent/list/route.ts`.
- `launch_surface.md` §2 row.

**Done when.**
1. `nav.test.ts` reads `My Tasks` for `/tasks`, and the label mirror in
   `launch_surface.md` §2 agrees.
2. `rg -n '"Tasks"' src/lib/nav.ts` returns nothing.
3. No string a member reads in `app/tasks/` or `app/calendar/` contains `GTD`
   or `gtd`. Fence: `src/app/tasks/lib/naming.test.ts`, a source grep over
   JSX text and string literals in `app/tasks/components/`.
4. The Projects app still says "task" for its rows. Nothing there changes.

### S6a — the CRUD tail through the lens · AGENT-SAFE · BUILT 2026-09-23

**Scope.** Group C of §3.1, each under `lensEnabled()`, the way the spine is.

| Function | Lens path |
|---|---|
| `apiItemDetail` | `GET /projects/tasks/{id}/timeline` (comments), `GET .../attachments`, `GET /projects/tasks?parent_task_id=` |
| `apiCaptureBatch` | new `POST /projects/my/tasks/batch`, one transaction |
| `apiBulkDispose` | `POST /projects/tasks/bulk` with a new `personal` action that upserts the overlay |
| `apiBulkArchive` | `POST /projects/tasks/bulk` `archive` / `unarchive` |
| `apiOrganize` | new `POST /projects/my/tasks/{id}/organize`, one transaction: overlay, due date, assignees, subtasks, optional move |
| `apiListSubtasks` / `apiAddSubtasks` | `GET /projects/tasks?parent_task_id=`, `POST /projects/tasks` with `parent_task_id` |
| `apiMergeInto` | `POST /projects/tasks/{target}/merge` with `sources: [id]`. The path names the survivor. |
| `apiFileUnder` | `POST /projects/tasks/{id}/move` with `parent_task_id` |
| `apiUploadAttachment` | `POST /projects/tasks/{id}/attachments` |
| `fetchStatusCatalog` | `GET /projects/nodes/{root}/statuses` |
| `workflow_stage` in `splitPatch` | §4.6 |

**Done when.**
1. `lens.test.ts` "the cutover seam is complete" fences every group C name.
2. The two new routes and the new bulk action carry R8 live checks in
   `tests/live/live_ws39_s6a.py`, run against tenant-scratch.
3. `test_client_route_contract.py` passes with the new paths.
4. `apiOrganize` under the lens completes a clarify decision in one request.
   A failed subtask insert rolls back the overlay write.

**Decisions taken at build, 2026-09-23.**

1. `MY_TASKS_FROM` gains a third arm. A task I delegated away stays in my
   list while my overlay says WAITING. The arm is bounded by my grant
   closure on the task's root. The rule: the overlay may narrow a grant,
   never widen one. Consequence: a member who reached a task by assignment
   alone and delegates it loses it from their lists. They hold no grant to
   keep it by. Fence: `test_projects_personal_s6a.py` and check 5c of the
   live script.
2. The planner never packs a WAITING task. `candidates` and `carry_forward`
   refuse it, and a delegate decision clears the delegator's block.
3. A personal child inherits the root's lanes. One lane vocabulary per
   member, so a move into an Area never remaps a status. `ensure_personal_child`
   finds an existing live child by name before it mints one.
4. A delegate decision on a task in the personal tree needs a company
   project. The panel requires one and sends `project_id`, so `organize`
   moves and assigns in one transaction. A delegated task cannot become a
   private project.

### S6b — Areas, and the local tree retires · AGENT-SAFE · BUILT 2026-09-23 (client)

⚠️ **SPLIT IN TWO, 2026-09-23.** The gateway half shipped on its own. One PR
carrying four routes, a privacy guard and a sidebar reviews badly, and the
guard should not wait behind a UI.

**S6b-1 ✅ BUILT** — `GET/POST/PATCH/DELETE /projects/my/areas`, the privacy
guard on node moves, and the fences. The gateway half landed in PR #391.

**S6b-2 ✅ BUILT 2026-09-23** — `ListsSidebar` gains the Areas section with
create, rename and delete. The Clarify "Where" picker reads Areas plus the
company projects. Group D of §3.1 retires under the flag. This lifts the UI
constraint H-29 carries. The backfill creates Areas, and a member now has a
place to rename or delete them.

**Build record, client half (2026-09-23).**

| Function | Lens path |
|---|---|
| `fetchAreas` | `GET /projects/my/areas` |
| `apiCreateArea` | `POST /projects/my/areas` |
| `apiRenameArea` | `PATCH /projects/my/areas/{id}` |
| `apiDeleteArea` | `DELETE /projects/my/areas/{id}`, and the answer names `outcome` |
| `fetchLocalHierarchy` | The Areas, as one flat level. `/tasks/hierarchy` is never called. |
| `apiCreateLocalProject` | `POST /projects/my/areas` |
| `apiCreateSpace`, `apiCreateFolder` | Refused by name. Areas are flat (D65). |

Fences: `lens.test.ts` (the SPINE fences 30 names), `areas.test.ts` (the
store slice, the picker groups, group D under the flag),
`test_client_route_contract.py` (the two new paths).

**Decisions taken at build, 2026-09-23.**

1. An Area is a scope, not a view. A selected Area narrows five views:
   My Next Actions, Waiting For, Someday / Maybe, Done and Archive. Their
   sidebar badges narrow with them. It never narrows the Inbox. A capture lands in
   the personal root before it has an Area, so an Area scope would empty the
   Inbox. The scope persists across views, like the source filter. The same
   row again clears it.
2. Membership is `projectId` alone. A subtask made through the lens carries
   its parent's `project_id`, so it needs no walk. `GtdItem` carries no
   root-project fact, and the client does not invent one.
3. Under the lens the Size=project decision is "make this an Area". The
   panel sends `kind: "project"` with `outcome`, and the gateway mints the
   child. The member picks no space or folder first.
4. The three Area writes throw with the flag off. `fetchAreas` answers `[]`
   with the flag off, because the section does not render then.
5. This slice restored the sidebar's view rows. Slice 4 (`b6192110`) removed
   them by mistake with the Workspaces list. `NavButton` and `PRIMARY` had
   stayed in the file unused. The altitude block did not come back (D65).
6. Clarify offers Areas, and "make this an Area", only for a task in my
   tree. `isPersonalTask` reads the personal root id and the loaded Areas.
   D62 refuses both moves for a task on a company board.
7. An organize decision re-reads the Areas on success and after a refusal.
   So an Area minted from Clarify reaches the sidebar at once.

**Scope.** The gateway learns to mint, rename, archive and list a member's
Areas: children of the personal root with `personal_owner` inherited.
Routes: `GET /projects/my/areas`, `POST /projects/my/areas`,
`PATCH /projects/my/areas/{id}`, `DELETE /projects/my/areas/{id}` (archive,
never a hard delete while tasks remain). The Clarify "Where" picker reads
Areas plus the company projects. `ListsSidebar` gains an Areas section with
create, rename and delete. Group D of §3.1 retires under the flag.

**Done when.**
1. A child created through `/my/areas` carries `personal_owner`, and
   `GET /projects/tree` does not list it. Fence:
   `tests/unit/test_personal_tree.py` gains the write case, and
   `live_ws39_personal_tree.sql` gains a two-member check.
2. `assert_move_keeps_privacy` refuses a move of an Area under a team node.
3. The Clarify picker shows Areas when the flag is on and never shows
   `/hierarchy` data.
4. This closes H-59 (2) and the constraint H-29 carries.

### S6c — the promote door, and Horizons off the surface · AGENT-SAFE · BUILT 2026-09-23

**Scope.** A "Move to project" action on the task card and in the detail
panel. The dialog asks for the destination project, then the destination's
required custom fields (migration 192), then assignees. It calls
`apiMoveTask`. The `horizons` view leaves `ListsSidebar` and the `ViewKey`
union. The data and the routes stay (D65).

**Built.** The dialog is the Projects app's own `MoveTasksDialog`, opened in
its `promote` mode from `PromoteDialog.tsx`, so both apps draw one card.

**Done when.**
1. `promote.test.ts` proves the payload `PromoteDialog` builds reaches
   `apiMoveTask`, and `promoteItem` in `taskStore.ts` is its one caller.
2. A move with a blank required field is refused with the field named.
   Fence: `test_projects_landing.py`, on both move routes.
3. Horizons is off the surface. The `ViewKey` union has no such member,
   and `ListsSidebar.test.ts` reads the sidebar and the union for it.
4. This closes H-59 (1) and (3).

### S6d — the AI and intake tail on the gateway · AGENT-SAFE · BUILT 2026-09-23

**Scope.** One data-access seam for the six `ai.py` routes, the five
`capture_email.py` routes, `email_link.py`, `tasks/planning.py`,
`whatsapp/transport/capture.py`, `whatsapp/transport/context.py` and
`scheduler.py`. The seam has two arms, `gtd_items` and `pm_*`, and
`agent_source()` picks one at call time, the way `calendar.py` does. The LLM
prompts, `propose()`, `propose_with_llm()` and the eval-locked helpers do not
change signature. Migration **211** adds `pm_tasks.origin` (§4.4) and
`wa_commitments.task_id`.

**Done when.**
1. With `TASKS_LENS=1`, every route in the scope reads and writes `pm_*` only.
   Fence: `tests/unit/test_tasks_ai_source.py`, an AST walk that refuses a
   bare `gtd_` string in any of those modules outside the `_GtdArm` class.
2. Email capture is idempotent on `origin->>'email_id'` under both arms.
   R8: `tests/live/live_ws39_s6d.py`.
3. `evals/trajectories/test_gtd_quality_trajectory.py` still passes.
4. The skill's tools need no change. They call `/tasks/*` paths, and the
   paths do not move.

**Accepted changes with the flag off**, measured by the verifier on
2026-09-23. Two behaviours moved on both arms.

1. `POST /tasks/plan/apply` with `target="clickup"` answers 410. No caller
   sends it, and D52 removed the connector.
2. `_annotate_workload` matches a person by email before it matches by name.
   The one store assigns by email.

### S6e — continuity with Projects · AGENT-SAFE · BUILT 2026-09-23

**Scope.** §4.8's five points.

Server: `GET /projects/my/inbox` gains `untriaged=true`, the rows with no
overlay row for me. `GET /projects/my/led` lists the projects where
`lower(lead) = :who`, with their open task counts and my assigned tasks.

Client: the "From Projects" inbox group. The led projects in the Projects view
of My Tasks. `ItemDetail.tsx` rebuilt from the `TaskPanel` composition. The
viewer's disposition chip in the Projects task panel. The project link on the
card landed in S6c (`ProjectLabel.tsx`), so S6e does not build it again.

**Done when.**
1. Assign a task to Bob in Projects. Bob's inbox shows it under "From
   Projects" in the same page load. Bob states a disposition, through
   Clarify or a quick dispose. It leaves the group. A context alone does not
   triage it (build record point 1). Corrected 2026-09-24 in S6g.
   Fence: `tests/unit/test_projects_personal_s6e.py` and a live check in
   `tests/live/live_ws39_s6e.py` with two members in one org.
2. Make Bob the lead of a project with no task assigned to him. Bob's My Tasks
   Projects view lists it. Alice's does not.
3. One task panel composition. `ItemDetail.tsx` imports from
   `app/projects/components/` and holds no field list of its own. Fence: a
   source test that refuses a second field list.
4. The visual pass of CLAUDE.md §4. Light mode, compact density and a changed
   accent. My Tasks beside Projects at four widths. Screenshots in the PR.

**Built.** Branch `my-tasks-s6e`. The record below holds the decisions the
scope did not settle.

1. **`untriaged=true` means "no stated disposition", on a company board.**
   The clause is `(p.task_id IS NULL OR p.disposition IS NULL) AND
   proj.personal_owner IS NULL`. Triage is a disposition write, which
   Clarify and a quick dispose always make. A context, an estimate or a
   planner block is not a triage (`apply_blocks` upserts the same row).
   The member may never have seen who assigned the task. A capture
   in my own tree is not "from Projects". The constant is
   `UNTRIAGED_CLAUSE` in `personal.py`, and the live check reads it.
2. **`is_triaged` did not change meaning.** It is the disposition fact, and
   the Weekly Review reads it. The client keeps a set of ids
   (`fromProjectIds`). A dispose or a clarify drops the id at once. The
   re-read of the server's set runs after the write resolves. A refused
   write puts the id back first. Fence: `fromProjects.test.ts`.
3. **Every inbox row now carries `project_name`.** A member reached by
   assignment alone may hold no grant on the project, so the row names
   itself.
4. **`/my/led` counts direct children only**, through the closed vocabulary
   (`done`, `cancelled`), the way the Areas count does. `my_tasks` is the
   member's open assigned tasks in the inbox shape, so the store holds one
   shape.
5. **The field blocks are one component**, `TaskBody.tsx` in
   `app/projects/components/`. `TaskPanel` and `ItemDetail` host it. The
   body gained a Priority cell and a Due cell. The field list names them,
   and neither panel drew them. My Tasks passes its overlay strip as
   `above` and keeps its Notes editor, so the body draws no Description.
6. **The order stays Projects' order.** Status, priority, assignees, due,
   description, tags, custom fields, links and subtasks, files, discussion.
   Both hosts draw it by construction.
7. **The legacy panel is thinner, not gone.** With the flag off the
   ClickUp-era provider sections are deleted, and the rest stays. S7
   retires the branch.
8. **The Projects view of My Tasks is `LedProjectView`.** The sidebar lists
   the projects I lead under the Areas. A row opens the view: my tasks
   first, the open count, and a link to `/projects`. There is no project
   deep link to copy (`projectMenu.ts` records that), so the link opens the
   board.
9. **The way back is one `Badge`** in the Projects panel header, read
   through `lensMyOverlay` under the lens. It says "Untriaged" when the
   viewer holds a row with a context and no disposition.
9a. **The strip's matrix section is "Focus matrix", never "Priority".**
   The body's Priority cell is `pm_tasks.importance`, the task's shared
   integer. The strip's chips are `GtdItem.important` and its pair (D53.8).
   Fence: `itemDetail.test.ts` refuses two equal labels in the lens host.
9b. **`GET /my/tasks/{id}/lanes` is the lane list** of the node that owns
   the set. It sits behind the same membership check as the single read.
   `/nodes/{id}/statuses` is behind the project grant, and a member reached
   by assignment alone holds none. A sibling read, not a field on
   `/my/tasks/{id}`: the three personal readers keep one key set. The
   shared body reads its lanes through `fetchMyTaskLanes`, and draws a read
   error where the body would have been.
10. **The five raw checkboxes are `Checkbox`.** `conformance.test.ts`'s
    baseline lost the five rows. `selectionParity.test.ts` now reads for
    `<Checkbox`.
11. **The chat gains one tool**, `my_led_projects`, class A. The
    `untriaged` flag needs no manifest row, because the manifest keys on the
    verb and the path.

**Verified.** `tests/live/live_ws39_s6e.py` on the dev database, 10/10
PASS. `test_projects_personal_s6e.py`, 12 tests. The vitest fences:
`itemDetail.test.ts` (10), `fromProjects.test.ts` (4), `lens.test.ts` (56),
`selectionParity.test.ts` (13).

### S6f — one set of fields across My Tasks and Projects · AGENT-SAFE · BUILT 2026-09-23

**Scope.** §4.10, decision D77. Branch `my-tasks-fields`, stacked on
`my-tasks-s8b` (#411). It merged `origin/main` for D75 and the Nudge, and
again for D76 (#429).

⚠️ **Reworked 2026-09-24 for D76.** The first build derived `important` from
`importance`, and the Important switch wrote the shared Priority. D76 is the
owner's decision and it says the opposite, so the rework took D76 whole. The
decision number moved from D76 to D77, and the migration moved from 215 to
216, because main took both first.

**Gateway.**

1. `effective_disposition` in `personal.py` is the one rule. The inbox, the
   planner (`planning._pm_row`) and the AI seam (`item_lens._pm_item`) call it.
   `_PM_ALIVE` now prunes TRASH only, because a stated DONE can be reopened.
2. `_MY_TASKS_SQL` selects `other_assignees`. `waiting_on_for` turns it into
   the waiting-on person, and the Nudge uses the same rule.
3. `DEFERRED_CLAUSE` adds `start_date <= CAST(:today AS date)` to the defer
   clause. `member_today` computes `:today` from the member's
   `user_settings.timezone` (F5, 2026-09-24). The first build used the
   database's `current_date`, which is UTC, while the client read the local
   date. My Tasks now stores the browser's zone on open, as the Calendar
   does. The parity fixture has explicit-today rows that both sides run
   with one instant and one zone. The bind is a `date`, because asyncpg
   refuses text for a date parameter.
4. `validate_overlay` refuses `time_estimate_mins` with a 422, and
   `_upsert_personal` refuses it with a ValueError. Neither refuses
   `important`, because it is the member's own answer (D76). Organize and
   email capture write `pm_tasks.estimate_mins`.
5. A delegation keeps a deadline the task already has.
6. `GET /projects/tasks/{id}` carries `time_spent_mins`. It is a field on a
   route that exists, so the D-PM-37 manifest does not change.
7. **Migration 216** copies each overlay estimate into an empty
   `estimate_mins`. The assignee's value wins, then the first assignee, then
   the earliest `updated_at`. It writes no priority. The ledger guard makes a
   replay a no-op, and a test holds the guard to the file's own name.
8. `skill-task-gtd` and `skill-projects` follow the split. The chat tool
   `set_my_overlay` refuses DONE and points at `complete`. Its card says
   "your overlay only", so a shared completion behind that card would move
   the board without asking. `complete` asks.

**Client.**

1. `lens.ts` maps `estimate_mins`, `start_date` and `tags`. `important`
   stays the overlay's, and `orgPriority` carries the shared Priority (D76).
   `TASK_KEYS` gains the estimate and the start date. `OVERLAY_KEYS` loses
   `time_estimate_mins` and keeps `important`.
2. The client writes no lane for a reopen. The gateway's
   `reopen_if_closed` covers every door (§4.10 choice 3).
3. The Important switch writes the member's overlay, as D76 says. No My
   Tasks path writes the shared Priority.
4. The list's Priority column and the card draw the shared Priority
   (`orgPriority`) and the tags. They use the Projects chips
   (`importanceChip`, `taskMeta`) and D76's words. Every control over the
   member's matrix says "Your focus". That covers the column, the sort, the
   group, the filter, the view and the clarify card. A Priority sort, group and filter
   read `orgPriority` (§4.10, "One word, one meaning").
   ⚠️ **The column key `priority` changed meaning.** It named the matrix
   column. It now names the shared Priority column, and the matrix column is
   the new key `focus`. A member who saved "show Priority" now sees the
   shared field under that name. This was accepted on 2026-09-24 (F6),
   because the header reads "Priority" and shows Priority.
5. `TaskBody` gains Start, Estimate, Time spent, Watch and a Description
   editor. The My Tasks strip loses Estimate and Notes under the lens. The
   Projects header loses its watch toggle.
6. The inbox tickles a task until its start date (`isTickled`, `resurfacesAt`).
7. **Energy is off by default in the list.** The rig measured the new Priority
   track at 1440 with both rails open. It pushed Due date off the edge.

**Verified.**

The repair round (F1 to F7) was verified on 2026-09-24, on a fresh
database built from this branch's own ladder (`acb_tenant_verify_f`, 214
files). The database was dropped afterwards.

- `tests/live/live_ws39_s6f.py`: **17/17 PASS**. It checks reopen, close,
  reassign, the estimate in the planner and in capacity, and the start date.
  Check 5 proves that the Priority reaches the planner beside my
  `important`, never as it. Check 6c proves that the start date is judged
  on the member's own date (F5). Check 7 proves that the upsert refuses the
  retired estimate and takes my `important`. Check 8b proves that the
  backfill leaves the Priority alone. Check 11c proves that Someday on a
  finished task leaves the lane done (F4). It also checks the backfill run
  twice, the ledger replay, time spent, the reopen and tenancy.
- `test_projects_personal_s6f.py`: 59 tests. The pytest run over every file
  that imports a changed module, with `test_priority_seed.py`: 3508 pass. One
  fails, and it passes alone: `test_h3_rls_promotion_rehearsal.py`, which
  depends on the order of the run.
- `npx tsc --noEmit` clean. `npx vitest run`: 3520 pass in 186 files, with
  D76's `priorityVocabulary.test.ts` and `priority.test.ts`. The fences are
  `sharedFields.test.ts` and `focusWording.test.ts`. `lens.test.ts` and
  `itemDetail.test.ts` carry the D77 cases, and `itemDetail.test.ts` holds
  D76's "Your focus" row to its name.
- The live scripts for S6a (13/13), S6d (33/33), S6e (10/10), S8a (7/7) and
  S8c (8/8) pass. S6d reads the capture's estimate off `pm_tasks` now.
- The visual pass: one task in My Tasks and in Projects, light, compact and
  dark at 1440, and 390. The captures are in
  `workbench/control_plane/ux-shots/s6f/`. They are from the first build. The
  rework renamed the member's cell column to "Your focus", and nobody has
  shot it again.

### S6g — one inbox and one promote path · AGENT-SAFE · BUILT 2026-09-24

**The owner's request, verbatim, 2026-09-24, in seven fragments.**

> "the inbox system properly in the My Tasks app"

> "dealing with both kinds of tasks"

> "added in the My Tasks app directly as personal tasks"

> "tasks that are added from the Projects app"

> "add a task or promote a task into the projects app from the My Tasks app"

> "when a task might become something that has to be part of a larger project"

> "That mechanism also needs to be properly set up in terms of UI/UX."

**What the audit found on `main`.** Two row components drew the Inbox, and
they had different actions. The header count, "Clarify next", the date pills,
the search, the selection and the keyboard ignored the board rows. The
sidebar badge counted them, so the two numbers disagreed.

Every capture wore a "Local" badge. "Move to project" was a right-click only,
and the dense list had no door to it. Two promote doors disagreed on the destination list, on
the required fields, on the copy and on the toast. Clarify never asked for a
required field, and the server refused the move after the card had moved.

**Scope.**

1. One list and one row component for both kinds.
2. An origin marker in place of `SourceBadge`.
3. A source filter: All, Mine, From Projects.
4. The same main action on both kinds, and a visible promote door.
5. A capture that lands straight on a project, through a chip or a `#` token.
6. One promote path for the Move dialog and for Clarify.
7. An Undo for a promote, as a short deferred commit.
8. Two keys, `m` and `o`.
9. A phone layout at 390px with no horizontal scroll.

**Build record (2026-09-24).** Branch `my-tasks-inbox`.

1. **The one list.** `lib/inbox.ts` is the rule. The Inbox holds every INBOX
   row and every untriaged board row, and it leaves out a tickled or archived
   row. The sidebar badge reads `inboxCount`. The header reads the same list,
   so the two numbers are equal. Board rows come first, then the captures.
   The member's sort applies inside each block. "Inbox zero" shows only when
   both kinds are empty. `FromProjectsGroup` is deleted.
2. **The kind of a row** is `isPersonalTask`. A row in my root or in one of my
   Areas is personal. Any other row is a board row. A capture that was
   promoted while it was still INBOX is a board row.
3. **The origin marker** is `InboxOrigin`. A personal row shows a neutral
   lock badge, "Personal". A board row shows `ProjectLabel` and "from
   {assigner}". Both show the shared Priority chip when it is High or Highest.
4. **The source filter** is the store's `sourceFilter`, with the values
   `all`, `personal` and `board`. Nothing set it before S6g. It narrows the
   Inbox list and nothing else. The stale Mine and Team chips are gone from
   the Inbox and from the other lists. The date pill that read "All" now reads
   "Any date", because two pills called "All" sat side by side.
5. **The actions** come from one function, `inboxRowActions`. The card, the
   table, the context menu and the keyboard read it. A personal row has
   **Move to project** and **Delete**. A board row has **Not mine** and
   **Open on board**, and no Delete. The actions are always visible.
6. ⚠️ **"Not mine" writes TRASH on my overlay, through the one-tap dispose.**
   The audit found that the delete path purges when its Undo window closes.
   On a board task, the purge is a hard DELETE of the team's task. So the
   board rows never use `requestDelete`. The `t` key and the bulk bar follow
   the same rule. The purge on other views is a finding. It is not part of
   this slice.
7. **The table** has a "Where" column in place of "Source".
8. **Capture to a project.** A chip at the right of the capture box reads
   "Inbox" until the member picks a destination. Typing `#` at the start of a
   word opens the same picker. `parseProjectToken` reads the longest run of
   words that names one Area or project. An exact name wins over the start of
   a longer name. The token leaves the title. The capture lands in my root
   first, so a failed move loses nothing. An Area is a move in my own tree,
   sent at once. A company project goes through the one promote path. A
   project can have a required field that the capture does not carry. Then
   the Inbox opens the promote dialog on that project, and sends nothing.
9. **One promote path.** `PromoteFields` holds the mapping preview, the
   required fields, the assignee editor and the losses. The Move dialog draws
   it, and Clarify draws it under Where. Both doors build the request with
   `promotePlan`. Both read the company tree through `useCompanyTree`, so a
   folder is drawn and cannot be picked in both. Both say one hint:
   "It moves onto the board. It stays in your lists while you are an
   assignee." Both schedule through `schedulePromote`, and `PromoteToast`
   is the only place that speaks.
10. **The gateway.** `OrganizeIn` gains `custom_fields` and `assignees`.
    `_organize` passes both into the one `MoveTask`. The answers count only
    when the task changes project. A decision that is not a delegate must keep
    the actor in the owner list, or the gateway refuses it with 422. Without
    this check the read-back would 404 inside the transaction. The route emits
    `pm.task.moved` when the project changed.
11. **Clarify's owner.** Clarify hides the assignee editor of
    `PromoteFields`, because its Owner step already answers who. A delegate
    still sends its one assignee.
12. **The "File it here" banner** on a personal task picks the project and
    opens the form on it. It does not send, because a promote asks its
    questions first.

**The Undo rule for a promote.** D62 refuses a move from a company board back
into my personal tree. So there is no Undo after a promote is sent. The Undo
is a short deferred commit:

1. The toast reads "Moving to {project}…" with **Undo**. Nothing is sent yet.
2. After `PROMOTE_UNDO_MS` (5 seconds) the store sends the request.
3. Undo before the send cancels it. Nothing reaches the server.
4. After the send, the toast reads "Moved to {project}" with **Open board**.
   It has no Undo.

The rule applies to the Move dialog, to Clarify and to the capture chip. The
Clarify walk moves on at once. An Undo puts the capture back in the walk. The
app's toast sits under the Clarify overlay, so the overlay carries the same
line and its Undo.

**Decisions the request did not settle, each an agent default.**

1. The badge and the header count the whole Inbox. The source filter narrows
   the list only.
2. A tickled board row goes to the Tickler, as a tickled capture does.
3. A promote that is still waiting is sent at once when a second promote
   starts. So there is never more than one Undo window.
4. The Move dialog closes before the send. A refusal after the delay speaks
   through the toast.
5. `e` (edit the title) works on a personal row only, because a board task's
   title belongs to the team.

**Fences.**

| Claim | Test |
|---|---|
| the badge and the header read one count, and "Inbox zero" needs both kinds empty | `inbox.test.ts` |
| no `SourceBadge` on an Inbox row | `inbox.test.ts` |
| the filter counts, and the order | `inbox.test.ts` |
| "Not mine" writes TRASH and never deletes | `inbox.test.ts` |
| `m` moves a capture, and `o` opens a board row | `inbox.test.ts` |
| `#` parsing | `captureTo.test.ts` |
| capture to a project, with and without a required field | `captureTo.test.ts` |
| both doors build through `promotePlan`, and the toasts are equal | `captureTo.test.ts` |
| the deferred commit sends after the delay, and Undo cancels it | `captureTo.test.ts` |
| `custom_fields` reaches the move, and `pm.task.moved` is emitted | `test_projects_personal_s6g.py` |
| a refusal moves nothing, on a real database | `live_ws39_s6g.py` |

**Verified.**

- `tests/live/live_ws39_s6g.py`: **9/9 PASS**, on a fresh database built from
  this branch's own ladder (`acb_tenant_s6g`: 01 plus 215 files, through
  217). The database was dropped afterwards. The live scripts for S6a (13/13),
  S6e (10/10) and S6f (17/17) pass on the same database.
- `test_projects_personal_s6g.py`: 7 tests.
- `npx tsc --noEmit` is clean. `npx vitest run` is green.
- The captures are in `workbench/control_plane/ux-shots/s6g/`. The visual
  pass found one defect, and this slice fixed it. The Move dialog mounts
  `PromoteFields` before a destination is picked. The idle preview answered a
  new empty list on each render, and the report effect looped. The idle answer
  is now two constants, and `captureTo.test.ts` holds it.

### S7 — the cutover · dev-phase window, reported by evidence · RUN 2026-09-23

**Scope.** Run `docs/TASKS_LENS.md`'s runbook in the corrected order (§6).

**Done when.**
1. `/version` reports `tasks_lens: true`.
2. `SELECT count(*) FROM gtd_items WHERE migrated_task_id IS NULL` is 0.
3. A task captured in `/tasks` appears in `/projects` under the caller's
   personal project in the same page load.
4. The pre-migration backup for the day is on disk before step 4 of §6.

### S8a — the assistant's task tools move onto the lens · AGENT-SAFE · BUILT 2026-09-23

**Scope.** `apps/skills/skill-task-gtd/skill_task_gtd/core.py`, the 29 tools
the `task-manager` chat agent calls. After S7 the browser read `pm_tasks`
through `/projects/my/*`. The skill still called `/tasks/items*`,
`/tasks/projects`, `/tasks/hierarchy`, `/tasks/settings`, `/tasks/accounts`
and `/tasks/sync`. Those routes read and write `gtd_items` only. So a chat
capture landed in the dead store and answered 200.

Every tool that reads or writes a task, a project or the tree now calls the
route the browser calls. `lens.ts` is the contract of record. The map:

| Tool | Lens route |
|---|---|
| `gtd_capture`, `gtd_capture_many` | `POST /projects/my/tasks`, `POST /projects/my/tasks/batch` |
| `gtd_list` | `GET /projects/my/inbox`, paged to the end, with the flags `VIEW_FLAGS` uses |
| `gtd_detail` | `GET /projects/my/tasks/{id}`, `GET /projects/tasks/{id}/timeline`, `.../attachments` |
| `gtd_subtasks`, `gtd_add_subtasks` | `GET /projects/tasks?parent_task_id=`, `POST /projects/tasks` |
| `gtd_update`, `gtd_schedule`, `gtd_unschedule`, `gtd_move` | Split the way `splitPatch` does. `PATCH /projects/tasks/{id}` and `PATCH .../personal` |
| `gtd_complete` | `POST /projects/tasks/{id}/complete`. Undo puts the task in the project's default lane, then NEXT |
| `gtd_set_stage` | `GET /projects/nodes/{project}/statuses`, then `PATCH /projects/tasks/{id}` with `status_id` (§4.6) |
| `gtd_organize` | `POST /projects/my/tasks/{id}/organize`. A `status` name is resolved after the move |
| `gtd_delegate` | `PUT /projects/tasks/{id}/assignees` and the WAITING overlay, as `lensDelegateItem` does. With `project_id`, the organize delegate path, one transaction |
| `gtd_archive` | `POST /projects/tasks/{id}/archive` or `/unarchive` |
| `gtd_list_projects` | `GET /projects/my/areas` and `GET /projects/nodes`, labelled `[AREA]` and `[PROJECT]` |
| `gtd_list_schedule` | `GET /projects/my/calendar` |
| `gtd_accounts`, `gtd_sync` | No call. They answer that no tool is connected (D52) |

Nine tools keep a `/tasks/*` route, because the handler picks its store at
call time or reads a table that survives. `gtd_clarify`, `gtd_inbox_insights`
and `gtd_plan_project` read through `item_source()` (S6d). `gtd_plan_day`,
`gtd_replan_day`, `gtd_rollover` and `gtd_day_digest` read through
`agent_source()`. `gtd_set_one_thing` writes `calendar_day_state`, and
`gtd_people` reads `people`.

`gtd_estimate_stats` is NOT one of them. `/tasks/calendar/estimate-stats`
answers from the retired store. So the tool calls
`GET /projects/my/calendar/estimate-stats`, as `lensEstimateStats` does.
Tool names and signatures do not change. S9 renames them, and
`TaskToolCards.tsx` keys on the names.

**Done when.**
1. Every tool calls the exact routes in the map. Fence:
   `tests/unit/test_skill_task_lens.py`, a recording transport.
2. No string in the skill names a retired door. The one `/tasks/items/...`
   path left is the clarify door. Same fence, an AST walk.
3. Every path the skill calls is a path the gateway serves, with that
   method. Same fence, with the routers imported the way
   `test_client_route_contract.py` imports them.
4. Every kept `/tasks/*` handler names `item_source()` or `agent_source()`
   in its source, or sits on the store-neutral list with a reason. Same
   fence, `inspect` over the mounted endpoint.
5. A capture answers `/my/inbox?disposition=INBOX`. Fences:
   `tests/unit/test_projects_personal_s8a.py` and
   `tests/live/live_ws39_s8a.py` (R8).
6. `SKILL.md` and `instructions.md` describe no connected tool.

**Decisions taken at build, 2026-09-23.**
1. A `[TEAM]` marker replaces `[SYNCED]`. A row carries the data fence when
   another member wrote it (`created_by`). It also carries the fence when
   its project is outside the caller's personal tree. The tree is the root
   from `GET /projects/my/project` plus the Areas. Comments carry the fence
   always. The two `sync_state` markers are gone.
2. Reopen is the reverse of `/complete`, and SHARED for the same reason
   (`task_manager_app.md` §13.5a decision 1). The task goes to the first
   lane by position whose category is neither closing nor triage, the rule
   `load_default_status` applies. Then the overlay says NEXT. The browser
   has no reopen yet. Chat is the one place it exists.
3. `gtd_sync` is read-only. It calls nothing.
4. **A capture states INBOX.** `create_personal_task` writes the overlay
   row with `disposition = 'INBOX'` and `clarified_at` NULL when the
   caller states no disposition. Without it the row derived SOMEDAY from
   the root's `backlog` lane, and no Inbox showed a fresh capture. A
   subtask gets no default. Recorded in `task_manager_app.md` §13.5a as
   decision 4.
5. `GET /projects/my/inbox` orders by `p.sort_key ASC NULLS LAST,
   t.created_at DESC, t.id`, the rule `tasks/lib/ordering.ts` applies. The
   route pages in Python, and an unordered set can repeat or skip a row
   across pages. `gtd_list` applies the same order before it cuts to 30.
6. `/my/inbox` and `/my/tasks/{id}` project `origin`, through `from_jsonb`,
   on the one seam (`_project_task`). The email marker in the skill was
   dead without it.
7. After a committed organize or delegate, the tool reports a lane-name
   miss with the valid names. It never raises over the committed writes.
8. `gtd_list_schedule` asks for done blocks (`include_done=true`) and marks
   them. A done block still occupies its hour.
9. `gtd_capture_many` sends batches of `MAX_BATCH` (100) and reports the
   total captured.

### S8c — meeting actions and the email brief move to the one store · AGENT-SAFE · BUILT 2026-09-23

**Scope.** Three modules still used `gtd_items` after S7. Production has
served `pm_tasks` since 2026-09-23 00:29 UTC, so all three failed.

1. `routes/notes/actions.py::_create_task_from_action` wrote an approved
   meeting action into `gtd_items`. No screen showed the task.
2. `routes/email/digest.py::_digest_commitments` read the open reply
   commitments from `gtd_items`. The brief listed none.
3. `routes/email/automation/drafting.py::_fetch_calendar_context` read
   the hard dates from `gtd_items`. The drafter saw an empty calendar.

All three now call the one seam, `item_source()` (S6d). They name no task
table. The map:

| Module | Seam call |
|---|---|
| Notes approve and `task` dispatch | `find_by_origin(key="action_item_id")`, then `insert_capture` |
| Email digest | `items_by_origin(key="account_id")` for the account's owner |
| Email drafter | `hard_dated_items(days, limit)`, a new seam read |

The capture lands in the member's personal root as a stated INBOX
capture, the S8a rule. Its origin is `{kind: "meeting", meeting_id,
action_item_id, segment_ids}`. The notes text is the description.

**Done when.**
1. An approved action answers `_MY_TASKS_SQL` for the member under INBOX,
   with the meeting origin. Fences: `tests/unit/test_notes_action_capture.py`
   and `tests/live/live_ws39_s8c.py` (R8).
2. Approving twice writes one task. A row that lost its ref also writes
   one task. Same fences.
3. The digest returns a reply-commitment capture with its latest message.
   A closed commitment leaves the brief. Fences:
   `tests/unit/test_email_digest.py` and the live script.
4. The drafter lists an open hard-date task and no soft date. Fences:
   `tests/unit/test_email_calendar_context.py` and the live script.
5. No string in `routes/notes/` or `routes/email/` names a `gtd_` table.
   Docstrings are exempt. Fence: the globbed `SCOPED` list in
   `tests/unit/test_tasks_ai_source.py`.

**Decisions taken at build, 2026-09-23.**
1. **The task id goes in `action_item.dispatch_ref`.** `resulting_task_id`
   stays NULL. Its foreign key names the legacy `task` table
   (`01_schema.sql`), and that key refuses a `pm_tasks` id. The live script
   proves the refusal. Migration 129 already put a task's id in
   `dispatch_ref`, so this follows the existing convention.
2. `notes/actions.py::task_ref` reads both columns. The meeting page links
   "In My Tasks" off the API field `resulting_task_id`. So the list reader
   fills that field from `dispatch_ref` for a `task` item.
3. The capture is idempotent by origin, not only by the action row.
   `_dispatch` commits the task and marks the row in two sessions. A
   failure between the two left a draft, and the retry wrote a second task.
4. **No migration.** `action_item_id` and `account_id` join `ORIGIN_KEYS`
   without an index. Each lookup runs inside one member's list, so the key
   filters few rows. The last migration on the base was 213.
5. The seam still reads `TASKS_LENS` on this base. Production sets it, so
   production takes the pm arm. S8 PR 1 deletes the gtd arm. The three
   callers need no change then.

### S8 — the contract: code first, then schema · AGENT-SAFE · PR 1 BUILT 2026-09-23 · PR 2 BUILT 2026-09-23

**Scope.** Two PRs, in order.

1. **Code.** Delete the `gtd_items` arms behind `lensEnabled()` and
   `agent_source()`. Delete both flags. `lens.ts` becomes the only path.
   `routes/tasks/hierarchy.py`, `sync.py`, `accounts.py`, `providers.py` and
   `broker_handlers.py` go. `items.py`, `hierarchy.py`, the status catalog in
   `settings.py`, `accounts.py` and `sync.py` lost their last caller in S8a.
   `test_client_route_contract.py` keeps the paths that survive.
2. **Schema.** A new migration calls `gtd_retirement_drop()`. It takes the
   next free number at build time (R1). Number 212 went to the backfill fix
   on 2026-09-23 and 213 to `pm_activities.seq` (#402). So this one is 214
   or later. Then it drops
   the tree tables, `gtd_contexts`, `gtd_retirement_arm` and the two guard
   functions. It renames `gtd_attachments`, `gtd_horizons` and `gtd_reviews`
   through the guarded prologue in their creating migrations (52 and 48).
   Each rename is registered in `test_gtd_rename_upgrade.py::RENAMED`. The
   contract half of `wa_commitments.task_id` drops `gtd_item_id`.
   The PR 1 build record named three blockers: notes approve, the email
   digest and the email drafter. S8c moved all three to the seam.

**PR 1 build record (2026-09-23).** Branch `my-tasks-s8b`.

**PR 1 merges no earlier than 2026-09-24 00:30 UTC, one full day after the
flip (§6 step 10).**

1. **Gateway.** `item_source()` and `agent_source()` return the one store
   with no flag. The `gtd_items` arms and `tasks_lens_enabled()` are gone.
   `/version` keeps `tasks_lens: true` as a constant for one release, so the
   monitoring that reads it does not break. S9 may drop the key.
2. **Deleted modules.** `routes/tasks/items.py`, `hierarchy.py`,
   `accounts.py`, `sync.py`, `providers.py`, `broker_handlers.py` and
   `scheduler.py`. Also the `/status-catalog` route and the four legacy
   planner routes (`/calendar/plan`, `replan`, `rollover`,
   `estimate-stats`). A grep found no caller for any of them. It covered the
   client, the skill, the agents, the operator console and the tests.
3. **Client.** `lensEnabled()` and `NEXT_PUBLIC_TASKS_LENS` are gone. Every
   function in `api.ts` answers through `lens.ts`. `lens.test.ts` refuses
   any retired `/tasks` path in `api.ts`.
4. **D73.9** is built in the same PR (§4.9).
5. **The fence widened.** `test_tasks_ai_source.py` walks the whole of
   `routes/tasks/`, `routes/projects/` and `apps/skills/`. Each allowed
   `gtd_` token carries a reason.
6. **Three modules outside the fenced trees used `gtd_items`:**
   `routes/notes/actions.py`, `routes/email/digest.py` and
   `routes/email/automation/drafting.py`. S8c (#410) moved them onto the
   seam, and the fence now walks `routes/notes/` and `routes/email/` too.
   `scripts/restore_db.sh`
   also names `gtd_task` in its examples and in its row-count check. PR 2
   updates it in the same change.

**PR 2 build record (2026-09-23).** Branch `my-tasks-s8d`, stacked on
`my-tasks-fields` (#427).

1. **Migration 217.** Main holds 215 (`sealed`) and 216 (the estimate
   backfill, #427), so this one is 217. The file is one transaction, and its steps run in this order:
   - (0) refuses when a `gtd_items` row holds a value in a column that the
     backfill never copied. There are 16 such columns, from `origin` to
     `horizon_id`. `flexible` is exempt. The RAISE names the column and the
     row count.
   - (c) copies `wa_commitments.gtd_item_id` into `task_id` through
     `gtd_items.migrated_task_id`, then drops the old column. A value moves
     only when its `pm_tasks` row exists.
   - (c2) rewrites `action_item.dispatch_ref` for `kind = 'task'` rows the
     same way. Migration 129 stored the gtd id there.
   - (a) arms the guard and calls `gtd_retirement_drop()`. The guard drops
     `gtd_items` and `gtd_waiting`, or it refuses.
   - (b) refuses when `gtd_projects`, `gtd_spaces`, `gtd_folders` or
     `gtd_contexts` holds a row, and names the table. Then it drops the four
     in foreign-key order, and then the arm table, the view and both
     functions. No step uses CASCADE.
2. **The arm moved into the migration.** §6 step 11 made the arm a hand
   INSERT on the box. Migration 217 now writes the arm row, so the act is
   reviewed code. The data check did not move. The guard from migration 190
   still counts the rows that have no `migrated_task_id`.
3. ⚠️ **Migration 217 fails closed.** It RAISES in three cases: an
   unmigrated row, an uncopied value, or a row in a tree table. The deploy then stops, and 217 changes nothing. This is the
   intended behaviour. Do not widen a guard.
   ⚠️ **The wider failure case, from the review.** `vps_apply.sh` applies the
   migrations and then restarts the gateway. Any failure between those two
   steps leaves the old code on the new schema. A refusal by 217 is one such
   failure, and any later step of `vps_apply.sh` is another. Migrations 48
   and 52 stay applied, so the old code names `gtd_attachments`, a table that
   is gone. File uploads then fail until a deploy completes. The old WhatsApp
   list also names the dropped `gtd_item_id` column.
4. **Production passed all three checks by hand on 2026-09-23**, at about
   19:00 UTC. The coordinator ran them read-only.
   - Both `gtd_items` rows are migrated. Every column that the backfill
     never copied holds its default. The one exception is `flexible = true`,
     and the new store reads that as its default. A second check at about
     19:30 UTC found `important = false` on both rows.
   - `gtd_projects`, `gtd_spaces`, `gtd_folders`, `gtd_contexts` and
     `gtd_waiting` hold 0 rows. `gtd_attachments` holds 2 rows, and they
     survive as `attachments`.
   - No `action_item.dispatch_ref` names a gtd id, and no
     `wa_commitments.gtd_item_id` is set.
   So production loses nothing. Migration 217 now makes the same checks on
   every other box.
5. **Three renames**, each in the migration that creates the table.
   `gtd_attachments` is `attachments` (52). `gtd_horizons` and
   `gtd_reviews` are `my_tasks_horizons` and `my_tasks_reviews` (48). The
   sweep came first and the prologues second. No later migration alters or
   indexes these tables by name. Migration 150 names the old name in
   comments only.
6. **Two guards for a lone re-run.** Migration 52 now guards its
   `ALTER TABLE gtd_items`, because it re-runs after 217 drops that table.
   Migration 217 drops an empty store that a lone re-run of 48 builds again.
   `test_gtd_backfill.py` pins the text of 48, so an edit to 48 must also
   touch 217.
7. **Code.** Both `attachments.py` modules write and read `attachments`.
   The WhatsApp list, the digest and the WhatsApp agent read `task_id`, and
   the API field is `task_id`. The member purge names no task table, and it
   still deletes no `pm_tasks` row (D63). `backup_db.sh` anchors on
   `pm_tasks`. `restore_db.sh` lists the real anchors.
8. **The generated tenancy files (H-104).** The ladder does not apply them,
   but an operator applies them by hand. So the six dropped tables leave all
   four files, and the three renamed tables take their new names.
   Constraint, index and policy names keep the old spelling.
   `gen_tenant_migration.discover_tables()` now reads the ladder in number
   order and honours `DROP TABLE`.
9. **Fences.** `test_no_gtd_table_names.py` reads the four code trees.
   These are `apps`, `packages`, `scripts` and `workbench`. It refuses a
   `gtd_` token that is not on its list. The list holds the 29 chat tool
   names, three settings helpers and one example tool name. S9 owns all of
   them. `test_gtd_backfill.py` fences the exact drop set of 217 and the
   survivors. It also replays the ladder against a real Postgres.

**Pre-flight for production.** Do both before the merge.
1. Confirm that today's backup is on disk:
   `ls -la /opt/acb/backups | tail -1`.
2. Confirm that the ledger holds 216 (the estimate backfill):
   `SELECT filename FROM schema_migrations WHERE filename LIKE '216_%';`.
   PR 1 (#411) added no migration.

The data checks are not a hand step. Migration 217 refuses by itself when a
row would be lost.

After the deploy, `\dt gtd_*` must return nothing, and the ledger must hold
`217_gtd_task_store_drop.sql`.

**Done when.**
1. `rg -l "gtd_" apps packages --glob '!infra/postgres/generated'` returns
   nothing but the rename prologues.
2. `\dt gtd_*` on tenant-scratch after a full ladder replay returns nothing.
3. The ladder replays three times clean in `pr-check.yml`.
4. This closes H-151 and H-29.

### S9 — identifier hygiene · AGENT-SAFE · BUILT 2026-09-23

**Scope.** The code names that follow the schema:

- `GtdItem` → `MyTask`. `GtdProject` → `MyTasksProject`.
- `skill-task-gtd` → `skill-my-tasks`. Tool names `gtd_*` → `my_tasks_*`,
  all 29 at once.
- `test_tasks_gtd.py` → `test_my_tasks.py`.
- `test_gtd_quality_trajectory.py` → `test_my_tasks_quality_trajectory.py`.
- A doc sweep of "GTD" where it names the app, never where it names the
  method.

**Done when.**
1. `rg -il "gtd" workbench/control_plane/src apps/skills apps/agents tests`
   returns only files that describe the method.
2. `npx tsc --noEmit && npx vitest run` green. The named pytest files green.

**Build record (2026-09-23, UTC).** Branch `my-tasks-s9`, PR #436. It was
built on `my-tasks-s8d`, then rebuilt on `main` after #427 and #434 merged.
No route path, JSON field or database object moved.
The screens a member sees are the same, with one exception in item 4.

1. **The rename map.**

   | Old | New | Where |
   |---|---|---|
   | `skill-task-gtd`, package `skill_task_gtd` | `skill-my-tasks`, `skill_my_tasks` | the skill, `pyproject.toml`, `uv.lock`, the agent |
   | 29 tools `gtd_<name>` | `my_tasks_<name>` | skill, agent, persona, `TaskToolCards.tsx`, tests |
   | `gtd_models`, `gtd_toggles`, `gtd_calendar_prefs` | `task_models`, `task_toggles`, `calendar_prefs` | `routes/tasks/settings.py` and five callers |
   | `GtdItemModel` | `MyTaskModel` | `routes/tasks/core.py`, `capture_email.py` |
   | `GtdItem`, `GtdProject`, `GtdContext` | `MyTask`, `MyTasksProject`, `TaskContext` | 62 client files |
   | `gtdMetaChips`, `GTD_TRIGGERS` | `taskMetaChips`, `CAPTURE_TRIGGERS` | the client |
   | `test_tasks_gtd.py` | `test_my_tasks.py` | `tests/unit` |
   | `test_gtd_quality_trajectory.py` | `test_my_tasks_quality_trajectory.py` | `evals/trajectories` |

2. **Kept on purpose.** `test_gtd_backfill.py`, `test_gtd_rename_upgrade.py`
   and `test_gtd_retirement_plan.py` keep their names. Each is about the
   `gtd_*` tables and the migrations that moved them. The migrations keep
   their names too. Prose keeps "GTD" where it names the method, such as
   contexts, dispositions and the decision tree.
3. **Stored tool names.** A saved chat message keeps the tool name it was
   saved with. So `TaskToolCards.tsx` holds `LEGACY_TOOL_NAMES`, which maps
   each old name to its new name before the card router reads it.
   `TaskToolCards.test.ts` pins the map to the skill's `__all__`.
   The other readers of a stored name need no map:
   - The gateway never stores a tool name, and `pm_activities` and the rows
     of the approval queue do not either. The code search found none.
   - The Mem0 partition `agent:task-manager` holds prose. A memory that says
     `gtd_list` is text, not a call.
   - A resumed chat can show the model an old call in its history. The model
     then sees the new tool list and calls the new name. No code path
     replays an old call.
   - The observability office matched `/task|gtd|todo/`. An old event name
     in that feed now falls to the default icon. That cost is cosmetic.
4. **One visible change.** The live "running tool" line in the chat
   capitalises the raw tool name. It read "Gtd Capture" and now reads
   "My Tasks Capture".
5. **The survivors of the fence.** `test_no_gtd_table_names.py` now refuses
   every bare `gtd_` token in `apps`, `packages`, `scripts` and `workbench`.
   Four survivors are left, and each has its reason:
   - `data/gtd_attachments`, the upload folder on the box. Moving the folder
     is a separate deploy act. `GTD_ATTACHMENTS_DIR`, its environment
     variable, stays for the same reason.
   - the migration files in `infra/postgres/`, outside the walk.
   - the three migration-history tests above, outside the walk.
   - `TaskToolCards.tsx`, the alias map. The fence skips it for tokens, and
     a second test checks that it spells only the 29 old tool names.
6. **The client fence.** `test_the_client_carries_no_gtd_identifier` runs
   the check `rg -l "GtdItem|Gtd[A-Z]|gtd_" workbench/control_plane/src`
   and accepts only the alias map. `naming.test.ts` stays green.
7. **Not renamed.** The agent's `config.json` description, its `gtd` tag and
   the description in `routes/agent.py` stay. A member can read them in the
   agent list, and they name the method. The `tasks_lens` key of `/version`
   stays too. S8 PR 1 said S9 may drop it, but S9 moves no JSON field.

### S7 run record (2026-09-23, UTC)

- Backup: `/opt/acb/backups/2026-09-22T235052Z/postgres.dump`, 2.4 MB, taken by
  `acb-backup.service` with a verified restore, before the move.
- Step 5 failed once. Migration 196's CHECK refused the root insert. Migration
  212 re-defined the function. It reached the ledger at 00:19 UTC.
- The move applied at 00:21 UTC: 1 owner, 1 personal root, 0 sub-projects,
  2 tasks. Both dispositions survived (INBOX, SOMEDAY). `gtd_backfill_plan`
  returned zero rows afterwards.
- The flags: `TASKS_LENS=1` in `/opt/acb/app/.env`, and `NEXT_PUBLIC_TASKS_LENS=1`
  in `workbench/control_plane/.env.local`. ⚠️ The apply script reconciles only
  the internal token into `.env.local`, so the browser flag must be written
  there by hand. `docs/TASKS_LENS.md` now says so.
- Deploy run 35801944712 (workflow_dispatch) rebuilt and restarted at 00:29 UTC.
  `/version` reports `tasks_lens: true`. The served bundle carries the inlined
  literal `NEXT_PUBLIC_TASKS_LENS:"1"`.
- The sweep ran after the flip for both organizations.
- Owed: the same-page-load check of step 9 by a signed-in member.

## 6. The cutover runbook, corrected

```
   S5, S6a, S6b, S6c, S6d merged and serving
      |
1. confirm the H-158 fix is in the served bundle (grep the build for the literal)
      |
2. confirm today's backup: ls -la /opt/acb/backups | tail -1
      |
3. SELECT * FROM gtd_backfill_plan;             -- read every row
      |
4. SELECT * FROM gtd_backfill_to_pm(false);     -- dry run
      |
5. SELECT * FROM gtd_backfill_to_pm(true);      -- the move (2 rows today)
   (needs migration 212 in the production ledger first -- see the note below)
      |
6. .env: NEXT_PUBLIC_TASKS_LENS=1 and TASKS_LENS=1, restart, rebuild
      |
7. curl /version  ->  tasks_lens: true
      |
8. SELECT * FROM gtd_backfill_to_pm(true);      -- sweep the window
      |
9. capture in /tasks, read it in /projects, same page load
      |
   ... one full day with the lens on ...
      |
10. S8 PR 1 merges (the code stops naming gtd_*)
      |
11. the S8 PR 2 pre-flight (§5 S8): backup, and 216 (the estimate backfill) in the ledger
      |
12. S8 PR 2 merges. Migration 217 arms, calls the guard, drops. 48 and 52 rename.
      |
13. \dt gtd_*  ->  nothing
```

**Step 5 needs migration 212 in the production ledger.** On 2026-09-23 step 5
failed on production and wrote nothing. Migration 196 added a CHECK that a
root project owns its statuses. The root insert in migration 189 did not set
`owns_statuses`, so the CHECK refused it. Migration 212 re-defines
`gtd_backfill_to_pm()` with the flag on the root and on each child. Run step 5
only after `SELECT filename FROM schema_migrations WHERE filename LIKE '212_%'`
returns one row.

**Step 11 changed on 2026-09-23.** It was a hand INSERT into the arm table.
Migration 217 now writes the arm row, so the arm is reviewed code. Step 11 is
the pre-flight that S8 PR 2 lists. During the dev-phase window (CLAUDE.md
§3a) an agent runs it and reports the three results in the same message. On
2026-10-01 the pre-flight returns to the owner.

## 7. Fences, in one table

| Claim | Test |
|---|---|
| the label is My Tasks, and the mirror agrees | `nav.test.ts` |
| no member-visible `gtd` in the app | `app/tasks/lib/naming.test.ts` |
| `api.ts` names no retired `/tasks` path (S8 PR 1) | `lens.test.ts` |
| every client path is a served path | `test_client_route_contract.py` |
| no module in `routes/tasks`, `routes/projects` or `apps/skills` names a retired table | `test_tasks_ai_source.py` |
| Next Actions groups by the lane category, and a drag resolves a lane (D73.9) | `statusCategory.test.ts` |
| a personal child never reaches the company board | `test_personal_tree.py`, `live_ws39_personal_tree.sql` |
| a task assigned to me in Projects reaches my inbox untriaged | `test_projects_personal_s6e.py`, `live_ws39_s6e.py` |
| My Tasks and Projects share one task panel composition | the S6e source fence |
| the rename prologues are guarded and not swept | `test_gtd_rename_upgrade.py` |
| the drop is inert until armed and accounted for | `test_gtd_backfill.py` |
| 217 drops exactly the planned set, and refuses a row it would lose | `test_gtd_backfill.py`, `live_ws39_s8d.py` |
| no code names a `gtd_` table | `test_no_gtd_table_names.py` |
| a work fact has one home, and My Tasks reads it (D77) | `test_projects_personal_s6f.py`, `live_ws39_s6f.py`, `sharedFields.test.ts` |
| the strip draws no work fact, and the body draws them all (D77) | `itemDetail.test.ts` |
| the lens never writes `time_estimate_mins`, and My Tasks never writes the shared Priority (D77, D76) | `lens.test.ts`, `sharedFields.test.ts`, `test_projects_personal_s6f.py` |
| the Inbox badge and header read one count, and a board row is never deleted (S6g) | `inbox.test.ts` |
| both promote doors build through `promotePlan` and wait through one deferred commit (S6g) | `captureTo.test.ts` |
| organize carries the promote answers, and a refusal moves nothing (S6g) | `test_projects_personal_s6g.py`, `live_ws39_s6g.py` |
| the ladder replays clean | `pr-check.yml` migrations job |

## 8. What this spec closes, and where

| Entry | Closed by |
|---|---|
| H-33 | S6a, S6d |
| H-59 | S6b, S6c |
| H-62 | §4.4, §4.6 |
| H-29 | S7, S8 |
| H-132 | this directive. The owner asked a third time, with the name |
| H-151 | S8, S9 |
