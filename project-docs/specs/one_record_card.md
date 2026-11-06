# One record card — the Projects frame and the CRM tabs

**Status: ACTIVE, 2026-11-06. D181. ORC-1 merged (`c2ab518da`). ORC-3 is in build, before ORC-2, on the owner's ask in chat (2026-11-06). ORC-2 waits in Backlog.** Verified against code on 2026-11-06 at `main` `06cfc43f8`, and §5 again at `4dfbe3b13`.

**Owner:** this file owns the shared frame and the tabs of a record card. `one_projects_app.md` §9c owns the task card's content (D172). `crm_platform.md` §19.8 owns the CRM record's content. Each links here and adds nothing about the frame.

## 1. The owner's ask

Owner, 2026-11-06, in chat, on a screenshot of a CRM deal:

```text
"The deal cards, or possibly even the other cards that exist across the CRM, are opening with slightly different UI/UX than the projects app's cards. Won't it be nice to have a continuity of UI/UX across the cards?"
"I really like the tab systems that you're using in the card for the CRM. Possibly we can reuse that for the projects app as well to organize things better. But the way in which the card can be opened as a sidebar as well as a centered card would be nice to lift from the projects app."
"I wanted to take the best out of both systems ... use the tab system to organize the projects card, as well as fix the system for showing all of these cards in the CRM app to have continuity of user experience across the entire app."
"No need to build out the task card for the projects system for now, but you can just do all the relevant documentation and create the tasks for it while you're actually implementing and building out the card system for the CRM in this session."
```

## 2. What each app has today

| | Projects task card | CRM record card |
|---|---|---|
| **How it opens** | A side panel docked in the page row. The list stays usable | A modal drawer at the right edge, `Modal` at `size="4xl"`. A scrim covers the page |
| **Full card** | One toggle opens a centered card, `max-w-3xl`, and the choice persists (`app/projects/lib/panelMode.ts`) | None |
| **The AI pill** | The card sits under the pill (`TASK_CARD_FRAME`, `lib/layers.ts`, D172.4) | The modal covers the page at `z-50` |
| **Phone** | A full-screen card above the nav | A sheet from the bottom |
| **Header** | `TaskHeaderRow`: place, copy link, chips, actions, expand, close, then the title | `Modal` title, then "Deal · Open in Zoho" |
| **Body** | One scroll of folded sections (`TaskBody`) | Four tabs, kept in `?rtab=` (`RecordTabBar`, `crm/components/RecordShell.tsx`) |
| **Expand and state** | Two mount points, so expand remounts the panel and loses a half-typed comment | Not applicable |

## 3. The one card

**Scope.** Each surface that shows ONE record to read and work on: a CRM record (deal, lead, contact, company, module record), and a task.

**Non-goals.**
- A form that makes a new record stays a dialog. So does a confirm (convert, move, delete).
- The settings cards of the CRM are not record cards.
- The content of each tab does not change here.
- No migration and no route.

**3.1 The frame** (`components/CardPanel.tsx`). One component, with three stops:

| Stop | Where | What it looks like |
|---|---|---|
| `side` | Docked in the page row, after `<main>` | `TASK_PANEL_WIDTH` wide, with a left border. No scrim, and the page stays usable |
| `full` | Over the page column (`TASK_CARD_FRAME.desktop`) | Centered, `max-w-3xl`, rounded, with a scrim. A click on the scrim closes it. The pill stays above it |
| `phone` | Fills the screen (`TASK_CARD_FRAME.phone`) | Full screen above the nav. No toggle |

The rules of the frame:
- **One mount.** The same element takes all three stops, so a change of stop keeps the tab, the scroll and a half-typed note.
- **Side is the default.** One toggle moves between side and full. The choice is `lib/panelMode.ts`, one preference for the member across the apps.
- **Escape closes the card.** `panelEscape` keeps its rule: Escape from a field with text blurs the field first. Escape does nothing while a dialog, a menu or a popup over the card is open, or while the focus is outside the page (the assistant).
- **Close gives the focus back** to what opened the card.
- **Never covers the pill.** D172.4 now holds for each record card.
- **A docked card leaves the page usable.** When the row cannot hold the card and 20rem of page, the card stands over the page at the full stop, and the toggle hides. This happens with the assistant docked at 1440 px. The member's choice stays stored for a wider page. The rule measures the row, so it cannot flip the stop and flip back.

**3.2 The header.** `TaskHeaderRow` (`components/TaskPanelHeader.tsx`), in one order: the place or the noun, the copy link, the app's chips, the app's actions, expand, close. The title is on the next line. A host passes its own words for close and copy.

**3.3 The tabs.** The tabs organize the body under the header, and the shared `Tabs` draws them (`chrome="inline"`, `variant="underline"`). The URL keeps the tab with a replace, so Back closes the card.

- **CRM:** Overview, Timeline, Related, Fields. These do not change.
- **Task card:** Overview, Related, Files, Discussion (ORC-3).
- **The same word means the same thing** on both cards.
- **Nothing that waits on the member hides behind a tab.** An open decision or an agent run shows above the tabs, on each tab.

**3.4 The board and the list.** While a card is open, the board and the list mark its record as selected. A click on another record opens it in the same card, and it replaces the URL, so Back closes the card.

## 4. The slices

| Slice | Task | Lane | What | Done when | Tier | Gate |
|---|---|---|---|---|---|---|
| ORC-1 | 1834 | L5 | The frame (§3.1), and the CRM record cards draw it, with the header (§3.2) and the mark (§3.4). `app/projects/lib/panelMode.ts` moves to `lib/panelMode.ts`, and the old path re-exports it | Each of the five record kinds opens at side, full and phone. Expand keeps the tab and a half-typed note. Escape and the fit rule follow §3.1. `j` and `k` still step. The open record shows as selected. The pill stays in reach at each stop. Four captures | STANDARD | AGENT-SAFE |
| ORC-2 | 1835 | L4 | `TaskPanel` and `projects/page.tsx` draw `CardPanel`, with one mount. The re-export goes | Side, full and phone look as they do today. A half-typed comment survives expand. A source scan finds no second overlay in `projects/page.tsx` | MEDIUM | AGENT-SAFE |
| ORC-3 | 1836 | L4 | Tabs in `TaskBody` (§5). `RecordTabBar` moves to a shared component, and both cards draw it | Each section of today is on one tab, in Projects, My Tasks and Calendar. `?ttab=` keeps the tab. §3.3's last rule holds. Four captures, side and full. **The owner checks the tab names at the preview** | MEDIUM | AGENT-SAFE |

ORC-2 waits on ORC-1. ORC-3 lands before ORC-2. `?ttab=` keeps the tab when expand mounts the body again, so ORC-3 does not need the one mount (§5). ORC-2 and TC-3 (task 1547) touch the same mount: whichever lands second adapts. ORC-3 and TC-4 (task 1548) touch the same Overview: the five facts and "More details" are the Overview tab.

## 5. The task card's tabs (ORC-3)

| Tab | Holds | Today in `TaskBody` |
|---|---|---|
| **Overview** | The facts (D172.2), Description, My plan (D172.3) | Details, Description |
| **Related** | Subtasks, related tasks, Links | Subtasks & related, Links |
| **Files** | Files | Files |
| **Discussion** | Comments first (D172.7), then activity | Discussion |

- **Why not "Timeline".** The CRM's Timeline holds notes, calls, meetings and tasks. A task's Discussion holds comments. D172.7 names it, so the word stays.
- **Counts.** A tab with content shows its count, through the `count` of `Tabs`.
- **The Add menu** opens the tab of what it adds. A file that is dropped or pasted on any tab lands in Files.
- **At the side stop** the four words fit in `TASK_PANEL_WIDTH`. ORC-3 proves it with the compact and the comfortable captures.
- **The custom fields** are facts, so they are on Overview, under Description.

**5.1 How ORC-3 builds it** (`TaskBody.tsx`, `components/useTaskTab.ts`, `lib/taskTabs.ts`)

- **Above the bar, on each tab:** the host's strip (`above`), the agent run, an open decision, then the Add row. They scroll away with the card.
- **An agent's question shows on the first tab.** Owner, 2026-11-06, at the preview: "the question card shows up in the first tab itself". The question card is part of the agent run, so it sits above the bar, and the card opens on Overview. Fence: `taskTabs.test.ts`, "nothing that waits on the member hides behind a tab".
- **The bar sticks** to the top of the card's one scroll region, so the tabs stay in reach under a long Overview. A new tab opens at its top.
- **Each tab stays mounted.** Only `hidden` changes. So the Related and Files counts stay true on each tab, and a half-typed subtask survives a look at another tab.
- **The composer** shows on Discussion, under Comments. On another tab, it hides with the comments.
- **A file dropped or pasted on the card** lands in Files, and Files opens, so the member sees it go up. A drop or a paste on the description or the composer stays inline.
- **The agent row keeps its space (M1).** Until its first read answers, the agent run panel holds the height of its "Ask an agent" row. So the Add row and the bar do not move when the row arrives.
- **Focus clears the bar (M4).** The scroll region sets `scroll-pt-12`, so a field that Tab reaches never stops under the bar.
- **Related counts each task once.** The gateway builds `blocked_by` from `links`, so the count is the subtasks and the links (`relatedCount`).
- **Files and Discussion** draw no section heading of their own, because the tab is the heading. Overview and Related keep their folds.
- **The URL.** `history.replaceState` writes `?ttab=`, and Overview takes the key out. The key leaves one tick after the last card closes. Until ORC-2, expand mounts the body again in the same commit, and that tick keeps the tab.
- **A stale key opens nothing.** A card reads `?ttab=` only when another card is open, or when the address names the task too (`tabOnMount`). Projects takes `?task=` out once the card opens, so a reload leaves the key with no card, and the next card opens on Overview.
- **A reader of the address keys on its own keys.** A change of `?ttab=` re-renders every reader of `useSearchParams`. The Calendar's link reader opened its day again on each change, so it reads `date`, `at` and `reminder` only (`linkQuery`).

## 6. The fences

- ORC-1: `components/CardPanel.test.ts` holds the three stops, the one mount, the fit rule and the Escape rule. `app/crm/components/crmCardPanel.test.ts` holds that both record sheets draw `CardPanel`, and that `RecordShell.tsx` does not draw `Modal`.
- ORC-2: a source scan that `projects/page.tsx` draws no overlay of its own.
- ORC-3: `app/projects/lib/taskTabs.test.ts`. It draws `TaskBody` and checks that each section of today is on one tab, and that the card opens on Overview. It checks that the agent run, the decision and the Add row come before the bar. Its source scan finds one tab bar (`components/RecordTabBar.tsx`) on both cards. `app/tasks/lib/itemDetail.test.ts` holds that each Add button opens its tab.

Each fence is advisory until its slice builds.

## 7. Verification

```
cd workbench/control_plane
npx tsc --noEmit
npx vitest run src/components/CardPanel.test.ts src/lib/layers.test.ts src/lib/taskPanel.test.ts src/app/projects/lib/panelMode.test.ts src/app/crm src/app/projects/lib/taskTabs.test.ts src/app/tasks/lib/itemDetail.test.ts
```

For each slice, also run the `visual-review` skill at light, compact, a changed accent and 390 px, at the side stop and the full stop.
