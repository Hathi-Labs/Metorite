# Calendar & Timeboxing — feature spec + roadmap

Status: **P0–P3 SHIPPED to main** (PR #71 merged, commit `7a5c72b2`) —
**verified against code on 2026-08-03**.
The day/week/month grid, drag-drop + resize timeboxing, energy/capacity prefs,
the AI "Plan my day" planner, chat-with-calendar tools, auto-reschedule
roll-over, deadline radar, and overlap detection are all live.
Only P4 (external Google/Outlook sync — **OWNER-GATE**: needs Google/Graph OAuth
client credentials on the VPS) and parts of P5 remain deferred (see cross-map,
§12). Since then the packer also gained **break geometry + lunch protection**
(`80722e17`, migration `97_gtd_planning_prefs.sql`) and **recurring day-template
windows** (migration `98_gtd_day_templates.sql`) — both live; see
`calendar_focus_os.md` §7.

**Update 2026-08-01 (doc-truth pass):** the old "draft PR / nothing auto-deploys
until reviewed + merged" caveat is obsolete — PR #71 merged
(`7a5c72b2 feat(calendar): timeboxing app … (#71)`) and `CalendarView.tsx` +
the calendar routes are in main. P3's "still to do" (nightly job + roll history)
has ALSO shipped since: migration `infra/postgres/78_gtd_calendar_rollover.sql`
(`calendar_rollover_log`, `auto_rollover` toggle, per-user `timezone`) and
`start_auto_rollover()` launched at gateway startup
(`apps/services/gateway/gateway/main.py:274-275`, defined at
`.../routes/tasks/calendar.py:1543`).
Roll-over SEMANTICS then changed in #235 (2026-07-26): unfinished blocks are
RELEASED back to the unscheduled list (`rolled_to = NULL` in the log) for
deliberate re-planning — NOT packed onto today — so §6's "rolls them to the
next open slot" no longer describes shipped behaviour. Successor roadmap:
`calendar_focus_os.md`.

## 1. Why

The GTD app captures, clarifies, prioritises and files next actions — but it has
no way to **place work in time**. Today the "Calendar" view is a flat list of
`is_hard_date` items sorted by `due_at`. There is no start time, no duration
block, no day/week/month grid, no "plan my day". The goal is a calendar that
lets the user **timebox** next actions against the hours they actually have,
**account for energy**, **sync** with Google/Outlook, and **plan the day by
chatting** with an assistant that already knows their Next Actions.

Design north star: Sunsama / Motion / Akiflow — a *task-first* calendar where the
calendar is the planning surface for the task list, not a separate silo.

## 2. Core concepts

- **Due date (`due_at` + `is_hard_date`)** — a *deadline*. Already exists. "This
  must be done BY X." Not a schedule.
- **Time block (`scheduled_start` + `scheduled_end`)** — *when you will actually
  do it*. NEW. A task can have a deadline of Friday but be timeboxed Wednesday
  10:00–10:45. This is the heart of timeboxing.
- **Estimate (`time_estimate_mins`)** — how long it should take. Already exists.
  Seeds the default block length (`end = start + estimate`).
- **Energy (`energy` low/med/high)** — already exists. Drives *where in the day*
  a task should land (high-energy work in your peak window).
- **Capacity** — how many focus-hours a day realistically holds. NEW (setting).

A task is thus in one of: **unscheduled** (in Next Actions, no block),
**scheduled** (has a block), **overdue-unscheduled** (deadline passing, no block →
nagged), **done**, **rolled-over** (auto-rescheduled, see §6).

## 3. Data model (scaffolded)

MVP scaffold adds two columns to `gtd_items` (migration `76_gtd_scheduling.sql`):

```
scheduled_start TIMESTAMPTZ   -- start of the time block (null = unscheduled)
scheduled_end   TIMESTAMPTZ   -- end of the block; default start + estimate
```

- Client `GtdItem`: `scheduledStart?`, `scheduledEnd?`.
- A task is "on the calendar grid" when `scheduled_start` is set (distinct from
  the deadline-driven `is_hard_date` list, which stays as an all-day lane).

**Phase 2 evolution (planned, not built):** promote to a `gtd_time_blocks` table
(`id, item_id, start, end, kind, source, external_event_id`) so one task can have
*multiple* blocks (split focus sessions, recurring), and so external calendar
events (meetings that are NOT tasks) can live on the same grid via
`kind='external'`. ~~The grid component is written against a `TimeBlock[]`
abstraction so this swap is non-breaking.~~

> **Update 2026-08-01 (doc-truth pass), re-verified 2026-08-03:**
> `gtd_time_blocks` is still unbuilt — no migration creates it; the only
> repo-wide match is the comment in `infra/postgres/76_gtd_scheduling.sql`.
> Its column set is specified in three places with
> different shapes (here, `calendar_focus_os.md` §5, and the comment at
> `infra/postgres/76_gtd_scheduling.sql:14`); **`calendar_focus_os.md` §5 is
> canonical** — this section and the migration comment defer to it.
> **When you build it, do not write an absolute migration number into any spec
> or commit message — list `infra/postgres/` and take the next free number.**

> **CORRECTION 2026-08-03 — the struck sentence above was FALSE, and it
> materially understated the cost of this table.** There is no `TimeBlock[]`
> seam. `blocksForDay(items, day)` in
> `workbench/control_plane/src/app/tasks/lib/scheduling.ts` *projects* blocks
> out of `gtd_items.scheduledStart/scheduledEnd`, and every mutation goes
> through `applySchedule(…{scheduledStart, scheduledEnd})`. Measured blast
> radius: **17 files** under `workbench/control_plane/src/app/tasks/` reference
> `scheduledStart|blocksForDay|applySchedule`, plus **3 gateway modules**
> (`apps/services/gateway/gateway/routes/tasks/{calendar,core,items}.py`),
> `apps/skills/skill-task-gtd/skill_task_gtd/core.py`, and
> `apps/agents/agent-task-manager/agents.py`. **This is 4 PRs, not a swap** —
> the slice plan (schema+API dual-write → client swap → packer/tool cutover →
> kinds) and the done-when live in `calendar_focus_os.md` §9.1.
> The same false claim is repeated in the `76_gtd_scheduling.sql:14` comment;
> fix it whenever that file is next touched.

## 4. Views (scaffolded: day / week / month grid)

Replace the flat list at
`workbench/control_plane/src/app/tasks/page.tsx`'s "calendar" branch with a dedicated
`CalendarView` (day/week/month toggle, persisted like the list/board toggle).

- **Day** — vertical hour grid (configurable day window, e.g. 07:00–22:00),
  blocks positioned by start/end, current-time line, an **unscheduled rail** of
  schedulable next actions on the side. Click/drag a task onto an hour to
  timebox it.
- **Week** — 7 day-columns × hour rows; same block rendering; drag across days.
- **Month** — calendar month grid; tasks appear as chips on their
  `scheduled_start` day (or `due_at` day for deadline-only items); click a day →
  jumps to that day view.
- **Deadlines lane** — `is_hard_date` items without a block show as all-day
  markers so a deadline is never invisible just because it isn't timeboxed yet.

Scheduling a task writes `scheduled_start/end` (store `scheduleItem(id, start,
end)` → PATCH). Rendering reads a date-range query `GET /tasks/calendar?from&to`.

## 5. Timeboxing + energy-aware planning

- **Estimate → block**: dropping a task defaults its block to
  `time_estimate_mins` (fallback 30m). Resize adjusts the estimate.
- **Capacity meter**: each day shows scheduled focus-hours vs the capacity
  setting; overcommit is flagged ("you've booked 9h of focus work today").
- **Energy lanes**: the user marks peak/trough windows (setting). The planner
  prefers high-energy tasks in peak windows, low-energy/administrative work in
  troughs. Energy of a task (already captured) is matched to the slot.
- **Conflict avoidance**: blocks can't overlap existing blocks or synced
  external events (meetings).

## 6. Smart daily planning + auto-reschedule

- **"Plan my day"** — given today's date, the planner pulls the user's Next
  Actions (mine, NEXT, not done), ranks by the existing priority matrix + due
  proximity + leverage, respects capacity and energy windows, works *around*
  already-scheduled blocks and synced meetings, and proposes a timeboxed day.
  The user accepts/edits.
- **Look-ahead**: the planner also reads upcoming deadlines and scheduled items
  for the next N days, so it can pull work *forward* ("Thursday is slammed —
  do the deck prep today"). This is the "get ahead of what needs to be done"
  behaviour.
- **Auto-reschedule (roll-over)**: a nightly job (reuse the existing
  scheduler pattern) finds blocks whose end has passed and whose task is **not
  done**, and rolls them to the next open slot on the next day that has
  capacity (respecting deadlines — a rolled task that would miss its `due_at` is
  escalated/flagged instead of silently moved). Every roll is recorded so the
  user sees "moved from Tue → Wed."

## 7. Chat with your calendar

Reuse the existing task assistant (`AgentChat` → `/api/agent/chat`, agent
`task-manager`). Two additions:
- **Persona context**: extend `buildTaskAssistantPersona` with today's blocks,
  free windows, capacity, energy windows, and upcoming deadlines.
- **Tools** (in `apps/skills/skill-task-gtd/skill_task_gtd/core.py`, registered
  by `apps/agents/agent-task-manager/agents.py`): `gtd_schedule(item, start, end)`,
  `gtd_reschedule`, `gtd_plan_day(date, energy_note)`, `gtd_unschedule`.

Then the user can say *"I'm low energy today, move the deep work to tomorrow and
give me admin tasks"* and the LLM re-timeboxes: it reads Next Actions + today's
grid + upcoming load, and reorganises the day around the stated energy, pulling
manageable work forward. This directly satisfies the "chat with my calendar,
account for energy, auto-organise the main tasks to focus on" request.

## 8. External sync — Google Calendar + Outlook (planned; seams scaffolded) · 🔒 **OWNER-GATE**

Reuse the email OAuth stack
(`apps/services/gateway/gateway/routes/email/transport/oauth.py` already does
Google + Microsoft Graph; encrypted tokens via `key_store`).
**Gate:** this cannot be built or verified without Google/Graph OAuth client
credentials on the VPS — see §13 P4.
- New `calendar_accounts` table (mirror `task_accounts`/`email_accounts`
  encrypted-token pattern). Scopes: Google `calendar.events`, Graph
  `Calendars.ReadWrite`.
- **Read**: pull external events into the grid as `kind='external'` blocks
  (read-only, for conflict-avoidance and context) — "don't book over my 2pm."
- **Write**: push timeboxed task-blocks out as real calendar events (two-way),
  so the phone calendar shows the plan; edits flow back.
- Scaffold ships `POST /tasks/calendar/sync` + `calendar.py` route returning
  `501 not_implemented` with the design noted, and a settings seam for
  connecting a calendar — no live OAuth wiring yet (needs client creds).

## 9. Powerful features to consider (brainstorm)

- **Auto-scheduling engine (Motion-style)**: continuously (re)optimise the whole
  week when tasks/estimates/deadlines change — not just a one-shot "plan my day."
- **Focus/Pomodoro integration**: start a block → start a focus timer; running
  over shifts subsequent blocks (ties into the deferred Pomodoro item).
- **Meeting-aware buffers**: auto-insert prep/travel/decompress buffers around
  meetings.
- **Ideal week / templates**: recurring "themes" (Mon = deep work AM, Fri =
  admin) the planner fills against.
- **Time-of-day heuristics from history**: learn when the user actually completes
  high-energy work and bias future planning toward it.
- **Deadline risk radar**: surface tasks that *cannot* fit before their deadline
  given remaining capacity ("you can't finish these 3 by Friday").
- **"What can I do in 15 min?"** — free-slot filler from short next actions.
- **Weekly review**: planned-vs-actual, roll-over count, focus-hours trend.
- **Calendar-as-input**: a captured meeting → suggested prep task blocks.
- **Shared/【delegated】visibility**: see when a delegate is free (later, org-aware).

## 9b. How timeboxing fails — and how this design overcomes it

| Failure mode | Overcome by |
|---|---|
| **Overcommitment** — planning more than fits | Capacity setting + "Xh/Yh over capacity" meter; the AI planner refuses to select more than fits and leaves the rest for another day |
| **No buffers** — one overrun cascades | Buffer-minutes setting the packer leaves between blocks |
| **Ignoring energy** — deep work in a trough | Energy windows (tinted on the grid); the planner places high-energy work in peak windows |
| **Interruptions / falling behind** — the plan goes stale | One-click roll-over of overdue-incomplete blocks into today's open slots; the planner's free slots start at *now* |
| **Deadline slip** — a due task never gets timeboxed | Deadline radar: due-soon badge + rail section + one-click "Today" |
| **Bad estimates** — tasks run long | Drag-resize a block in seconds; the planner uses estimates and shows total load |
| **Double-booking** — overlaps hide each other | Side-by-side lane layout + a red "double-booked" flag |
| **Fragmentation** — scattered context switches | The planner is told to batch similar contexts |
| **Tedium** — manual timeboxing is work | One-click "Plan my day"; or just tell the assistant "I'm low energy, move deep work to tomorrow" |
| **Rigid plans** — replanning is manual | Re-plan with an energy note; roll-over; chat to reorganize |
| **Timezone/boundary bugs** | The client sends resolved ISO geometry to the planner (no server tz guessing); the assistant gets the current local time + offset |

## 10. Phased roadmap

- **P0 — scaffold ✅ DONE:** scheduling columns (mig 76) + `GtdItem` fields + API
  mapping; `CalendarView` day/week/month grid; unscheduled rail + click-to-
  schedule; `GET /tasks/calendar` range endpoint; external-sync seams.
- **P1 — timeboxing usable ✅ DONE:** drag-and-drop scheduling + block resize
  (native DnD + pointer events, 15-min snap, drop highlight); capacity meter +
  working-window/buffer/energy-window prefs (mig 77) that the grid + planner
  honor; energy-window tint bands; overlap detection with side-by-side lanes +
  "double-booked" flag; deadline all-day markers on the grid.
- **P2 — smart planning ✅ DONE:** `POST /calendar/plan` LLM planner (judgment =
  LLM, geometry = deterministic packer; priority/energy/capacity/deadline aware;
  fallback ranking) + the "Plan my day" review modal with an energy-note re-plan;
  chat-with-calendar = persona calendar context + `gtd_schedule`/`gtd_unschedule`
  /`gtd_list_schedule` agent tools.
- **P3 — auto-reschedule ✅ DONE (manual trigger):** `POST /calendar/rollover`
  packs overdue-incomplete blocks into today (deadline-aware) + the roll-over
  banner; deadline radar (due-soon badge + rail section + one-click timebox).
  *Still to do: a nightly automatic roll-over job (scheduler) + roll history.*
  *(Update 2026-08-01: the nightly job + `calendar_rollover_log` history SHIPPED —
  mig 78 + `start_auto_rollover()`; and #235 changed the semantics to
  release-to-list, see the header note. P3 is CLOSED.)*
- **P4 — external sync (DEFERRED · 🔒 OWNER-GATE — needs OAuth client creds):**
  `calendar_accounts` + Google/Graph read (conflict-avoidance) then two-way
  write. Seamed at `GET /tasks/calendar/accounts` + `POST /tasks/calendar/sync`
  (501). Done-when + the credential requirement: §13.
- **P5 — DEFERRED · AGENT-SAFE:** `gtd_time_blocks` table (multiple blocks/task,
  external events on the grid) + continuous auto-scheduling engine + ~~Pomodoro~~
  + ~~ideal-week templates~~ + learned-estimate heuristics.
  *(Update 2026-08-01, re-verified 2026-08-03: **Pomodoro SHIPPED** via
  `calendar_focus_os.md` F1's Focus Mode — pomodoro/flow timer with cycle dots,
  2026-07-22, `workbench/control_plane/src/app/tasks/components/FocusMode.tsx`.
  **Ideal-week templates SUBSTANTIALLY SHIPPED** 2026-07-23 — migration
  `98_gtd_day_templates.sql` + settings round-trip + editor + grid render +
  packer honouring; only the unused-window/adherence gap remains
  (focus_os §9.6). **Packer breaks + lunch protection SHIPPED** 2026-07-23
  (`80722e17`, mig 97) — but as buffer geometry, so *typed* break blocks and
  cycle telemetry feeding learned estimates still ride on `gtd_time_blocks`.
  The rest of P5 tracks under focus_os F2/F3 — see §12, and §9 there for
  acceptance.)*

## 11. Files this touches (map)

*(Paths re-qualified 2026-08-03 — every entry here was previously one or two
tree levels short, which broke the anchor check. All paths are repo-root
relative.)*

- Migration: `infra/postgres/76_gtd_scheduling.sql`
  (+ `infra/postgres/schema.generated.sql`).
- Backend, all under `apps/services/gateway/gateway/`:
  `routes/tasks/core.py` (model + row map), `routes/tasks/items.py`
  (patch fields + `GET /tasks/calendar`), `routes/tasks/calendar.py` (the
  planner, packer, rollover, day-state and the external-sync stubs),
  `routes/tasks/settings.py` (calendar prefs + `day_templates`).
- Agent surface: `apps/skills/skill-task-gtd/skill_task_gtd/core.py`
  (`gtd_schedule` / `gtd_unschedule` / `gtd_list_schedule`) +
  `apps/agents/agent-task-manager/agents.py` (registers those tools).
- Frontend, all under `workbench/control_plane/src/app/tasks/`:
  `lib/types.ts` (`ViewKey` already has `calendar`; scheduled fields),
  `lib/api.ts` (`mapItem` + `apiSchedule`/`apiCalendarRange`/`apiGetDayState`/
  `apiSetDayState`), `lib/taskStore.ts` (`scheduleItem`, calendar range loader),
  `lib/scheduling.ts` (+ `lib/scheduling.test.ts`) — `blocksForDay`,
  `applySchedule`, the block projection,
  `components/CalendarView.tsx` and `components/calendar/*` (day/week/month
  subviews, `TimeGrid`, `MonthGrid`, `NowNextBar`, `ScheduleSheet`,
  `PlanDayPanel`, `EndOfDayReview`, `CalendarSettings`, `shared.ts`),
  `components/FocusMode.tsx`, `page.tsx` (routes calendar → `CalendarView`),
  `lib/taskAssistantPersona.ts` (calendar context, P2),
  `lib/focusPrefs.ts` (the residual client-only Focus-OS state).

## 12. Cross-map to `calendar_focus_os.md` F0–F3

*(Added 2026-08-01; re-verified against code 2026-08-03.)*

| This doc | focus_os | State | Label |
|---|---|---|---|
| P0–P2 (grid, timeboxing, planner, chat) | shipped foundation under F0/F1 | **SHIPPED** (PR #71; F0/F1 2026-07-22) | — |
| P3 remainder (nightly roll-over + history) | — (closed here) | **SHIPPED** (mig 78 + `start_auto_rollover()` at `apps/services/gateway/gateway/main.py:274-275`; #235 release-to-list, `CalendarView.tsx:387-396`) | — |
| — (packer breaks + lunch) | F2 item, now closed | **SHIPPED 2026-07-23** (`80722e17`, mig 97) as *geometry*; typed `kind='break'` rows still owed under `gtd_time_blocks` | AGENT-SAFE |
| — (per-day Focus-OS state) | F2 clause | **SHIPPED** (mig 92 `calendar_day_state` + `GET/PUT /tasks/calendar/day-state`); only ritual stamps + `timerMode` remain local | AGENT-SAFE |
| P4 external sync | F3 item | **OPEN** — `calendar_accounts` absent; `POST /tasks/calendar/sync` still 501 (`calendar.py:57-64`); `GET /calendar/accounts` returns `[]` | **OWNER-GATE** (OAuth client creds) |
| P5 `gtd_time_blocks` / batch / recurring blocks | F2 | **OPEN** — table unbuilt; focus_os §5 canonical, §9.1 has the 4-slice plan | AGENT-SAFE |
| P5 Pomodoro | F1 Focus Mode | **SHIPPED 2026-07-22** (`FocusMode.tsx`) | — |
| P5 ideal-week templates | F3 | **SUBSTANTIALLY SHIPPED** (mig 98 + settings + editor + grid + packer + 2 tests); only the unused-window/adherence gap remains — focus_os §9.6 | AGENT-SAFE |
| P5 continuous auto-scheduling / learned-estimate heuristics | F3 | **OPEN** — no acceptance written; not dispatchable | AGENT-SAFE |
| Focus Shield | F2 (slipped from F1) | **OPEN** — primitive absent (zero grep hits); needs a design before dispatch | AGENT-SAFE *once specced* |
| Block reminders / notifications | — | **OPEN and unregistered** — lives only in `calendar_ux_review.md` P1; see focus_os §9.13 | AGENT-SAFE *once specced* |

## 13. Acceptance & verification

*(Added 2026-08-01; **verified against code 2026-08-03** — P3 below was the one
clause the 2026-08-01 pass got fully right and it is confirmed unchanged. The
verify command has been rewritten; the old `-k` form was unsafe.)*

- **P3 nightly roll-over — SHIPPED; acceptance restated to match #235:** after a
  user's local midnight, yesterday's unfinished flexible timeboxes are RELEASED
  to the unscheduled rail on first load without user action
  (`scheduled_start/end → NULL`, at most once per local day, `auto_rollover`
  opt-out honoured), and each release is recorded as a `calendar_rollover_log` row
  with `rolled_to = NULL`; re-planning them is deliberate (drag, or Rebuild my
  day, which sweeps them in per #232).
- **P4 external sync — OPEN · 🔒 OWNER-GATE; done when:** a `calendar_accounts`
  row can be created through a real OAuth connect flow (Google/Graph, reusing
  the `apps/services/gateway/gateway/routes/email/transport/oauth.py` pattern);
  external events render on the grid as
  `kind='external'` and the planner/packer refuses to book over them;
  `POST /tasks/calendar/sync` returns data instead of 501; two-way write pushes
  a timeboxed block out as a real calendar event.

  > **Why this is owner-gated, and what the owner must do.** Clause 1 cannot be
  > satisfied — or even verified — without **Google Calendar and/or Microsoft
  > Graph OAuth client credentials (client id, client secret, redirect URI)
  > provisioned on the VPS** and registered in the Integration Registry.
  > Obtaining, installing and consenting to those is an owner action; an agent
  > must refuse and report. Verified state 2026-08-03: `calendar_accounts` does
  > not exist in any migration or query (the only matches repo-wide are three
  > comments in `apps/services/gateway/gateway/routes/tasks/calendar.py`),
  > `GET /tasks/calendar/accounts` returns `[]` (`calendar.py:44-50`), and
  > `POST /tasks/calendar/sync` raises `501` (`calendar.py:53-64`, the raise at
  > `:60-64`).
  > **This gate is not yet registered in `work_plan.md` §6 — register it.**
  >
  > Clauses 2–4 are *partly* agent-safe once the table exists: `kind='external'`
  > rendering and packer avoidance can be built and unit-tested against seeded
  > rows. But the slice cannot be *accepted* without clause 1, so the whole item
  > stays owner-gated.

- **Verify:**

  ```
  cd workbench/control_plane && npx tsc --noEmit && npm test    # vitest
  ```

  ```
  uv run pytest tests/unit/test_calendar_planner.py \
                tests/unit/test_email_calendar_context.py \
                tests/unit/test_tasks_gtd.py
  ```

  **Name the files. Never run `pytest tests/unit -k calendar`** (the form this
  section carried until 2026-08-03): `-k` filters *after* collection, so it
  still collects the whole `tests/unit/` directory — the construction that hangs
  on the Windows dev box.

  Measured 2026-08-03: the two calendar files alone → **28 passed in 1.16s**;
  all three files → **157 passed in 166.79s** (`test_tasks_gtd.py` is the slow
  one — budget ~3 minutes, it is not hung).

  Coverage: `test_calendar_planner.py` = packer geometry (free intervals,
  buffers, energy windows, lunch carve-out, day-template block/focus windows,
  weekday filter); `test_email_calendar_context.py` = the email-side calendar
  context; `test_tasks_gtd.py` = the GTD API surface.

- **Everything else open** — `gtd_time_blocks` and its four slices, typed break
  blocks, batch blocks, Email windows, Waiting-on chase, Focus Shield, mobile
  timeline, AI breakdown-on-drop, the ideal-week residual, weekly review and
  Top-5 outcomes — has its acceptance, its AGENT-SAFE / OWNER-GATE label and its
  dependencies in **`calendar_focus_os.md` §9**, which is canonical for the
  F2/F3 surface. Do not write a second copy here.
