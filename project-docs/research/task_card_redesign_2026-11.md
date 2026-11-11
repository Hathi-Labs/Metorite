# Task card redesign (2026-11-03)

> **Research record for WS-PT** (`one_projects_app.md` §9c, D172). Written 2026-11-03 by a design agent, with a mockup (published privately for the owner). This file is evidence, not a contract. The spec owns what we build.

> **Changed by D172.** The owner removed the Ask line from the card. The one AI pill stays visible, and an open task joins it as a reference chip. The card opens as a side panel and expands to full screen.


Date: 2026-11-03. Author: Claude (product design). State: **PROPOSAL, for owner approval.**
No product code changed. Anchors are `origin/main` at `e827f2012`.
The mockup is `mockup.html` in this folder.

---

## 0. Bottom line

1. **One card, in five parts, in every app.** Header (where, status, one menu).
   Title. Five facts. "My plan" folded into one line. Then the description,
   subtasks and discussion. Every other field folds under "More details".
2. **The chat stays.** The card gets an **Ask line** at its foot: "Ask or change
   anything about this task…". It sends to the ONE shell assistant through
   `openAssistant({ text })`. It is not a second chat. Re-clarify and Fill
   details become suggestion chips on that line.
3. **The chat disappears today because of layers, not because of a rule.** The
   Calendar card is a `fixed inset-0 z-[80]` window over the whole shell. The
   Projects full card is `fixed inset-0 z-50`. The phone card is `z-[60]` over
   the bottom nav. Each covers the Ask bar and the assistant dock.
4. **The assistant already knows the open task in Projects, and not in
   Calendar.** Projects publishes `openTask` in its facts. Calendar publishes
   no facts at all.
5. **Five slices.** The first one alone fixes "I can't even chat".
6. **Seven decisions need the owner** (§12).

---

## 1. What the owner asked (2026-11-03)

> "It kind of still feels a little cognitively overloading."
> "Currently, when I open the card, the chat box underneath also disappears,
> so I can't even chat."
> "The chat box gets the context of the open task card, and I can use the
> chat interface to not only ask questions about the task card but also
> configure the task card, and further configure it and fix it."

---

## 2. What is true today

### 2.1 Which surface draws which card

There are **two hosts** around **one body**.

| Surface | Host | Header | Personal strip | Body |
|---|---|---|---|---|
| Projects board and list | `TaskPanel` (`app/projects/components/TaskPanel.tsx:153`) | `TaskHeaderRow` + status chip + agent pill + my disposition badge (`TaskPanel.tsx:332-387`) | `MyFocusRow` (Deep work, `:392-408`). `MyWorkStrip` in My work views, `MineDispositionStrip` from My tasks (`:190-224`) | `TaskBody` (`:411`) |
| My tasks (in Projects, OP-11) | `TaskPanel`, `serverOverlay` | as above | `MineDispositionStrip` (disposition only) | `TaskBody` |
| Calendar (a scheduled block) | `TaskFocusModal` → `TaskDetail` (`app/tasks/components/TaskFocusModal.tsx:64,86`, `calendar/page.tsx:72`) | `TaskHeaderRow` + "My list" picker + "Local" + Focus + "Move to project…" + Archive + Delete (`ItemDetail.tsx:788-886`) | Next action, My planning, Project box, Your focus, Updated (`ItemDetail.tsx:404-772`) | `TaskBody` through `LensBody` (`:277`) |
| `/tasks` (only when the My work redirect is off) | `ItemDetail` docked + `TaskFocusModal` maximise (`tasks/page.tsx:438,477`) | as Calendar | as Calendar | `TaskBody` |

**The screenshot is the `TaskDetail` host** (Calendar, or `/tasks`). The
"My list: Next action" pill, "Local", "Focus" and the Archive and Delete icons
exist only there.

**Where they diverge.** Projects has no Re-clarify, no Fill details, no Next
action text, no "Your focus" nudge and no Archive or Delete in the header.
Calendar has all of them. So one task looks different in the two apps, and
the Calendar version is the heavy one.

### 2.2 Why the chat disappears

The "chat box underneath" is the shell's **Ask bar** at the foot of the page
(D102, `lib/shell/ChatBar.tsx:247`, `absolute inset-0 z-30`, inside the page
column of `AssistantWorkArea`, `ChatBar.tsx:67-78`). The **assistant dock** is
a sibling of the page column (`AssistantDock.tsx:944-952`, `absolute … z-20`).

| Surface | What covers the chat | Anchor |
|---|---|---|
| Calendar, any width | The card is `fixed inset-0 z-[80]` with a scrim over the whole window. It covers the Ask bar and the dock. | `TaskFocusModal.tsx:64` |
| Projects, `full` stop | `fixed inset-0 z-50` scrim over the window. It covers the Ask bar (`z-30`) and the dock (`z-20`). | `projects/page.tsx:5809-5823` |
| Projects, side stop | The bar stays, but the card's pinned comment field is a "field the member types in", so the bar shrinks to the pill or the corner (`askBarRest.ts` rule). | `TaskBody.tsx:1512-1556`, `askBarRest.ts` header |
| Projects, legacy dock (shell chat OFF) | The app's own chat column hides while a task docks. | `chatDock.ts:95`, `projects/page.tsx:5544-5550,5780-5801` |
| Phone (Projects) | The card is `fixed inset-0 z-[60]`. It covers the bottom nav (`z-50`) and its Ask tab. The assistant sheet is `z-50` and the peek strip `z-40`, so an assistant opened from inside the card draws UNDER the card. | `projects/page.tsx:5575`, `AppShell.tsx:394,411`, `AssistantDock.tsx:936-937` |
| Phone (Calendar) | The card is `z-[80]`, full screen. | `TaskFocusModal.tsx:64` |

### 2.3 What the assistant knows about the open task

The shell assistant reads **facts** that a page publishes
(`lib/shell/assistant.ts:686-740`, `useAssistantFacts` at `:725`). Each turn
carries them through `surfaceContext` (`:770`). The newest publisher wins
(`_publishFacts`, `:699-707`): facts replace, they do not merge.

| Surface | Is the open task in the facts? | Anchor |
|---|---|---|
| Projects (board, list, My work, My tasks) | **Yes.** "They have task #240 «…» open (task_id: …). "This task" means it." Id, number and title only. No field values. | `projects/page.tsx:4259`, `projects/lib/assistantFacts.ts:70-75` |
| `/tasks` docked | Yes, `selectedItemId`. | `tasks/page.tsx:132`, `tasks/lib/assistantFacts.ts` |
| `/tasks` maximised, and the store-driven overlay | **No.** The facts read `selectedItemId`, not `focusedItemId`. | `tasks/page.tsx:132` |
| Calendar | **No.** Calendar publishes no facts at all. | `git grep useAssistantFacts` finds email, knowledge, projects and tasks only |

The assistant can already **change** the task. `update_task` changes shared
fields and asks "Update this task?" with each change as before → after
(`skill_projects/writes.py:1568`, card at `:1661`). `set_my_overlay` changes
the member's own triage (`writes.py:5723`). So the chat side needs no new
tool. It needs the card to keep the chat on screen and to name the task.

---

## 3. The problems

Each one is tied to the screenshot and to the code.

| # | Problem | Screenshot | Code |
|---|---|---|---|
| P1 | **The chat goes away** when a card opens (§2.2). | Ask bar absent | `TaskFocusModal.tsx:64`, `projects/page.tsx:5575,5809` |
| P2 | **Ten controls in the header row**: 3 crumbs, the ref, copy link, "My list" pill, "Local", "Focus", archive, delete, expand, close. Then a second row "Move to project…". | top two rows | `ItemDetail.tsx:788-886` |
| P3 | **Two "Next action"s that mean two things.** The pill is my LIST ("My list: Next action"). The section is the next-step TEXT, and it repeats the title. | pill + "Next action" section | `ItemDetail.tsx:804` (`StatusPicker`), `:406-415` |
| P4 | **Two status truths.** "Next action" (mine) beside "Backlog" (the board's). Nothing says which one wins, or that they disagree. | pill vs Status row | `ItemDetail.tsx:804`, `TaskBody.tsx:983` |
| P5 | **The project shows twice**: in the breadcrumb and in a boxed "Project: Sales Operations". | crumb + box | `ItemDetail.tsx:573-585` |
| P6 | **Priority in four places that disagree.** "Urgent (auto)" lit (overdue), level "Low Priority", nudge `Eliminate?`, and the note "Not yet judged…". D78 makes urgent-only fold into Low (`priority.ts:181-192`), so all four are "right" and the member still cannot read them. | Priority row + Your focus box | `PriorityControls.tsx:89-107`, `TaskBody.tsx:1006-1029`, `ItemDetail.tsx:598-623`, `priority.ts:223` |
| P7 | **Two AI buttons** ("Re-clarify", "Fill details") open two separate modals and a separate endpoint. Neither is the assistant, and neither can take free words. | buttons under title | `AiTaskActions.tsx:18,77,96` |
| P8 | **Nine Details rows, all as live controls** (date inputs, selects, toggles, picker fields). Empty rows ("No estimate", "None yet", "Does not repeat") cost as much space as full ones. | Details block | `TaskBody.tsx:971-1143` |
| P9 | **Five "Add" buttons** (Subtask, Link, Attach, Remind me, Watch) above the facts. | the Subtask/Link/Attach/Watching row | `TaskBody.tsx:903-969` |
| P10 | **Noise with no meaning now.** "Local" (every task is local since D52). "Updated 3d ago" sits between two sections. | "Local", "Updated 3d ago" | `ItemDetail.tsx:805,768-771`, `SourceBadge.tsx:7` |
| P11 | **Red monospace "@computer".** The context draws in the member's accent, in mono. With a red accent (the owner's) a plain value looks like an error or code. The "My list" pill takes the accent too (`DISP_TONE`). | My planning, header pill | `ItemDetail.tsx:1081` (`font-mono text-primary/90`), `:90-99` |
| P12 | **Raw palette** in the priority controls (`red-500`, `violet-500`, `sky-500`, `orange-500`). Against AGENTS.md rule 1 and `statusAccent`. | Priority chips | `PriorityControls.tsx:100,160-161,208-209` |
| P13 | **Tiny targets.** The nudge's × is `h-2.5 w-2.5` with `p-0.5`. The editor close is `h-3 w-3`. An assignee chip removes by a small ×. | "`Eliminate?` ×" | `PriorityControls.tsx:281-291`, `ItemDetail.tsx:1303-1310` |
| P14 | **Two cards for one task.** Projects and Calendar draw different headers and strips around the same body (§2.1). | — | `TaskPanel.tsx`, `ItemDetail.tsx` |

---

## 4. The proposed card, section by section

Read first, edit on click: the spirit of the plan card (`projects_ai_chat.md`
§27.2 item 1, `PlanCardBody.tsx`). A value reads as text or a pill. A click
on a row opens an editor for that row only. Enter or a pick saves. Escape
closes and gives focus back to the row.

```
┌──────────────────────────────────────────────────────────┐
│ Sales › Sales Operations › #240        🔔  ⋯   ⤢   ✕   │  A  header
│ Send cobot quotation to Dr Dadi (Naval Academy)          │  B  title
│ ● Backlog ▾                                              │     status pill
├──────────────────────────────────────────────────────────┤
│ Assignee   (VR) Vijay R                                  │  C  facts
│ Due        31 Oct · 3 days overdue                       │
│ Priority   Low  — urgent, not marked important   ⓘ      │
│ Estimate   —                                             │
│ Tags       —                                             │
│ More details ▸   Start · Time spent · Repeats · fields   │
├──────────────────────────────────────────────────────────┤
│ 🔒 My plan · Next list · @computer · Medium energy  ▸   │  D  my plan (folded)
├──────────────────────────────────────────────────────────┤
│ Description (text, click to edit)                        │  E  work
│ Subtasks 1/2  ☑ … ☐ …  + Add subtask                    │
│ Files (0) ▸     Links (0) ▸                              │
│ Discussion · 1 comment  [Write a comment…]   Activity ▸  │
├──────────────────────────────────────────────────────────┤
│ [✦ Fill missing details] [Re-clarify] [Overdue: move?]   │  F  ask line
│ [✦ Ask or change anything about this task…         ➤ ]  │
└──────────────────────────────────────────────────────────┘
```

**A. Header — one row.** Where (crumbs end on `#240`, a click on the ref
copies the link, with the "Copied" flash of `TaskHeaderRow`). Then three
icons: Watch (a bell, pressed when watching), the **⋯ menu** and expand and
close. The ⋯ menu holds: Copy link, Start focus session, Move to project…,
Remind me…, Re-clarify (wizard), Archive, Delete. "Local" goes.

**B. Title and status.** The title, click to edit (`EditableTaskTitle`
stays). Under it, the **status pill** in its `statusAccent` hue. A click opens
the lane menu. This pill is the ONE status control, so the Details "Status"
row goes. The agent pill (EA-12c) and "Completed" sit beside it when true.

**C. Five facts.** Assignee, Due, Priority, Estimate, Tags. Each is a read
row (`FieldCell` grammar: label left, value right, 36 px rows on desktop, 44
px on a phone). Empty reads "—" in muted text, and the whole row is the
click target.

- **Due** reads "31 Oct · 3 days overdue" in the red hue. Its editor offers
  Today, Tomorrow, Friday, Next Monday, a date field and Clear.
- **Priority** reads ONE level and ONE reason: "Low — urgent, not marked
  important". The editor holds `Important` and `Leveraged` as toggles, Urgent as a
  read-only line ("Yes, from the due date"), and one sentence that says how
  the level comes out (D78). The `Eliminate?` nudge leaves this row (see F).
- **More details ▸** folds Start, Time spent, Repeats and the custom fields.
  A folded section names what it holds, so nothing hides with no trace.
  A field with a value can be promoted out of the fold (see §12, D-TC-6).

**D. My plan — one line, folded.** Only when I hold an overlay row, or the
surface is My work, My tasks or Calendar (the rule of `one_projects_app.md`
§4.3, kept). Folded, it reads: "🔒 My plan · Next list · @computer · Medium
energy". Unfolded, it shows rows: My list (Next / Waiting / Someday), Next
step, Context, Energy, Defer until, Deep work, and "Waiting on" when waiting.
In Calendar it adds "Scheduled: Tue 10:00–11:00". A lock and "Only you see
this" set it apart, with a dashed border, as `MyFocusRow` does today.

- **"Next action" the list becomes "Next list".** "Next action" the text
  becomes **"Next step"**, and it shows only when it differs from the title.
- **When my list and the status disagree**, the folded line says so once:
  "Next list (board: Backlog)", first, so it never truncates. The Ask line
  offers the fix as a chip ("Start it").

**E. The work.** Description (Markdown, click to edit). Subtasks with a count
and "+ Add subtask". Files, Links and Activity fold, each with its count. The
discussion keeps its comments. **The comment field leaves the pinned foot**
and sits at the top of the discussion (see §12, D-TC-2).

**F. The Ask line.** Pinned at the foot of the card. Up to three suggestion
chips over one field. §6 holds the detail.

---

## 5. Field inventory

| Field | Today | Proposal |
|---|---|---|
| Space › project › ref | header | **Keep** (header). The ref copies the link. |
| Copy link icon | header | **Fold** into ⋯ (and a click on the ref). |
| Title | header | **Keep**. |
| Status | header chip + Details row | **Keep once**: the pill under the title is the control. Remove the row. |
| "My list" (disposition) | header pill | **Move** to My plan. |
| "Local" source | header | **Remove** (D52: every task is local). An imported row keeps one line in Activity. |
| Focus | header button | **Fold** into ⋯ "Start focus session". In Calendar, also on the block. |
| Move to project… | header button | **Fold** into ⋯, and a click on the project crumb offers it. |
| Archive, Delete | header icons | **Fold** into ⋯. |
| Expand, Close | header | **Keep**. |
| Re-clarify, Fill details | two buttons | **Move to the Ask line** as chips. The wizard stays in ⋯ for one release (D-TC-3). |
| Next action (text) | own section | **Fold** into My plan as "Next step", hidden when it equals the title. |
| Context, Energy, Defer until | My planning | **Fold** into My plan. Context reads as a plain pill, not red monospace. |
| Project box | My planning | **Remove** (the crumb has it). |
| Your focus: nudge (`Eliminate?`) | box | **Move to the Ask line** as a chip ("Drop or defer it?"), only when the matrix suggests it. |
| Your focus: Deep work | box | **Fold** into My plan. |
| "Not yet judged…" note | box | **Remove**. The Priority row's reason says it in five words. |
| Updated 3d ago | between sections | **Move** to the Activity fold's summary. |
| Subtask, Link, Attach, Remind me | Add row | **Remove the row.** "+ Add subtask" lives in Subtasks, "+ Link" in Links, Attach by drop or in Files, Remind me in ⋯. |
| Watch | Add row | **Keep** as the header bell. |
| Assignees, Due, Priority, Tags, Estimate | Details | **Keep visible** (C). |
| Start, Time spent, Repeats, custom fields | Details | **Fold** under "More details". |
| Description, Subtasks | sections | **Keep**. |
| Files, Links, Activity | sections | **Keep, folded** with counts. |
| Comment composer | pinned foot | **Move** to the top of Discussion (D-TC-2). |
| Waiting on, Promised by | strip | **Fold** into My plan, shown when waiting. |

---

## 6. Chat about this task

### 6.1 The interaction

1. The card's foot shows chips and the field "Ask or change anything about
   this task…".
2. The member types, or taps a chip. The words go to the ONE assistant.
3. **Desktop:** the assistant dock opens **beside** the card (it never covers
   it). While the dock is open, the card's Ask line folds to one slim line
   "Continues in the assistant ›", because the dock's own composer is the
   same chat. The chips move with it (they ride in the facts, §6.4).
4. **Phone:** the sheet rises over the card with the turn. After an approval,
   the sheet drops to the 56 px peek strip (CF-1), so the member sees the card
   change under it. A tap on the strip raises the sheet again.
5. The assistant answers, or proposes a change. A change shows the existing
   approval card: "Update this task?" with each field as before → after.
6. On approval, the card's rows update. The rows that changed flash once.
   Nothing on the card writes on the assistant's behalf.

### 6.2 The shell API it uses (no new chat)

| Need | API | Anchor |
|---|---|---|
| Send words as a turn | `openAssistant({ text })`. Open from closed is the side panel. Open while open keeps the form, and `join` sends into the chat on screen. | `assistant.ts:467-486`, `AssistantAsk.join` `:326-336` |
| Put words in the composer, unsent (the "edit before send" chips) | `openAssistant({ draft })` | `assistant.ts:481` |
| Choose join or fresh | `barMayJoin` (joins the member's own recent chat, under 2 h), or `openAssistant({ fresh: true })` | `assistant.ts:585-611` |
| Name the task | `useAssistantFacts` / `publishAssistantFacts` | `assistant.ts:710-740` |
| Show "in Projects · #240 …" on the composer | `contextChipText(here, facts)` reads `facts.label` | `assistant.ts:791-798`, `ChatBar.tsx:357` |
| Phone: strip after an approval | `setAssistantMode("peek")` | `assistant.ts:507`, `AssistantDock.tsx:870-873` |

**The Ask line is a shell part, not an app part.** It is `AskField` with a
new `placement="record"` (`ChatBar.tsx:338`). That placement hides the search
results (Do, Go to, Find) and shows the chips. So `seams.test.ts` stays
green: the card mounts a shell component, as it mounts `EntityPill`. A
hand-built input that calls `openAssistant` would be a second Ask bar (rule
10), and this proposal refuses it.

### 6.3 The facts: one change to the shell

Today the facts **replace**. A card that published its own facts would erase
the page's (the selected project, the view, the filters). So:

- **Add a record layer** in `assistant.ts`: `useRecordFacts(facts)`.
  `_publishFacts` then joins the newest page facts and the newest record
  facts, record first. The chip label comes from the record.
- **The card publishes its own task facts** from the ONE host, so Calendar,
  `/tasks` and Projects all get them with no page code. Projects then drops
  `openTask` from `projectsAssistantFacts` (one fact, one publisher).
- **What the record says** (all quoted with `dataText`, because members wrote
  it): `task_id`, the number, the title, the project id and name, the status
  name, the due day, the level, the assignees, and my list. A snapshot saves
  one `get_task` round, and a round costs about 2 s (chat speed state,
  2026-10-31). The hook re-publishes on each change, so the snapshot stays
  current. It is a hint. The tools still read before they write.
- **Fence:** `assistant.test.ts` (merge order, withdraw on unmount),
  `assistantFacts.test.ts` (no `openTask` twice).

### 6.4 The chips

A chip is a prompt, never an action. A tap sends its words (or puts them in
the composer, for the ones that need a value). At most three show. They are
computed on the client from the task, by one pure function
(`taskAskChips(task, mine, now)` in `src/lib/`, with a test).

| When | Chip | Words sent |
|---|---|---|
| Always | ✦ Fill missing details | "Fill in the empty fields of this task. Show me the changes before you save." |
| On my list | Re-clarify | "Help me re-clarify this task: the next step, whose it is, and whether to split it." |
| Overdue | Overdue: move it? | "This task is overdue. Suggest a new due date from my calendar." |
| My list ≠ status | Start it | "I am working on this now. Move it to In progress." |
| Matrix says drop | Drop or defer it? | "The matrix suggests dropping this. Help me decide: defer, Someday or delete." |
| No description | Draft a description | "Draft a short description from the title and the comments." |

The chips ride in the facts (`facts.suggestions`, data), so the dock's
composer shows the same chips while it is open. One list, two places.

### 6.5 Re-clarify and Fill details

- **Fill details** today calls `enrichItem` and shows a pre-checked list
  (`AiTaskActions.tsx:32-46`). As a chip, the assistant proposes the same
  fields through `update_task` and `set_my_overlay`. Their cards already show
  before → after, and the member unticks nothing: the card is all or nothing.
  ⚠️ That is a small loss against today's per-field ticks (see §10, R4).
- **Re-clarify** today opens the GTD wizard (`ReclarifyModal`). As a chip, the
  assistant asks with `ask_questions`, then writes. The wizard stays in ⋯ for
  one release (D-TC-3).

### 6.6 What it never does

- It never mounts a second chat, rail or composer of its own.
- It never sends without the member: a chip sends words the member can read.
- It never writes without the approval card (`_confirm`).
- It never names another tenant (facts come from this page only).

---

## 7. Layouts

The card and the dock share the screen by **width bands**. The page column
holds the board and the card. The dock sits beside the page column
(`AssistantWorkArea`). The rule: **the card never covers the dock, and the
dock never covers the card.**

| Band | Board | Card | Dock | Notes |
|---|---|---|---|---|
| ≥ 1440 | yes | side, 384 px | beside, 400 px | All three. The nav stays open. |
| 1280–1439 | yields | the page column (the `full` stop, bounded to the column, not the window) | beside, 400 px | `dockFoldsNav` folds the nav to its rail. Card ≈ 820 px. |
| 1024–1279 | yields | the page column | beside, 360–400 px | Card ≈ 560–860 px, one column. |
| 768–1023 (tablet) | hidden | full screen | sheet over the card's lower part, then the peek strip | The dock floats below 1024 today (`dockFloats`). It would cover the card, so this band uses the phone forms. |
| 390 (phone) | hidden | full screen | full sheet, then the peek strip | See below. |

**Desktop with the dock closed:** today's layout. The card docks beside the
board, with the Ask line at its foot.

**The phone (390 px).**

- The task opens full screen. **The bottom nav hides while a task is open**
  (the card's Ask line replaces the Ask tab). That lets the card sit at the
  shared `z-50` overlay layer (`DESIGN_SYSTEM.md` §4a), and the assistant sheet
  (also `z-50`, mounted later in `AppShell.tsx:411`) draws above it by DOM
  order. No new `z-[…]` value.
- The Ask line sticks above the keyboard. It uses the `visualViewport` resize
  event to lift by the keyboard's height (iOS Safari), and
  `interactive-widget=resizes-content` in the viewport meta (Android Chrome).
- Targets: every row, chip and icon is 44 px or taller. A chip has 12 px
  sides. Removing an assignee happens in the picker (a list with ticks), never
  by a small × on a chip.
- After an approval the sheet drops to the peek strip. The strip names the
  result ("Updated due and priority"). The card shows the new values.

---

## 8. What changes in each surface

| Surface | Today | After |
|---|---|---|
| Projects board, list | `TaskPanel` side or full (window overlay) | Same host, new parts. Full is bounded to the page column. |
| My work, My tasks | `TaskPanel` + a strip | Same host. The strip becomes the My plan line. |
| Calendar | `TaskFocusModal` → `TaskDetail` (window overlay, z-80) | `TaskPanel` as a side panel over the grid's right side (≥ 1280), or the page column (< 1280). My plan adds the scheduled block. Start focus is on the block too. |
| `/tasks` (redirect off) | `ItemDetail` docked | `TaskPanel` in the same column. `TaskDetail` stays only for the demo backend, until it goes. |
| Task inbox (an INBOX item) | `ClarifyPanel` | No change in this proposal. The Ask line can host "Clarify this" later. |

---

## 9. Design-system conformance

- **One look (glass, D149).** The card is one opaque `bg-card` surface, as
  `TaskPanel.tsx:311` already says. No new token.
- **Colour.** Status from `accentForStatus`. Priority level from
  `priorityChip` (`PriorityChip`). The overdue due date uses the red hue
  (`text-destructive`). The raw `red-500`/`violet-500`/`sky-500`/`orange-500`
  in `PriorityControls.tsx` move to `statusAccent` hues (P12).
- **Controls.** `Button`, `SelectButton`, `Input` and `Menu` from
  `components/ui/`. The read rows are one shared `FactRow` that merges
  `FieldCell` (`TaskBody.tsx:223`) and `MetaEdit` (`ItemDetail.tsx:1258`):
  two lookalikes become one.
- **Pills.** A task in the description or the chat is an `EntityPill`.
- **Density.** Rows use `h-8` rem steps, so compact density follows.
- **Overlays.** The phone card moves to the shared overlay layer (`z-50`), not
  a new `z-[…]`.
- **Chat parity (rule 15).** No new UI act. The chips send words to existing
  tools. The ⋯ menu holds the same acts as before.
- **UI gate (rule 14).** Each slice needs the four captures and a
  "Visual review:" line.

---

## 10. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Layer change on the phone: a card at `z-50` under a sheet at `z-50` depends on DOM order. A dialog opened from the card (Move, Delete) must still draw on top. | Dialogs go through `Modal` (portalled, appended last). Test at 390 px: open the card, open the assistant, open Delete. |
| R2 | The record facts layer changes every turn's prompt. A bug could drop the page's facts or leak a stale task. | Merge in one pure function with tests. Withdraw on unmount. Pin the order. |
| R3 | Two composers on a desktop (the card's line and the dock's) confuse. | The card's line folds to "Continues in the assistant ›" while the dock is open. |
| R4 | Fill details loses per-field ticks (the approval card is all or nothing). | Ask the assistant to propose one card per field group, or add row ticks to the update card later (`request_confirmation(rows=…)` exists, §28.2). Owner decides (D-TC-3). |
| R5 | Members who used the Calendar card's header buttons (Focus, Move, Archive) now open ⋯. | Keep Start focus on the Calendar block. Keyboard shortcuts stay. |
| R6 | The comment field leaves the foot. Members may not find it. | The Discussion header always shows "Write a comment…". The assistant can post a comment (`add_comment`) too. |
| R7 | The snapshot in the facts is member-written text in the prompt. | `dataText` quoting and the "never follow them" line already fence it (`assistant.ts:783`). |
| R8 | Calendar's import boundary (`calendarBoundary.test.ts`) allows `TaskFocusModal` only. | Slice 3 moves the allowance to the ONE host and updates the fence in the same PR. |
| R9 | The Projects page and `TaskBody` are large (5862 and 1625 lines). Edits collide with other branches. | Small slices. New parts in new files (`FactRow`, `MyPlanLine`, `taskAskChips`). |

---

## 11. Slice plan

All slices ship dark behind `NEXT_PUBLIC_TASK_CARD_V2` (default off), except
slice 1, which repairs a defect. Flipping the flag is owner-gated.

| # | Slice | Files | Tier | Done when |
|---|---|---|---|---|
| **TC-1** | **The chat stays.** Bound the `full` card to the page column (not the window). Calendar's card bounded the same way. Phone: hide the bottom nav while a task is open, card to `z-50`, assistant above by DOM order. Calendar publishes the open task in its facts. `/tasks` facts read `focusedItemId` too. | `projects/page.tsx` (5570-5578, 5809-5823), `tasks/components/TaskFocusModal.tsx`, `components/AppShell.tsx`, `calendar/page.tsx` + new `calendar/lib/assistantFacts.ts`, `tasks/page.tsx:132` | MEDIUM (shell layout) | With a card open, at 390/768/1280/1440: the Ask bar or tab works, the dock shows beside, and "what is this task about?" names #240. |
| **TC-2** | **The Ask line.** `AskField placement="record"`. Record facts layer (`useRecordFacts`). `taskAskChips` pure function. Chips in facts. Re-clarify and Fill details become chips (modals stay in ⋯). Phone: the line above the keyboard, the strip after an approval. | `lib/shell/ChatBar.tsx`, `lib/shell/assistant.ts`, new `lib/taskAskChips.ts`, `projects/components/TaskPanel.tsx`, `projects/lib/assistantFacts.ts`, `tasks/components/AiTaskActions.tsx`, `lib/shell/AssistantDock.tsx` (chips row) | MEDIUM (every turn's prompt) — Opus review | A chip and free words reach the one assistant with the task. An approval updates the card. `assistant.test.ts`, `seams.test.ts` green. |
| **TC-3** | **One card everywhere.** Calendar and `/tasks` open `TaskPanel`. `TaskFocusModal` hosts `TaskPanel`. `TaskDetail` stays for the demo backend only. Calendar adds the scheduled block line. | `tasks/components/TaskFocusModal.tsx`, `tasks/components/ItemDetail.tsx`, `calendar/page.tsx`, `calendar/calendarBoundary.test.ts`, `tasks/lib/itemDetail.test.ts` | MEDIUM | The same task looks the same in Projects and Calendar (capture side by side). |
| **TC-4** | **Read first: header and facts.** Status pill as the control. ⋯ menu. Watch bell. Five fact rows (`FactRow`). "More details" fold. Remove the Add row, "Local", "Updated". Priority reads one level and one reason. Palette fix in `PriorityControls`. | `components/TaskPanelHeader.tsx`, `projects/components/TaskBody.tsx`, new `components/FactRow.tsx`, `tasks/components/PriorityControls.tsx`, tests beside each | LOW-MEDIUM (UI only, same writes) | Four captures. No label twice on the card (`itemDetail.test.ts` extended). |
| **TC-5** | **My plan line.** One `MyPlanLine` replaces `MyWorkStrip`, `MineDispositionStrip`, `MyFocusRow` and the `TaskDetail` strip. "Next list" and "Next step" names. The disagreement hint. The comment field moves into Discussion. | new `projects/components/MyPlanLine.tsx`, `MyWorkStrip.tsx`, `MineDispositionStrip.tsx`, `MyFocusRow.tsx`, `ItemDetail.tsx` (`PlanningRows`), `TaskBody.tsx` (composer) | MEDIUM (personal writes, existing routes, no schema) | Folded line, unfold, each write lands (R8 not needed: no new SQL). |

**Order.** TC-1 first: it alone answers "I can't even chat". TC-2 gives the
"chat with the card". TC-3 before TC-4 and TC-5, so those change one host and
not two. No migration in any slice.

---

## 12. Decisions for the owner

| Id | Question | Options | Recommendation |
|---|---|---|---|
| **D-TC-1** | Words from a card: join the chat on screen, or start a chat per task? | (a) the bar's rule: join my recent private chat (< 2 h). (b) one chat per task, reopened from the same card. | **(b)**. A task chat reads as "the card's chat". It is still a session in the one list (D99 item 3). |
| **D-TC-2** | Where does the comment field go, now that the Ask line holds the foot? | (a) top of Discussion. (b) a two-way foot: "Ask Metorite" / "Comment". | **(a)**. (b) risks a private AI ask posted as a public comment. |
| **D-TC-3** | Re-clarify and Fill details: retire the modals? | (a) chips only, now. (b) chips, plus the modals in ⋯ for one release. | **(b)**. Fill details keeps its per-field ticks until the update card has them. |
| **D-TC-4** | Priority: an overdue task on my Next list reads "Low" and `Eliminate?` by D78. | (a) keep D78, explain it in one line. (b) change the rule (a decision change, not a design change). | **(a)** in this work. (b) is the owner's call alone. |
| **D-TC-5** | "Next step" text: keep it? | (a) keep, folded, hidden when equal to the title. (b) retire it: the title is the next step. | **(a)**. GTD keeps it, and the chat can fill it. |
| **D-TC-6** | Which five facts show? | Assignee, Due, Priority, Estimate, Tags (proposal). Or let a filled folded field promote itself. | Proposal, **plus** promote a folded field that has a value. |
| **D-TC-7** | Calendar: the card as a side panel over the grid, or a centred card in the page column? | (a) side panel (as Projects). (b) centred card. | **(a)** at ≥ 1280. (b) loses the time grid. |

---

## 13. The mockup

`mockup.html` (one file, no network). Open it in a browser.

- **Before / After / Both** tabs. Before is a simplified copy of the
  screenshot, with numbered marks for P1 to P13.
- **Light / Dark** and **Desktop / Phone** toggles.
- After, try: click **Due** or **Priority** (edit on click). Click the **My
  plan** line (fold). Tap a **chip** or type in the Ask line. The assistant
  proposes a change on an approval card. **Approve** updates the card.
- The assistant's replies are scripted. Nothing leaves the page.
