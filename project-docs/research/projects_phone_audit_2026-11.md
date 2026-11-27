# Projects on a phone: UX audit

> **Research record for WS-PT, epic 1500** (`one_projects_app.md` §9b). Written 2026-11-03 by an audit agent. This file is evidence, not a contract. The spec owns what we build.

> **The captures** are not in the repository. They lived in the audit worktree and are cited by file name only.

**Date:** 2026-11-03. **Base:** origin/main 19d998d99, worktree `C:/wt-mt-b2d99abf-pjp-1-pills-swatches`.
**Captures:** `workbench/control_plane/ux-shots/mob/` in that worktree (122 PNGs, a `*.measure.json` beside most, and `*.notes.txt` request logs).

**Verdict first:** Projects on a phone has one broken state and one weak core surface. **P0:** the Overview is a one-way door. Only a reload leaves it. **The biggest single gain:** give the List a phone row. The List is the landing screen, and today it is a 640 px desktop table that hides the due date, clips the status and offers no tick.

**Counts:** P0 1, P1 12, P2 12, P3 7.

## How the captures were made

- Dev server on port 3145 with the production build flags: `NEXT_PUBLIC_PROJECTS_MY_WORK=1`, `NEXT_PUBLIC_PROJECTS_COMPACT_CHROME=1`, `NEXT_PUBLIC_SHELL_NAV/BAR/CHAT/DOCK=1`, `NEXT_PUBLIC_CHAT_FIRST=1`. `one_projects_app.md` line 710 and `chat_first_surface.md` line 3 state that production has them on.
- 390 x 844 with `isMobile`, `hasTouch` and a scale of 2. A subset at 360 x 780. Dark is the default. Light adds `.light` on `<html>`. Glass is the only look (D149).
- The API is stubbed. The fixtures hold 12 tasks of mine over 4 projects, a Firmware project with 8 lanes and 40 tasks, a subproject, an empty project with a 90-character name, and three error and empty scenarios.
- `measure.json` lists each target under 44 px, each text under 12 px, and each element past the right edge. **No capture has page-level horizontal overflow.** `scrollWidth` equals the viewport width in all of them.
- The soft keyboard is simulated by a smaller viewport (390 x 520). A real browser also scrolls the focused field into view, so treat keyboard captures as weak evidence.
- The Next dev badge (`nextjs-portal`) is hidden in the captures. It is dev-only.

## Findings by surface

### My tasks, the landing screen (`/projects` opens My tasks, chip Today, mode List)

**P0-1 Overview is a one-way door.** `b09-overview--dark.png`, `f01-overview-mytasks-tab--dark.png`, `f02-overview-after-sheet-row--dark.png`, `g02-after-list-command--dark.png`, `g.notes.txt`, `f.notes.txt`.
In Overview, `MyTasksSpace` draws no chips, no Filters and no View button, so no mode control is left on the screen. The "My tasks" tab opens the Projects sheet. Its "My tasks" row keeps Overview. The search command "List view" changes only a project's mode, and My tasks stays in Overview. Only a reload returns to the List. A project's Overview has the same gap (`d12-overview--dark.png`, "View buttons visible in overview: 0"). There the only exits are the search command "Board view" or a different project.
Fix: on a phone, keep the View button (or a compact mode row) in Overview, and let `setMode` act on My tasks when My tasks is open. Files: `src/app/projects/components/MyTasksSpace.tsx` (`mode === "overview" ? null`), `src/app/projects/page.tsx` (`selected && !overview ? <FilterChrome`, and `actions.setMode`).

**P1-1 The List is the desktop table.** `a01-landing--dark.png`, `b02-chip-all--dark.png`, `b03-list-scrolled-right--dark.png`, `b02-chip-done--dark.png`, `e07-360-mytasks--dark.png`.
`TaskList` draws `<table className="w-full min-w-[640px]">`. On a 390 px screen, Assignees and Details (due date, overdue mark, Urgent, tags, subtasks) sit off-screen to the right in a sideways scroll. The Today chip lists tasks because they are due, but it does not show the date. The status pill clips to "Spec rea", "In progr" and "Code rev". At 360 px it clips to "To", "Op" and "Sp". The "#144" column spends about 60 px. Column widths move from chip to chip, and the Done chip pushes Status off the screen.
Fix: on a phone, draw a two-line row: tick, title, then project path, due chip and status chip, with no `#` column and no minimum width. Files: `src/app/projects/components/TaskList.tsx` (line 339). Reuse the phone board card (`TaskCardShell`, `TaskCardActions.tsx`).

**P1-2 No one-tap complete in the List.** `a01-landing.measure.json`, `f09-list-selected--dark.png`, `f.notes.txt` ("list checkbox box 16x16").
A List row has no tick. In a project, the selection checkbox is 16 x 16 px. To finish a task from the landing screen, a member opens it, scrolls to Status, opens the menu and picks a stage. That is four taps. The board card on a phone already has a 40 px tick (`g03-mark-done-feedback--dark.png`).
Fix: the phone row of P1-1 carries the board card's tick. Files: `TaskList.tsx`, `TaskCardActions.tsx`.

**P1-6 An empty My tasks is a dead end.** `e01-empty-landing--dark.png`, `e02-empty-all--light.png`, `e03-empty-overview--dark.png`.
"Nothing in Today." offers "Show all". "Show all" then says "No tasks here yet." with no action. Nothing suggests a project to pick up work from, which the owner asked for. Nothing offers Capture.
Fix: an empty state with "Capture a task" and up to three projects that hold open, unassigned work, each one tap away. Files: `MyTasksSpace.tsx` (`emptyChip` and the All path), `TaskList.tsx` (empty branch). The project list can come from the tree counts. A ranked read of unassigned work needs a server read.

**P2-1 The My tasks board opens on an empty lane.** `b06-board--dark.png`, `g.notes.txt` ("cards on first tab: 0").
The phone board shows one lane, and it picks the first tab, "Backlog 0", which says "Nothing here". "To do 7" is one tap away.
Fix: open the first tab with a count above zero. File: `src/app/projects/lib/compactChrome.ts:159` (`activeTabKey`).

**P3-6 The chip row hides its last chips.** `a01-landing--dark.png`. "Someday" fades at the edge and "Done" is off-screen. A scroll strip is acceptable. A shorter label set would fit more.

**P3-7 The My tasks View sheet paints "Group by stage" in the accent.** `b04-view-sheet--dark.png` against `d13-view-sheet--dark.png`. The accent marks a value that is not the default, but stage is the default for My tasks. File: `src/app/projects/components/ViewSheet.tsx`.

### Navigation: the bottom bar, the Projects sheet and the address

**P1-3 The project tree hides behind a second tap.** `c18-projects-sheet--dark.png`, `a01-landing--dark.png`.
The bar reads Menu, My tasks, Capture, Search, Ask. No control says "Projects". The tree opens only when a member taps "My tasks" while My tasks is already open (`phoneMyTasksTap`). A new member has no way to learn that. The sheet itself is good: My work, Personal and Spaces, with 40 px rows.
Fix: make the compact app bar title a button ("My tasks" or the project name, with a chevron) that opens the Projects sheet. Keep the second tap as a shortcut. Files: `src/app/projects/page.tsx` (phone `AppTopBar`), `src/components/AppTopBar.tsx`, `src/app/projects/lib/phoneTabs.ts`. ⚠️ The bar shape belongs to `navigation_shell.md` and `launch_surface.md`, so a new tab is an owner decision. The title button is not.

**P1-4 Back leaves Projects, and a reload loses the place.** `c2.notes.txt`: "url with task http://localhost:3145/projects?app=my-tasks", then "url after back about:blank". `d.notes.txt`: "url project http://localhost:3145/projects".
An open task does not enter the address. On a phone the task is full screen, so the system Back gesture leaves Projects and does not close the task. `?project=` is consumed by `router.replace`, so a reload or a shared link loses the project. `DESIGN_SYSTEM.md` M13 asks for the opposite.
Fix: on a phone, push `?task=<id>` when a task opens and close it on `popstate`. Keep `?project=` in the address. Files: `src/app/projects/page.tsx` (the `deepLink` effect near line 3329 and the `projectLink` effect near line 3320).

**P3-1 Bottom bar labels are 10 px.** `a01-landing.measure.json`. This is shell-wide (`src/components/AppShell.tsx`, `TaskTab`). iOS uses 10 px tab labels too. Report only.

**P3-2 Tracked capitals in the Projects sheet.** `c18-projects-sheet--dark.png`. "MY WORK" and "SPACES" are 11 px capitals (`DESIGN_SYSTEM.md` §10 tell). File: `ProjectNav` in `src/app/projects/page.tsx`.

### A project: List, Board, Table, Timeline, Calendar, Overview

**P1-8 Calendar is a seven-column grid.** `b08-calendar--dark.png`, `d11-calendar--light.png`, `g04-calendar-week--dark.png`.
Each day is 52 px wide. Cards read "Cr…", "Re…" and "Fol…". Chips spill out of the cells ("Urg", "yeste", "cust"). Each day's "+" is 43 x 20 px. The week view keeps the same seven columns.
Fix: on a phone, draw an agenda: one row per day that has work, with full-width task rows and one "Add" per day. Files: `src/app/projects/components/CalendarView.tsx`, `src/app/projects/lib/calendar.ts`.

**P1-9 Timeline cannot be used on a phone.** `d10-timeline--dark.png`.
The name column takes about 90 % of the width, and the bars get a 25 px strip. The help line says "Drag a bar to move it, an edge to change one date". Touch has no path to any of that.
Fix: take Timeline out of the phone's mode list with one line that says it needs a wider screen, or draw a read-only list of date ranges. Files: `page.tsx` (`MODE_OPTIONS` on a phone), `src/app/projects/components/TimelineView.tsx`.

**P1-10 The View sheet hides modes past the right edge.** `b04-view-sheet--dark.png`, `d13-view-sheet--dark.png`, `e11-360-view-sheet--dark.png`.
At 390 px only an edge of the Calendar icon shows, and Timeline does not show. At 360 px neither shows. Nothing says the row scrolls.
Fix: inside the phone sheet, draw `ModeSwitch` as a grid (`layout="sheet"`, which `page.tsx` already uses near line 2026). File: `src/app/projects/components/ViewSheet.tsx` (lines 151 to 170).

**P1-7 An empty project has no add action.** `d18-longname-empty--dark.png`, `e04-project-empty--dark.png`.
"No tasks here yet." has no button. The list's "+ Add" rows live under status groups, and an empty project has none. The bottom bar's Capture files to the inbox, not to this project.
Fix: give the empty state an "Add a task" action that opens the quick add of the first status. Files: `TaskList.tsx` (empty branch), `page.tsx`.

**P1-11 The bulk bar takes 37 % of the screen.** `f08-bulk-bar--dark.png`.
One selected card raises six rows of controls above the board. "Restore" clips at the right edge. "Apply to 1" sits in the middle of the block, far from the thumb.
Fix: on a phone, a bar at the bottom with the count, Status, Move and More, and the rest in a sheet. File: `src/app/projects/components/BulkBar.tsx`.

**P2-2 The card menu is a 20-row popover.** `f07-card-more-menu--dark.png`, `f07-card-more-menu.measure.json`.
The "···" menu opens as an anchored popover that reaches the bottom bar. Its rows are 32 px tall. The board itself works well on a phone: one lane, tabs, and a 40 px tick and "···" on each card (`d04-board--dark.png`, `d04-board--light.png`).
Fix: open the menu as a bottom sheet on a phone, with 44 px rows. Files: `src/app/projects/components/TaskBoard.tsx` (the card menu), `TaskCardActions.tsx`.

**P2-7 Quick add scrolls the List sideways.** `d19-quick-add--dark.png`, `d20-quick-add-keyboard--dark.png`.
Focus on "Add to Backlog" scrolls the 640 px table. The checkboxes then sit on the screen edge and the pills clip. P1-1 closes this.

**P3-3 Undo and Redo always show in a project's phone bar.** `d03-project-default--dark.png`. Two disabled icons take about 80 px of the bar. My tasks hides them when idle. File: `page.tsx` (`projectActions(true)`).

**P3-4 "month" and "week" are lowercase.** `b08-calendar--dark.png`. Every other toggle is sentence case. File: `CalendarView.tsx`.

**P3-5 The due chip "4d ago" reads as an age.** `d04-board--dark.png`. "4d late" or "Due 4d ago" says what it is.

### Task detail (full screen on a phone)

**P1-12 Targets under 24 px.** `c02-task-open.measure.json`, `c05-status-menu.measure.json`.
"Unassign you" is 12 x 12 px and "Remove customer" is 10 x 10 px. The `Important` and `Leveraged` toggles are 23 px tall. The status options are 24 px rows. The rest of the panel reads well: the fields stack, the due date uses the native picker, and the comment field stays at the bottom (`c02-task-open--dark.png`, `c04-task-bottom--dark.png`).
Fix: add `pointer-coarse:` sizes of 40 px (the variant exists in `globals.css`). Files: `src/app/projects/components/AssigneeChips.tsx` (line 59), the tag chip in `TagPicker.tsx`, `WeightToggles`, and the options of `src/components/ui/SelectButton`.

**P2-10 The sticky header takes a quarter of the screen.** `c02-task-open--dark.png`, `c04-task-bottom--dark.png`.
The breadcrumb, the title, the status chip and the "Your focus" row stay on the screen, about 175 px. With the comment field, fixed chrome takes 26 % of the height.
Fix: on a phone, let the "Your focus" row scroll with the body. File: `src/app/projects/components/TaskPanel.tsx`.

**P2-11 Close is only at the top right.** `c02-task-open--dark.png`. The 40 px ✕ is the far corner from the thumb, and Back does not close the panel (P1-4).
Fix: a back chevron at the top left on a phone, in the same slice as P1-4. File: `src/components/TaskPanelHeader.tsx`.

**Seen once, not confirmed:** at 360 px a task opened from My tasks showed "Status" in place of its stage (`e09-360-task-longtitle--light.png`). A timing race with the stub is likely. Re-check on staging.

### Capture and search

**P1-5 Capture leaves the current screen.** `c13-capture--dark.png`, `c15-after-capture-close--dark.png`, `c2.notes.txt` ("url after capture http://localhost:3145/projects").
Capture switches the page to My task inbox and then opens the sheet. Close the sheet and the member is in My task inbox, not where they were. Inside a project, Capture does not file to that project.
Fix: open the quick capture over the current view, and fill in the project when one is open. Files: `page.tsx` (the `projects-capture` handler near line 1493), `src/app/projects/components/MyWorkHost.tsx`, `src/app/tasks/components/QuickCapture.tsx`.

**P2-4 Keyboard hints on a touch screen.** `c13-capture--dark.png` ("Enter to add · keep going · Esc to close"), `f03-search-list-command--dark.png` (the "v l" keys), `f06-search-results--dark.png` (the "esc" chip).
Fix: hide them under `pointer-coarse:`. Files: `QuickCapture.tsx`, `src/app/projects/components/SearchPalette.tsx`.

**P2-5 The capture placeholder clips.** `c13-capture--dark.png`. It reads "Capture to my task inbox… (#". Fix: a short phone placeholder, "Capture a task". File: `QuickCapture.tsx`.

**P2-8 Search prints a raw exception.** `c16-search--dark.png`: "Cannot read properties of undefined (reading 'trim')". My stub caused it (no `query` field). The defect is that the palette prints any `err.message`. With a correct stub, search reads well (`f06-search-results--dark.png`).
Fix: a plain line with Retry. File: `SearchPalette.tsx:132`.

### Loading, error and keyboard

**P2-3 The loading skeleton is the desktop board.** `e06-loading--dark.png`. Two columns draw, the second clips at the right edge, and no app bar shows. Fix: a phone skeleton of the app bar, the chip row and list rows. File: `page.tsx` (`renderState("loading")`).

**P2-9 A failed tree read is silent.** `e05-error--dark.png`. A 503 on the tree shows "Metorite is updating". My tasks still draws, which is good, but the rows lose their project path and nothing says the projects did not load. I did not open the Projects sheet in this state. Fix: a Retry line in the sheet and in place of the path chips. File: `page.tsx` (the tree read).

**P2-6 The List container shows no focus ring.** `f10-tab-walk.txt`, stop 11: `DIV "Task list — arrow keys move…" NO-RING`. Every other stop showed a 2 px ring, in page order. Fix: a `focus-visible:` ring on the container. File: `TaskList.tsx` (the `outline-none` wrapper near line 336).

**P2-12 Table avatars use 8 px initials.** `b07-table.measure.json`. Fix: 10 px or more on a phone. File: the avatar in `AssigneeChips.tsx`.

## What holds up

- No page-level sideways scroll at 390 or 360 px.
- The bottom bar never covers the last row (`c01-list-bottom--dark.png`).
- The phone board: one lane, swipeable tabs, a 40 px tick and "···" on each card.
- Filters and View open as sheets, with a clear "Show 12 tasks" action (`b05-filters-sheet--light.png`).
- Status colours hold under three accents. `h.notes.txt` reads `rgb(94, 176, 255)` for "To do" under blue, green and violet. Only selection and main actions move (`h01-accent-violet--dark.png`).
- Light mode reads well, with no heavy native controls (`a01-landing--light.png`, `d04-board--light.png`).
- The Projects sheet, once found, is clear (`c18-projects-sheet--dark.png`).
- Is Today the right landing for a phone? Yes. The problem is the List under it (P1-1), not the choice of Today.

## Slices

Rank order. Each slice ships alone.

1. **Leave Overview on a phone.** Closes P0-1. Files: `MyTasksSpace.tsx`, `page.tsx` (`FilterChrome` gate, `actions.setMode`). Tier **LOW**. Test: a Playwright case at 390 that picks Overview and then returns to List.
2. **A phone row for the List.** Closes P1-1, P1-2, P2-7, and P2-6 if done in the same file. Files: `TaskList.tsx`, `TaskCardActions.tsx`, a pure layout helper in `lib/` for the vitest fence. Tier **MEDIUM**: it changes the landing screen of every phone member. Needs the four UI captures (`ui-gate-binds-in-build`).
3. **The address follows the phone.** Closes P1-4 and P2-11. Files: `page.tsx` (`?task=` push and `popstate`, keep `?project=`), `TaskPanelHeader.tsx`. Tier **MEDIUM**: history handling touches deep links from chat cards and the bell.
4. **Find, add, capture.** Closes P1-3 (the title button only), P1-5, P1-7, P2-4, P2-5. Files: `page.tsx`, `AppTopBar.tsx`, `MyWorkHost.tsx`, `QuickCapture.tsx`, `TaskList.tsx`, `SearchPalette.tsx`. Tier **MEDIUM**. A new bottom tab stays out until the owner decides.
5. **Empty My tasks suggests work.** Closes P1-6. Files: `MyTasksSpace.tsx`, `TaskList.tsx`, plus a read of projects with open unassigned work. Tier **MEDIUM** if it adds a server read. That read must stay tenant-scoped through the seam (R5).
6. **Phone shapes for Calendar, Timeline and the View sheet.** Closes P1-8, P1-9, P1-10, P2-1, P3-4. Files: `CalendarView.tsx`, `lib/calendar.ts`, `TimelineView.tsx`, `ViewSheet.tsx`, `lib/compactChrome.ts`. Tier **LOW**.
7. **Touch sizes and sheets.** Closes P1-11, P1-12, P2-2, P2-10, P2-12, P3-3. Files: `BulkBar.tsx`, `AssigneeChips.tsx`, `TagPicker.tsx`, `WeightToggles`, `ui/SelectButton`, `TaskBoard.tsx`, `TaskPanel.tsx`, `page.tsx`. Tier **LOW**, but `ui/SelectButton` is shared, so capture its neighbours in other apps.
8. **States.** Closes P2-3, P2-8, P2-9. Files: `page.tsx`, `SearchPalette.tsx`. Tier **LOW**.

P3-1, P3-2, P3-5, P3-6 and P3-7 stay as reports.

## Not opened

- **A real device and a real soft keyboard.** The keyboard captures shrink the viewport. They do not show what iOS Safari or Chrome on Android do to fixed bars and focus.
- **The shell Menu drawer.** The dev-only Next badge sat over the Menu button and took the tap (`g.notes.txt`). The drawer belongs to the shell.
- **Long press on a card.** A CDP touch did not open the menu (`d06-card-menu--dark.png` shows no menu). The "···" path works (`f07`).
- **Drag on a phone, pull to refresh, swipe between board lanes.** The rig did not drive them.
- **Mark done feedback.** The tick sent the right write (`{"status_id":"f-done"}`), but the stub is not stateful, so the capture shows no change or toast.
- **The tree in an error state.** I captured My tasks under a failed tree read, but not the Projects sheet.
- **Compact and comfortable density.** One compact capture only (`h02-accent-violet-compact--dark.png`). I did not review it.
- **Reports, Needs attention, My task inbox, Settings and the AI chat slot** on a phone. I opened My task inbox only as a side effect of Capture.
- **Subtasks nested in the List, saved views with data, custom fields, a real people directory** (the assignee picker said "Nobody in the directory yet" because the stub did not answer the people read).
- **Analytics panels.** The stub answers 404, so every Overview shows "Analytics could not be loaded just now". That line is a stub effect, not a finding.

## Verdict

**Give the List a phone row** (slice 2). The List is the first screen every phone member sees. Today it is a 640 px desktop table that hides the due date, clips the status, wastes a column on `#144` and has no tick. One row component (tick, title, project, due, status) fixes the landing screen, the project default view and the 360 px case at once. Ship slice 1 first: it is small, and it removes the only state a member cannot leave.
