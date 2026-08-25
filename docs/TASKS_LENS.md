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

---

## The cutover runbook (S3b → flip → S3c)

**Added 2026-08-26 with migrations 189 and 190.** Everything below is the
owner's act: `work_plan.md` §6 (f) gates *running* the move against a real
database, and building it — which is what landed — is the half that was
agent-safe. The migrations ship **inert**: applying them adds a column, an empty
table, a view and two functions, and moves nothing.

### Order, and why it is this order

```
   slice 5 lands            (the CRUD + AI tail stops writing gtd_items)
      ↓
1. deploy 189 + 190         inert — nothing moves, nothing drops
      ↓
2. SELECT * FROM gtd_backfill_plan;          ← read this before anything
      ↓
3. SELECT * FROM gtd_backfill_to_pm(false);  ← dry run, writes nothing
      ↓
4. SELECT * FROM gtd_backfill_to_pm(true);   ← the move
      ↓
5. flip BOTH flags to 1, restart, rebuild    ← reads switch to pm_*
      ↓
6. SELECT * FROM gtd_backfill_to_pm(true);   ← sweep anything written in step 5's window
      ↓
   ... let it run. Days, not minutes ...
      ↓
7. INSERT INTO gtd_retirement_arm …          ← arm the drop, by hand
      ↓
8. next deploy applies 190, which drops gtd_items + gtd_waiting
```

**Step 6 is not optional and it is why the backfill is re-runnable.** Between
the move and the flag flip the app is still writing `gtd_items`; those rows
carry no `migrated_task_id`, so a second pass picks up exactly them and nothing
else. Proven by `live_ws39_s3b.sql` checks 9a/9b.

**Steps 4 and 8 are separated by days on purpose.** R6: we cannot roll back.
The gap is the only window in which a mis-mapped row can be noticed while the
source data still exists.

### What the backfill refuses

A row whose `user_id` matches no `app_user` is **not moved, not deleted, and not
assigned to anybody** — it is reported as `unmappable`. That includes the
literal `'anonymous'`, which `routes/tasks/core.py::_uid` writes for an
unauthenticated capture. §12.8 names the failure this avoids: a mis-mapped
`member_email` does not lose a task, it publishes one person's private task into
somebody else's lens.

Those rows then **block step 8**, by design — `gtd_backfill_plan` must return
zero rows before the drop will proceed. Resolve each one deliberately (give the
address an `app_user`, or delete the row) rather than widening the guard.

### What S3c does *not* drop

`gtd_settings` · `gtd_day_state` · `gtd_rollover_log` (D53.6 — the Calendar's),
the five `gtd_people*` tables (the People directory), `gtd_horizons` (WS-21
owns it), `gtd_reviews` (WS-18), and `gtd_projects` · `gtd_spaces` ·
`gtd_folders` · `gtd_contexts` · `gtd_attachments` (the LOCAL project tree —
these wait on slice 5's port to `pm_projects`). Pinned by
`tests/unit/test_gtd_backfill.py::test_190_does_not_drop_the_tables_that_survive`.

### Fences for the move itself

| Claim | Test |
|---|---|
| 189 defines the backfill and never calls it (the gate stays intact) | `test_gtd_backfill.py::test_189_defines_the_backfill_but_never_calls_it` |
| tenant comes from `app_user`, explicitly — a migration has no RLS | `::test_189_resolves_the_tenant_from_the_directory` |
| an unresolvable owner is refused, not guessed | `::test_189_refuses_rather_than_guesses_an_owner` |
| 190 is inert until armed **and** every row is accounted for | `::test_190_is_inert_until_two_independent_conditions_hold` |
| 190 drops exactly the two tables S3b replaced, without CASCADE | `::test_190_drops_exactly_the_two_tables_s3b_replaced`, `::test_190_drops_without_cascade` |
| two orgs: one member's private task never enters another's lens | `tests/live/live_ws39_s3b.sql` checks 4a–4g (**real Postgres**, R8) |
| nothing is lost in the move (disposition, matrix, Waiting-For quartet) | `live_ws39_s3b.sql` checks 5a–5f |
| re-running moves nothing and duplicates nothing | `live_ws39_s3b.sql` checks 8a–9b |
| every S3c refusal path actually refuses | `tests/live/live_ws39_s3c.sql` checks 1a–4a |

Run the live pair against scratch Postgres, never against a real database:

```bash
docker exec -i tenant-scratch psql -U acb -d acb_tenant \
  -v ON_ERROR_STOP=1 < tests/live/live_ws39_s3b.sql
docker exec -i tenant-scratch psql -U acb -d acb_tenant \
  -v ON_ERROR_STOP=1 < tests/live/live_ws39_s3c.sql
```

`live_ws39_s3c.sql` exercises a real `DROP TABLE` inside a transaction it then
rolls back — Postgres DDL is transactional, so the scratch database is left as
it was found.
