# One Projects app — My Tasks becomes a view, and the phone shows the work

**Status: ACTIVE, 2026-10-23.** The owner accepted D-OP-1 to D-OP-8 (§8). Owner directive of 2026-10-23, recorded in §1.
Verified against code on 2026-10-23, at `origin/main` `f206f5251`. Board row
**WS-64**. OP-1 is merged (#1255), dark behind `NEXT_PUBLIC_PROJECTS_COMPACT_CHROME`. OP-7 is built, not merged. Fix round 1 is done, and done-when 4 is met (visual review passed 2026-10-23 at 390 and 1280 px, light, compact, changed accent). OP-6a is built, not merged. OP-2 to OP-5 and OP-6b to OP-6e are not built.

§7 holds **D-OP-1** to **D-OP-8**. The owner accepted every default on
2026-10-23 (§8). Every slice in §9 is
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

## 5a. Every act of the app from the chat (D-OP-9)

Owner, 2026-10-23, verbatim:

> "The in-app chat should also be allowed to delete with permissions. So it'll
> just show a permission card and it should be allowed to delete."

> "every single aspect of the control of the project's app should be possible
> to do with the chat application, including deleting"

**What is true today (measured 2026-10-23).** The chat deletes a task and a
project behind one card (`skill_projects/guarded.py:287` and :329, #1223,
in production since 2026-10-22). `skill_projects/manifest.py` maps 167
Projects routes. The chat reached 128 of them. 39 were class X (an earlier
count said 38). OP-6a gave the three area routes a tool, so 36 are class X now.

**The rule.** Every act that a member can do in the Projects app, the chat
can do too. A class C card guards each act that destroys data, writes access
or reaches another person. The card is a brake on a mistake. It is not
authority: the route still checks the member's own access.

**The class X routes, sorted.**

| Group | Routes | OP-6 does |
|---|---|---|
| Areas, now Personal folders | `POST/PATCH/DELETE /projects/my/areas…` | Three tools: `create_area`, `rename_area`, `delete_area`. **BUILT in OP-6a.** Rename and delete take a card |
| Grants | `POST/DELETE /projects/nodes/{id}/grants…` | Two tools, each behind a card. **This reverses D-PM-40** (owner, 2026-10-23) |
| Nudge | `POST /projects/tasks/{id}/nudge` | One tool behind a card, because it reaches a colleague |
| Export | `GET /projects/export/tasks.csv` | One tool. The file comes back as a chat document (S8) |
| Attachments | `POST /projects/tasks/{id}/attachments` | One tool that attaches a file the member uploaded to the chat (H-229) |
| Report delivery | recipients and schedule | Tools behind a card. The send switch `PROJECT_REPORT_EMAIL_ENABLED` stays the owner's (§3a rule 3) |
| Agent runs | the eight `PLANNED` tools | The member can approve or decline an agent's archive or delete from the chat. **NO-GO (OP-6e)** until the owner reverses EA3-40 |
| Stays X | view state, positions, delta, raw bytes, `/projects/nodes`, `/search/refs`, `/vocabulary` | These are client plumbing or a second door to a read the chat has. There is no act to give |
| Stays X | the import wizard, the Calendar planner routes | The import needs the review grid. The Calendar app owns the planner. Both have a door in their own app |
| Stays X | the six `/my/...` doors of My Tasks (project, batch capture, organize, checklist, purge, lanes), `GET /vocabulary/{kind}/{id}/impact` and `POST /tasks/{id}/agent-delete` | The chat gives the same acts through `create_personal_task`, `update_task`, `add_subtasks` and `delete_task`. The impact count belongs to the admin screen. The agent-delete door is for an outside agent (EA3-51, pinned by `test_mcp_edge_coverage.py`) |

`tests/unit/test_projects_chat_x_pin.py` pins the whole list. It names the 27
routes that stay class X, and it names the 9 routes that OP-6b to OP-6d still
owe, each with its slice.

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
| **D-OP-9** | Every act of the Projects app is reachable from the chat. A class C card guards each act that destroys data, writes access or reaches a person. Reverses D-PM-40 | Owner directive, 2026-10-23 (§5a) |
| **D-OP-10** | A delete shows the task's subtasks as a tree and lets the member mark each one: keep or delete. Every subtask is kept by default. A kept subtask under a deleted one moves up to its nearest kept ancestor. A delete never removes a subtask that the caller cannot see: it stays, and it moves up too (§9 OP-7, "The rule of the core"). One transaction, with one tombstone for each deleted row. The same choice is in the app, the chat card and the MCP tool. It amends D-PM-38 B10, which only promoted | Owner directive, 2026-10-23 (§9 OP-7) |
| **D-OP-8** | The phone tab bar of Projects is Menu, My work, Capture, Search, Ask | Accept |

---

## 8. The owner's answers (2026-10-23)

The owner accepted every default in chat on 2026-10-23: "go ahead with your
recommendations for all the open questions". So D-OP-1 to D-OP-8 are
**ACCEPTED**.

1. **The name** is **My work**.
2. **The landing view.** Projects opens on My work → Today. A deep link to a
   space still opens that space.
3. **The nav.** "My Tasks" leaves the left nav when the OP-4 flag goes on.
   `/tasks` redirects from that release.
4. **Sharing.** A member cannot share a Personal folder. "Move to a project"
   makes a company project, and that is the way to share.

---

## 8a. The board of the project "Projects/Tasks App" (2026-10-23)

The Metorite project "Projects/Tasks App" held 41 tasks on 2026-10-23. Each
open task was checked against the code and the git history.

**New tasks for this spec.** #295 OP-1, #296 OP-2, #297 OP-3, #298 OP-4,
#299 OP-5, and #300 EA-6h (`external_agent_access.md`).

**Folded into a slice.**

| Task | Slice | Why |
|---|---|---|
| #20 sideways scroll | OP-1 | The phone gets status tabs. The desktop keeps the scrollbar |
| #151 compact My Task Inbox | OP-2 | The Inbox is built again in My work |
| #175 waiting-on state | OP-2 | "Waiting for" becomes a My work view |
| #132 full name on hover | OP-2 | OP-2 changes the same sidebar |
| #8 phone Menu button | OP-4 | OP-4 rebuilds the phone tab bar |

**Closed as built** (moved to Done, with the evidence).

| Task | Evidence |
|---|---|
| #264 Archive view in the sidebar | `ProjectTree.tsx:558-580`, #1218 |
| #263 Archived projects hidden by default | `splitArchived` puts them in a closed fold |
| #262 Delete a project from the chat with approval | `skill_projects/guarded.py:287`, a guarded card |
| #169 Auto-link a task named in text | `routes/projects/text_links.py` |
| #165 Auto-link GitHub refs | `routes/projects/github_autolink.py`, #1142 |
| #164 GitHub refs on a task | `pm_task_refs`, `routes/projects/refs.py` |
| #154 Paste an image into a field | `useInlineUploads.ts` |
| #126, #125 One title bar per app | `AppTopBar.tsx`, #473, #725, #826 |
| #13 Add Task button | `QuickAdd.tsx`, `CardInput.tsx` |
| #5 Notification engine | `NotificationBell.tsx`, `routes/projects/notifications.py` |
| #153 Paused project's tasks | `project_management_app.md` D-PM-32: the state is derived |

**Built, still open on the board.** The session's permission check refused
the status change of #155 (paste artifacts onto a task, `useInlineUploads.ts`),
#15 (edit the title in the panel, `TaskPanel.tsx:264`) and #43 (delegate to an
agent, `DelegateDialog.tsx`, #1045). The owner moves them to Done.

**Leaves the board.** #129 (rename to "My Task Inbox") names a surface that
D73 and this spec retire. An archive request waits for the owner's approval
on the task.

**Stays open, not part of WS-64.** #168, #166, #128, #50, #49, #48, #47, #46,
#45, #44, #19, #18, #14, #11 and #7. Each is a real gap or a bug that needs a
browser check.

---

## 9. Slices

Each slice ships dark behind `NEXT_PUBLIC_PROJECTS_MY_WORK`, default off,
except OP-1. OP-1 is a layout change of a live app, and it ships behind
`NEXT_PUBLIC_PROJECTS_COMPACT_CHROME`, default off.

### OP-1 — the phone chrome · AGENT-SAFE

**Status.** BUILT DARK, 2026-10-23. Done-when 2, 3 and 4 and the structure half of 1 pass in `compactChrome.test.ts`. Done-when 1 passes: `e2e/projects-compact-chrome.spec.ts` gave 2 passed, and the first row sits at y = 164 with the flag on (y = 421 with it off). Done-when 5 passes: a visual review at 390, 768 and 1280 px in light mode, compact density and a changed accent found no P0 or P1. Follow-ups: a laned board on a phone has no status tabs, and the View sheet lists Overview and Timeline on a phone.

**Scope.** §5.2 items 1 to 8 for the Projects company views, and the desktop
changes of §5.3. No change to the tab bar (that is OP-4).

**Flag.** `app/projects/lib/compactChrome.ts` reads the literal
`process.env.NEXT_PUBLIC_PROJECTS_COMPACT_CHROME`, in the idiom of
`importFlow.ts:22-28`. Add the flag to `publicFlags.test.ts`.

**Files.** `app/projects/components/FilterBar.tsx` (split into the tool row,
`FilterSheet` and `ViewSheet`), `AgentWorkStrip.tsx`, `MoreTasksBar.tsx`,
`TaskBoard.tsx` (status tabs on a phone), `TaskCardActions.tsx`.

**Done when.**
1. At 390 × 844 with the flag on, the top of the first task row is at
   y ≤ 281 (one third), with no filter on. Fence: a new
   `e2e/projects-compact-chrome.spec.ts` that sets the viewport to
   390 × 844. The flag is inlined at build time, so the e2e server starts with
   `NEXT_PUBLIC_PROJECTS_COMPACT_CHROME=1`. A vitest test also asserts the
   structure: one tool row, and the other controls in the sheets.
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

### OP-6 — every act from the chat · AGENT-SAFE · split into OP-6a to OP-6e

The spec-auditor split OP-6 on 2026-10-23, because one slice cannot hold a
HIGH grant change, a file upload and a report schedule. **Scope.** §5a. New
tools in `skill_projects` for each row that leaves class X. Each moves its
route from class X to class B or C in `manifest.py`.
`test_projects_chat_coverage.py` stays the fence, and
`test_projects_chat_x_pin.py` pins the routes that stay X.

**Done when, for the whole of OP-6.**
1. Only the routes in the three "Stays X" rows of §5a are class X. Fence:
   `test_projects_chat_x_pin.py`, when `PENDING_OP6` is empty.
2. Each new class C tool shows a card before the request, and a declined
   card writes nothing. Fence: `test_projects_agent_writes.py`.
3. A grant write from the chat succeeds only where the member could write the
   grant in the app. Fence: an R8 test with two members.
4. The chat eval `evals/projects_ops/` gains one case for each new tool.

#### OP-6a — the member's areas, and the pin · MEDIUM · AGENT-SAFE

**Status: BUILT 2026-10-23 on branch `ws64-op6a-chat-areas-mt-e22721d2`, not yet merged.**
Three tools in `skill_projects`. `create_area` (class B) is in `inbox.py`.
`rename_area` and `delete_area` (class C) are in `guarded.py`. A name resolves
among the member's own live areas only. Two near-equal names ("Home" and
"home!") are a question to the member, never a guess.

The delete card says what the route does. An area with no task at all is
deleted for good. An area with any task, open or done, is archived and its
tasks stay. The card shows the open count, and the receipt says which one
happened. The card is not authority. `_load_my_area` in
`routes/projects/personal.py` finds an area by its owner, so another member's
area is not found.

Fences: the three tools in `test_projects_agent_writes.py`, then
`test_projects_area_tools_r8.py` (R8, two members) and
`test_projects_chat_x_pin.py`. The cases PO-12 to PO-14 in
`evals/projects_ops/` run the tools end to end.

⚠️ The auditor wrote "class B, no card" for `create_area`. The fence
`test_every_class_b_tool_awaits_the_one_confirm_door` holds that every class B
tool shows one card. So `create_area` shows a short card with the name. A tool
with no card is a new class, and the owner must decide on it.

#### OP-6b — grants · HIGH · BLOCKED

Two tools for `POST/DELETE /projects/nodes/{id}/grants…`, each behind a class
C card. **This reverses D-PM-40.** BLOCKED on an owner answer: who may write a
grant? Today `tree.py` `create_grant` and `delete_grant` check only that the
member can see the project.

A card is not authority, so the chat would give
every viewer a way to widen access. The route needs a rule before the tool.
Done-when 3 above is this slice's fence.

#### OP-6c — nudge, export, attach · MEDIUM

1. **Nudge.** One tool behind a class C card, because it reaches a colleague.
   It sends an in-app notification only, and it never loops.
2. **Export.** One tool. The file comes back as a chat document through the
   safe write of `acb_skills/documents.py`. The route answers 422 above
   `MAX_EXPORT_ROWS`, and the tool says that in words.
3. **Attach.** One tool that attaches a file the member uploaded to the chat.
   The bytes come through `read_attachment` in `acb_skills/attachment_tools.py`.
   A task that others share takes a card.

#### OP-6d — report recipients and schedule · HIGH-adjacent

Tools for the four recipient and schedule routes, each behind a class C card.
They never touch `PROJECT_REPORT_EMAIL_ENABLED`, which stays the owner's
(§3a rule 3). A recipient is a person outside the chat, so the card names the
address.

#### OP-6e — agent-run approval · NO-GO

The eight `PLANNED` agent-run tools stay unbuilt. EA3-40 says only the run's
member answers, in the card, and never a chat tool. The owner must reverse
EA3-40 first. The parity tools go to EA-12e.

### OP-7 — choose which subtasks go, in the app and in the chat · AGENT-SAFE

**Status: BUILT 2026-10-23 on branch `ws64-op7-subtask-pick-mt-b78601e2`, not yet merged.**
The app dialog is `projects/components/DeleteTaskDialog.tsx`, and its tree rules
are `projects/lib/deleteSubtasks.ts`. Both read `GET /projects/tasks/{id}/subtree`,
a new read that shows the visible subtasks and counts the hidden ones. The chat
card is `delete_task(subtasks_to_delete=)` in `skill_projects/guarded.py`. It uses
the card with rows, with the task as the first row. Done-when 4 (the visual
review at 390 and 1280 px) is **met**: it passed on 2026-10-23, at 390 and
1280 px, in light mode, at compact density and under a changed accent.

**Fix round 1 (2026-10-23).** (1) The chat card draws the tree depth first, so
a grandchild sits under its own parent. (2) A long list of ids overran the URL.
`DELETE /projects/tasks/{id}` now takes `?delete_all_subtasks=true`, the core's
own `delete_all`. The dialog and the chat send it for "all". A list holds at
most 200 ids, with the message "Pick up to 200 subtasks, or choose Delete all."
(3) The app always opens the dialog, so a task with only archived or hidden
subtasks shows the plain words. (4) The summary counts only what the member can
see, and says "N you cannot see stay". (5) The tag "moves up" wraps under the
title on a phone. (6) R8 cases for the subtree read are in
`tests/unit/test_mcp_edge_delete.py`, class `TestTheSubtreeRead`.

Owner, 2026-10-23, verbatim: "Sub tasks should also be deleted. The user
should be shown that the task has what subtasks and if the subtasks should be
promoted to tasks or be deleted as well." Then: "What if you only want to
delete some of subtasks? [...] select which subtasks to keep, and which
subtasks to delete."

**Scope.** D-OP-10. EA-6h builds the core:
`delete_task_in(..., delete_ids=)`, the field on `DELETE /projects/tasks/{id}`,
and `subtasks_to_delete` on the MCP tool. OP-7 builds the app and the chat.

**The rule of the core.**

- Only the subtasks that the member marks are deleted. Deleting a subtask does
  not delete its own subtasks unless the member marks them too.
- A kept subtask moves up to its nearest kept ancestor. With no kept ancestor,
  it moves to the parent of the deleted task. A task at the top has no parent,
  so its kept subtasks become top-level tasks.
- An id that is not a subtask of this task gets 422, and nothing changes.
- **A delete removes only the subtasks that the caller can see** (supervisor
  decision, 2026-10-23, EA-6h fix 1). A hidden subtask is never deleted. It
  is kept, and it moves up under the rule above. "Delete all" means every
  subtask that the caller can see. For an agent, "see" also means inside the
  pins of its connection. An id of a hidden subtask gets the same 422 as a
  wrong id, so the answer says nothing about the hidden task. The response
  gives `subtasks_kept_hidden`, a count with no titles.
- One `pm.task.deleted` event goes out for the task and one for each deleted
  subtask, after the commit. The agent's events carry `via`.

**The app dialog.**

```
┌ Delete "Launch the beta" ─────────────────────────┐
│ This task has 5 subtasks. Mark the ones to delete. │
│ The others stay, and move up one level.             │
│                                                     │
│ [Keep all]  [Delete all]                            │
│                                                     │
│ ☐ Write the release notes                           │
│ ☑ Set up the beta list              ▸ 2 subtasks    │
│    ☑ Import the waitlist                            │
│    ☐ Write the invite email       → moves up        │
│ ☐ Book the demo call                                │
│                                                     │
│ Deletes 3 tasks · keeps 3                           │
│                    [Cancel]  [Delete 3 tasks]       │
└─────────────────────────────────────────────────────┘
```

1. The dialog shows the subtasks as an indented tree, with each title and its
   status. Every box is clear at first, so the default is the old behaviour.
2. A tick on a parent ticks its subtasks too, and the member can clear any of
   them again. Clearing a parent clears nothing below it.
3. A kept row under a ticked parent shows "→ moves up", so the member sees
   where it goes.
4. "Keep all" and "Delete all" set every box at once.
5. The summary line and the button name the counts. The task itself is always
   in the count.
6. On a phone the dialog is a bottom sheet (`Modal placement="sheet"`), and a
   whole row is the tap target.
7. A task with no subtasks shows the plain confirm and no tree.

**The chat card.** `delete_task` in `skill_projects/guarded.py` gains
`subtasks_to_delete`: "none", "all", or a list of ids. The card shows the same
tree with check boxes, through the multi-select of the chat cards
(`allow_multiple`). The model can pre-tick the subtasks that the member named,
and the member confirms or changes the ticks on the card. The impact line
names the counts.

**Done when.**
1. A mixed choice deletes only the marked subtasks, and a kept grandchild
   moves up to its nearest kept ancestor. Fence: vitest on the dialog state,
   and `test_projects_agent_writes.py` for the card.
2. A tick on a parent ticks its subtree, and a clear on a child keeps the
   parent ticked. Fence: vitest.
3. The counts in the summary, on the button and on the card match what the
   route reports after the delete. Fence: vitest and the R8 tests of EA-6h.
4. The visual-review skill passes on the dialog at 390 and 1280 px.

### OP-5 — one filter component, and the old shell retires · AGENT-SAFE

**Scope.** §5.4. One filter component with facet sets. `TaskToolbar.tsx`,
`app/tasks/page.tsx` and the My Tasks phone tabs retire. The views move from
`app/tasks/components` to `app/projects/components/my-work/`.

**Done when.** No import of `app/tasks/components` is left outside the moved
folder, and the e2e of OP-2 still passes.

---

## 10. Verification

For OP-1, in `workbench/control_plane`: `npx tsc --noEmit`,
`npx vitest run src/app/projects src/components/ui`, and
`npx playwright test e2e/projects-compact-chrome.spec.ts` with the flag set.

```
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/projects src/app/tasks src/lib/nav.test.ts
uv run pytest tests/unit/test_personal_tree.py
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)" && uv run pytest tests/unit/test_personal_tree.py
```

For OP-1 and OP-2, also run the visual-review skill at 390, 768 and 1280 px.
