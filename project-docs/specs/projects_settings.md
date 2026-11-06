# Projects settings — one place for every setting of the Projects app

**Status: ACTIVE — PS-1 and PS-2 built 2026-09-29. On 2026-11-06 the owner specified PST-1 to PST-5 (§9). Nothing of them is built.** Owner directive, 2026-09-29. Board row
**WS-42**. This spec records **D81**. §9 records **D185**. The first merge wins a D-number, so the author of the PR must re-check that D185 is still free at merge.

Verified against code on 2026-09-29 at `main` `02658314`. The author checked §9 against code on 2026-11-06 at `main` `06cfc43f8`.

**Status order (task 1792, 2026-11-05).** One rule orders every status lane: stage, then position, then name. The Statuses dialog shows up and down arrows on each row. The board has a Statuses control that opens the dialog. A new task never starts in a Done or Cancelled lane.

## 0. One paragraph

Projects gets ONE settings surface. It is a pane inside Projects, beside
Analytics and Reports, and it opens from a "Settings" row in the Projects
sidebar. It holds two scopes, and it says which one each setting is in. 

The **organization** scope holds the import and the export, and later the
vocabulary shared by every space. The **space** scope holds the space's name,
icon and colour, its statuses, custom fields, tags, task types and lifecycle.
The right-click menus stay as shortcuts. They open the same controls, so there
is one body per setting and never a copy.

**Built in PS-1, from its review:** a change in the pane re-reads the board's
fields, tags and lanes at once. The sidebar row and a `?app=settings` link
start at the selected space. The list of imports re-reads when the wizard
closes. An inline lifecycle save says "Saved.", because nothing closes.

## 1. What the owner asked for

The owner did not like the "Import from ClickUp" row in the sidebar. The owner
asked for "a settings button somewhere that opens up settings for the projects
app". It must "roll up" custom statuses, roll-ups, custom fields and colours,
and "the import/export". The right-click menus stay: the settings surface comes
"in addition to right-clicking".

The reference is ClickUp's settings page. It has a left list of sections in
groups, and the chosen section on the right. The owner asked that the result
be "logical, intuitive, easy to understand", and that it follow Metorite's own
design system.

## 2. D81 — Projects has one settings pane, in two scopes

1. **One pane, inside Projects.** It is a Projects app pane, like Analytics.
   It is not a nav entry. So the allowlist of `launch_surface.md` §2 does not
   change. `projectApps.ts` says an app pane is never a route and never a
   `PANES` entry.
2. **Two scopes, always named.** A setting is either the organization's or
   one space's. The section list shows each group under its own heading.
   The space group names the space it edits.
3. **One body per setting.** Each existing manager draws its body inside a
   frame. The managers are statuses, fields, tags, lifecycle and the space's
   identity. The frame is a `Modal` from the row menu and an inline section in the pane. A second
   implementation of a manager is a defect.
4. **The row menus stay.** They are shortcuts to the same bodies.
5. **The import moves into the pane.** The sidebar row goes. The empty tree
   had one link to it, and the owner removed it on 2026-09-30, because
   Settings already holds the import. Settings, Import & export is now the
   only entry.

## 3. Scope and non-goals

**In scope (PS-1):** the pane, its section list, the space picker, the five
existing managers inline, and the import section.

**Later slices (§7):** a task-type manager (none exists), the shared
vocabulary (HANDOFF H-4), and the export (not designed).

**Not in scope:**

- **A roll-up setting.** None exists, and none is needed now. Progress and
  roll-ups follow the status categories: a Done status counts as finished,
  and a Cancelled status leaves the count. The Statuses section says so.
- **Space members and grants.** `project_management_app.md` §9.12.6 parks
  them, so this spec does not add them.
- **A change to the status model.** Migration 196 and its rule stay: the
  nearest owner wins (D185). §9 does not change the data model.

**Reversed on 2026-11-06 (D185):** this section said that a project's own
status set stays in its row menu and that the space section only says where it
lives. That is no longer true. §9 names the owner of the set on every screen,
lists the projects that own their own set, and adds a chip on the board.

## 4. The surface

### 4.1 Where it opens

- A **"Settings" row** with a gear icon, last in the Projects app list, after
  Reports and AI chat. It is drawn like the rows above it.
- The command palette: "Projects settings".
- The header menu of a selected project: "All settings for this space", which
  opens the pane at the project's space.

### 4.2 The layout

On a desktop, the pane has a title row, then two columns:

- **The section list**, about 14 rem wide, on the left. It holds two groups.
  - **Organization**: Shared vocabulary, then Import & export. Every member
    sees Shared vocabulary. Import & export needs the import grant.
  - **Space**: a space picker at the top of the group, then General,
    Statuses, Custom fields, Tags, and Lifecycle. Task types join in PS-2.
- **The section**, on the right, up to `max-w-3xl`, scrolling on its own.
  Its heading names the section and, for a space section, the space.

The active section uses the active style of the design system
(`bg-primary/10 text-primary`). The list reuses the look of the Projects app
rows, so the pane reads as part of Projects.

On a phone, the pane shows the section list first. Choosing a section shows
it alone, under a Back control that returns to the list.

### 4.3 The space picker

- It lists the spaces the member can see, from the tree the page already
  holds. It is a `SelectButton`.
- It starts at the selected space, or at the space of the selected node, or
  at the first space.
- An organization with no spaces shows the Space heading with one line under
  it. The line says a space must exist first, and points to the import.

### 4.4 The sections

| Group | Section | Body | Scope on the server |
|---|---|---|---|
| Organization | Import & export | The recent imports (`ImportHistory`), "Import from ClickUp", which opens the wizard, and "Export", marked Soon | The organization |
| Space | General | `SpaceSettings`: name, icon and colour | The space |
| Space | Statuses | `StatusManager` for the space's set, and a line on how roll-ups read it | The space's status set |
| Space | Custom fields | `FieldManager` | The space, and org-wide rows |
| Space | Tags | `TagManager` | The space, and org-wide rows |
| Space | Task types | `TypeManager` (PS-2): add, rename, icon, colour, delete | The space. Epic and org-wide rows are shown and locked |
| Space | Lifecycle | `LifecyclePolicy`: auto-archive, auto-close and the time zone | The space |

### 4.5 Permissions

- The pane is open to every member who can reach Projects. A section draws
  what the member can see, and the server refuses a write as it does today
  (`projects:settings:write`, `admin:settings:manage`).
- **Import & export** shows only with `admin:access:manage` and
  `NEXT_PUBLIC_PROJECTS_IMPORT`, the gate the wizard already uses.
- The pane never hides a section to mean "not permitted". `preview` means
  "not built", as everywhere in Metorite.

## 5. The frame (one body, two places)

`components/ManagerFrame.tsx` takes `inline`, `title`, `description` and
`onClose`.

- **`inline` false:** it draws the `Modal` the manager drew before, with the
  same title, description and size. The row menu path does not change.
- **`inline` true:** it draws a section heading and the body in a bordered
  card, with no Close. The pane owns the navigation.

Each manager takes an `inline` prop and passes it to the frame. Nothing else
in a manager changes.

## 6. Fences (R7)

| Rule | Fence |
|---|---|
| One body per setting | `projectsSettings.test.ts`: each of the five managers renders a `ManagerFrame` and imports no `Modal`, and the pane renders the same five managers inline |
| The pane is an app pane, never a nav entry | `nav.test.ts` stays green, and `projectsSettings.test.ts` checks that `settings` is a `ProjectAppId` |
| The import entry left the sidebar | `projectsSettings.test.ts`: `page.tsx` has no "Import from ClickUp" sidebar row |
| The look | `conformance.test.ts`, plus the visual-review walk: light mode, compact density, a changed accent, and 390 px |
| Every status-set sentence comes from one module (§9.5) | `statusSetCopy.test.ts` (PST-1): each case of §9.5 returns its exact text, and no possessive form of an owner name occurs. `projectsSettings.test.ts` checks that no status component builds the scope line itself |
| One read for the owner, the count and the exceptions (§9.4) | `tests/unit/test_projects_status_set_read_r8.py` (PST-1), on a real database. The other fences of §9 sit in the slice rows of §7.1 |

## 7. Slices

| Slice | Delivers | Done when |
|---|---|---|
| **PS-1** | The pane, the section list, the space picker, the five managers inline through `ManagerFrame`, Import & export, and the entry points of §4.1 | An admin opens Settings from the sidebar, picks a space, edits a status, a field, a tag, the lifecycle and the space name without leaving the pane, and starts an import. The row menus still open the same dialogs. The walk passes in light mode, at compact density, under a changed accent, and at 390 px |
| **PS-2** ✅ built 2026-09-29 | Task types: list, create, rename, recolour and delete, for the space | The section edits types through the existing routes (`admin.py:858-1009`). **Met:** a browser walk against a real gateway and database added a type, set its colour and its icon, renamed it and deleted it, and found Epic's rename and delete disabled. **No default:** `pm_task_types.is_default` exists, but `create_task` never reads it, so the screen offers no default it cannot keep. HANDOFF H-203 asked the owner whether new tasks should take one, and the owner ruled NO on 2026-10-01: types stay optional. The walk found a 500: a name the space already held fired migration 175's unique index. `create_type` and `patch_type` now answer 409, and `tests/live/live_ws42_types.py` proves it on Postgres. The seed stores icons in kebab case, so the picker compares names without case (`taskTypes.test.ts`) |
| **PS-3** ✅ built 2026-09-29 | Shared vocabulary: the organization's own tags, fields and types, with rename (D-PM-33) | HANDOFF H-4 closes. The owner can then decide H-5. **Met:** `GET /projects/vocabulary` (`vocabulary.py`) lists only org-wide rows of the caller's tenant, with open-task counts for a member who holds `admin:settings:manage` and none for anybody else. `tests/live/live_ws42_vocabulary.py` proves the tenant fence, the counts and the member case on Postgres, and fails six ways when the tenant predicate is removed. A browser walk against a real gateway renamed a shared tag after the count dialog (4 tasks in 2 spaces), renamed a shared type, and found the new name locked in a space's Task types. **Not in this slice:** a create, because `PROJECTS_ORG_VOCABULARIES` is off (H-5), and a retire, because D-PM-33 refused merge and delete. PS-3b adds both after the H-205 ruling |
| **PS-3b** ✅ LIVE 2026-10-01 (#557, serving `b047d559`, and `PROJECTS_ORG_VOCABULARIES=1` on production) | Retire and add shared entries. Owner rulings 2026-10-01: **H-205**: an admin may delete a shared tag, field or type, and merge a shared tag into another shared tag, after the count. **H-5**: creating shared entries goes live once removal ships, from an Add form in Shared vocabulary. **H-203**: a new task takes NO default type, so types stay optional | `GET /vocabulary/{kind}/{id}/impact` gives the count of tasks and spaces first, and each delete or merge asks with it. The create goes through each kind's one create route with `scope: "org"`, anchored on a space the admin can see. **A fix on the way:** `governed_tasks_scope` now leaves out a space that keeps its OWN tag or field of the same identity. Tags are text and field values are keyed, so an organization-wide rename, merge or delete used to rewrite that space's own data too. **Met:** `tests/live/live_ws42_retire.py` passes 17/17 on Postgres, and 8 checks fail with the exclusion removed. `live_ws27bj_rename.py` and `live_ws42_vocabulary.py` still pass. A browser walk against a real gateway added, deleted and merged shared tags through the count dialogs |
| **PS-4** | Export | A spec section says what an export holds and in which format, before any build |

### 7.1 Status-set slices (§9, D185)

Gate label of each slice: **AGENT-SAFE**. Risk tier: STANDARD, so each slice skips the audit when its Metorite task states the acceptance criteria. A slice that changes a screen needs the visual-review walk of §6. No slice adds a migration.

**PST-1 (task 1798). Goes first.**

- **Does:** the extended read of §9.4. The copy module of §9.5. The scope line on every Statuses screen (the dialog and the pane). The list of projects that own their own set, with links. The pane section gets the name "Space default statuses". The text "Client Projects's set" is fixed.
- **Files:** `apps/services/gateway/gateway/routes/projects/admin.py` (`describe_status_set`, `_OVERRIDES_BELOW_SQL`). A count query beside `_STATUS_SCOPE_SQL` in `routes/projects/core.py`. In `workbench/control_plane/src/app/projects`: `lib/statusSetCopy.ts` (new), `components/ProjectsSettings.tsx`, `components/StatusManager.tsx`, `components/StatusSetControl.tsx`, and the file that holds the section labels.
- **Done when:**
  1. `GET /projects/nodes/{id}/status-set` returns `owner_kind`, `used_by` and the richer `overrides`.
  2. The Space default statuses section of a space with 27 users shows "27 projects use these statuses." It lists the projects that own their own set. Each row links to that project's editor.
  3. The dialog of a subproject shows "Uses the statuses of {owner} (shared with {N})".
  4. A project named "Client Projects" never reads "Client Projects's set".
  5. The section list, the heading and the palette entry use "Space default statuses".
- **Fence (R7):** `tests/unit/test_projects_status_set_read_r8.py` on Postgres. It checks the counts for cases 1, 2 and 3. It checks a nested owner that the count must skip. It checks that tenant B cannot change the count of tenant A. It checks a hidden project that the list leaves out and the count includes. It fails when the `NOT p.owns_statuses` test leaves the recursive arm. Also `statusSetCopy.test.ts` and `projectsSettings.test.ts` (§6).

**PST-2 (task 1799). After PST-1. Parallel with PST-3 and PST-4.**

- **Does:** "Give this project its own statuses" and "Go back to the inherited statuses" replace the two radios. Each opens the preview, then `TaskMoveChoice` (§9.7), then the confirm. Both use the existing routes `POST /projects/nodes/{id}/status-set/preview` and `POST /projects/nodes/{id}/status-set`.
- **Files:** `components/StatusSetControl.tsx`, `components/TaskMoveChoice.tsx` (new, extracted from the inline mapping), `lib/statusSetCopy.ts`. `routes/projects/admin.py` only if the preview lacks a field.
- **Done when:**
  1. A member with the right gives a subproject its own statuses. The copy comes from the set that it uses today. The member sees the count of moving tasks first.
  2. The same member goes back. The own set stays saved (dormant). It returns on the next "Give this project its own statuses".
  3. A space shows the text of §9.5 and no button.
  4. The confirm stays off while a lane has no target.
- **Fence (R7):** `statusSetCopy.test.ts` (button text and target line). `tests/unit/test_projects_status_sets.py` gets a case: own, then inherit, then own again returns the dormant lanes. `TaskMoveChoice.test.ts` (confirm off while a target is empty).

**PST-3 (task 1800). After PST-1. Parallel with PST-2 and PST-4.**

- **Does:** the board chip of §9.5 replaces the Statuses button of task 1792. A click opens the editor. The permission gate of §9.6 hides the arrows and the two buttons of PST-2 from a member with `may_edit` false. The chip stays visible to that member.
- **Files:** `components/TaskBoard.tsx` (the `onManageStatuses` control). `page.tsx` (passes the read, and the comment above `projectActions` gets one line that points to §9.3). `components/StatusOrderArrows.tsx`. `components/StatusManager.tsx` (passes `may_edit`).
- **Done when:**
  1. The board shows the chip and not the Statuses button.
  2. The chip text matches §9.5 for each case.
  3. A member without `projects:settings:write` sees no arrows, and the dialog opens read-only.
  4. The chip does not show when the board does not group by status.
- **Fence (R7):** `statusChip.test.ts` (text per case, absent when not grouped by status). `StatusManager.arrows.test.ts` gets a case: no arrows when `may_edit` is false. `tests/unit/test_projects_agent_grants.py` stays green. It holds the server side of the same predicate.

**PST-4 (task 1790, the chat half). After PST-1. Parallel with PST-2 and PST-3.**

- **Flag (decided by the session lead, 2026-11-06):** `STATUS_SET_GUARD_ORGS`, a per-organization list in `Settings`. It is off by default. A flip reloads with no restart (`docs/flag_reload.md`). The only reader is `describe_status_set` in `admin.py`, which returns `guard_on` (§9.4). With the flag off, the three tools behave as they do today: no refusal, no shared-set text on the card, and the raw `position` argument.
- **Choice on `position`:** the server accepts `slot` and `after` at all times, and the old `position` field keeps working there (additive, so unflagged). The change in the TOOL is behind the same flag. With the flag on, the tool takes `slot` or `after` and no longer sends `position`. With the flag off, the tool keeps `position`.
- **Does (with the flag on):** `create_status`, `update_status` and `delete_status` read the §9.4 read first.
  1. When the project does not own its set, the tool refuses. It says that the set belongs to {owner} and that N projects share it. It offers `set_status_set` mode=own first.
  2. The member can ask on purpose to change the shared set. Then the card names the set and the count: "Changes the statuses of {owner} for {N} projects".
  3. `position` becomes a 1-based `slot`, or an `after` lane name. The server renumbers. The model can no longer send a raw sort key.
- **Files:** `apps/skills/skill-projects/skill_projects/writes.py` (`create_status`, `update_status`, `status_edit_refusal`, `_next_position`). `skill_projects/guarded.py` (`delete_status`). `routes/projects/admin.py` (`create_status` and `patch_status` accept `slot` or `after`). The tool schemas and the pack summary of the changed tools.
- **Done when:**
  1. A chat request to add a lane to a subproject that uses the space's set gets a refusal. The refusal names the space and N, and offers mode=own.
  2. The same request with an explicit "change the shared set" shows a card with the set and N.
  3. "Put Done last" with slot 4 of 4 puts it last in its stage group. The set order stays stage, then position, then name.
  4. The server refuses a slot outside the stage of the lane, and gives the allowed range.
  5. With the flag on, the tool does not send a raw position from the model any more.
  6. With the flag off, all five behaviours above are absent and the tools act as before this slice.
- **Fence (R7):** `tests/unit/test_projects_status_chat.py` (refusal, card text, slot and after, and a case that the flag off keeps today's behaviour, including `position`). `tests/unit/test_flag_reload.py` gets `STATUS_SET_GUARD_ORGS` in its list of reloading flags. `tests/unit/test_projects_status_slot_r8.py` on Postgres (the renumbering keeps the stage rule). The new chat tool fence set applies: claim strings, pack summary and pinned tables.

**PST-5 (task 1791, the delete flow). The server read can start after PST-1. The UI goes after PST-2.**

- **Does:** a delete of a status that holds tasks asks where the tasks go, in the app and in the chat, through the one flow of §9.7. The app already asks (`moveTo` in `StatusManager.tsx`). The chat already needs `move_to`. This slice makes the two the same: one count line, one proposed target by stage, one set of words. The chat card names the count and the target. It does not tell the model to "pass move_to".
- **Files:** `routes/projects/admin.py` (`GET /projects/statuses/{id}/delete-plan`). `components/StatusManager.tsx`. `components/TaskMoveChoice.tsx`. `skill_projects/guarded.py` (`delete_status`).
- **Done when:**
  1. The delete dialog shows the count, the proposed target and the other lanes.
  2. The chat tool shows the same on its card. It proposes a target when `move_to` is empty.
  3. The three blocked cases (the only lane, the last Done lane, the last closing lane) say why before any card.
  4. An empty lane deletes with no question.
- **Fence (R7):** `tests/unit/test_projects_status_delete_plan_r8.py` on Postgres (the plan matches what `DELETE` then moves, and the blocked reasons). `TaskMoveChoice.test.ts` (shared with PST-2). `tests/unit/test_projects_status_chat.py` (the card).

## 8. Verification

```bash
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

The visual-review skill walks the pane in the contexts of §6. Without them the
change is not done: nothing else tests layout.

## 9. Status sets: which set, who shares it

Owner request: 2026-11-05, Metorite epic task 1796. Owner decisions: 2026-11-06, **D185**.
Verified against code on 2026-11-06 at `main` `06cfc43f8`. **Nothing in this section is built yet.**
Slices: PST-1 to PST-5 (§9.8). Gate label of every slice: **AGENT-SAFE**.

### 9.1 What the owner asked

The owner opened Settings in the Projects sidebar and chose Statuses. The owner could not tell
what the screen showed. The owner asked if it was the set for the whole application. The owner
also asked what happens when different projects have different statuses. The rule for the fix
is: "leave no doubt in the minds of the users when they're configuring the statuses and the lanes."

The cause is real. On production, on 2026-11-05, the Metorite space owned a set that 27 more
projects used. The User Stories & Use Cases project owned its own set. The chat changed the
Metorite set for 28 projects when the owner asked about one subproject. No screen said so.

### 9.2 The model (stated once)

A **status set** is a group of lanes. Every lane has a stage (its category), a position and a name.
Migration 196 added `pm_projects.owns_statuses`. A node that does not own a set uses the set of
its nearest ancestor that owns one. A space always owns its set (a CHECK constraint). The resolver
is `status_owner_id` in `apps/services/gateway/gateway/routes/projects/core.py`.

Three cases cover every project:

| Case | Name | The project uses | An edit changes |
|---|---|---|---|
| 1 | **Owns** | Its own set | This project and each project below it that does not own a set |
| 2 | **Uses the space's set** | The set of its space | Every project that uses the space's set |
| 3 | **Uses an ancestor project's set** | The set of the nearest ancestor project that owns one | Every project that uses that ancestor's set |

A space is always case 1. A set that a project stops using goes dormant. It stays saved and
returns if the project owns its statuses again.

### 9.3 The decision (D185)

1. **The nearest owner wins.** The model of migration 196 stays. Every screen that shows
   statuses names the owner of the set.
2. **Settings, Statuses stays, and it is renamed "Space default statuses".** It shows the scope line.
   It lists each project that owns its own set, with a link.
3. **The board gets a chip that names the set.** A click opens the editor. The chip replaces the
   Statuses button that task 1792 added above the board.

**This supersedes one older rule, for statuses only.** The owner rule of 2026-08-31 (the comment
above `projectActions` in `page.tsx`) keeps management controls out of the view chrome. Decision 3
puts the chip in the view chrome. The chip is a label that opens the editor. It is not a toolbar
of management buttons. The rule of 2026-08-31 still binds for fields, tags and lifecycle.

### 9.4 The read: one server read for every surface

**Where it lives.** `GET /projects/nodes/{project_id}/status-set` already exists
(`describe_status_set` in `routes/projects/admin.py`). It already returns `owns`, `owner_id`,
`owner_name`, `inherit_from_id`, `inherit_from_name`, `overrides`, `can_inherit`,
`has_dormant_set`, `may_edit` and `edit_refusal`. PST-1 extends this read. PST-1 adds no second
route. The Settings pane, the board chip, the Statuses dialog and the chat tools all call it.
A second implementation of this read is a defect.

**What PST-1 adds to the response:**

| Field | Type | Meaning |
|---|---|---|
| `owner_kind` | `"space"` or `"project"` | The kind of the owner node |
| `used_by` | integer | Projects that use the set, not counting the owner. It counts every depth below the owner, and stops at a node that owns a set (`_STATUS_SCOPE_SQL`) |
| `overrides` | list of `{id, name, path, used_by}` | Each project below the owner that owns its own set. It keeps the rule of `_OVERRIDES_BELOW_SQL`: a node under another owner belongs to that owner's screen. `path` is the names from the space down, and `used_by` is the count for that project's set |

`used_by` is also the number in "shared with N". For a project that uses a set, N is the owner
plus the other users, which equals `used_by`. For the owner, N is `used_by`.

**Tenancy (R5).** The route keeps `_tenant_session()` and the visibility check
(`load_visible_project`). The counts come from `pm_projects` rows that Postgres RLS filters by
`organization_id`. No tenant comes from request input. The count includes projects that the
member cannot see, because an edit changes them too. The list `overrides` holds only projects
that the member can see. A line "and N more that you cannot see" covers the others, so the
member can read the full impact and no hidden name leaks. The R8 test proves both.

**The chat tools call the same read.** `status_edit_refusal` in `skill_projects/writes.py` already
reads it. PST-4 adds the shared-set checks to that one place. The read also returns `guard_on`
(boolean). It is true when `STATUS_SET_GUARD_ORGS` holds the organization of the caller. The route is the
ONE reader of that flag. The chat tool reads `guard_on` from the response and never reads the setting itself.

### 9.5 Each surface and its exact copy

One module holds every string: `workbench/control_plane/src/app/projects/lib/statusSetCopy.ts`.
It takes the read of §9.4 and returns the text. No component builds its own sentence. The
module builds a name phrase with no possessive, so "Client Projects's set" cannot occur. The
current text sits in `StatusSetControl.tsx` (`${info.owner_name}'s set`). PST-1 removes it.

**Scope line** (one line, under the title of every Statuses screen, in the dialog, the pane and the editor):

| Situation | Text |
|---|---|
| Case 2 or 3 | `Uses the statuses of {owner} (shared with {N})` |
| Case 1, project, `used_by` is 0 | `Uses its own statuses. No other project uses them.` |
| Case 1, project, `used_by` is more than 0 | `Uses its own statuses (shared with {N})` |
| Case 1, space (pane), `used_by` is 0 | `No other project uses these statuses.` |
| Case 1, space (pane), `used_by` is 1 | `1 project uses these statuses.` |
| Case 1, space (pane), `used_by` is more than 1 | `{N} projects use these statuses.` |

The scope line has a second sentence when an edit reaches other projects: `A change here changes
the lanes of {N} projects.` The word "projects" is "project" when N is 1.

**Pane section.** The Statuses section of Settings has the name **Space default statuses**. Its heading
names the space. Under the scope line, it lists the projects that own their own set. Each row has
the project name, its path and a link that opens that project's Statuses editor. The list heading is
`{count} projects keep their own statuses` (`1 project keeps its own statuses`). With no such
project, the list shows `No project keeps its own statuses.` The Statuses section label in the
section list, the heading and the palette entry all use the new name.

**Board chip.** The chip sits in the place of the Statuses button of task 1792. It shows only when
the board groups by status. It reads:

| Situation | Chip text |
|---|---|
| Case 1, project | `Own statuses` |
| Case 1, space, `used_by` more than 0 | `Statuses: {space} (shared)` |
| Case 1, space, `used_by` is 0 | `Own statuses` |
| Case 2 | `Statuses: {space} (shared)` |
| Case 3 | `From {owner}` |

A click opens the Statuses editor of the selected node (the `StatusManager` dialog). The
tooltip is the scope line. The chip is a button with an accessible name equal to the scope line.

**Dialog (editor) of a project.** Two plain buttons replace the two radios of `StatusSetControl`:

- Case 2 or 3: `Give this project its own statuses`.
- Case 1 (a project, not a space): `Go back to the inherited statuses`. The line under it names the
  target: `{project} will use the statuses of {inherit_from_name}. Its own statuses stay saved.`
- A space shows neither button. It shows `A space always has its own statuses.`

**Member with no right.** The editor, the arrows and the buttons above are read-only. The
sentence is the one that the dialog already shows (`Statuses come from {owner}. Changing them needs
the projects:settings:write permission. Ask an organization admin.`).

### 9.6 The permission rule

One predicate decides: `can_manage_settings` in `routes/projects/core.py`
(`projects:settings:write`). The server write calls `assert_can_manage_settings` and answers 403.
The read of §9.4 returns the answer as `may_edit` and the server words as `edit_refusal`.

The UI must follow the read. A member with `may_edit` false sees no up and down arrows. That
member sees no "Give this project its own statuses" button and no "Go back" button. That member
still sees the chip, the scope line and the lane list. A click on the chip opens the dialog in
read-only form. The server stays the real gate. The UI gate only removes a control that would
end in a 403 (the review of task 1792 found one).

### 9.7 Where do the tasks go: one flow

Three acts move tasks out of a lane or a set:

1. Delete a status that holds tasks (task 1791).
2. Give a project its own statuses (PST-2). The tasks map from the old set to the copy.
3. Go back to the inherited statuses (PST-2). The tasks map from the own set to the inherited one.

All three use one flow, and each asks the same question: where do these tasks go?

- **One component:** `workbench/control_plane/src/app/projects/components/TaskMoveChoice.tsx`.
  PST-2 builds it from the inline mapping of `StatusSetControl.tsx`.
  The delete dialog of `StatusManager.tsx` uses it from PST-5.
- **It leads with the count.** `{n} tasks are in "{lane}". Choose where they go.`
- **It proposes a target by stage.** The target is the first lane in the same stage. If that stage
  has no lane, it is the nearest stage that has one. The proposal comes from the server
  (`_moves_for` for a set switch, the delete plan for a delete). The member can change it.
- **It never moves without a choice.** The confirm button stays off while a lane has no target.
- **The chat uses the same words.** A chat tool names the count and the proposed target on its
  card. It does not ask the member to pass a hidden argument (§9.8, PST-5).
- **One server read for a delete:** `GET /projects/statuses/{status_id}/delete-plan`. It returns
  `tasks`, `targets` (the other lanes of the set), `suggested_id` and `blocked_reason`. The reasons
  are the three 409 cases of `delete_status` in `admin.py`. The write stays `DELETE
  /projects/statuses/{status_id}?move_to=`. The read is read-only and tenant-scoped like §9.4.

### 9.8 Slices

| Slice | Task | Tier |
|---|---|---|
| PST-1 | 1798 | STANDARD |
| PST-2 | 1799 | STANDARD |
| PST-3 | 1800 | STANDARD |
| PST-4 (the chat half) | 1790 | STANDARD |
| PST-5 (the delete flow) | 1791 | STANDARD |

No slice adds a migration or a table. PST-1, PST-2, PST-3 and PST-5 have no flag, because each one changes
only words, the place of controls that exist, or a read. **PST-4 ships dark** (decided by the session lead on
2026-11-06, under the owner's standing instruction to follow the recommendations). Its chat refusal changes
what a write tool does, so it sits behind the flag `STATUS_SET_GUARD_ORGS` (§7.1). The owner can change these choices.

Order and parallel work:

- **PST-1 goes first.** It builds the read of §9.4 and the copy module of §9.5. Every other slice uses both.
- **After PST-1, three slices run in parallel:** PST-2, PST-3 and PST-4. They touch different files.
  PST-4 touches Python only.
- **PST-5 goes after PST-2.** It uses `TaskMoveChoice.tsx` and edits `StatusManager.tsx`, which PST-2 also edits.
  The server part of PST-5 (the delete plan) can start as soon as PST-1 merges.

The full definition of each slice is in §7. The rows there are the contract.
