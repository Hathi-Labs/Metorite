# One Projects app — My Tasks becomes a view, and the phone shows the work

**Status: PROPOSED, 2026-10-23.** Owner directive of 2026-10-23, recorded in §1.
Verified against code on 2026-10-23, at `origin/main` `f206f5251`. Board row
**WS-64**. Nothing is built.

§7 proposes **D-OP-1** to **D-OP-8**. Each has a default. The owner must
answer the four questions of §8 before slice OP-4. Every slice in §9 is
AGENT-SAFE to build dark, except where §9 says OWNER-GATE.

**What this spec owns.**

1. **One app for all tasks.** Projects (`/projects`) holds the company work
   and the personal work. My Tasks stops being a separate app and becomes the
   **My work** section of Projects.
2. **The personal space.** The Areas of My Tasks become the folders of one
   private space in the Projects tree.
3. **Capture and clarify in Projects.** The inbox, the Clarify flow and the
   lists of Getting Things Done (GTD) move into Projects.
4. **The phone layout of Projects.** The filter and view controls fold away,
   so a phone shows tasks first. Part of this also goes to the desktop.

**What this spec does not own.**

- **The store.** `pm_tasks` and `pm_task_personal` stay the one task store
  (D52, D53). This spec adds no table and no column.
- **The GTD rules.** `task_manager_app.md` §13.4 keeps every disposition, the
  rule for `expected_by`, and the overlay of each member.
- **The Calendar.** `/calendar` stays its own app (D54). It reads the same
  store.
- **The shell.** `navigation_shell.md` (D87 to D89) keeps the shell bar, the
  one assistant and the manifest. This spec only changes the manifest entries
  of Projects and My Tasks.
- **The chat tools.** `skill_my_tasks` and `skill_projects` keep their tools.
  A later spec can merge them.

---

## 1. What the owner asked for

Verbatim, 2026-10-23:

> "I want the tasks app to just become a specific view of the projects app,
> where we basically look at our own tasks"

> "the projects app itself gains the ability for task inbox capturing,
> clarification, etc., before some things are actually put into a proper
> project process"

> "So it's always the projects app"

> "spaces and areas is more or less the same, but can become like a personal
> space in the projects app"

> "we should be able to see personal tasks and projects, the personal task
> inbox, and basically one cohesive app"

> "a large part of the work area is taken by filters and options and can't
> really actually see the tasks at hand"

> "some of this can also get carried over to desktop if needed"

**What this reverses.** On 2026-08-31 the owner removed "My work" from the
Projects sidebar, because `/tasks` was the personal door
(`projects/lib/projectApps.ts:44`). D73 (2026-09-23) named the separate app
"My Tasks". This directive keeps the store of D53 and D73, and reverses only
the surface: one app, one door. It is a new owner directive, not a re-opened
decision. D-OP-1 records it.

---

## 2. Why one app

The store is already one store. Two apps on one store make three costs.

1. **The member must choose.** A task assigned to me shows in both apps. The
   member does not know where to work on it.
2. **Two sets of controls.** Projects has `FilterBar.tsx`. My Tasks has
   `TaskToolbar.tsx`. They filter the same rows in two ways.
3. **Two detail panels.** `my_tasks_cutover.md` §4.8 item 3 found
   `ItemDetail.tsx` and `TaskPanel.tsx`. S6e joined them, and the two shells
   around them still differ.

One app removes the choice. The GTD method stays. It becomes the way that
Projects treats **my** work.

---

## 3. What is true today (measured 2026-10-23)

All paths are under `workbench/control_plane/src` unless the path says
otherwise.

### 3.1 The two apps

| Thing | My Tasks (`/tasks`) | Projects (`/projects`) |
|---|---|---|
| Page | `app/tasks/page.tsx` (450 lines) | `app/projects/page.tsx` (4933 lines) |
| Sidebar | `app/tasks/components/ListsSidebar.tsx`: Inbox, My Next Actions, Waiting For, Someday / Maybe, Done, Archive (:34-47), then Areas (:234) | `PROJECT_APP_SECTIONS` in `app/projects/lib/projectApps.ts` (Reports, AI chat, Settings), then the Spaces tree |
| Lists | `ItemList.tsx`, `InboxView.tsx`, `EngageView.tsx`, `WaitingForView.tsx`, `LedProjectView.tsx` | `TaskBoard.tsx`, `TaskList.tsx`, `TableView.tsx`, `CalendarView.tsx`, `TimelineView.tsx` |
| Filters | `TaskToolbar.tsx:88`, from `useTaskStore` | `FilterBar.tsx` (922 lines) |
| Store | `app/tasks/lib/taskStore.ts` | page state in `app/projects/page.tsx` |
| Phone tabs | Inbox, Lists, Capture, Assistant (`components/AppShell.tsx` :806-832) | Menu, Projects, Views, Search, Ask (`AppShell.tsx` :828-846) |
| Gate | `feature:tasks` | `feature:projects` |

### 3.2 Areas and spaces

- **An Area** is a child of the member's personal root. It carries
  `personal_owner`. The routes are `GET/POST/PATCH/DELETE /projects/my/areas`
  in `apps/services/gateway/gateway/routes/projects/personal.py`.
- **A space** is a company node with level `"space"` in the tree
  portfolio > space > folder > project > subproject (`app/projects/lib/api.ts:240`).
- `GET /projects/tree` (`routes/projects/tree.py:254`) leaves out every node
  with `personal_owner`. So Projects cannot show the personal root today.
- `assert_move_keeps_privacy` refuses a move of a personal node under a team
  node. That guard stays.

**Conclusion.** The personal root is already a node of the one tree. It is
only hidden. The personal space of this spec is that root, shown to its owner
alone.

### 3.3 The phone chrome of Projects

The owner's phone capture (2026-10-23, 923 × 2000 px) shows the first task
card at about **y = 1340**. The chrome above it is **about 60 %** of the
screen. The bottom tab bar takes about 10 % more.

| # | Element | File | Phone logic |
|---|---|---|---|
| 1 | Title bar | `components/AppTopBar.tsx:140` | compact on phone |
| 2 | Search, stage, assignee, four chips, Archived, Group by, Lanes, Subtasks, Fields, Export | `app/projects/components/FilterBar.tsx:362-718` | none, it only wraps |
| 3 | Tag chips with counts | `FilterBar.tsx:736-770` | none |
| 4 | Saved views, "Save view" | `FilterBar.tsx:771-870`, `components/ViewChips.tsx` | `sm:px-4` only |
| 5 | Agent strip (five counts, three at zero) | `app/projects/components/AgentWorkStrip.tsx:43` | none |
| 6 | "Showing 100 of 219" banner | `app/projects/components/MoreTasksBar.tsx:49` | none |
| 7 | Card action row (done, subtask, edit, more) | `app/projects/components/TaskCardActions.tsx` | always shown on touch |

The page knows it is on a phone: `useViewMode()` at `app/projects/page.tsx:627`,
and the phone branch at :4670-4695. The list view is the phone default
(:733). `components/ui/Modal.tsx` has `placement="sheet"` (:147-153), a
bottom sheet. Nothing uses it for filters.

---

## 4. The shape of the one app

### 4.1 The sidebar

```
Projects
├── My work                     ← new section, for me only
│   ├── Inbox            12     capture lands here
│   ├── Today             5     my Next Actions with a date of today or before
│   ├── Next actions     31
│   ├── Waiting for       4
│   ├── Someday / Maybe   9
│   └── Done
├── Personal  🔒                ← my personal space (the personal root)
│   ├── Health                  ← an Area, now a folder of the space
│   ├── Home
│   └── Side project
├── Spaces                      ← the company tree, as today
│   ├── Engineering
│   └── Sales
└── Reports · AI chat · Settings
```

- **My work** is a set of views across every project, company and personal.
  It shows the tasks assigned to me, and the tasks of my personal space. It is
  the lens of D53: the disposition comes from `pm_task_personal`.
- **Personal** is a space like any other, with the board, list, table,
  calendar and timeline views. Only its owner sees it. It has a lock mark.
- **Spaces** is the company tree. Nothing changes there.

### 4.2 The flow of a task

1. **Capture.** A quick add in My work, or the capture tab on the phone, makes
   a task in the personal root with no disposition. It shows in Inbox.
   A `#project` token or a project chip sends it straight to a project
   (S6g already built this in My Tasks).
2. **Clarify.** The Inbox opens the Clarify flow of `ClarifyPanel.tsx`. The
   member picks: do it, a next action, waiting for, someday, delete, or move
   to a project.
3. **Organise.** "Move to a project" is the one promote path of S6g, through
   `move.py`. A task can also stay in a personal folder.
4. **Engage.** Today and Next actions show my work with context and energy.
   A company task keeps its own stage. My disposition is mine (D53).

### 4.3 What the member sees in a task

One panel, `TaskPanel.tsx`. In a My work view, the overlay strip of S6e
(disposition, context, energy, estimate, defer) sits above the fields. In a
company view, the strip shows only when I hold an overlay row. This is the
rule of `my_tasks_cutover.md` §4.8 item 4.

### 4.4 Routes

- `/projects` keeps its URL state grammar. My work adds a view key in that
  same grammar. The builder of OP-2 picks the key and writes it here.
- `/tasks` and each of its deep links redirect to the matching My work view.
  Links in My Day cards, WhatsApp replies and email stay valid.
- `/calendar` does not change.

### 4.5 The gate

`feature:tasks` keeps the My work section and the Personal space.
`feature:projects` keeps the Spaces tree. A member who holds only
`feature:tasks` opens `/projects` and sees My work and Personal, and no
company tree. D-OP-4 holds this. With the flat seat (`core`) every member
holds both today, so the case is rare.

---

## 5. The phone layout

### 5.1 The rule

**On a phone, the first task row shows in the top third of the screen.** The
chrome above the list is one bar and an optional row of active filters, no
more. Every other control opens in a bottom sheet.

### 5.2 Top to bottom

```
┌──────────────────────────────────────┐
│ ☰  Metorite Application    ↶ ↷  ⋯   │  title bar (as today)
├──────────────────────────────────────┤
│ 🔍 Search tasks…   [⚲ Filters 2] [▤] │  one tool row
│ (Overdue ✕) (Feature ✕)   Clear      │  active filters only, scrolls sideways
├──────────────────────────────────────┤
│ Backlog 41 · To do 22 · Doing 9 · …  │  status tabs, scroll sideways, sticky
├──────────────────────────────────────┤
│ ○ Link artifacts into agent memory   │
│   AI Agent · Feature   #137     ⋯   │  task rows
│ ○ Govern agent memory: access…       │
│   …                                  │
│         Load 100 more (100 of 219)   │  at the end of the list
├──────────────────────────────────────┤
│  ☰ Menu   ✓ My work   ＋   🔍   ✦ Ask │  tab bar
└──────────────────────────────────────┘
```

1. **The tool row.** A search field, a **Filters** button with the number of
   active filters, and a **View** button (▤).
2. **The Filters sheet** (`Modal placement="sheet"`). It holds stage,
   assignee, Overdue, Watching, Waiting on me, Agent work, Archived, and the
   tags with their counts. It has "Clear" and "Done".
3. **The View sheet.** It holds Board, List or Table, Group by, Lanes,
   Subtasks, Fields, Export, and the saved views with "Save view".
4. **The active-filter row.** It shows only when a filter is on. Each chip
   has a ✕. The row scrolls sideways and never wraps.
5. **The agent strip** folds into one chip in the active-filter row, for
   example "1 waiting · 2 working". A count of zero does not show. With no
   count above zero, the strip does not show.
6. **The load banner** moves to the end of the list, as a "Load 100 more"
   row. The note "Lane counts and grouping describe what is loaded" moves to
   the help text of the View sheet. The tool row shows "100 of 219" in small
   type when the list is not complete.
7. **The status tabs.** On a board view, the phone shows one status at a
   time, full width, with a sideways row of status tabs and their counts.
   A swipe moves to the next status. This replaces columns that the screen
   cuts in half.
8. **The card actions.** On touch, the row of done, subtask and edit does
   not show on each card. A tap opens the task. A long press or the ⋯ opens
   the actions. The tick to complete stays on the left of each row.
9. **The tab bar.** Menu, My work, a capture button (＋), Search and Ask.
   The capture button opens quick add, which writes to the Inbox. "Projects"
   and "Views" leave the tab bar: the tree opens from Menu, and the views
   open from the View button.

### 5.3 What goes to the desktop

| Phone change | Desktop |
|---|---|
| Filters and View sheets | Yes, as two popovers (`AnchoredPanel`). The tool row is one line |
| Active-filter row | Yes |
| Zero counts hidden in the agent strip | Yes |
| Load banner at the end of the list | Yes |
| Status tabs in place of columns | No. The desktop keeps the columns |
| Card actions on long press | No. The desktop keeps them on hover |

D-OP-7 holds this split.

### 5.4 The same layout in My work

The My work views use the same tool row and sheets. The Filters sheet of My
work adds context, energy and source (All, Mine, From Projects), from
`TaskToolbar.tsx`. One filter component serves both, with a set of facets
for each view. `TaskToolbar.tsx` retires in OP-5.

---

## 6. Risks

1. **A personal task leaks to a team view.** The personal root enters the
   tree response. Every team read must still leave it out. Fence: OP-3 adds a
   two-member case to `tests/unit/test_personal_tree.py` and to
   `live_ws39_personal_tree.sql`. R8 applies.
2. **A broken link.** `/tasks` links live in My Day, Reach and WhatsApp
   replies. Fence: OP-4 adds a redirect test for every route that
   `app/tasks` serves.
3. **The page grows.** `app/projects/page.tsx` has 4933 lines. My work must
   not add its views inline. OP-2 mounts the existing My Tasks views as
   components, imported and not copied.
4. **Two stores in the client.** `useTaskStore` and the Projects page state
   both hold tasks. OP-2 keeps `useTaskStore` for My work views. OP-5 decides
   if one client store is worth the cost. It is not a goal of this spec.

---

## 7. Proposed decisions

| Id | Decision | Default |
|---|---|---|
| **D-OP-1** | My Tasks stops being an app. Projects holds it as the My work section. The store of D53 does not change | Accept (owner directive) |
| **D-OP-2** | The personal root shows in Projects as one private space, named "Personal". Areas are its folders | Accept |
| **D-OP-3** | The name of the section is **My work** | Accept. Other names: "My tasks", "Me" |
| **D-OP-4** | `feature:tasks` gates My work and Personal. `feature:projects` gates the company tree | Accept |
| **D-OP-5** | `/tasks` redirects. The nav entry "My Tasks" leaves `launch_surface.md` §2, so eleven panes are live | Accept, in OP-4 |
| **D-OP-6** | On a phone, every filter and view control lives in two bottom sheets. The first task row shows in the top third | Accept |
| **D-OP-7** | The desktop takes the sheets as popovers, the active-filter row, the hidden zero counts and the load row at the end. It keeps the columns and the hover actions | Accept |
| **D-OP-8** | The phone tab bar of Projects is Menu, My work, Capture, Search, Ask | Accept |

---

## 8. Questions for the owner

1. **The name.** "My work" (default), "My tasks" or "Me"?
2. **The landing view.** When I open Projects, do I land on My work → Today
   (default), or on the last space I opened?
3. **The nav.** Does "My Tasks" leave the left nav at OP-4 (default), or stay
   one release as a link into Projects?
4. **The Personal space for others.** Can a member share one personal folder
   with a colleague? The default is **no**. A shared folder is a company
   project, and "Move to a project" makes one.

---

## 9. Slices

Each slice ships dark behind `NEXT_PUBLIC_PROJECTS_MY_WORK`, default off,
except OP-1. OP-1 is a layout change of a live app, and it ships behind
`NEXT_PUBLIC_PROJECTS_COMPACT_CHROME`, default off.

### OP-1 — the phone chrome · AGENT-SAFE

**Scope.** §5.2 items 1 to 8 for the Projects company views, and the desktop
changes of §5.3. No change to the tab bar (that is OP-4).

**Files.** `app/projects/components/FilterBar.tsx` (split into the tool row,
`FilterSheet` and `ViewSheet`), `AgentWorkStrip.tsx`, `MoreTasksBar.tsx`,
`TaskBoard.tsx` (status tabs on a phone), `TaskCardActions.tsx`.

**Done when.**
1. At 390 × 844 with the flag on, the top of the first task row is at
   y ≤ 281 (one third), with no filter on. Fence: a Playwright test in the
   visual rig.
2. With two filters on, both show as chips with ✕, and the Filters button
   reads "Filters 2". Fence: a vitest test of the tool row.
3. A count of zero never shows in the agent strip. Fence: vitest.
4. Every control of today's `FilterBar` is reachable in a sheet or a popover.
   Fence: a vitest test that lists the controls by role and name.
5. The visual-review skill passes in light mode, compact density and a
   changed accent, at 390, 768 and 1280 px.

### OP-2 — My work in the Projects sidebar · AGENT-SAFE

**Scope.** The My work section of §4.1. Each entry mounts the existing view
component of `app/tasks/components` inside the Projects work area. The task
panel is `TaskPanel.tsx` with the overlay strip.

**Done when.**
1. With the flag on, My work shows Inbox, Today, Next actions, Waiting for,
   Someday / Maybe and Done, each with the count that `/tasks` shows today.
   Fence: vitest on the section, and an e2e that compares the counts.
2. No file under `app/projects` copies a component of `app/tasks`. Fence:
   a source test.

### OP-3 — the Personal space · AGENT-SAFE · R8

**Scope.** `GET /projects/tree` returns the caller's own personal root and its
Areas as a space, marked `personal: true`. No other member's root returns.
The sidebar draws it with a lock. The Areas routes stay as they are.

**Done when.**
1. Member A sees their personal root. Member B, with every grant, does not.
   Fence: `tests/unit/test_personal_tree.py` and
   `live_ws39_personal_tree.sql`, on a real database.
2. A move of the personal space, or of a folder in it, under a team node
   fails. Fence: the existing `assert_move_keeps_privacy` test, with the new
   shape.

### OP-4 — capture, the tab bar, and the redirect · OWNER-GATE for the nav

**Scope.** The capture button and §5.2 item 9. `/tasks` and each of its deep
links redirect into My work. The nav entry change of D-OP-5.

**Gate.** Building it is AGENT-SAFE, dark. Taking "My Tasks" out of the nav
changes the live surface for every org, so the flip is the owner's, after
the answers of §8.

**Done when.**
1. Each route under `app/tasks` redirects to its My work view. Fence: a
   redirect test.
2. `nav.test.ts` and `launch_surface.md` §2 agree on eleven live panes after
   the flip.

### OP-5 — one filter component, and the old shell retires · AGENT-SAFE

**Scope.** §5.4. One filter component with facet sets. `TaskToolbar.tsx`,
`app/tasks/page.tsx` and the My Tasks phone tabs retire. The views move from
`app/tasks/components` to `app/projects/components/my-work/`.

**Done when.** No import of `app/tasks/components` is left outside the moved
folder, and the e2e of OP-2 still passes.

---

## 10. Verification

```
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/projects src/app/tasks src/lib/nav.test.ts
uv run pytest tests/unit/test_personal_tree.py
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)" && uv run pytest tests/unit/test_personal_tree.py
```

For OP-1 and OP-2, also run the visual-review skill at 390, 768 and 1280 px.
