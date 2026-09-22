# My Tasks — the cutover plan (WS-39, phase 2)

**Status: ACTIVE.** Owner directive, 2026-09-23. Board row **WS-39**.
Decisions this spec rests on: **D52 · D53 · D54 · D62 · D65**, and **D73**,
which this spec records. Owning spec for every slice named in §5.

Where this spec and `task_manager_app.md` §13 disagree, this spec wins. Where
this spec and `work_plan.md` §2 disagree, the board wins.

---

**Built so far.** S6a built 2026-09-23 on branch `my-tasks-s6a`. S6d built
2026-09-23 in PR #390.

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

### S6b — Areas, and the local tree retires · AGENT-SAFE

⚠️ **SPLIT IN TWO, 2026-09-23.** The gateway half shipped on its own. One PR
carrying four routes, a privacy guard and a sidebar reviews badly, and the
guard should not wait behind a UI.

**S6b-1 ✅ BUILT** — `GET/POST/PATCH/DELETE /projects/my/areas`, the privacy
guard on node moves, and the fences. See the build record below.

**S6b-2 🔲 OWED** — `ListsSidebar` gains the Areas section with create, rename
and delete. The Clarify "Where" picker reads Areas plus the company projects.
Group D of §3.1 retires under the flag. **H-29 stays blocked until this
lands.** Its constraint is the UI one. The backfill creates Areas, and a
member needs somewhere to rename or delete them.

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

### S6c — the promote door, and Horizons off the surface · AGENT-SAFE

**Scope.** A "Move to project" action on the task card and in the detail
panel. The dialog asks for the destination project, then the destination's
required custom fields (migration 192), then assignees. It calls
`apiMoveTask`. The `horizons` view leaves `ListsSidebar` and the `ViewKey`
union. The data and the routes stay (D65).

**Done when.**
1. `rg -l "apiMoveTask" src/app/tasks/components/` returns one hit.
2. A move with a blank required field is refused with the field named.
3. `rg -c -i horizon src/app/tasks/lib/` returns zero.
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

### S6e — continuity with Projects · AGENT-SAFE

**Scope.** §4.8's five points.

Server: `GET /projects/my/inbox` gains `untriaged=true`, the rows with no
overlay row for me. `GET /projects/my/led` lists the projects where
`lower(lead) = :who`, with their open task counts and my assigned tasks.

Client: the "From Projects" inbox group. The led projects in the Projects view
of My Tasks. `ItemDetail.tsx` rebuilt from the `TaskPanel` composition. The
project link on the card. The viewer's disposition chip in the Projects task
panel.

**Done when.**
1. Assign a task to Bob in Projects. Bob's inbox shows it under "From
   Projects" in the same page load. Bob sets a context. It leaves the group.
   Fence: `tests/unit/test_projects_personal_s6e.py` and a live check in
   `tests/live/live_ws39_s6e.py` with two members in one org.
2. Make Bob the lead of a project with no task assigned to him. Bob's My Tasks
   Projects view lists it. Alice's does not.
3. One task panel composition. `ItemDetail.tsx` imports from
   `app/projects/components/` and holds no field list of its own. Fence: a
   source test that refuses a second field list.
4. The visual pass of CLAUDE.md §4. Light mode, compact density and a changed
   accent. My Tasks beside Projects at four widths. Screenshots in the PR.

### S7 — the cutover · dev-phase window, reported by evidence

**Scope.** Run `docs/TASKS_LENS.md`'s runbook in the corrected order (§6).

**Done when.**
1. `/version` reports `tasks_lens: true`.
2. `SELECT count(*) FROM gtd_items WHERE migrated_task_id IS NULL` is 0.
3. A task captured in `/tasks` appears in `/projects` under the caller's
   personal project in the same page load.
4. The pre-migration backup for the day is on disk before step 4 of §6.

### S8 — the contract: code first, then schema · AGENT-SAFE

**Scope.** Two PRs, in order.

1. **Code.** Delete the `gtd_items` arms behind `lensEnabled()` and
   `agent_source()`. Delete both flags. `lens.ts` becomes the only path.
   `routes/tasks/hierarchy.py`, `sync.py`, `accounts.py`, `providers.py` and
   `broker_handlers.py` go. `test_client_route_contract.py` keeps the paths
   that survive.
2. **Schema.** Migration **212** calls `gtd_retirement_drop()`. Then it drops
   the tree tables, `gtd_contexts`, `gtd_retirement_arm` and the two guard
   functions. It renames `gtd_attachments`, `gtd_horizons` and `gtd_reviews`
   through the guarded prologue in their creating migrations (52 and 48).
   Each rename is registered in `test_gtd_rename_upgrade.py::RENAMED`. The
   contract half of `wa_commitments.task_id` drops `gtd_item_id`.

**Done when.**
1. `rg -l "gtd_" apps packages --glob '!infra/postgres/generated'` returns
   nothing but the rename prologues.
2. `\dt gtd_*` on tenant-scratch after a full ladder replay returns nothing.
3. The ladder replays three times clean in `pr-check.yml`.
4. This closes H-151 and H-29.

### S9 — identifier hygiene · AGENT-SAFE

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
11. INSERT INTO gtd_retirement_arm (armed_by, note) VALUES (...)
      |
12. S8 PR 2 merges. Migration 212 calls the guard, drops, renames.
      |
13. \dt gtd_*  ->  nothing
```

Step 11 is a human act. During the dev-phase window (CLAUDE.md §3a) an agent
does it and reports the row in the same message. On 2026-10-01 it returns to
the owner.

## 7. Fences, in one table

| Claim | Test |
|---|---|
| the label is My Tasks, and the mirror agrees | `nav.test.ts` |
| no member-visible `gtd` in the app | `app/tasks/lib/naming.test.ts` |
| every client function consults the flag until S8 | `lens.test.ts` |
| every client path is a served path | `test_client_route_contract.py` |
| the AI modules name no store outside the seam | `test_tasks_ai_source.py` |
| a personal child never reaches the company board | `test_personal_tree.py`, `live_ws39_personal_tree.sql` |
| a task assigned to me in Projects reaches my inbox untriaged | `test_projects_personal_s6e.py`, `live_ws39_s6e.py` |
| My Tasks and Projects share one task panel composition | the S6e source fence |
| the rename prologues are guarded and not swept | `test_gtd_rename_upgrade.py` |
| the drop is inert until armed and accounted for | `test_gtd_backfill.py` |
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
