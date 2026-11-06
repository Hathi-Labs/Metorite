# My Day command centre — capture, plan, start, focus and close from one page (WS-MDC)

**Product:** Metorite · **Feature:** My Day runs the whole day of one member · **Updated:** 2026-11-07 · **Owner:** vjvarada

**Status:** ✅ **ACTIVE** (owner decision 2026-11-07, task 1946). No slice is built. The owner approved MD-Q1 to MD-Q8 as recommended, and added the "Needs you" tabs (D-MD-9).

**Verified against code on 2026-11-07** at `origin/main` `f2671c706`. Each anchor in §2 comes from the research on task 1946. Re-check each anchor at dispatch.

**Board:** WS-MDC in `work_plan.md` §2. **Decision that binds it:** D188 (D-MD-1 to D-MD-9). **Decisions it uses:** D12, D87, D89, D99, D101, D145, D170, D171.

**Metorite:** project "Calendar App", epic 1946. This spec is task 1967 (CC-0). The slices are in §9 with their task numbers.

**Gate summary:** every slice is AGENT-SAFE to build dark. CC-2 and CC-5 add a migration and are HIGH tier. CC-10 changes the chat tools and is HIGH tier. No slice flips a production flag. Each flag flip is named in its own PR.

**Design source:** the approved proposal page `project-docs/specs/mockups/my_day_command_centre.html`. It holds the mockups. This spec holds the rules.

---

## 0. The owner's ask, and the answer in one screen

The owner, 2026-11-06, in chat, with three screenshots of the open-source app Stride:

```text
"Fold in aspects of the calendar where we can start the focus mode and start working from the
My Day app itself. ... My intention is to have a seamless user experience between the calendar
app, My Day, projects, or other surfaces. My Day becomes the main operations command center
dashboard for managing my day. I should be able to start work, do Pomodoro, capture tasks, etc.,
from just this one place, and should seamlessly work with all the other tasks of Metorite."
```

On 2026-11-07 the owner chose "Approve all eight recommendations" on task 1946. Then the owner added in chat:

```text
"Ensure that you actually use the "Needs You" tabs because there are multiple sub-apps that
might need you."
```

**The answer.**

1. **My Day hosts the whole loop of a day.** The loop is capture, clarify, plan, do and close (§1). My Day changes by phase: Plan, Do and Close (§5).
2. **Each verb has one rule and one component** (§4). No surface builds a second copy.
3. **The parts mostly exist.** The focus session, the now and next bar, the planner and Quick Capture are built. They sit in Calendar or in My work. This spec moves them to My Day by reuse (§2).
4. **Calendar keeps the week, the full grid and events. Projects keeps the structure.** All three read the same tasks, the same time blocks and the same focus sessions. So no sync exists.
5. **"Needs you" gets one tab for each app that can need the member**, each with its count (§6).
6. **A phone gets one column** (§7).

---

## 1. The loop

A day of work follows one loop. Each step has one home on My Day.

| Verb | The question | Where it happens on My Day |
|---|---|---|
| **Capture** | "What came in?" | The capture box at the top. Every capture goes to the Inbox (D170) |
| **Clarify** | "What is it?" | "Clarify 5" opens a quick pass over the Inbox. Projects keeps the full Inbox and the project tree |
| **Plan** | "When will I do it?" | The Plan phase. Pick the top 3 and the One Thing, then "Fit my day" |
| **Do** | "What do I do now?" | The Do phase. The Now card, today's plan, the day rail |
| **Close** | "What is done, and what moves?" | The Close phase. Stats, roll over, tomorrow's One Thing |

---

## 2. What the code does today

Paths are under `workbench/control_plane/src/` unless a path says gateway. Gateway paths are under `apps/services/gateway/gateway/`.

### 2.1 Built, and in the wrong place

| Part | Where it is | Anchor |
|---|---|---|
| The global focus session and its dock (25/5, 50/10, Flow) | Mounted in the shell. Only Calendar starts it | `app/tasks/components/FocusMode.tsx`, `components/AppShell.tsx:338,410`, `enterFocusSession` at `app/tasks/lib/taskStore.ts:1595`, starts at `CalendarView.tsx:756,841,986` |
| The now and next bar, "Fit what's left", the startup ritual, the end-of-day review, the One Thing | Calendar | `NowNextBar.tsx`, `PlanDayPanel`, `StartupRitual`, `EndOfDayReview`, `calendar_day_state` |
| The planner routes (plan, replan, rollover) | Gateway | `routes/projects/planning.py:345-361` |
| The day state routes | Gateway | `routes/tasks/calendar.py:119,147` |
| Quick Capture with sweep, AI split and "Remind me" | Mounted only in `MyWorkHost` and `/tasks` | `QuickCapture.tsx` |
| The feed acts, the Reminders band and the AI waiting group | My Day | `my_day_feed.md` §11 |
| The 07:30 brief and the events store | Built, dark | `core_brief.md`, `calendar_focus_os.md` (CE-4) |

### 2.2 Missing, or in conflict

1. **Rule conflict.** `navigation_shell.md` §4.3 gave My Day the job Notice. It gave Calendar the start, the focus and the end of the day. D-MD-1 changes this.
2. **Two "Today" rules.** My Day reads time blocks. The My work Today chip reads due and start dates (`app/tasks/lib/today.ts:35-45`). The two lists disagree.
3. **No capture on My Day.** The command bar "New task" only opens `/projects?do=capture`.
4. **The focus room reads a store that My Day never loads.** It reads `useTaskStore.items`. Its capture button shows only on `/tasks*` (`FocusMode.tsx:108`). So the button is dead since OP-4.
5. **No focus log.** Actual time is one `actual_start` and `actual_end` pair for each member and task (`infra/postgres/187`). A new session overwrites it. No pause, no auto-stop. The timer mode lives in `localStorage` (`focusPrefs.ts`).
6. **No top 3.** Only the One Thing exists. No today flag exists.
7. **My Day acts are few.** The feed acts are Done, Mark read and the menu. "Plan my day", "Fit what's left" and "Block time" are links. Every `TodayCard` row links to `/calendar`. The A\* acts of MD-8 are not built.
8. **The chat cannot plan the day.** It can set one block (`set_my_overlay`), make reminders and capture. Plan, replan, rollover and the One Thing are class X (`skill_projects/manifest.py:113-115,342-345`). CAL-1 (task 1675) and CAL-2 to CAL-6 (epic 1652) are in Backlog.

### 2.3 Rules that bind

D101 (a card acts in place). D171.4 (no approval in one click). D89 (one shell seam). The My Day production flags are owner-only (`navigation_shell.md` §13.1). `calendar_focus_os.md` and `mockups/calendar_focus_os.html` already draw Plan, Do and Review modes for the Calendar. This spec reuses them.

---

## 3. The jobs of the three apps

This section replaces the table of `navigation_shell.md` §4.3 for My Day and Calendar (D-MD-1).

| App | Its job | What it may change |
|---|---|---|
| **My Day** | Run the day: notice, plan, start, focus and close | Capture, the top 3, the One Thing, today's blocks, the focus session, done, roll over |
| **Calendar** | Plan the week and the full grid | The schedule of any day, events, recurring windows, the planner settings |
| **My Tasks** | Commit | Every task field, every list |
| **Projects** | Hold the structure | Projects, boards, statuses, the full Inbox |

The same task, block and focus session show on each surface. A change on one surface shows on the others with no sync.

---

## 4. The one rule for each verb

A second implementation of any rule below is a defect (CLAUDE.md §4, "Respect the seams").

| Verb | The one rule | The one component or route |
|---|---|---|
| **Capture** | Every capture goes to the Inbox (D170). A named time ("at 3 pm") also makes a block or a reminder, and the toast offers Undo (D-MD-7). "AI split" proposes many items. The member confirms once | `QuickCapture.tsx`, moved to a shared mount. Chat uses `capture_intake` |
| **Today** | One rule for "Today": the One Thing, the top 3 that the member picks, what is on today's calendar, and what is due or starts by today (D-MD-2) | One helper, `todayRule`, read by My Day, the My work Today chip and the Calendar "To fit" tray |
| **Plan** | A plan is time on a task: `pm_task_personal.scheduled_*`. The planner routes write it. Every AI plan shows before and after. One Approve writes it. One Undo reverses it | `POST /projects/my/calendar/{plan,replan,rollover}` |
| **Start** | One focus session runs at a time, global. ▶ on any task or block starts it. A company task in a To do lane moves to In progress, with Undo (D-MD-6) | `enterFocusSession` and the shell dock |
| **Finish** | A session ends with "Is it done?" (done, almost, not now, blocked). The check-in logs the minutes and runs the follow-up | The check-in card and the focus-session log (D-MD-5) |
| **Close** | The day close-out rolls the rest over and sets tomorrow's One Thing. The 18:30 brief points to it | `EndOfDayReview` logic, shown in the Close phase |

"Today's focus" is a mark on a task (D-MD-3). The cap is 3 by default. The member sets it from 1 to 10. The One Thing is one marked task, with a gold star.

---

## 5. The phases of My Day

### 5.1 The three phases

| Phase | What it shows |
|---|---|
| **Plan** | The 07:30 brief. The pick list for the top 3 and the One Thing. "Clarify" for the Inbox. "Fit my day" with before and after |
| **Do** | The capture box. The Now card with the focus ring. "Up next". Today's plan, with ▶ and ✓ on each row. "To fit". The day rail. Needs you |
| **Close** | Stats: tasks done, minutes focused, the One Thing result, Inbox left. Roll over each open task. Tomorrow's One Thing |

### 5.2 How the phase is chosen

1. Before the member starts the day, the phase is **Plan**. The day starts when the member approves a plan, starts a focus session, or taps "Start the day".
2. After the day starts, the phase is **Do**.
3. After 17:30 local time, or when the member taps "Close the day", the phase is **Close**. The hour is a member setting.
4. The chosen phase is only a default. Three tabs let the member switch by hand. The switch holds until midnight.
5. `calendar_day_state` holds the day state. No second state store exists.

### 5.3 The first open of the day

The startup ritual of Calendar shows in the Plan phase. My Day does not copy it. It mounts the same component (this keeps the rule "No second ritual" of the old §4.3).

---

## 6. Needs you: one tab for each app (D-MD-9)

The owner said that several apps can need a member. So the "Needs you" group of My Day shows tabs.

1. **One tab for each app that has a needs provider.** The first tab is "All". Each other tab names one app, for example Projects, Email, Approvals, Waiting, CRM and Calendar. Each tab shows its count.
2. **The source is the one feed.** `GET /shell/needs` already merges each app's provider (`navigation_shell.md` §7.2). The tabs filter that feed by app. No new read exists.
3. **A tab shows only for an app that the member can open.** A pane in `preview` shows no tab. A tab with a count of 0 stays visible but quiet, so the row does not jump.
4. **A new app joins by its manifest.** It adds a provider. It adds no tab code (D89).
5. **The Waiting tab shows the top 5** of `/waiting` and links to the page. `waiting_on_me.md` owns that page.
6. **Each row acts in place** under D101 and D171.4. A row that needs more context opens the owning app.
7. **One item shows once.** An item in a tab does not repeat in another card (the rule of `navigation_shell.md` §4.5).

---

## 7. The phone

At 390 px and at 360 px, My Day is one column. The order is:

1. The running session (the Now card, small).
2. The capture box and the Ask bar at the thumb.
3. Today's plan. The Today card merges into it (D-MD-8).
4. Needs you, as tabs that scroll sideways.

The day rail and the three-column Plan view do not show on a phone. A tap target is 44 px or more. A long row wraps and never clips.

---

## 8. Decisions

D188 in `work_plan.md` §3 records these. The owner approved them on 2026-11-07.

| Id | Question | Decision |
|---|---|---|
| **D-MD-1** (MD-Q1) | The job of My Day | My Day is the command centre: notice, plan, start, focus and close. Calendar keeps the week, the full grid and events. Projects keeps the structure. This amends `navigation_shell.md` §4.3 |
| **D-MD-2** (MD-Q2) | One Today | One rule: the One Thing, the top 3, what is on today's calendar, and what is due or starts by today. Every surface reads one helper |
| **D-MD-3** (MD-Q3) | The top 3 | A "today's focus" mark on a task. The cap is 3 and the member sets it from 1 to 10. The One Thing is one of them |
| **D-MD-4** (MD-Q4) | Phases | Plan until the day starts, Do while the member works, Close after 17:30 or on request. A switch lets the member pick |
| **D-MD-5** (MD-Q5) | The focus log | A new table, one row for each session. It is private to the member and scoped to the organization. It holds the task, start, end, pauses and the answer to "Is it done?". Only the member sees their own minutes |
| **D-MD-6** (MD-Q6) | The board | Focus on a company task in a To do lane moves it to In progress, with Undo. "Done" in the check-in closes it there |
| **D-MD-7** (MD-Q7) | Capture with a time | A capture goes to the Inbox. A named time also makes a block or a reminder. The toast offers Undo |
| **D-MD-8** (MD-Q8) | The phone | One column: the running session, capture and Ask at the thumb, today's plan, then what needs you |
| **D-MD-9** (owner addition) | Needs you tabs | "Needs you" on My Day has one tab for each app that can need the member, each with its count (§6) |

---

## 9. Slices

Each slice ships alone, dark where it changes behavior, through the preview, the Approve, the local gate and the merge (CLAUDE.md §4).

| Slice | Task | What | Done when | Tier |
|---|---|---|---|---|
| **CC-0** | 1967 | This spec, D188, the amendment of `navigation_shell.md` §4.3, and one task for each slice | INDEX lists the spec, and each slice has a task | LOW |
| **CC-1** | 1968 | The capture box and "Clarify" on My Day, by reuse of Quick Capture | A capture from My Day lands in the Inbox. A time today also sets a reminder, with one Undo | MEDIUM |
| **CC-2** | 1969 | One Today rule and the "today's focus" mark. Adds a migration | My Day, the Today chip and the Calendar tray list the same tasks | HIGH (migration) |
| **CC-3** | 1970 | Today's plan on My Day, with ▶ and ✓ on each row, in place of the Today card | One tap starts focus or finishes a task from My Day | MEDIUM |
| **CC-4** | 1971 | Start focus from anywhere: My Day, the Projects task panel and the Now card. Fix the dead capture button. The board moves to In progress | The dock runs the same session from each door | MEDIUM |
| **CC-5** | 1972 | The focus-session log and the "Is it done?" check-in. Adds a migration with RLS | Each session is a row, minutes add up, and the follow-up runs | HIGH (migration, RLS) |
| **CC-6** | 1974 | The day rail: the Calendar day grid in a compact form, with drag from "To fit" | A drop on My Day shows in Calendar at once | MEDIUM |
| **CC-7** | 1975 | The Plan phase: the brief, the top 3 pick, "Fit my day" with before and after | One Approve writes the blocks, with one Undo | MEDIUM |
| **CC-8** | 1976 | The Close phase: stats, roll over, tomorrow's One Thing | The day closes from My Day | LOW |
| **CC-9** | 1977 | The phone layout | At 390 px and 360 px, the session, capture and plan work with one hand | MEDIUM |
| **CC-10** | 1978 | Chat parity: capture, start and stop focus, plan and close the day (with CAL-1) | Each verb works from the chat with one card | HIGH (chat tools) |
| **CC-11** | 1973 | The "Needs you" tabs (D-MD-9) | One tab for each app with a provider shows its count. A tab filters the one feed | MEDIUM |

### 9.1 Order and dependencies

1. CC-1, CC-2 and CC-4 start first. They do not depend on each other.
2. **CC-3 needs CC-2 and CC-4.**
3. **CC-6 needs CC-3.**
4. **CC-7 needs CC-2 and CC-3.**
5. **CC-8 needs CC-5.**
6. CC-9 follows CC-3. CC-10 follows CC-4, CC-5 and CAL-1.
7. **CC-11 waits on PR 1789.** That PR changes `FeedCard.tsx`. CC-11 starts after it merges.

### 9.2 CC-2 in detail: one Today rule and the today's-focus mark

The spec audit of 2026-11-07 asked for these choices before the build. Each is a technical choice inside D-MD-2 and D-MD-3.

**What exists.** Three rules decide "Today" now:

- `isInToday` in `src/app/tasks/lib/today.ts:35-45` (client).
- Its server twin `in_today` in `routes/projects/mine.py:291`.
- The time-block list that the Calendar and My Day read.

None of them reads the One Thing, a focus mark or a block.

**Storage (migration 349, expand only).**

- `pm_task_personal.focus_on date NULL`. A mark is the member's date of the mark. It is "today's focus" only while it equals the member's today, so it expires by itself.
- `user_settings.today_focus_cap smallint NULL`. Null reads as 3. A write clamps it to 1..10, and a read clamps it again.
- Both columns ride tables that are already tenant-scoped with FORCE RLS. Identity comes from the session only (R5).
- The build takes the number 349 (main is at 347, and 348 is on another branch). Re-check it at merge (R1).

**The Today set.** One SQL predicate in the gateway decides it, for one member and one local day. The day is the member's local day, read from the zone that `in_today` uses now. A task is in the set when it is open, visible to the member, and one of these is true:

1. It is the day's One Thing (`calendar_day_state.one_thing_id`).
2. Its `focus_on` equals today.
3. It has a block that starts today (`scheduled_start` in the local day).
4. It is mine (`core.mine_clause`), not deferred, and due or starting on or before today. This is the rule of `in_today` now.

**The read.** `GET /projects/my/today` answers the set. Each row carries the task, its block if any, and `why`: a list of `one_thing`, `focus`, `scheduled` and `due`. The response also carries `focus_cap` and `focus_count`.

- My Day reads it. The Today chip of My work and its count read the same predicate.
- The Calendar's "To fit" tray is the set minus the tasks with a block today.
- `in_today` becomes a call of the one predicate. `isInToday` stays only as an optimistic local check. A shared fixture file holds the cases, and the pytest and the vitest both read it, so the two cannot disagree.

**The write.** The existing per-member overlay route and `set_my_overlay` take `focus_on`. The only accepted values are the member's today and null. Another date answers 422.

**The cap.** The server enforces it.

- One statement counts the member's marks for today and writes the new mark, under a transaction lock on the organization, the member and the day.
- The count is the tasks with `focus_on` equal to today, plus the One Thing when it has no mark.
- A mark past the cap answers 409 `focus_cap_reached`, with words that say to unmark one first. The client also disables the control, and the server stays the rule.
- Setting the One Thing never refuses. It counts toward the cap.
- A lower cap keeps the marks that exist and refuses new ones until the count is under it.

**The flag.** `TODAY_RULE_ORGS`, default empty. It reloads by SIGHUP (`docs/flag_reload.md`). With it off, the Today chip and its count keep the old rule, and nothing that a member sees changes. The new route and the mark exist with the flag off, and no UI calls them until CC-3 and CC-7. The flip is gate `enforcement-flip`.

**Not in CC-2.** The phases, the focus log, the day rail, the chat tools (CC-10) and a control for the cap setting. The control ships with CC-7, which picks the top 3.

**Verify with:**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_today_rule_r8.py tests/unit/test_today_focus_cap_r8.py -q
uv run ruff check apps/services/gateway/gateway/routes/projects/
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/shell/todayRule.test.ts
```

Run the R8 files with the dev database exported, and confirm that no R8 test skips.

**Gate labels.** The migration, the route, the write and the predicate are AGENT-SAFE, HIGH tier. The flip of `TODAY_RULE_ORGS` is OWNER-GATE until a dated `enforcement-flip` grant covers it.

### 9.3 CC-5 in detail: the focus-session log and the check-in

CC-5 ships in two parts. **CC-5a** is the server log. **CC-5b** is the check-in in the app. CC-5b needs CC-4 merged, because it calls the one start door of CC-4 (`useStartFocus` in `src/app/tasks/lib/focusStart.ts`).

**What exists.** One `actual_start` and `actual_end` pair on `pm_task_personal` (`infra/postgres/187`). Each session writes over the pair. No row holds a session, a pause or an outcome. `estimate-stats` learns from the pair (`routes/projects/planning.py:187-199`).

**CC-5a, storage (migration 350, expand only).** A new table `focus_sessions`:

- `id uuid` primary key, `member_email text NOT NULL`, and `task_id uuid NOT NULL REFERENCES pm_tasks(id) ON DELETE CASCADE`. A deleted task takes its sessions with it.
- `organization_id uuid NOT NULL REFERENCES organization(id) DEFAULT current_setting('app.tenant_id', true)::uuid`, in the idiom of migration 340. A guarded `DO` block enables and forces RLS, with the policy `focus_sessions_tenant_isolation` on `app.tenant_id` for USING and WITH CHECK. `test_tenant_coverage.py` and `test_tenancy_boundary.py` must pass with no exemption.
- `started_at timestamptz NOT NULL`, `ended_at timestamptz NULL`, `planned_end timestamptz NULL` (the end of the block, or the start plus the timer length).
- `paused_seconds integer NOT NULL DEFAULT 0`, and `mode text` with a CHECK for `25/5`, `50/10` and `flow`.
- `outcome text NULL`, with a CHECK for `done`, `almost`, `not_now`, `blocked`, `replaced` and `auto_closed`.
- An index on `(organization_id, member_email, started_at)`.
- A partial unique index on `(organization_id, member_email) WHERE ended_at IS NULL`. A member has at most one open session.
- The DDL is idempotent, as `infra/postgres/README.md` asks.

A new table can have NOT NULL columns and defaults, because no old code reads it (R6). The build takes the number 350, after CC-2's 349. Re-check both at merge (R1).

**CC-5a, privacy.** A session row belongs to its member. Every route reads and writes only the rows of the session's member. Identity comes from the session, never from the request (R5). A manager sees no other member's minutes (D-MD-5). The RLS of the table is tenant RLS only, as on `pm_task_personal`. The routes filter by the member. No repo policy keys on the member, so the table test proves the tenant, and the route test proves the member.

**CC-5a, routes.**

- `POST /projects/my/focus-sessions` starts a session for a task that the member can see. A task that the member cannot see answers 404. In one transaction, it ends any open session of the member with the outcome `replaced`, then inserts the new row. On a unique violation of the open-session index, it retries once.
- `PATCH /projects/my/focus-sessions/{id}` pauses, resumes, extends (`+10 min` moves `planned_end`) or ends a session with an outcome. Only the session's member can write it.
- `GET /projects/my/focus-sessions?day=` lists the member's sessions of one local day, with the minutes of each and the total. The minutes are the end minus the start minus the paused seconds. The day is the member's local day, in the zone that the Today predicate of CC-2 uses.
- **Auto close.** No job runs. A read or a start first runs ONE statement: `UPDATE … SET ended_at = planned_end, outcome = 'auto_closed' WHERE` the member's row is open and `planned_end` is more than 15 minutes ago. A second reader at the same time updates no row. Never read first and then write.
- **The overlay pair.** The routes own the `actual_start` and `actual_end` stamps of a session: a start stamps `actual_start`, and an end stamps `actual_end`. A replaced session stamps the end of its own task. An auto-closed session stamps `planned_end`. Until CC-5b, no client calls these routes, so the room's own stamp stays the only writer. CC-5b removes the room's stamp in the same change that calls the routes. So the two never write the same pair.
- Each new route gets a row in `skill_projects/manifest.py` as class X, with the reason "the tools come with CC-10", so `test_projects_chat_coverage.py` passes.

**CC-5a, flag.** `FOCUS_LOG_ORGS`, default empty, reloaded by SIGHUP. Add it to `acb_common/settings.py` in the idiom of `studio_orgs`, add the prefix `FOCUS_LOG_` to `env_guard.py` (a tenant must not set it), read it through a small `flags.py` reader in the idiom of `routes/studio/flags.py`, and record it in `docs/flag_reload.md`. With it off, each route answers 404 before any database work, and nothing writes the overlay.

**CC-5b, the check-in.** When the timer ends, the room and the Now card ask "Is it done?". The answers:

- **Done** ends the session with `done` and finishes the task by the tick's write (the board's status write).
- **Almost** extends the session by 10 minutes.
- **Not now** ends it with `not_now`, and moves the task's block to the next free slot through the planner route.
- **Blocked** ends it with `blocked`, and opens the existing "Waiting for" control of the task.
- Esc keeps the session and closes the card.

The Close phase (CC-8) and the Calendar read the minutes from `GET /projects/my/focus-sessions`.

**Not in CC-5.** The phases, the day rail, the chat tools and any report for a manager.

**Fences.** `tests/unit/test_focus_session_rls_r8.py` holds that organization A and member X see only their own rows, through the routes and through the table. `tests/unit/test_focus_sessions_r8.py` holds that a start ends the open session, and that pauses do not count. It also holds that an end stamps the overlay pair, and that a stale session closes itself. CC-5b adds `src/app/tasks/lib/checkIn.test.ts` and `e2e/focus-check-in.spec.ts`.

**Verify with (CC-5a):**

```bash
uv run pytest tests/unit/test_focus_session_rls_r8.py tests/unit/test_focus_sessions_r8.py tests/unit/test_tenant_coverage.py tests/unit/test_tenancy_boundary.py tests/unit/test_projects_chat_coverage.py -q
uv run ruff check apps/services/gateway/gateway/routes/projects/ packages/acb_common
```

The R8 files hold an `apply_ladder` fixture. They also prove two starts at once leave one open row, a flag-off POST writes nothing, and the auto close runs once. Run the env_guard and flag tests that `scripts/affected_tests.py` lists.

Run them with a database exported, and confirm that no R8 test skips.

**Gate labels.** CC-5a is AGENT-SAFE, HIGH tier: a migration and a table with RLS. CC-5b is AGENT-SAFE, MEDIUM. The flip of `FOCUS_LOG_ORGS` is OWNER-GATE until a dated `enforcement-flip` grant covers it.

---

## 10. Fences (R7)

A fence below is a test to write in the slice named. Each is advisory until that slice builds it.

| Rule | The test that fails when the rule breaks | Slice |
|---|---|---|
| One Today predicate. The set holds the One Thing, the marks, today's blocks and the due rule. The tray is the set minus the blocks. Each organization and member sees only its own rows | `tests/unit/test_today_rule_r8.py` | CC-2 |
| The client check and the server predicate agree on every case of one shared fixture file | `src/lib/shell/todayRule.test.ts` | CC-2 |
| The today's-focus cap holds, from 1 to 10. A mark past it answers 409, and a lower cap keeps the marks that exist | `tests/unit/test_today_focus_cap_r8.py` | CC-2 |
| A focus-session row is tenant-scoped and private to the member | `tests/unit/test_focus_session_rls_r8.py` | CC-5 |
| Capture from My Day lands in the Inbox. A time today also sets a reminder, and one Undo removes both | `src/lib/shell/captureTime.test.ts`, `e2e/my-day-capture.spec.ts` | CC-1 |
| One focus session at a time, from any door | `src/lib/focus/singleSession.test.ts` | CC-4 |
| Focus on a To do task moves it to In progress, with Undo | `tests/unit/test_focus_start_board_r8.py` | CC-4 |
| The phase default follows the clock and the day state | `src/app/my-day/phase.test.ts` | CC-7 |
| A tab exists for each live provider only | `src/lib/shell/needsTabs.test.ts` | CC-11 |
| `navigation_shell.md` §4.3 names the command centre | `tests/unit/test_my_day_command_centre_spec.py` | CC-0 follow-up |

No test file above exists today. Until it exists, the rule is advisory.

---

## 11. What this spec does not own

| Owner | What it keeps |
|---|---|
| `navigation_shell.md` | The shell frame, the bell, the manifest and the flags. Its §4.3 now cites this spec |
| `my_day_feed.md` | The signals, the cards, the order, the life of a card and the acts (D101) |
| `calendar_focus_os.md` and `calendar_timeboxing.md` | The Calendar grid, the week, events and the planner |
| `one_projects_app.md` | The task store, My work and the Inbox (D170, D171) |
| `waiting_on_me.md` | The `/waiting` page and the wait model |
| `core_brief.md` | The 07:30 brief and the 18:30 close-out record |
| `proactive_reach.md` | Delivery outside the app |

---

## 12. Open questions

None block a slice. Two items need a check at build time.

1. **The Close hour.** The default is 17:30 (D-MD-4). CC-8 decides if the hour comes from the work-hours setting of the member.
2. **The tab list.** CC-11 reads the provider list at build time. If a provider has no stable app id, CC-11 adds one to its manifest and asks no owner question.
