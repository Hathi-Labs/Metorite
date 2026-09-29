# Projects settings — one place for every setting of the Projects app

**Status: ACTIVE — PS-1 and PS-2 built 2026-09-29.** Owner directive, 2026-09-29. Board row
**WS-42**. This spec records **D81**.

Verified against code on 2026-09-29 at `main` `02658314`.

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
   keeps one link to it. An organization with no spaces is often moving in
   from another tool.

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
- **A project's own status set.** A project that owns its set (migration 196)
  keeps it in its row menu. The space section edits the space's set and says
  where a project's own set lives.

## 4. The surface

### 4.1 Where it opens

- A **"Settings" row** with a gear icon, last in the Projects app list, after
  Reports and AI chat. It is drawn like the rows above it.
- The command palette: "Projects settings".
- The header menu of a selected project: "All settings for this space", which
  opens the pane at the project's space.
- The empty tree: "Import from another tool", which opens the pane at
  Import & export.

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

## 7. Slices

| Slice | Delivers | Done when |
|---|---|---|
| **PS-1** | The pane, the section list, the space picker, the five managers inline through `ManagerFrame`, Import & export, and the entry points of §4.1 | An admin opens Settings from the sidebar, picks a space, edits a status, a field, a tag, the lifecycle and the space name without leaving the pane, and starts an import. The row menus still open the same dialogs. The walk passes in light mode, at compact density, under a changed accent, and at 390 px |
| **PS-2** ✅ built 2026-09-29 | Task types: list, create, rename, recolour and delete, for the space | The section edits types through the existing routes (`admin.py:858-1009`). **Met:** a browser walk against a real gateway and database added a type, set its colour and its icon, renamed it and deleted it, and found Epic's rename and delete disabled. **No default:** `pm_task_types.is_default` exists, but `create_task` never reads it, so the screen offers no default it cannot keep. HANDOFF H-203 asks the owner whether new tasks should take one. The walk found a 500: a name the space already held fired migration 175's unique index. `create_type` and `patch_type` now answer 409, and `tests/live/live_ws42_types.py` proves it on Postgres. The seed stores icons in kebab case, so the picker compares names without case (`taskTypes.test.ts`) |
| **PS-3** ✅ built 2026-09-29 | Shared vocabulary: the organization's own tags, fields and types, with rename (D-PM-33) | HANDOFF H-4 closes. The owner can then decide H-5. **Met:** `GET /projects/vocabulary` (`vocabulary.py`) lists only org-wide rows of the caller's tenant, with open-task counts for a member who holds `admin:settings:manage` and none for anybody else. `tests/live/live_ws42_vocabulary.py` proves the tenant fence, the counts and the member case on Postgres, and fails six ways when the tenant predicate is removed. A browser walk against a real gateway renamed a shared tag after the count dialog (4 tasks in 2 spaces), renamed a shared type, and found the new name locked in a space's Task types. **Not in this slice:** a create, because `PROJECTS_ORG_VOCABULARIES` is off (H-5), and a retire, because D-PM-33 refuses merge and delete (H-205) |
| **PS-4** | Export | A spec section says what an export holds and in which format, before any build |

## 8. Verification

```bash
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

The visual-review skill walks the pane in the contexts of §6. Without them the
change is not done: nothing else tests layout.
