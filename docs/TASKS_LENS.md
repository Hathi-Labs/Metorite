# The Tasks lens flags — `NEXT_PUBLIC_TASKS_LENS` and `TASKS_LENS`

**Two variables, one decision, one `.env` file. Set them together or not at all.**

Board **WS-39** · decisions **D52 / D53 / D54** · specs `task_manager_app.md`
§13.5a · `calendar_focus_os.md` §10.7.

---

## What they switch

Under D53 there is **one task store** — `pm_tasks` plus the per-member overlay
`pm_task_personal` — and `/projects`, `/tasks` and `/calendar` are three lenses
on it. The old store, `gtd_items`, still holds every task anybody has captured.

These two flags decide which store the Tasks and Calendar surfaces read.

| Variable | Read by | When |
|---|---|---|
| `NEXT_PUBLIC_TASKS_LENS` | the **Next.js build** (`app/tasks/lib/lens.ts`) | at build time — the deploy rebuilds the workbench on the box, from this same `.env` |
| `TASKS_LENS` | the **gateway process** (`routes/tasks/calendar.py`) | at call time — a flip is a restart, not a release |

Both accept `1` · `true` · `yes` · `on` (case-insensitive, trimmed). Anything
else, including unset, is **off**.

---

## Why there are two

Not an oversight, and it was avoided everywhere it could be.

The browser picks its store by **picking a route** — `/projects/my/*` versus
`/api/tasks/*` — so for everything a person clicks, one flag in the browser is
enough and the server needs none.

Three surfaces have no browser and so cannot pick a route:

- the **agent planner** (`/tasks/calendar/{plan,replan,rollover}-today`), called
  by the chat assistant;
- **`/calendar/day-summary`**, which the assistant reads before answering;
- the **nightly roll-over sweep**, which runs unattended, per tenant, and
  **writes**.

Those read `TASKS_LENS`. There is no way to give them the browser's value: it is
baked into a JavaScript bundle by a build that has already finished.

---

## ⚠️ What goes wrong if they disagree

Nothing crashes. That is the problem.

- **Browser on, gateway off** — the Tasks UI shows tasks from `pm_*`; the
  assistant plans, summarises and rolls over `gtd_items`. "Plan my day" appears
  to work and schedules nothing you can see.
- **Browser off, gateway on** — the reverse. The nightly sweep releases blocks
  in a store the UI is not reading, so the member's real leftovers are never
  released, every night, silently.

**So the gateway's value is reported on `/version`**, unauthenticated, and that
is the whole reason a second flag is tolerable:

```console
$ curl -s https://api.metorite.com/version
{"sha":"da22106e…","env":"prod","tasks_lens":false}
```

Compare it against what the workbench was built with. A mismatch is a
configuration bug you can see from a laptop, mid-incident, with no box access —
which is the standard CLAUDE.md §3.8 sets for everything else about a deploy.

---

## ⚠️ When they may be turned on

**Not yet, and not on their own.**

`gtd_items` still holds every existing task. The backfill that moves those rows
into `pm_tasks` + `pm_task_personal` is **WS-39 slice S3b**, it is
🔴 **owner-gated** (`work_plan.md` §6 (f)), and it has not run.

Turning these on before the backfill does not break the app — it **empties** it.
Every read answers correctly that the new store holds none of that member's
rows, and it answers on a 200. There is no error to notice.

The order is:

1. **S3b** — the backfill runs (owner).
2. **Both flags on**, same `.env`, one restart + one workbench rebuild (owner).
3. Verify by evidence: `/version` reports `tasks_lens: true`, and a task
   captured in `/tasks` appears in `/projects` in the same page load.
4. **S3c** — `gtd_items` and its satellites are dropped, a release later
   (owner). ⚠️ `gtd_settings`, `gtd_day_state` and `gtd_rollover_log` **survive**
   (D53.6): they are per-member calendar state, not tasks, and a sweep that
   deletes everything matching `gtd_*` takes the calendar's preferences, day
   state and roll-over log with it.

Slice 3 is not the last one, either — the AI routes, subtasks, bulk actions and
the workspace family are still on the old store when the flag is on. `H-33`
enumerates what remains.

---

## Adding them

They belong in the box's `.env`, adjacent, with the comment that they are a
pair. `.env.example` should carry them too — that edit is owner-gated
(`work_plan.md` §6), and **`H-34`** asks for it.

```dotenv
# The Tasks/Calendar store cutover (WS-39, D53). BOTH or NEITHER — see
# docs/TASKS_LENS.md. Do not enable before the S3b backfill has run.
NEXT_PUBLIC_TASKS_LENS=0
TASKS_LENS=0
```

---

## Fences

| Claim | Test |
|---|---|
| the gateway flag defaults off and is read at call time | `tests/unit/test_calendar_task_source.py::test_the_flag_is_off_unless_a_deployment_says_otherwise`, `::test_the_flag_is_read_at_call_time` |
| every browserless surface asks `agent_source()` rather than naming a store | `::test_every_browserless_surface_asks_which_store` |
| `/version` still reports the flag, so a mismatch stays observable | `::test_the_mismatch_is_reportable` |
| the browser flag defaults off | `src/app/tasks/lib/lens.test.ts` → "lensEnabled" |
| the client's spine all consults it | `lens.test.ts` → "the cutover seam is complete for this slice" |
