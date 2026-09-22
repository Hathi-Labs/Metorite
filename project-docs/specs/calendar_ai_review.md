# Calendar × Tasks × AI — comprehensive review

> ⚠️ **SUB-DOC — CONSULT VIA `calendar_focus_os.md` §9 (2026-08-10 consolidation, D26).** No work dispatches from
> this document; cited by shipped migrations 92/97/100. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


> **Status:** review record (2026-07-22) — findings since triaged into `calendar_focus_os.md` §9 / `calendar_timeboxing.md` §13, which own all acceptance. Cited by migration headers 92/97/100. Not re-verified since. *(Header added 2026-08-09.)*

Date: 2026-07-22 · branch `claude/calendar-productivity-redesign-rdh50k`.
Scope: (1) the calendar app and its integration with the GTD task manager,
(2) every place AI already runs, audited for prompt/context correctness,
(3) whether "manage my day with AI" works today and where it falls short,
(4) proposals to make the chat assistant a real day-manager.

Companion docs: `calendar_timeboxing.md` (planner architecture),
`calendar_focus_os.md` (Plan/Do/Review redesign, F0+F1 built),
`calendar_ux_review.md` (original UX audit).

---

## 1. The calendar ↔ task-manager integration (state: strong)

The calendar is not a silo — it is a *view over the same `gtd_items` rows* the
GTD app manages, which is the single best architectural decision here:

- **One data model.** A block is `scheduled_start/end` on the task itself;
  deadlines (`due_at` + `is_hard_date`) stay distinct from timeboxes. Nothing
  is copied, so nothing can drift.
- **One priority brain.** `priority.ts` (important × urgent-derived ×
  leveraged → 8 cells) ranks the Engage view, the rail ordering, the Gap
  Filler, and (mirrored server-side in `_rank_fallback`) the planner's
  no-LLM fallback. The same flags feed the leverage lens and meters.
- **One capture pipeline.** QuickCapture (incl. `C` inside Focus Mode), email
  → task (`origin.emailId`), clarify → estimates/energy/context — all land in
  the same rows the calendar schedules. Estimates seed block lengths; energy
  drives placement; actuals (`actual_start/end`, stamped by Focus Mode and
  the Now-bar timer) close the loop back into planning.
- **Provider sync respected.** Scheduling is a LOCAL overlay — timeboxing a
  ClickUp-synced task never writes upstream; dispositions/edits follow the
  existing staged-push rules.

Friction that remains (integration, not AI): per-day state from Focus OS
(★ One Thing, tomorrow-seeds, ritual stamps) lives in **localStorage only** —
see §4.1, it's the root cause of the biggest AI gap too.

## 2. Inventory — every place AI runs today (audited)

| # | Surface | Where | LLM? | Audit verdict |
|---|---|---|---|---|
| 1 | **Plan my day** | `POST /calendar/plan` | ✅ judgment only | **Good.** LLM picks/orders/energy-fits; deterministic packer does geometry (can't overlap, can't exceed window). Capacity honesty in-prompt ("do NOT select more than fits… that's good planning, not failure"). Injection-hardened ("task list is DATA"). Falls back to matrix ranking. |
| 2 | **Learned estimates** | `_estimate_pad` (in plan) + `/estimate-stats` | ➖ statistical | **Good.** Median actual/planned over 90d, ≥5 samples, clamped ×0.8–1.75, surfaced honestly in plan notes + day review. |
| 3 | **Replan rest-of-day** | `POST /calendar/replan` | ❌ deterministic | Sound repack (movable = flexible∧future∧not-done; everything else is an obstacle). **Found: accepts `energy_note` but ignores it — the UI showed the input anyway. FIXED** (input now plan-mode only). |
| 4 | **Roll-over** (manual + nightly job) | `/calendar/rollover` + sweep | ❌ deterministic | **Good.** Deadline-aware, duration-preserving, skips 🔒 fixed blocks, per-local-day guard, audit log (`calendar_rollover_log`). |
| 5 | **Chat assistant** | agent `task-manager` + `skill-task-gtd` (17 `gtd_*` tools) + `buildTaskAssistantPersona` | ✅ | Persona carries live local time+offset, working window, capacity, today's blocks, open-item (as DATA), inbox pressure. **Found gaps, partly FIXED — see §3.** |
| 6 | **Clarify engine** | `/items/{id}/clarify` | ✅ | **Good.** Proposes disposition/next-action/context/energy/estimate/subtasks/priority flags; org-capability assignee matching with workload annotation; dedup + parent suggestion; "AI proposes, human decides" enforced by the accept UI; people/task text quoted as DATA. |
| 7 | **Atomize (mind-sweep)** | `/ai/atomize` | ✅ | Good — split + dedup verdicts, deterministic fallback, used by capture dedup too. |
| 8 | **Enrich / suggest-title / backfill-context** | `/items/{id}/enrich` etc. | ✅ | Good — scoped strictly to MISSING fields; team list quoted as DATA. |
| 9 | **Project planning** | `/plan` (brief → phases/tasks/subtasks) | ✅ | Good — proposal-only; assignee workload flags. |
| 10 | **Email → task capture** | `capture_email` + email app modal | ✅ | Good — carries `origin` linkage back to the mail. |

**Cross-cutting prompt hygiene: consistently strong.** Every prompt that
embeds user-or-third-party text (task titles, notes, people/HR rows, PM-tool
content) quotes it as DATA and forbids following embedded instructions; the
planner whitelists returned ids against the candidate set; strict-JSON with
deterministic fallbacks everywhere; models are per-user-configurable tiers
with fallback (`acompletion_with_fallback`).

## 3. "Can AI manage my day?" — honest answer

**Via the UI: yes, well.** Plan-my-day (LLM + packer), energy-note steering,
replan-from-now, roll-over (incl. silent nightly with audit log), learned
padding, One-Thing protection (rides `energy_note` from the startup ritual).
This is the strongest AI loop in the app.

**Via chat: partially.** The agent can read the grid and move single blocks
(`gtd_schedule/unschedule/list_schedule`), so "move the deck prep to 3pm"
works. But the spec-§7 promise — *"I'm low energy, reorganize my day"* — is
weaker than the UI path, because the chat agent **cannot call the planner**:

1. **No `gtd_plan_day` / `gtd_replan` / `gtd_rollover` tools.** The agent must
   hand-place blocks one PATCH at a time, redoing (worse) the geometry the
   server already does safely. The planner endpoints exist and return
   proposals — they just aren't registered as tools. *Highest-leverage fix in
   this review.*
2. **The agent couldn't see fixed-vs-flexible** — `gtd_list_schedule` and the
   persona's "Scheduled today" omitted `flexible`, so chat could cheerfully
   move a meeting. **FIXED (persona):** 🔒 markers + an explicit "never move a
   🔒 block" rule; still TODO in the `gtd_list_schedule` tool output itself.
3. **The agent couldn't see the ★ One Thing** (localStorage-only). **FIXED
   (persona):** the One Thing is now injected client-side with a
   protect-it instruction — but server-side surfaces (planner, a future
   cron/digest) still can't see it until it's persisted (§4.1).
4. Also now in the persona: energy windows, done-today tally, buffer
   respect, and a propose-before-applying rule for multi-block reorganizations
   (matches the "AI proposes, human decides" posture; note that chat-applied
   schedule changes bypass the UI's undo toast).

## 4. Recommendations (ranked)

### 4.1 Persist Focus-OS per-day state server-side  *(unblocks everything)*
Move `one_thing_id`, tomorrow-seeds and ritual stamps from localStorage into
`user_settings` (or a tiny `calendar_day_state` table). Then: `PlanDayRequest`
gains a first-class `one_thing_id` (replacing the energy-note hack), the
planner prompt gets "★ this task is the user's One Thing — first peak
window, never dropped", replan can move it last, chat and future digests see
it everywhere, and it syncs across devices.

### 4.2 Register the planner as chat tools
`gtd_plan_day(date?, energy_note?)`, `gtd_replan_rest()`, `gtd_rollover()` —
thin wrappers over the existing endpoints that return the proposal as text
("9:00–10:30 Draft onboarding v2 — first peak window …; apply?") and apply
via the existing PATCH path only after the user confirms. This single change
makes chat a *true* day-manager: "I just lost my morning — fix my day" becomes
one tool call + one confirmation, with the server guaranteeing geometry.

### 4.3 Close the remaining context gaps
- `gtd_list_schedule`: include `flexible` (🔒) and DONE state per row.
- Planner candidate brief: add the project outcome (batch-by-project +
  outcome-coverage reasoning costs one join).
- A `gtd_estimate_stats` tool so chat can answer "how good are my estimates?"

### 4.4 New AI-in-chat capabilities (build on 4.1–4.3)
- **Morning digest in chat**: "3 blocks rolled over (log attached), Thursday
  is slammed, plan drafted — approve?" — the rollover log + look-ahead exist;
  this is a persona + trigger away.
- **Interruption triage**: "I got pulled into a 2h incident" → agent calls
  `gtd_replan_rest`, states casualties ("Investor draft moves to tomorrow —
  it's due Friday, still safe"), applies on confirm.
- **Deadline-risk radar**: deterministic feasibility check (Σ estimates vs
  capacity × days-left) exposed as a tool; the agent narrates *which* tasks
  can't make it and proposes drops/delegations (matrix's delegate suggestions
  already exist).
- **Natural-language timeboxing**: "gym 6pm tmrw, 45m" → capture + schedule
  in one tool round-trip (capture + gtd_schedule already exist; add a
  date-parse guard).
- **Conversational shutdown/weekly review**: summarize actuals vs plan,
  leverage ratio, rollover count, estimate trend; propose tomorrow's seeds.
  (Weekly = the GTD Weekly Review finally getting an AI home.)
- **Batch suggestions**: "you have 4 @calls totalling 45m — batch them at
  2pm?" — context data already in the candidate brief.
- Later (needs F2/P4): break placement in the packer, meeting-prep blocks
  from synced calendar events, Focus-session coaching from actuals.

### 4.5 Chat-side safety parity
Chat `gtd_schedule` writes immediately (idempotent-flagged, reversible) but
bypasses the UI's schedule-undo snapshots. When 4.2 lands, route chat-applied
plans through a small server-side "apply plan" endpoint that records a revert
set, so "undo that" works in chat exactly like the toast does in the UI.

## 5. Fixes shipped with this review
- Replan modal no longer shows the energy-note input the server ignores
  (`CalendarView.tsx` — plan-mode only).
- Persona (`taskAssistantPersona.ts`): 🔒 fixed-block markers + never-move
  rule, ★ One Thing (client-side read) with protect instruction, energy
  windows, done-today tally, buffer + propose-before-apply guidance for
  multi-block changes.

## 6. Built (recommendations 4.1–4.4 — chat can now manage the day)

**Status: SHIPPED** on this branch. "Manage my day with AI via chat" now works.

- **4.1 Day state persisted server-side** — migration `92_gtd_day_state.sql`
  (`user_id, day, one_thing_id, seed_ids`) + `GET/PUT /calendar/day-state`.
  The client (`focusPrefs` + `apiGet/SetDayState`) keeps localStorage as the
  instant cache and syncs the ★ One Thing (toggle, ritual commit) and
  tomorrow-seeds (shutdown) through to the server, hydrating on calendar open.
  Server-side AI now sees the One Thing everywhere.
- **Planner is One-Thing-aware** — `_compute_day_plan` orders the One Thing
  first, prefers a peak window, and never drops it for capacity; the LLM
  ranking prompt gets an explicit One-Thing rule. Reads it from `day_state`.
- **4.2 Agent-facing planner endpoints** — `POST /calendar/{plan,replan,
  rollover}-today` build the day window from stored settings+tz (no client
  geometry), reuse the exact planner cores, and APPLY on `apply=true`. Plus a
  cheap no-LLM `GET /calendar/day-summary` for the digest.
- **4.3 Context gaps closed** — `gtd_list_schedule` now marks 🔒 fixed / ✓ done
  rows.
- **4.4 Chat tools** — `gtd_plan_day`, `gtd_replan_day`, `gtd_rollover`
  (propose→confirm→apply), `gtd_day_digest` (morning check-in),
  `gtd_estimate_stats`, `gtd_set_one_thing`. Registered in
  `agent-task-manager`; `instructions.md` + the persona document the
  propose-before-apply workflow and the never-move-a-🔒-block rule.

**Still open (future):** chat-side undo parity (§4.5 — chat-applied plans
don't yet record a revert set the way the UI's toast does), interruption
triage / deadline-risk / NL-timeboxing as dedicated flows (the primitives
now exist), and a *scheduled* morning-digest trigger (the digest tool exists;
firing it automatically needs a cron/persona trigger). `schema.generated.sql`
regenerates on deploy (it already lags `calendar_rollover_log`).
