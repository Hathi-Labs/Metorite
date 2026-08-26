# Calendar → Focus OS — evaluation & redesign brainstorm

> 🔴 **READ §10 FIRST — 2026-08-24, D54 gives Calendar its own app.**
> The calendar stops being a view inside `/tasks` and becomes **`/calendar`**, a
> top-level `live` pane in the **Personal Center** section. Board row **WS-39**.
>
> 🔴 **AND A CORRECTION THIS SPEC'S FAMILY HAS BEEN CARRYING:** measured
> 2026-08-24, **`gtd_time_blocks` does not exist and `calendar_accounts` does not
> exist** — there is no `CREATE TABLE` for either anywhere in `infra/postgres/`.
> `calendar_timeboxing.md` §13 P4 and `work_plan.md`'s WS-21 row both cite them as
> if built. What the calendar actually persists to is **`gtd_items` directly**
> (scheduling is fields on the task row) plus `gtd_settings`, `gtd_day_state` and
> `gtd_rollover_log`. Any plan that begins "move the calendar's tables" is built on
> a table that was never created. See §10.3.

Status: **F0 + F1 BUILT** (2026-07-22, branch
`claude/calendar-productivity-redesign-rdh50k`) — **verified against code on
2026-08-03**: leverage lens + One Thing +
leverage meter + outcome ribbon, Gap Filler (2-minute pile), Startup ritual
(breathe → review → commit), Shutdown (leverage ratio, One-Thing verdict, seed
tomorrow, close the day) and Focus Mode (pomodoro/flow, subtask checklist,
+15 reflow, capture-in-focus via `C`).

**Also shipped since (do not re-dispatch these):**
- **Breaks in the packer — SHIPPED 2026-07-23** (`80722e17`, migration
  `infra/postgres/97_gtd_planning_prefs.sql`; the commit message's "mig 93" is
  the pre-renumber number and is wrong — the file itself records `93→97`).
  `gtd_settings.max_focus_run_mins` / `break_mins` + an optional protected lunch
  window; the packer widens the buffer behind the block that trips the
  focus-run limit
  (`apps/services/gateway/gateway/routes/tasks/calendar.py` — `_planning_prefs`,
  `_lunch_interval`, and the `want_break → buf = buffer_mins + break_mins` arm
  in `_compute_day_plan`), reports breaks + lunch in the plan notes, and applies
  lunch protection to rollover, replan and the nightly job.
  **Caveat that keeps F2 alive:** a break is a *gap the packer leaves*, not a
  row. There is no `kind='break'` block, nothing renders on the grid, nothing
  is countable in the review. Typed break blocks stay F2, under
  `gtd_time_blocks`.
- **Per-day Focus-OS state is no longer localStorage-only.** Migration
  `infra/postgres/92_gtd_day_state.sql` (`gtd_day_state`) +
  `GET/PUT /tasks/calendar/day-state` persist the ★ One Thing and the
  tomorrow-seeds server-side; the client already calls them
  (`workbench/control_plane/src/app/tasks/components/CalendarView.tsx` hydrates
  on open and writes on toggle,
  `.../components/calendar/EndOfDayReview.tsx` writes seeds on "close the day").
  `workbench/control_plane/src/app/tasks/lib/focusPrefs.ts` is now a *cache* for
  those two, and remains the only home for **ritual stamps**
  (`startupDoneOn`, `startupStreak`, `streakStampedOn`, `dayClosedOn`) and
  **`timerMode`** — that residue is all that the F2 "migrate the local state"
  clause still owes.
- **Ideal-week templates — SUBSTANTIALLY SHIPPED** (see §7 F3): migration
  `infra/postgres/98_gtd_day_templates.sql` + settings API + editor + grid
  render + packer honouring. Only the named gap in §9 remains.

The One-Thing planner directive rides the existing
`energy_note` seam. **Still open per §7/§9:** `gtd_time_blocks` (and everything
that needs block *kinds* — typed breaks, batch blocks, recurring ritual blocks),
Email windows, Waiting-on chase, the Focus Shield, and external sync.
**Follow-up (same day):** block context menu (right-click on desktop,
long-press on touch — Open · Focus · Done · One Thing · Pin · Reschedule… ·
Remove from calendar · Delete), undoable scheduling (every timebox/move/
resize/unschedule/plan-apply/roll-over/+15-reflow lands in the undo toast via
`applySchedule`), and mobile parity: the long-press menu is the touch path to
everything the hover micro-buttons do, and week view keeps a readable minimum
column width (horizontal scroll) on phones.

This doc evaluates the calendar shipped via `calendar_timeboxing.md` +
`calendar_ux_review.md` and proposes the configuration that makes it the
*primary* daily tool — the place you focus, complete work and plan the day —
effectively replacing the task list as the surface you live in. Mockups:
`mockups/calendar_focus_os.html`.

---

## 1. Where the calendar stands today (honest evaluation)

### What is already genuinely strong

| Capability | State |
|---|---|
| Timeboxing mechanics | ✅ day/week/month grid, drag-drop + resize, 15-min snap, overlap lanes, deadline all-day markers |
| AI planning | ✅ "Plan my day" + "Replan rest of day" (LLM judgment / deterministic packer), energy-note re-plan, rationale per block |
| Energy awareness | ✅ energy windows tinted on the grid; planner places high-energy work in peak windows |
| Capacity honesty | ✅ daily capacity target + booked meter + over-capacity flag; buffer minutes |
| Falling behind | ✅ one-click roll-over of overdue blocks; fixed vs flexible blocks; replan-from-now |
| Execution basics | ✅ Now/Next live bar with countdown; complete-from-block; focus timer (actualStart/End) |
| Feedback loop | ✅ end-of-day review, planned-vs-actual per block, learned estimate-accuracy stats |
| Mobile | ✅ tap-to-schedule sheet + FAB (view + basic scheduling works on touch) |

This is already at or past Sunsama/Akiflow parity **on planning**. The review
doc's "next six" have all shipped.

### Where it still falls short of "the tool I live in"

1. **Execution is a bar, not a place.** The Now/Next strip is great, but when
   it's time to *do* the work you're still staring at a grid of everything else
   — visual noise is the enemy of focus. There is no full-screen "Do" surface,
   no Pomodoro cycle, no breaks, no session ritual. The focus timer exists as a
   data-capture mechanism, not an experience.
2. **The day has no shape.** No morning startup or evening shutdown ritual as a
   first-class flow; planning is a modal you *may* open. Habits form around
   rituals, not features.
3. **All blocks look the same.** A leveraged, outcome-moving deep-work block
   renders identically to "expense report". The 80/20 signal the task manager
   already captures (`leveraged`, `important`, priority matrix) is invisible on
   the calendar — the one surface where it should scream.
4. **Small tasks and gaps don't meet.** `isTwoMinute` exists at clarify time,
   and Engage can filter "≤15 min", but the calendar never says *"you have 20
   free minutes right now — knock out these three."* Free time evaporates.
5. **No batching surface.** Contexts exist (`@calls`, `@computer`) and the
   planner is *told* to batch, but there's no visible batch block ("Calls ×4,
   45m") and no one-click "batch these".
6. **Breaks/recovery don't exist.** The packer knows `buffer_mins` but a buffer
   is dead space, not a break. Nothing suggests a walk after 90 focus minutes;
   nothing protects lunch; meditation is nowhere.
7. **Outcome blindness.** Projects carry `outcome` + `purpose`, yet a block
   never says *which outcome it advances*. A day can feel busy and be pointless.
8. **One block per task** (known P5): no split sessions, no recurring ritual
   blocks, no ideal-week templates — all needed by the ideas above.

---

## 2. The thesis: one calendar, three modes — **Plan · Do · Review**

The standout move is not "more calendar features". It's re-centering the app on
the *daily loop* every productivity method ultimately serves:

```
   PLAN (morning, 5 min)  →  DO (all day, one block at a time)  →  REVIEW (evening, 2 min)
        ↑                                                              │
        └────────────── learned estimates, carry-forward, tomorrow-seed ┘
```

- **Plan** = today's grid + unscheduled rail + AI planner (exists, gets the
  leverage/batch/break upgrades below).
- **Do** = a new full-screen focus surface: the current block, a Pomodoro/flow
  timer, the outcome it advances, and *nothing else*. The calendar shrinks to a
  thin "up next" ribbon. This is where the user spends 90% of their time — and
  it's the surface no competitor makes primary.
- **Review** = the existing end-of-day review, extended into a shutdown ritual
  that also *seeds tomorrow's plan* (closing the loop is what makes the morning
  plan take 5 minutes instead of 20).

Task lists don't disappear — they become the *backlog behind the calendar*.
Every list view keeps working, but the default landing surface is the calendar
in whichever mode fits the time of day (before day-start → Plan; during →
Do/Now; after day-end → Review).

---

## 3. Method-by-method mapping

How each philosophy the user named lands in this design — what exists (✅),
what's proposed (→):

| Method | Today | Proposed |
|---|---|---|
| **GTD** | ✅ capture→clarify→next actions feed the rail; engage criteria (energy/time/context) drive planner | → Weekly Review gets a calendar home (recurring ritual block + review flow); tickler (`deferUntil`) items surface on their day |
| **Timeboxing** | ✅ core mechanic | → split sessions (multi-block), recurring blocks, ideal-week templates |
| **Prioritizing** | ✅ 8-cell matrix ranks the planner's picks | → priority is *visible* on blocks (leverage lens, §4.3); planner guarantees the top leveraged task gets the first peak window |
| **Pomodoro** | ❌ | → Focus Mode cycles: work N min → break M min, configurable per block or "flow mode" (no interrupts, just elapsed); cycle count feeds actuals |
| **Breaks** | ⚠️ buffer minutes only | → first-class break blocks the packer inserts (after ≥90m focus, lunch protection); break menu: walk / stretch / breathe / hydrate |
| **2-minute rule** | ⚠️ flag exists at clarify | → Gap Filler: any free gap ≥10 min offers the shortest matching tasks; "clear 5 two-minute tasks" appears as one micro-batch chip |
| **Meditation** | ❌ | → startup ritual opens with an optional 1–5 min breathing timer; "breathe" is a break type; a recurring meditation block is one tap from settings |
| **Planning** | ✅ AI plan/replan modal | → becomes the *morning ritual* (auto-prompted at day start): review carry-forwards → pick the One Thing → accept plan. 5 minutes, guided |
| **Task breakdown** | ⚠️ subtasks exist in detail view | → drop a >90m task on the grid → AI offers to split into sessions/subtasks with estimates; oversized blocks get a "break this down" nudge |
| **80/20 rule** | ⚠️ `leveraged` captured, invisible here | → Leverage lens: gold accent on leveraged blocks; a daily **leverage meter** (% of focus-time on leveraged/important work); review reports it |
| **Batching** | ⚠️ planner prompt only | → Batch blocks: one grid block containing n same-context micro-tasks with an internal checklist; one-click "batch these 4 @calls" from the rail |
| **Outcome-focus** | ⚠️ project outcomes exist, not shown | → every block shows its project outcome ("→ Ship v2 onboarding"); day header: "Today advances 3 outcomes"; Do mode displays the outcome above the task |

---

## 4. Standout features (the brainstorm, ranked)

### 4.1 Focus Mode — the "Do" surface ⭐ the differentiator
Tap ▶ on any block (or the Now bar) → full-screen focus:
- Big timer ring: **Pomodoro** (25/5, 50/10, custom) or **Flow** (count-up,
  no interruptions). Cycle dots show progress through the block.
- The task title, its clarified next action, and the **project outcome** it
  advances. Checklist of subtasks ticks off in place.
- One line of context: what's next after this, and when the next break is.
- Controls: pause · done early (feeds learned estimates) · +15 min (auto-shifts
  the rest of the flexible day — no guilt, the plan reflows) · switch task
  (logged, so the review can show context-switch count).
- **Focus Shield** (tips 5/15/26/84 — control your devices, kill alerts): while
  a focus session runs, Metorite's own notifications (email pings, chat,
  approvals) are *held* and released at the next break — batched, not lost.
  The shield state is visible ("6 held · released at your break"), which is the
  honest version of Do-Not-Disturb: nothing is missed, everything is deferred.
  Full-screen by design; single-theme ultra-dim "quiet" mode.
  *(Update 2026-08-03: **the hold/release primitive genuinely does not exist.**
  `grep -rniE "focus_shield|focusShield|notification_hold|hold_notifications"`
  over `*.ts`/`*.tsx`/`*.py` returns **zero hits** repo-wide. So the Shield is
  two pieces of work, not one: (a) a notification hold/release primitive on the
  platform's own notification surface, and (b) the Focus-Mode UI that arms it.
  **Both are AGENT-SAFE** — this touches only Metorite's own surfaces,
  needs no OS/browser permission, no external credential and no deploy gate.
  It is blocked on being **specced**, not on an owner action; the earlier
  "blocked on a platform primitive" note overstated it. Neither piece has a
  done-when yet — write one before dispatch.)*
- **Capture without leaving** (tips 20/22/87 — swirling-thoughts problem): the
  existing QuickCapture hotkey (`C`) opens a minimal capture drawer *inside*
  Focus Mode — the stray thought goes to the GTD inbox and the timer never
  stops. Closing the open loop is one keystroke; triaging waits for later.
- **Ambient sound** (tip 21): optional white-noise/rain loop and tick sounds,
  off by default, remembered per user.
- Ending a block → micro-transition: "Break for 5?" with break menu, then
  auto-advance to the next block.
Why it stands out: Motion/Reclaim auto-schedule but dump you back into a grid;
Sunsama has a timer but not a *place*. A first-class execution room, fed by an
AI planner, is the unclaimed spot.

### 4.2 Daily rituals — Startup & Shutdown
- **Startup (morning, auto-offered once per day):** 3 guided steps —
  (1) optional 1-min breathe, (2) review carry-forwards + calendar risk
  ("Thursday is slammed"), (3) confirm the **One Thing** + accept the AI plan.
- **Shutdown:** existing review + (4) "seed tomorrow": pick up to 3 candidates
  for tomorrow's plan; planner pre-loads them next morning. Ends with an
  explicit "day closed" state — permission to stop (the Zeigarnik release).
- Both are recurring ritual blocks on the grid (visible, skippable, streaked).

### 4.3 Leverage lens + the One Thing (80/20 made visible)
- Leveraged blocks get a gold left-edge + subtle glow; important-not-leveraged
  neutral; busywork intentionally muted.
- **One Thing:** the startup ritual asks "if only one thing gets done today…" —
  that block gets a ★ and the planner schedules it in the first peak energy
  window, protected (planner never books over it, replan moves it last).
- **Leverage meter** next to the capacity meter: "2.5h / 6h booked on leveraged
  work". The end-of-day review reports the ratio and its weekly trend.

### 4.4 Batch blocks
- Rail groups schedulable micro-tasks by context; "Batch 4 @calls (45m)" is one
  drag. On the grid it's a single block with an internal checklist; in Focus
  Mode it plays as a rapid-fire queue (done → next, satisfying).
- Planner batches automatically and *labels* the block as a batch.

### 4.5 Gap Filler — "you have 22 minutes"
- Tap any free gap (or the Now bar when nothing is scheduled): "22 min until
  your 3:00 — here's what fits": 2-minute tasks first, then short next actions
  filtered by current energy (reuses Engage's exact matching logic).
- One tap schedules it *now* and drops straight into Focus Mode.

### 4.6 Breaks & recovery as first-class citizens
- Packer rule: no more than N focus-minutes without a break (default 90 → 10);
  lunch window protected by default. **✅ SHIPPED 2026-07-23** (`80722e17`,
  migration `97_gtd_planning_prefs.sql`) — but as *geometry*: the packer widens
  the buffer behind the tipping block. The break is a gap, not a row.
- Break blocks have types (walk · stretch · breathe · coffee) with tiny guided
  timers; skipping is one tap (tracked, gently reported in review).
- Buffers remain for meeting decompression; breaks are for recovery.

### 4.7 Outcome ribbon + the Top-5 constraint
- Blocks show a truncated outcome tag; the day header shows the distinct
  outcomes today advances. Week view rolls up "outcome coverage" — a project
  starved for ≥7 days gets flagged in the weekly review.
- **Top-5 outcomes** (tip 97, Buffett's rule): the Horizons view (currently a
  "soon" placeholder in the sidebar) becomes the place you pick the ≤5 active
  outcomes that matter. The planner *favors* their tasks, the leverage meter
  counts work on them as leveraged-by-association, and the weekly review asks
  the uncomfortable question: "4h went to outcomes not in your five — demote
  the work or promote the outcome?" Everything else is an avoid-list, which is
  also the calendar's institutional way of **saying no** (tip 3): the planner
  declines to schedule over capacity and tells you *what it declined and why*.

> **⚠️ OWNERSHIP COLLISION — recorded 2026-08-03, unresolved.** "Top-5 outcomes
> (Horizons build-out)" is carried here (F3, §4.7) **and** in `work_plan.md`'s
> WS-18 row (Tasks Phase 3), where the 2026-08-02 audit declared it
> **NO-GO and MIS-ASSIGNED** — no acceptance criterion exists anywhere,
> `gtd_horizons` (present since migration 48) has **no link column** to items or
> projects, and `task_manager_app.md` puts Horizons in *Phase 4*, not 3. Two
> rows gesture at Horizons and **neither owns it**. Resolving this needs a
> single-owner decision in `work_plan.md` §4 (the single-owner registry) — it is
> deliberately *not* resolved here. Until it is, **no agent should dispatch
> Top-5 outcomes from either doc.**

### 4.8 AI task breakdown on drop
- Dropping a task with estimate >90m (or none + big title) prompts: "Split into
  sessions?" → AI proposes subtasks/sessions with estimates; accepts as
  multiple blocks (needs multi-block, §5).

### 4.9 Today timeline (mobile Do view)
- On phones, day view defaults to a vertical *agenda journey* (done ✓ above the
  now-marker, upcoming below, breaks as small beads) instead of the hour grid.
  Grid stays one toggle away. Execution-first ergonomics for the surface where
  drag-and-drop is weakest.

### 4.10 Email windows — the inbox gets an appointment
Tips 10/11/83/91/98 all say the same thing: email (and social/chat) is checked
compulsively unless it has *scheduled* time. Metorite owns the email app,
so this can be real, not aspirational:
- A recurring **Email window** batch block (default 2×/day, e.g. 11:30 + 16:00)
  is the sanctioned time to process mail. Outside it, the Focus Shield holds
  email notifications (§4.1).
- Inside the window, the block deep-links into the email app's triage; the
  existing email→task capture (`TaskCaptureModal`, `origin.emailId`) already
  turns "this needs real work" into a GTD item — which the tip-98 rule then
  routes to *tomorrow's* plan seed by default, not into today's focus.
- The end-of-day review counts email time honestly ("52m in email · plan was
  40m"), the same planned-vs-actual treatment as any block.

### 4.11 Waiting-on chase — the delegation loop on the calendar
The app already has WAITING items, `waitingOn` people, `delegatedAt` stamps and
a delegate suggestion in the priority matrix (tips 33/88/93). What's missing is
*when chasing happens*. A small recurring **Chase block** (10–15m, 2–3×/week)
auto-fills with WAITING items sorted by age and deadline; each row is
one-tap "nudge" (drafts the follow-up via the email app) / "got it" (marks
received) / "escalate". Delegation without follow-up is abdication; this makes
follow-up a scheduled habit instead of a guilty memory.

### 4.12 Foundations this unlocks (already spec'd as P5)
`gtd_time_blocks` (multi-block tasks, recurring blocks, break/ritual/external
kinds), ~~ideal-week templates~~ (**shipped 2026-07-23** — §7 F3), external
calendar sync (**OWNER-GATE**, §9.11). The features above are
the *reason* to now build that table — and it is **four PRs, not one**: see the
slice plan in §9.1. External sync (P4) is also what makes
timeboxing **transparent** (tip 1's shared-calendar clause) — colleagues see
the block, not the task detail — and what lets the packer respect commutes,
meetings and travel buffers (tips 16/48).

---

## 4b. Cross-check against the "100 tips" list

The user-supplied tips list, mapped. Tips that changed this spec are bold;
the rest either confirm existing design or are consciously out of scope.

| Tips | Theme | Where it lands |
|---|---|---|
| 1, 68, 19 | Timebox into a (shared) calendar; scheduled > listed; plan the week around non-negotiables | The core thesis (§2); weekly planning joins the ritual family; shared visibility via P4 sync (§4.12) |
| 2, 32, 86 | Prioritize ruthlessly; effective > efficient; hard stuff first | Existing 8-cell matrix + leverage lens (§4.3); "hard stuff first" = One Thing in the first peak window; effectiveness is *the* leverage-meter argument |
| **3** | Say no | Capacity refusal made legible: the planner reports what it declined and why (§4.7) |
| **5, 15, 26, 84** | Control devices, kill alerts, avoid visual distraction | **Focus Shield** — notifications held during focus, batch-released at breaks (§4.1) |
| 4, 6, 36, 96 | Move, short breaks, long lunch, scheduled decompression | Typed break blocks (walk/stretch/breathe), lunch protection, max-focus-run rule (§4.6) |
| 9 | 2-minute rule (batch the small stuff, don't mix with deep work) | Gap Filler + the 2-minute pile, never interleaved into focus blocks (§4.5) |
| **10, 11, 83, 91, 98** | Scheduled email/social time; inbox ≠ to-do list; emails → tomorrow's plan | **Email windows** (§4.10) — real because the email app is in-house |
| 13, 25, 52 | Know thyself; biological prime time | Energy windows exist; learned time-of-day heuristics close the loop (§5 telemetry) |
| 14, 61 | Breathe, meditate, be present | Startup ritual's breathe step; "breathe" break type; recurring meditation block (§4.2, §4.6) |
| 16, 28, 30, 48, 63 | Meeting hygiene | Mostly out of scope until P4 sync; then meeting-aware buffers + default-shorter-slot suggestions |
| **20, 22, 87** | Single-task; write it down; close open loops | Focus Mode is single-tasking as architecture; **capture-drawer inside Focus Mode** (§4.1); shutdown's "close the day" |
| **21** | Sound & music | Ambient sound option in Focus Mode (§4.1) |
| 23, 56 | Break tasks down; just start | AI breakdown-on-drop (§4.8); Gap Filler's "start the pile" = zero-ceremony starts |
| 24, 31, 97 | 80/20; focus on outcomes; **Buffett's five goals** | Leverage lens + outcome ribbon + **Top-5 outcomes in Horizons** (§4.3, §4.7) |
| 29 | Batch similar tasks | Batch blocks (§4.4) |
| **33, 88, 93** | Delegate; waiting-on list; set deadlines | **Waiting-on chase block** (§4.11) on top of existing WAITING/delegation machinery |
| 37, 41, 74 | Time yourself; flow; rituals | Focus/flow timers + actuals (exist); Startup/Shutdown rituals (§4.2) |
| 44, 94 | Public commitment; accountability | Lightweight: shared-calendar visibility (P4); a future "today's plan" share is noted, not designed |
| 45, 73, 79 | Celebrate, reward, gamify | Done tally, One-Thing verdict, ritual streaks — deliberately gentle, no dark-pattern gamification |
| 51, 65, 70 | Reclaim lost pockets of time | Gap Filler is exactly this (§4.5) |
| 53 | Protected time for yourself | "Protected" is a block property, not lunch-only — recharge blocks the planner won't touch |
| 62, 76 | Systemise; personal agile | The AI planner + rollover *is* the system; weekly ritual ≈ a personal sprint boundary |
| 8, 12, 27, 42, 43, 50, 64, 80, 92, 95, 99, 100 | Diet, sleep, hydration, desk, clothing, etc. | Out of scope — a work calendar shouldn't nag about chewing gum; the break menu's walk/hydrate types are as far as we go |
| 17, 34 | Site blockers, ignore the news | Out of scope (OS/browser territory); the Focus Shield covers our own surfaces only |

## 4c. Ecosystem fit — every feature has an existing home

No feature above is an island; each plugs into a surface that already exists:

| New feature | Existing surface it builds on |
|---|---|
| Focus Mode | `TaskFocusModal` (detail card), Now/Next bar's `actualStart/End` focus timer, `openFocus` store action |
| Focus Shield | Metorite notification/approvals surface (holds + batch-release); email app's unread state |
| Capture-in-focus | `QuickCapture` + the global `C` hotkey in `page.tsx` — reused, not rebuilt |
| Gap Filler | `EngageView`'s energy/time/context matching + `isTwoMinute` flag + Engage's `TIME_OPTS` |
| Leverage lens / meter | `leveraged`/`important` flags + `priority.ts` matrix — display-only change on the grid |
| One Thing | planner's existing rank + `firstFreeSlot`; a per-day setting; protected = `flexible:false` semantics |
| Batch blocks | `GtdContext` (@calls…) + planner's batching instruction; needs `gtd_time_blocks` + members |
| Breaks / rituals | packer's `buffer_mins` seam + settings popover; needs block kinds |
| Startup ritual | `PlanDayPanel` (plan mode) + carry-forward = rollover banner logic, re-sequenced as steps |
| Shutdown | `EndOfDayReview` + `apiEstimateStats` — extended with leverage ratio + tomorrow seed |
| Email windows | email app triage + `TaskCaptureModal` (`origin.emailId` linkage already lands in the GTD inbox) |
| Waiting-on chase | WAITING disposition, `waitingOn`/`delegatedAt`, `DelegateDialog`, email app for nudge drafts |
| Top-5 outcomes | `GtdProject.outcome/purpose/areaId` + the Horizons sidebar placeholder (`soon: true`) |
| Outcome ribbon | `projectId` → project outcome — display-only |
| AI breakdown | subtasks (`parentItemId`/`subtaskCount`) + clarify AI (`clarify.ts`) |
| Mobile timeline | mobile single-pane flow + `ScheduleSheet`; grid stays as the toggle |

## 5. Data model deltas

- **`gtd_time_blocks`** (promote from spec §3 phase-2): `id, item_id NULLABLE,
  start, end, kind('task'|'batch'|'break'|'ritual'|'external'), flexible,
  actual_start, actual_end, source, external_event_id, recurrence_rule`.
  `item_id` nullable because breaks/rituals aren't tasks. Batch blocks join to
  members via `gtd_block_members(block_id, item_id, done_at)`.
  *(Update 2026-08-01 (doc-truth pass), re-verified 2026-08-03: this column set
  is **CANONICAL** for `gtd_time_blocks`. The table is specified in three places
  with different shapes — `calendar_timeboxing.md` §3, here, and the comment at
  `infra/postgres/76_gtd_scheduling.sql:14` — the other two now defer here.
  The table is still unbuilt: `grep -rl gtd_time_blocks` over `*.sql`/`*.py`/
  `*.ts` matches exactly one file, the comment in `76_gtd_scheduling.sql`.
  **Do not write an absolute migration number into this spec** — find the next
  free number by listing `infra/postgres/` at build time.)*
  *(Update 2026-08-03: **the "non-breaking swap" claim in
  `calendar_timeboxing.md` §3 and in `76_gtd_scheduling.sql:14` is FALSE.**
  There is no `TimeBlock[]` seam: `blocksForDay(items, day)` in
  `workbench/control_plane/src/app/tasks/lib/scheduling.ts` *projects* blocks
  out of `gtd_items.scheduledStart/scheduledEnd`, and every mutation goes
  through `applySchedule(…{scheduledStart, scheduledEnd})`. Measured blast
  radius on 2026-08-03: **17 files under
  `workbench/control_plane/src/app/tasks/` reference
  `scheduledStart|blocksForDay|applySchedule`** (`lib/scheduling.ts`,
  `lib/scheduling.test.ts`, `lib/types.ts`, `lib/api.ts`, `lib/taskStore.ts`,
  `lib/taskAssistantPersona.ts`, `components/CalendarView.tsx`,
  `components/FocusMode.tsx`, `components/SchedulePopup.tsx`,
  `components/StartupRitual.tsx`,
  `components/calendar/{TimeGrid,MonthGrid,NowNextBar,ScheduleSheet,EndOfDayReview,PlanDayPanel}.tsx`,
  `components/calendar/shared.ts`) plus **3 gateway modules**
  (`apps/services/gateway/gateway/routes/tasks/{calendar,core,items}.py`),
  `apps/skills/skill-task-gtd/skill_task_gtd/core.py`, and the tool
  registration in `apps/agents/agent-task-manager/agents.py`. This is a
  multi-PR migration, not a swap — see the slice plan in §9.)*
- **`gtd_items`**: no change needed beyond what exists (leveraged, isTwoMinute,
  energy, estimates, actuals all present) — the redesign is mostly *surfacing*
  captured data.
- **Settings**: `pomodoroWorkMins/BreakMins`, `maxFocusRunMins`, `lunchWindow`,
  `oneThingId (per-day)`, ritual toggles, break-type prefs, email-window
  schedule, focus-shield on/off, ambient-sound choice, top-5 outcome ids.
- **New telemetry**: focus sessions (cycles, switches, completed-early/late) →
  powers review stats + learned time-of-day heuristics.

## 6. Why this stands out vs Motion / Sunsama / Akiflow / Reclaim

- **Motion/Reclaim**: world-class auto-scheduling, zero execution experience,
  zero philosophy. We match the planner and add the room you work in.
- **Sunsama**: owns the ritual niche (calm planning) but is manual and
  meeting-centric; our rituals are AI-accelerated (5 min, not 20) and the
  80/20 + outcome layer is absent there.
- **Akiflow**: fast command-bar timeboxing, but tasks are still the center; the
  calendar is a target, not a home.
- **Unique combination here**: AI planner ＋ execution room ＋ leverage/outcome
  visibility ＋ the whole thing already living inside the same brain that reads
  your email, tasks and org — capture-to-calendar with zero re-entry.

## 7. Suggested phasing (for the "what do we build" discussion)

- **F0 (no schema change, ~1 sprint):** Leverage lens + One Thing ＋ leverage
  meter; Gap Filler; outcome tag on blocks; Startup/Shutdown flows reusing the
  existing plan + review modals.
- **F1:** Focus Mode (Pomodoro/flow, subtask checklist, +15 reflow, capture-in-
  focus via the existing QuickCapture, ambient sound) — timer state is
  client-side; actuals API already exists. **SHIPPED 2026-07-22.**
  Focus Shield slipped to F2 — see the §4.1 note: the hold/release primitive
  does not exist, but it is **AGENT-SAFE once specced**, not owner-gated.
- **F2:** `gtd_time_blocks` + typed break blocks + batch blocks + recurring
  ritual blocks + Email windows + Waiting-on chase block + Focus Shield.
  ~~breaks in the packer~~ — **SHIPPED 2026-07-23** (`80722e17`, migration
  `97_gtd_planning_prefs.sql`; the commit message's "mig 93" is wrong). What
  shipped is *break geometry*: the packer widens the buffer after
  `max_focus_run_mins` of continuous focus and protects a lunch window. What
  F2 still owes is *typed break rows* — a break you can see on the grid, skip,
  and count in the review — which needs block kinds, i.e. `gtd_time_blocks`.
- **F3:** ~~ideal-week templates~~ (**SUBSTANTIALLY SHIPPED 2026-07-23** —
  migration `98_gtd_day_templates.sql` (`gtd_settings.day_templates`), the
  settings API round-trip
  (`apps/services/gateway/gateway/routes/tasks/settings.py` — model field,
  patch field, `_day_templates` normaliser, the write path's JSON dump), the
  editor in
  `workbench/control_plane/src/app/tasks/components/calendar/CalendarSettings.tsx`,
  the grid render via `TimeGrid.tsx` + `calendar/shared.ts`, and the packer
  honouring them — `kind='block'` windows become busy time, `kind='focus'`
  windows bias matching energy via `_THEME_ENERGY`
  (`.../routes/tasks/calendar.py` `_expand_templates`); covered by
  `tests/unit/test_calendar_planner.py::test_block_template_is_busy_focus_template_is_not`
  and `::test_template_day_of_week_filter_skips_other_days`). **Re-scoped to
  the named gap in §9** — do not carry "ideal week" as an unbuilt F3 item.),
  Top-5 outcomes (Horizons build-out — **see the ownership-collision warning in
  §4.7; do not dispatch**), mobile timeline view, AI breakdown-on-drop, weekly
  review surface, external sync (**OWNER-GATE**, see §9).

## 8. Mockups

`project-docs/specs/mockups/calendar_focus_os.html` — self-contained HTML
(open in any browser) showing: Plan mode grid with leverage lens / batch /
break / ritual blocks + meters; Focus Mode; Gap Filler; Startup ritual;
Shutdown review; mobile Today timeline. Visual language matches the control
plane's dark theme (cyan primary, gold = leverage).

## 9. Acceptance & verification for F2/F3 open items

*(Added 2026-08-01; **rewritten 2026-08-03 after verifying every clause against
the code.** The 2026-08-01 pass wrote a `gtd_time_blocks` done-when whose first
two clauses were **already green against shipped code** — an implementer could
have "passed" it by creating an unused table. Those clauses are deleted below.)*

### 9.0 How to read this section

Every open item carries a label:

- **AGENT-SAFE** — an independent agent can build it end to end: no credential,
  no flag flip, no deploy, no reach outside this repo.
- **OWNER-GATE** — needs an owner action named in `work_plan.md` §6 before the
  work can even be verified. Do not dispatch; report and stop.

Two standing constraints for anyone implementing from this section:

1. **Never write an absolute future migration number** into a spec, a commit
   message or a code comment. Find the next free number by listing
   `infra/postgres/` at build time. This corpus already carries the disease:
   `80722e17`'s message says "mig 93" (real: 97), and
   `apps/services/gateway/gateway/routes/tasks/calendar.py`'s `_planning_prefs`
   / `_day_templates` docstrings still say "migration 93" / "migration 94"
   (real: 97 / 98).
2. **Paths are repo-root-relative and fully qualified** —
   `workbench/control_plane/src/app/tasks/…` for UI,
   `apps/services/gateway/gateway/routes/tasks/…` for the gateway,
   `apps/skills/skill-task-gtd/…`, `apps/agents/agent-task-manager/…`,
   `infra/postgres/…` for migrations. Earlier revisions of this section wrote
   `app/tasks/lib/focusPrefs.ts` and `routes/tasks/calendar.py`, both one tree
   level short.

### 9.1 F2 `gtd_time_blocks` — 4 slices, not one PR · **AGENT-SAFE**

**The "non-breaking swap" claim is false.** `calendar_timeboxing.md` §3 and the
comment at `infra/postgres/76_gtd_scheduling.sql:14` both assert the grid is
written against a `TimeBlock[]` abstraction so promoting to a table is a drop-in
swap. It is not: blocks are *projected* from `gtd_items.scheduled_start/end` by
`blocksForDay()` and *mutated* through `applySchedule({scheduledStart,
scheduledEnd})`. The measured blast radius (2026-08-03) is in §5. Anyone who
plans this as one PR is planning to break the calendar.

**Slices — each independently shippable and reviewable:**

| # | Slice | Shape |
|---|---|---|
| S1 | **Schema + API, dual-write** | Migration (next free number at build time) creates `gtd_time_blocks` + `gtd_block_members` per §5. `PATCH /tasks/items/{id}` scheduling and `GET /tasks/calendar` write/read **both** the columns and the table; the columns stay authoritative. No UI change. |
| S2 | **Client swap** | `blocksForDay()` reads blocks from the API instead of projecting from item columns; `applySchedule` targets block ids. Table becomes authoritative, columns become a mirror. All 17 touching files move together. |
| S3 | **Packer + tool cutover** | `_compute_day_plan`, rollover, replan, `apps/skills/skill-task-gtd/skill_task_gtd/core.py` (`gtd_schedule`/`gtd_unschedule`/`gtd_list_schedule`) and `apps/agents/agent-task-manager/agents.py` emit blocks. Item columns dropped from the write path. |
| S4 | **Kinds** | `kind` values `break` / `ritual` / `batch` / `external` + `gtd_block_members` become real: the packer emits typed break rows instead of widened buffers, batch blocks carry members, ritual blocks recur. |

**Done when — every clause must fail against today's code:**

*(Deleted from the 2026-08-01 version because they were already true:
"blocks persist server-side / survive reload / appear on a second device" —
`scheduled_start/scheduled_end` have been `gtd_items` columns since
`76_gtd_scheduling.sql` and have always been server-side; and "the One Thing and
tomorrow-seeds move off localStorage" — done by `92_gtd_day_state.sql` +
`GET/PUT /tasks/calendar/day-state`.)*

1. **One task holds two blocks on the same day and both render.** Split a 3h
   task into 09:00–10:30 and 14:00–15:30; both appear on the day grid, both
   count once each in the capacity meter, and completing the task closes both.
   *(Impossible today: one row, one `scheduled_start`.)*
2. **A `kind='break'` row inserted by the packer is visible on the grid and
   excluded from the leverage meter.** "Plan my day" with
   `max_focus_run_mins=90` produces a break the user can see, skip, and that the
   end-of-day review counts — and it contributes **zero** minutes to both the
   booked-focus meter and the leverage meter. *(Today the break is a widened
   buffer: invisible, uncountable, unskippable.)*
3. **A batch block with 3 `gtd_block_members` ticks members off
   independently of the parent.** Drag "Batch 3 @calls" onto the grid; the block
   shows an internal checklist; ticking one member marks that `gtd_item` done
   and leaves the block and the other two open; the block closes when the last
   member does.
4. **Residual local state is gone.** The only remaining localStorage residue in
   `workbench/control_plane/src/app/tasks/lib/focusPrefs.ts` — the **ritual
   stamps** (`startupDoneOn`, `startupStreak`, `streakStampedOn`, `dayClosedOn`)
   and **`timerMode`** — is server-backed, so the startup streak survives a
   different browser. *(One Thing + seeds already are; do not re-do them.)*
   This clause is satisfiable **independently of S1–S4** and may ship first as
   its own small PR on `gtd_day_state`.

### 9.2 F2 Email windows · **AGENT-SAFE**

Foundation already shipped 2026-07-23: `gtd_settings.day_templates`
(`98_gtd_day_templates.sql`) already reserves a recurring window, and
`_THEME_ENERGY` in `apps/services/gateway/gateway/routes/tasks/calendar.py`
already recognises `theme` values `"email"` and `"inbox"`. Missing: the
email-app deep link, the shield hold, and the review accounting.

**Done when:**

1. A recurring Email window renders as a real block on the grid (not merely a
   tinted template band).
2. The block deep-links into the email app's triage surface.
3. Email-captured tasks (`origin.emailId`, via `TaskCaptureModal`) route to
   **tomorrow's** plan seed by default, not into today's focus.
4. The end-of-day review reports email planned-vs-actual like any block —
   **see the decision below for what "email time" means.**

> **DECISION (agent-proposed 2026-08-03, owner may overrule) — what counts as
> email time.** Clause 4 was untestable because "email time" was never defined.
> Proposed definition, chosen because both halves already exist in the data:
> - **Planned email minutes** = the total minutes of that local day's
>   `day_templates` entries whose `theme` normalises to `"email"` or `"inbox"`
>   (the `_THEME_ENERGY` mapping is the existing normaliser — reuse it, do not
>   add a second one).
> - **Actual email minutes** = summed `actual_start`→`actual_end` (migration
>   `80_gtd_actuals.sql`, stamped by Focus Mode) of every block whose item
>   carries `origin.emailId`, **plus** any block whose interval falls inside a
>   planned email window regardless of origin.
>
> Rejected alternative: counting time spent in the email app itself. It would
> need new client telemetry, and it measures the app rather than the
> commitment — the review's whole point is planned-vs-actual against a block.
> **If the owner prefers app-time, clause 4 changes shape and this slice grows
> a telemetry sub-slice.**

### 9.3 F2 Batch blocks · **AGENT-SAFE** · depends on §9.1 S4

**Done when:** the unscheduled rail groups schedulable micro-tasks by
`GtdContext` and offers "Batch 4 @calls (45m)" as one drag; the resulting grid
block is a single `kind='batch'` row with `gtd_block_members`; Focus Mode plays
it as a rapid-fire queue (done → next); and the AI planner, when it batches,
emits a batch block rather than n adjacent task blocks.

### 9.4 F2 Waiting-on chase block · **AGENT-SAFE** *(nudge SENDING is OWNER-GATE)*

**Done when:** a recurring `kind='ritual'` Chase block auto-fills with WAITING
items sorted by age then deadline, reusing the shipped
`workbench/control_plane/src/app/tasks/lib/waiting.ts` predicates (Waiting-For
surfacing landed 2026-08-02 under WS-18 — **do not rebuild it**); each row
offers nudge / "got it" (marks received) / escalate; and "got it" clears the
open `gtd_waiting` row.

> **OWNER-GATE inside this slice:** *drafting and sending* the follow-up email
> goes through a real mail account. The chase surface, the ordering, and the
> "got it"/escalate paths are all agent-safe; the nudge **send** is not. Build
> the surface with the nudge action stubbed behind the existing confirm-before-
> send gate; do not wire an outbound send.

### 9.5 F2 Focus Shield · **AGENT-SAFE once specced** · currently unspecced

The primitive does not exist (§4.1: zero grep hits repo-wide). It needs no
external access, so it is **not** owner-gated — it is blocked on a design.
Before dispatch, someone must spec: where held notifications queue, what
"release" means for each notification kind, and what happens to a hold if the
session is abandoned. **Do not dispatch on §4.1 prose alone.**

**Done when (draft, needs the design above first):** starting a focus session
holds Metorite's own notifications; the Focus Mode header shows a live
count ("6 held · released at your break"); ending the session or reaching a
break releases them in one batch; nothing is dropped; and a crash or tab close
releases the hold rather than stranding it.

### 9.6 F3 Ideal week — **SUBSTANTIALLY SHIPPED**, re-scoped to one gap · **AGENT-SAFE**

Do not carry "ideal week" as unbuilt work — see the §7 F3 note for the shipped
inventory (migration `98_gtd_day_templates.sql`, settings round-trip, editor,
grid render, packer honouring, 2 unit tests). **Recommendation: strike
"ideal week" from the WS-21 row title** and carry only the named gap.

**Remaining gap — done when:** a `kind='focus'` themed window that goes unused
is visible as such (today an unfilled focus window is indistinguishable from
empty time), and the weekly view rolls up template adherence — "your Mon-AM deep
work window took admin work 3 weeks running". Everything else about ideal week
is done.

### 9.7 F3 Mobile timeline · **AGENT-SAFE**

**Done when:** on a viewport under the `md` breakpoint the day view defaults to
a vertical agenda journey (done above the now-marker, upcoming below, breaks as
beads), the hour grid stays one toggle away, and the toggle persists like the
existing list/board toggle.

### 9.8 F3 AI breakdown-on-drop · **AGENT-SAFE** · depends on §9.1 (multi-block)

**Done when:** dropping a task with `time_estimate_mins > 90` (or no estimate
and a long title) offers "split into sessions?"; accepting creates **multiple
blocks** for the one task with per-session estimates; declining is remembered
for that item.
> **EVAL-LOCKED:** `propose()` / `propose_with_llm()` in
> `apps/services/gateway/gateway/routes/tasks/ai.py` are locked by the golden
> eval — do not mutate them. Add a new function.

### 9.9 F3 Weekly review surface · **AGENT-SAFE, but NOT owned here**

`work_plan.md` WS-18 owns the GTD Weekly Review and its 2026-08-02 audit ruled
it **NO-GO** (`task_manager_app.md` §9.2 is a bare checkbox; `gtd_reviews.summary`
is untyped JSONB). The calendar's contribution is only the *recurring ritual
block + planned-vs-actual/roll-over/focus-hours rollup*. **Do not dispatch a
weekly review from this doc** — it must follow WS-18's JSON contract once that
exists.

### 9.10 F3 Top-5 outcomes (Horizons) · **DO NOT DISPATCH**

See the ownership-collision warning in §4.7. Needs a `work_plan.md` §4
single-owner decision.

### 9.11 F3 External sync · **OWNER-GATE**

Canonical done-when: `calendar_timeboxing.md` §13 (P4). Verified state
2026-08-03: `calendar_accounts` **does not exist** (no migration, no code —
the only matches are three comments in
`apps/services/gateway/gateway/routes/tasks/calendar.py`),
`GET /tasks/calendar/accounts` returns `[]` (`calendar.py:44-50`), and
`POST /tasks/calendar/sync` raises **501** (`calendar.py:53-64`, the raise at
`:60-64`).

> **OWNER-GATE — credential requirement:** clause 1 is *"a `calendar_accounts`
> row can be created through a real OAuth connect flow"*. That requires
> **Google Calendar and/or Microsoft Graph OAuth client credentials
> (client id + secret + redirect URI) provisioned on the VPS** and registered in
> the Integration Registry. An agent cannot obtain, install or verify these.
> **This gate is currently unregistered in `work_plan.md` §6 — register it.**

### 9.12 Verify

```
cd workbench/control_plane && npx tsc --noEmit && npm test    # vitest
```

```
uv run pytest tests/unit/test_calendar_planner.py \
              tests/unit/test_email_calendar_context.py \
              tests/unit/test_tasks_gtd.py
```

**Name the files. Never run `pytest tests/unit -k calendar`** (the form this
section carried until 2026-08-03) — `-k` still *collects* the whole directory,
and whole-directory collection hangs on the Windows dev box. The named-file
form is the only safe one.

Measured 2026-08-03 on this branch:
- the two calendar files alone → **28 passed in 1.16s**;
- all three files → **157 passed in 166.79s** (`test_tasks_gtd.py` is the slow
  one — budget ~3 minutes, it is not hung).

Coverage: `test_calendar_planner.py` = packer geometry (free intervals, buffers,
energy windows, lunch carve-out, day-template block/focus windows, weekday
filter); `test_email_calendar_context.py` = the email-side calendar context;
`test_tasks_gtd.py` = the GTD API surface.

### 9.13 Recorded, not fixed

- **The reminders/notifications deferral went invisible.** `calendar_ux_review.md`
  §"P1 — mobile & reminders" and its ranked list item 4 carry *"block
  reminders/notifications when a block starts… without reminders, blocks are
  ignored"*. The word "reminder" appears in **no other calendar spec** and in no
  `work_plan.md` row, so the deferral was silently dropped rather than decided.
  It is a real open item and it shares a surface with the Focus Shield (§9.5) —
  both need the same notification primitive. Whoever specs the Shield should
  decide whether reminders ride along or are explicitly killed.
- **This spec family has four docs, not two.** `calendar_focus_os.md`,
  `calendar_timeboxing.md`, `calendar_ai_review.md` and `calendar_ux_review.md`.
  The WS-21 row names two; `calendar_ai_review.md` is cited by three migration
  headers (`92`, `97`, `98`) yet is referenced by no other spec, no `work_plan.md`
  row and no `project-docs/AGENTS.md` index entry — and **the specs index in
  `project-docs/AGENTS.md` has no calendar row at all.** Cross-deferral
  between focus_os and timeboxing is clean (§5 here is canonical for
  `gtd_time_blocks`; `calendar_timeboxing.md` §13 is canonical for P4); the other
  two docs are unregistered.

## 10. Calendar becomes its own app (D54) — 2026-08-24

**Status:** owner directive 2026-08-24, recorded as **D54** in `work_plan.md` §3.
Board row **WS-39**, slice **S2**. This section owns *where the calendar lives*;
everything above it still owns *what the calendar does*.

### 10.1 What the owner asked for

> *"It might make sense to also remove calendar from the tasks app and make it into
> an app by itself under personal center."*

### 10.2 The change, precisely

| | Before | After |
|---|---|---|
| Route | ⚠️ **none** — a `ViewKey` inside `/tasks` | **`/calendar`**, a real route |
| Nav | a row in the Tasks sidebar | a pane in **Personal Center** (`src/lib/nav.ts`) |
| Gate | `feature:tasks` | **`feature:tasks`** — unchanged, see below |
| Launch status | live by inheritance | **`live`**, explicitly |
| Owner of behaviour | WS-21 | **still WS-21** |

⚠️ **Correction, measured while building S2:** this section first said the route
was `/tasks/calendar`. **There was no such route.** The calendar was a *view
mode* — `selectedView === "calendar"`, a `ViewKey` in the shared task store,
rendered full-width by `tasks/page.tsx`. That is why the move is a genuine
extraction rather than a rename, and why `ViewKey` still carries a `"calendar"`
member that nothing can select (its `itemsForView` and `viewQuickAdd` rules are
still real; the member is annotated in `tasks/lib/types.ts`).

🔴 **The gate stays `feature:tasks`, and this reverses what D54.1 first said.**
A new `feature:calendar` slug is **a grant nobody holds**. Minting one would
ship this app **dark to every existing member**, and un-darkening it is an
owner-gated role write (`work_plan.md` §6, the WS-24 (d) class) *on top of* a
migration that has to reach a box first. ⚠️ **Corrected 2026-08-26: this cited
H-1 for “`main` is many migrations ahead of every box”.** H-1 no longer exists
— its Check passed and the entry was deleted — and the claim is now false: the
2026-08-25 deploy reported *“0 applied, 186 already recorded”*, i.e. the box is
current with `main`. The argument is unaffected and stands on its own, which is
why the conclusion below is unchanged — a new slug is a grant nobody holds
whether or not migrations are behind. Riding the grant that already covers this
surface keeps reachability exactly as it is today: the calendar lived inside
Tasks, so everyone holding `feature:tasks` already had it. Minting
`feature:calendar` is a later, deliberate act that must ship **with** its grant
migration, not before it.

**It ships `live`, and the count fence moves with it.** `launch_surface.md` §2's live
set goes **8 → 9** and `nav.test.ts`'s assertion is updated in the same PR. That fence
exists so a pane cannot be added without someone deciding its launch status — so this
is a deliberate edit, not a test that broke. Shipping it `preview` was considered and
rejected: the calendar is reachable today inside a live app, so `preview` would
*withdraw* a capability customers already have.

**"Personal Center" is D49's section label**, not a Center projection. D49 withdrew
the Centers surface; `lib/centers.ts`, the `center.*` features and the `group:<slug>`
vocabulary are untouched.

### 10.3 ⚠️ The measurement that changes the plan

`routes/tasks/calendar.py` (68 KB, live) reads and writes:

| Table | Role | Fate |
|---|---|---|
| `gtd_items` | **the tasks themselves** — scheduling is fields on the task row | ⚠️ **re-points to `pm_tasks`** when D53's S3a lands |
| `gtd_settings` | per-member calendar preferences (migrations 77, 78) | **survives** D53's retirement (D53.6) |
| `gtd_day_state` | per-member day state | **survives** |
| `gtd_rollover_log` | roll-over audit (migration 78) | **survives** |

So the calendar is a **third lens on the same rows** — Projects is the company board,
Tasks is my list, Calendar is my time — and not a separate system with a store to
move. Its own three tables do not move at all.

**This is why S2 is sequenced before S3a and does not touch the store.** Extracting
the surface is a route + registry change with no data semantics; re-pointing the task
reads is a store change. Landing them in one diff would put a nav edit and a store
migration in the same review.

### 10.4 Scope — personal, and deliberately so

**In:** my time blocks, my scheduled work, my day plan / roll-over / shutdown ritual,
my connected external calendars (when the OAuth gate below is opened).

**Out, explicitly:** Center-wide and company calendars. They require a model for whose
blocks are legible to whom, which is a new owner decision and not an extension of D54.
Naming them out here so the next agent does not read "Calendar app" as "all calendars".

### 10.5 What does NOT change

F0/F1 as built, the leverage lens, One Thing, Gap Filler, the Startup and Shutdown
rituals, Focus Mode, the packer and its breaks, P3 roll-over, ideal week — all of it
is behaviour, all of it stays, and **WS-21 keeps owning it** (D54.6). Horizons remains
WS-21's per `work_plan.md` §4 and remains DO-NOT-DISPATCH: it still has no acceptance.

**External sync stays 🔴 OWNER-GATE** — Google Calendar / Microsoft Graph OAuth client
credentials provisioned on the box and registered in the Integration Registry. D54
does not touch that gate, and giving the calendar its own front door does not open it.

### 10.6 Acceptance — WS-39 S2 · AGENT-SAFE

**Done when:** ✅ **ALL MET — BUILT 2026-08-24.**

1. ✅ `/calendar` renders the calendar surface as a real route (`next build`
   emits `○ /calendar`), and the surface is **no longer reachable inside
   `/tasks`** — `tasks/page.tsx` contains no `CalendarView` and no
   `selectedView === "calendar"` branch. *(Amended: there was never a
   `/tasks/calendar` route to retire — see §10.2's correction. Nothing to
   redirect.)*
2. ✅ `src/lib/nav.ts` carries a Personal Center pane for `/calendar` with
   `launch: "live"`. *(Amended: gate is `feature:tasks`, per §10.2 — a new slug
   would ship the app dark.)*
3. ✅ `nav.test.ts` asserts the live set is exactly **nine** `(section, href)`
   pairs, and `launch_surface.md` §2's table lists the ninth row.
4. ✅ **The dependency runs one way.** Tasks imports nothing from
   `app/calendar/`; Calendar imports Tasks' shared **lib and store** plus
   exactly the four store-driven overlays it raises.
   ⚠️ **This clause was CORRECTED during the build.** It first read "the
   calendar components no longer import from `src/app/tasks/`", which was
   written before the coupling was measured and is **wrong**: satisfying it
   would mean either duplicating a 95 KB store — the CLAUDE.md §5 defect, and
   the re-introduction of exactly the sync D53 removes — or promoting it days
   before S3a rewrites it. Sharing the store is the *point* of D53; what must
   not happen is the dependency pointing back. The asymmetric rule is what is
   fenced.
   *(One real violation was found and fixed: `FocusMode`, which `AppShell`
   mounts **globally**, imported `fmtClock` from the calendar's `shared.ts`.
   `fmtClock` was promoted to `app/tasks/lib/utils.ts` and re-exported, so the
   five calendar call sites kept their path and no second definition exists.)*
5. ~~`feature:calendar` exists as a grantable feature and `/access` reports
   it.~~ **STRUCK** — §10.2: no new slug is minted, so there is nothing for
   `/access` to report that it does not already report for `feature:tasks`.
6. ✅ `npx tsc --noEmit` clean · `npx vitest run` **2553/2553** · `next build`
   succeeds.

**Fence (R7):** `nav.test.ts`'s count assertion (structural — it fails on *any*
undeclared pane) plus `src/app/calendar/calendarBoundary.test.ts`, which walks
every file under both app directories and asserts (a) Tasks imports nothing from
Calendar, (b) Calendar reaches only Tasks' lib/store and the four allowed
overlays, and (c) the calendar surface is gone from `tasks/page.tsx` — the
half-move where a new route is added while the old entry point still works.

### 10.7 The day planner plans the ONE store (WS-39 S3a-client slice 2)

**Built 2026-08-25.** §10.5 said the Calendar's *behaviour* does not change under
D54, and that is still true — but it turned out to be hiding something, and the
audit that found it is worth recording because the symptom would have been
invisible.

**The Calendar reads its tasks from the shared task store.** `CalendarView` takes
`items`, `projects`, `applySchedule`, `updateItem` and the rest off
`useTaskStore`, not off a list endpoint of its own. So the grid, the unscheduled
rail, every drag and every schedule edit followed the lens the moment slice 1
landed — for free, with no work in `app/calendar/` at all.

⚠️ **Except "Plan my day", and that one was the dangerous one.** The planner is a
SERVER-side computation, and `routes/tasks/calendar.py` read and ranked
`gtd_items`. Under the flag the UI would have shown `pm_*` tasks while the
planner packed rows from the retiring store: the plan comes back with blocks for
tasks the UI has never heard of, or — far more likely, since the backfill has not
run — **with no blocks at all.** A 200, an empty day, no error and no log line.

#### The shape of the fix

The planner is not duplicated and is not branched on a flag. It reads through a
**`TaskSource`** — an object answering the six reads it performs
(`scheduled_today`, `carry_forward`, `candidates`, `overdue`, `busy_window`,
`estimate_ratio`). `GTD_SOURCE` is the retiring store, moved behind the seam with
its WHERE clauses character-for-character unchanged; `LENS_SOURCE` is
`pm_tasks` + `pm_task_personal`. **The packer, the LLM ranker, the horizon
parser, the capacity arithmetic and the eviction rules do not change at all** —
that code is where this feature's behaviour lives, and a second copy of it would
diverge on the first bug fix only one of them received.

**The store is chosen by which ROUTE the client calls.** `POST
/projects/my/calendar/{plan,replan,rollover}` and `GET
/projects/my/calendar/estimate-stats` serve the lens; `/tasks/calendar/*` stays
on the old store until S3c. That is deliberate and it is the reason the whole
cutover needs exactly **one** flag: a server-side flag would be a second one, and
two flags that must agree are a mismatch waiting to be found by a user whose day
planned itself out of the wrong table.

None of the four writes anything. Each returns a `DayPlan` **proposal** the
client reviews and applies through the ordinary overlay PATCH — which is why the
apply path needed no work: slice 1 already routed it.

#### ⚠️ The semantic that nearly broke it, and the rule it produced

`gtd_items.disposition` is a stored, `NOT NULL` column. `pm_task_personal` has no
row at all until a member triages a task, so the disposition is **derived** from
the task's status and assignment (`derive_disposition`): an assigned, open,
non-backlog task is `NEXT`.

A planner that filtered on the stored column would therefore have found **nothing
to plan for any member whose company board is untriaged — which is every member,
on day one.** Again: a 200 with an empty plan.

So the lens queries **prune with the stated disposition and decide with the
effective one**. `(p.disposition IS NULL OR p.disposition = 'NEXT')` keeps
untriaged rows in the result set for Python to rule on; `derive_disposition` is
then *called*, never restated in SQL. A SQL copy of that function would be a
mirror, and mirrors go stale and then lie.

The prune can only be wrong in one direction — dropping a row it should have kept
— so `live_ws39_s3a_client2.py` drives it from both sides: an untriaged assigned
task IS a candidate (check 1), and a backlog task the member filed as `NEXT` is
too (check 3). **12/12 against PostgreSQL 16.**

#### What is still on the old store, on purpose

The **agent** planner (`/tasks/calendar/{plan,replan,rollover}-today` and
`/calendar/day-summary`). The agent surface has no browser and therefore no
client flag to read; choosing a store there needs a server-side answer, which is
the second flag this design exists to avoid. Slice 3 — H-33 carries it, the code
carries a comment at the endpoints, and
`test_calendar_task_source.py::test_the_agent_planner_is_still_on_the_old_store`
fails the day somebody routes it without reading either.

✅ **CLOSED 2026-08-25 by slice 3 — see §10.8.** The mechanism is decided
(`TASKS_LENS`, an env flag consulted through `agent_source()`), and sizing it
found two more browserless surfaces, both of which WRITE.

### 10.8 The browserless surfaces, and the second flag (WS-39 S3a-client slice 3)

**Built 2026-08-25.** §10.7 closed the browser planner and named one thing it
left open: the agent's `-today` endpoints, which have no browser and so cannot
pick a store by picking a route. Sizing that turned up **two more**, and both are
worse than the one that was known.

| Surface | Why it cannot use the client flag | |
|---|---|---|
| `/calendar/{plan,replan,rollover}-today` | called by the chat assistant | read |
| `/calendar/day-summary` | the assistant reads it before answering | read |
| the agent **apply** path (`_apply_plan_blocks`) | replays a reviewed plan server-side | **write** |
| the **nightly roll-over sweep** | runs unattended, per tenant, on a timer | **write** |

The sweep is the one that mattered. It runs every night for every customer, and
left pinned to `gtd_items` it would have gone on releasing blocks in the retiring
store after the cutover — while the members' real leftovers were never released
at all. Nothing would have failed.

⚠️ **It was missed by slice 2 for an instructive reason.** Slice 2 moved the
planner's five READS and stopped, on the argument that the browser applies a
plan through the ordinary overlay PATCH — which was true, and which is exactly
why the two server-side writers did not come up. *"The client already does it"*
is not the same claim as *"nothing else does it."*

#### The decision

**`TASKS_LENS`, an env flag on the gateway**, read at call time (the idiom
`projects/core.org_vocabularies_enabled` already uses), consulted through one
function — `agent_source()` — that every browserless surface calls. None of them
names a store.

That is a **second** flag, alongside the browser's build-time
`NEXT_PUBLIC_TASKS_LENS`, and the alternatives were weighed and rejected:

- *derive it from the data* (does this member have `pm_*` rows?) — magic, and
  wrong for exactly the member who has none yet;
- *have the browser tell the server* — the assistant has no browser to ask;
- *serve the browser's flag from the gateway* — makes `lensEnabled()` async at
  call sites that are synchronous, and fails OPEN to the old store if the fetch
  has not landed, which is a silent wrong answer at the worst moment.

**What makes two flags tolerable is not that there are only two. It is that a
disagreement is observable.** `/version` now reports `tasks_lens`,
unauthenticated, so "is this box on the lens?" is one `curl` from a laptop with
no box access — the same standard CLAUDE.md §3.8 sets for the deployed SHA. An
invisible mismatch is the thing worth refusing; a second variable in the same
`.env`, checkable by evidence, is not. `docs/TASKS_LENS.md` is the pair's
write-up and `H-34` asks the owner to add them (`.env*` is §6-gated).

#### The write half of the seam

`TaskSource` gained `apply_blocks(place, clear)`. The lens implementation is an
**UPSERT, not an UPDATE**, and that is not a stylistic choice: a member can be
handed a task they have never opened and have the assistant schedule it the same
day, so there may be no `pm_task_personal` row yet. An UPDATE would report
success and write nothing, and the plan would silently not apply. It goes through
`_upsert_personal` rather than hand-rolled SQL so the agent's writes use the same
binding and coercion path as the browser's PATCH — which is where migrations
187/188's timestamp and jsonb handling lives.

Ownership needs no `AND user_id = :uid` guard any more: the key `(task_id,
member_email)` does that job, because the row **is** the member's. Verified live
— clearing my block on a shared task leaves the other assignee's block standing
(`live_ws39_s3a_client2.py` check 15).

#### ⚠️ The fence that passed a regression before it was tightened

`test_every_browserless_surface_asks_which_store` reads the module and requires
each of the six surfaces to consult `agent_source()`. Its first draft also
accepted the presence of a `src.` call — and pinning the nightly sweep back with
`src = GTD_SOURCE` left every `src.` call in place, so the fence saw a source
variable and approved. It was caught by mutating the code and watching the test
*not* fail. **A fence that holds a bug still is worse than none**, and the second
condition (no surface may name `GTD_SOURCE` or `LENS_SOURCE` directly) is there
because of it.
---

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-21 — **Calendar F2/F3** (`gtd_time_blocks`, email windows, mobile timeline, external sync)
**State cell (as of the move):** 🟡 partial
**Narrative (verbatim):** **Re-audited 2026-08-03 → GO-NARROWED.** P3 roll-over was already shipped (released-to-unscheduled, mig 78 + `start_auto_rollover`). ~~"ideal week"~~ **struck — substantially shipped** (mig 98 + settings round-trip + editor + grid render + packer honouring + 2 unit tests); only the unused-focus-window / template-adherence gap remains (§9.6). **Breaks-in-the-packer SHIPPED 2026-07-23** (`80722e17`, mig **97**) as *packer geometry* — a widened buffer plus lunch protection, **a gap, not a `kind='break'` row**, which is exactly why F2 survives (§5 residual 4, now closed). **The 2026-08-01 acceptance was satisfiable by doing nothing** — 2 of its 3 `gtd_time_blocks` clauses were already green against shipped code; they are deleted and replaced with four that all fail today. **`gtd_time_blocks` is 4 slices, not 1 PR** (§9.1 S1–S4): the "non-breaking `TimeBlock[]` swap" claim was **FALSE** — the measured blast radius is 17 TS files + 3 gateway modules + `apps/skills/skill-task-gtd/` + `apps/agents/agent-task-manager/`. **Focus Shield is AGENT-SAFE, not owner-gated** (§9.5) — it needs a design, not a credential; do not dispatch on §4.1 prose alone. **Top-5 outcomes (Horizons) — DO NOT DISPATCH:** it collides with WS-18; §4 assigns it here, and WS-18's title keeps it struck. **Verify by naming test files — never `pytest tests/unit -k calendar`**: `-k` still collects the whole directory, and whole-directory collection hangs on the Windows box. **Dispatchable today:** §9.1 S1 · the ritual-stamp localStorage residue (§9.1 done-when 4, independently shippable) · §9.6 · §9.7. **OWNER-GATE:** external sync (§9.11 / timeboxing §13 P4) needs Google Calendar and/or Microsoft Graph OAuth client credentials provisioned on the VPS.

**Corrections applied 2026-08-09:**
- current as moved
- Horizons ownership: WS-21 owns it per §4 of the board, still DO-NOT-DISPATCH (no acceptance).
