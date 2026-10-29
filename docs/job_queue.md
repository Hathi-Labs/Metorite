# The shared job queue

**Status:** BR-1a is BUILT and dark (WS-BRIEF, Metorite #582, epic #479).
BR-1b-a is BUILT and dark (Metorite #642): the detached mode, `jobs.prune` and
the timeout label. Its review round 1 added the settle clock, `give_up`, one
try for a detached kind, and the cap and belt of the prune.

AUTO-2 (Metorite #814) adds the ledger of unattended runs in section 9. It is
BUILT and dark, and its switch `UNATTENDED_LEDGER` is OFF.

The switch `JOB_QUEUE` is OFF. No producer exists yet. The rest of BR-1b
moves the first producers.

This page is the engineering reference of the queue. It is not a product
spec, so `project-docs/INDEX.md` does not list it.

## 1. What it is

The queue holds background work in Postgres, in the table `jobs`. A job is one
row of one organization. It has a kind, a JSON payload, a state and a next run
time. The state is in Postgres only, so a job survives a restart.

| Part | Path |
|---|---|
| The table | `infra/postgres/312_jobs.sql` (the number can change at merge, R1) |
| The SQL: enqueue, claim, hold, settle | `apps/services/gateway/gateway/jobs/queue.py` |
| The registry and the loop | `apps/services/gateway/gateway/jobs/worker.py` |
| The start and the stop | `apps/services/gateway/gateway/main.py`, next to the reminder loop |
| The fence | `tests/unit/test_job_queue.py` (R8) |

## 2. How a job moves

1. A producer calls `queue.enqueue(db, kind, payload)` inside its own
   `tenant_session(org)`. The row starts `queued`.
2. The worker ticks every 15 seconds. It lists the orgs with the one discovery
   read, `email_ingestion.scheduler._list_organizations`. For each org it opens
   `tenant_session(org)`.
3. The claim takes the oldest due row with `FOR UPDATE SKIP LOCKED`. It sets
   `running`, adds 1 to `attempts`, writes a new fence token in `locked_by`
   and sets `lease_until`. Then it commits.
4. The run opens a second `tenant_session(org)`. `queue.hold` locks the row
   and checks the token. The handler runs in a SAVEPOINT on that session.
5. The settle runs in the same transaction. A pass sets `done`. A failure sets
   `queued` with a backoff, or `failed` at the last try. `last_error`
   keeps the error.
6. The settle reads the clock again after the handler returns. So the
   backoff of a slow try starts at the end of the try. So a try of 240
   seconds does not come back in the same tick. The other orgs do not wait.

The writes of a handler and `done` commit together, or neither commits.

## 3. The rules

- **The org is never an argument.** `enqueue` writes no `organization_id`. The
  column DEFAULT reads the bound tenant. A session with no tenant fails the
  NOT NULL. A session of another tenant fails the WITH CHECK (R5).
- **The worker never reads an org from a payload.** RLS binds each statement.
  A claimed row of another org is refused.
- **A handler must not call `commit()`.** A commit ends `SET LOCAL` and the row
  lock.
- **A handler must use only the session that it receives.** A second session
  is a second transaction. Its writes commit when the try fails, and its
  locks can wait on the row lock of the run.
- **A `session` handler runs while a session is open.** Do not call a model
  or a provider inside it. Use the `detached` mode for that work.
- **A `detached` handler runs with no session open.** Register it with
  `worker.register(kind, handler, mode="detached")`. The handler is
  `async handler(org, payload, claim)`. The worker commits the claim, and
  then calls the handler with no session and no pooled connection. The
  handler opens its own short `tenant_session(org)` for each step. In the
  last one, it runs `hold`, writes the result and runs `complete`. Section 5a
  gives the steps.
- **A detached handler must settle its job.** After the handler returns, the
  worker reads the row. A row that is still `running` with the token of the
  claim fails the try with `Unsettled: the handler returned without
  settling`. A handler that raises fails the try in a new session.
- **A detached kind gets one try, unless you say otherwise.** The default is
  `idempotent=False`. Then the worker gives up the claim of try 2 or later
  before the handler runs. The job is then `failed`, and `last_error` holds
  `worker.NOT_IDEMPOTENT`. Register with `idempotent=True` only when a
  second try is safe. An example is a handler that reads an intent mark
  first.
- **A detached handler must await all of its work.** It must not start a
  task with `create_task`, and it must not leave work to run after it
  returns. The time limit cancels the current task only, so a task that the
  handler starts runs on past the limit and past the settle.
- **A handler that must not try again calls `queue.give_up`.**
  `queue.give_up(db, claim, error, now=...)` sets `failed` for good, with
  tries left or not. The fence token guards it, as for `fail`. It answers
  False when the claim is gone. It logs `jobs.failed` at error. Name the
  reason before the first colon of `error`, because the log line holds that
  part only.
- **A handler runs for at most `worker.HANDLER_TIMEOUT` (240 seconds).** At the
  limit the worker cancels the handler, and the try fails as an error
  does. `last_error` then says `HandlerTimeout: the handler timed out`. The
  label stays when the limit cancels a database call and the session then
  breaks. A test fences the order `HANDLER_TIMEOUT` < `queue.LEASE` (5 minutes) < the
  600 second `idle_in_transaction_session_timeout` of `acb_common.db`.
- **A lost settle rolls back.** When `complete` changes no row after `hold`
  passed, the worker raises inside the session. The writes of the handler do
  not commit, and the outcome is `lost`.
- **The worker claims only the kinds that it has a handler for.** A job of an
  unknown kind waits. A worker that does not know the kind never fails it.
- **The payload is a JSON object of at most 64 KiB.** `enqueue` refuses NaN,
  Infinity and a NUL character before it writes.
- **One live job for each dedupe key.** A key is unique for each org and kind
  among the `queued` and `running` rows. A second `enqueue` answers the live
  job with `created=False`.

## 4. A killed worker, and the retry

- A worker that dies drops its connection, so its row lock goes. The row stays
  `running` until `lease_until` passes. Then the next claim takes it, and the
  claim counts as a try.
- A live worker holds the row lock for the whole run. A second worker skips
  the row, also after `lease_until`.
- A lost lease at the last try sets `failed` with `lease expired`.
- The wait after failed try n is 30 seconds times 2 to the power n minus 1.
  The wait is at most 1 hour. `max_attempts` is 5 by default, 25 at most.
- A worker that lost its lease changes nothing. The settle matches the fence
  token, so it changes 0 rows.

## 4a. The signals

| Event | Level | When |
|---|---|---|
| `jobs.attempt_failed` | warning | A try failed, and the queue retries it |
| `jobs.failed` | error | The last try failed, or a handler or the worker gave the job up (`outcome="given_up"`). The job is `failed` for good |
| `jobs.lease_expired` | warning | A lost lease with tries left. The claim takes the row again |
| `jobs.lease_expired` | error | A lost lease at the last try. The job is `failed` |
| `jobs.complete_lost` | warning | `complete` changed no row. The worker rolled back the writes of the handler |
| `jobs.lease_lost` | info | Another claim holds the row. The worker settles nothing |
| `jobs.run_failed` | warning | The session of a run broke. The worker settles the try in a new session |
| `jobs.claim_failed` | warning | A run raised out of `run_one`. The worker stops the batch of that org |
| `jobs.prune_seed_failed` | warning | The daily seed of `jobs.prune` failed. The next tick tries again |
| `jobs.pruned` | info | The prune job deleted rows, with the count |
| `jobs.tick` | info | One line for each tick that ran a job, with the counts |

No log line holds a payload or an error text. A line holds the error type
only. For a give-up, the line holds the text before the first colon of the
error, which is its name.

## 5. How to add a kind (BR-1b)

```python
from gateway.jobs import queue, worker

async def send_later(db, payload: dict) -> None:
    ...  # the work, on the tenant session db

worker.register("email.send_later", send_later)

# The producer, inside its own tenant_session(org):
await queue.enqueue(db, "email.send_later", {"draft_id": draft_id},
                    run_at=when, dedupe_key=f"draft:{draft_id}")
```

A kind matches `[a-z][a-z0-9_.]{0,63}`.

## 5a. The detached mode (BR-1b-a, built)

The adversarial review of BR-1a recorded this contract. BR-1b-a built it.

1. **Work that touches only the database** may use the `session` handler of
   section 5.
2. **A call to a provider, a model or a send** must use the `detached` mode.
3. The handler first reads its domain row. When it holds an intent mark and
   no result, the call possibly ran. Then the handler does not call again.
4. Otherwise the handler commits an intent mark on the domain row, in its own
   short session.
5. Then it calls the provider with no session open. Where the provider takes
   an idempotency key, it sends the job id.
6. Then it opens a new session. It runs `hold`, writes the result and runs
   `complete`. When `hold` answers False, another claim took the row, and the
   handler writes nothing.
7. When step 3 finds a mark with no result, the handler calls `give_up` with
   a named error. The job is then `failed`, and `jobs.failed` tells a person.
   Never settle an uncertain send as `done`. That loses it with no signal.
8. A kind that follows steps 3 and 7 registers with `idempotent=True`. A kind
   that does not keeps the default, so the worker gives it one try only.

**The intent mark is a column on the domain row.** No helper and no shared
table exist for it. The owner of the domain table adds the column, for
example `phrasing_at` on a brief run. The mark commits before the call. A
test proves the pattern with a plain table:
`test_r8_an_intent_mark_stops_a_second_provider_call`.

```python
async def send(org: str, payload: dict, claim: queue.Claim) -> None:
    async with tenant_session(org) as db:
        row = await read_draft(db, payload["draft_id"])
    if row.sent_mark is not None:                # the call possibly ran
        async with tenant_session(org) as db:
            await queue.give_up(db, claim, "UncertainSend: the provider "
                                "call possibly ran, so no second call", now=now())
        return
    async with tenant_session(org) as db:
        await mark_sent_intent(db, row.id)       # commits on exit
    result = await provider.send(row, idempotency_key=claim.id)
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return
        await write_result(db, row.id, result)
        await queue.complete(db, claim, now=now())

worker.register("email.send_later", send, mode="detached", idempotent=True)
```

## 5b. Retention: `jobs.prune` (BR-1b-a, built)

- **What it deletes.** The `done` and `failed` rows of the bound org that
  finished more than 30 days ago (`queue.RETENTION`). It deletes 500 rows in
  each statement (`queue.PRUNE_BATCH`), and 10 statements in one run at most
  (`queue.PRUNE_MAX_BATCHES`). So one run takes at most 5000 rows, and the
  next day takes the rest of a backlog. A `queued` or `running` row stays, at
  any age.
- **Two binds.** RLS binds each DELETE to the org, as for every statement
  of the queue. The DELETE also names the bound tenant
  (`organization_id = queue._BOUND_ORG`). So a session that bypasses RLS,
  such as the owner role, still deletes the rows of the bound org only. With
  no tenant bound, it deletes nothing.
- **Where it runs.** `worker.py` registers `jobs.prune` at import, as a
  `session` handler. So it runs only when `JOB_QUEUE` is on.
- **The schedule.** `worker.run_org` seeds one job for each org and each UTC
  day, with the dedupe key `prune:<day>` and `max_attempts=3`. A cache in
  memory holds the seeded day of each org. So only the first tick of a day
  writes. Every later tick of that day costs no statement for the seed. Two
  workers with two caches still give one live job, because of the dedupe
  key.
- **One live prune job for each org and day.** A restart empties the cache.
  When the job of the day is already `done`, the dedupe key does not match
  it. So the next tick can seed a second job for that day. That job is
  harmless: it finds no old rows, or only a few.
- **Why a daily seed, and not a job that queues the next one.** A chain of
  jobs breaks for good when one link fails. A seed on each day starts again
  on the next day.
- **A hook for another table.** `worker.prune_hooks` is a list of
  `async hook(db, today)` callables. The prune job runs each one after its
  own delete, on the same session. A hook that raises fails the try, and the
  whole prune rolls back. BR-4 adds the retention of the brief runs here,
  with its own 90 day cut-off (`core_brief.md` §3.4).

## 6. The switch

`JOB_QUEUE` (setting `job_queue`) is default OFF. With it off,
`start_job_worker()` starts no task and `run_tick()` does nothing. The one
reader is `worker.worker_enabled()`. A flip on a box is the gate
`enforcement-flip`. `env_guard` refuses the key from a tenant.

**The load with the switch ON.** `jobs.prune` is registered at import, so
the worker always has a kind to claim. Each tick of 15 seconds costs these
statements:

- One discovery read of the orgs.
- For each org, one claim session. A tick with no due job costs only that
  session for the org.
- For each org, one more session on the first tick of each UTC day, for the
  seed of the prune job. Then the prune job runs once for each org and day.

## 7. Acceptance of BR-1a

Each item is an R8 test in `tests/unit/test_job_queue.py`. Without the ladder
database every test SKIPS, and the run reads green.

| Item | Test |
|---|---|
| a. A job queued before a restart runs once after it | `test_r8_a_job_queued_before_a_restart_runs_once_after_it` |
| b. Retry with a backoff, then `failed` with `last_error` kept, and `done` on try N | `test_r8_a_failing_job_retries_with_backoff_then_fails_with_its_error` |
| c. A lost lease is claimed again and runs | `test_r8_a_running_job_with_a_lost_lease_is_claimed_again_and_runs` |
| d. Two workers never run one job twice | `test_r8_two_workers_never_run_one_job_twice` |
| e. Org A cannot claim, settle or read a job of org B | `test_r8_org_a_cannot_claim_settle_or_read_a_job_of_org_b` |
| f. NaN, Infinity and a large payload are refused | `test_r8_a_bad_payload_is_refused_at_enqueue_and_writes_nothing` |
| g. A frozen clock, no sleeps | every test passes `now` |

The review round adds these tests:

| Rule | Test |
|---|---|
| The order of the three time limits | `test_the_handler_timeout_is_below_the_lease_and_the_idle_timeout` |
| A hung handler fails its try, and the next org runs | `test_r8_a_hung_handler_times_out_and_the_next_org_still_runs` |
| `jobs.failed` logs once, at the last try only | `test_r8_a_job_that_fails_for_good_logs_jobs_failed_once` |
| One summary line for a tick | `test_a_tick_that_ran_a_job_logs_one_summary_line` |
| The worker reads the clock for each job | `test_r8_the_worker_reads_the_clock_for_each_job` |
| A lost settle rolls back the handler | `test_r8_a_complete_that_changes_no_row_rolls_back_the_handler` |

BR-1b-a adds these tests:

| Rule | Test |
|---|---|
| A detached handler runs with no session and no pooled connection | `test_r8_a_detached_handler_runs_with_no_session_open` |
| A detached handler that lost its lease settles nothing | `test_r8_a_detached_handler_that_lost_its_lease_settles_nothing` |
| An intent mark stops a second provider call | `test_r8_an_intent_mark_stops_a_second_provider_call` |
| A detached handler must settle its job | `test_r8_a_detached_handler_must_settle_its_job` |
| A detached handler that hangs fails as `HandlerTimeout` | `test_r8_a_detached_handler_that_hangs_times_out` |
| A hang in the database keeps the `HandlerTimeout` label | `test_r8_a_handler_that_hangs_in_the_database_fails_as_a_timeout` |
| Prune deletes only the old terminal rows of the bound org | `test_r8_prune_deletes_only_old_terminal_rows_of_the_bound_org` |
| One prune job for each org and day, and no statement after the seed | `test_r8_run_org_seeds_one_prune_job_for_each_org_and_day` |
| No prune job with no prune handler | `test_r8_no_prune_job_is_seeded_with_no_prune_handler` |
| The register modes, and the prune kind at import | `test_register_takes_a_mode_and_refuses_another`, `test_the_prune_kind_is_registered_at_import` |

The review round 1 of BR-1b-a adds these tests:

| Rule | Test |
|---|---|
| The backoff of a slow try starts at its end | `test_r8_a_slow_try_takes_its_backoff_from_its_end` |
| An uncertain send fails with a named error and `jobs.failed` | `test_r8_an_intent_mark_stops_a_second_provider_call` |
| A detached kind that is not idempotent gets one try | `test_r8_a_detached_kind_that_is_not_idempotent_gets_one_try` |
| One prune run takes a capped number of rows | `test_r8_one_prune_run_takes_a_capped_number_of_rows` |
| A prune that bypasses RLS takes the bound org only | `test_r8_a_prune_on_a_session_that_bypasses_rls_takes_the_bound_org_only` |

## 8. Out of scope of BR-1a and BR-1b-a

- The rest of BR-1b: move the reminder loop, send-later and the agent-run
  sweeps onto the queue. No producer of provider work exists yet.
- The retention of the brief runs. BR-4 adds it as a prune hook.
- A heartbeat that extends the lease of a long detached handler.
- AUTO-2: at-most-once unattended runs. Section 9 has it.
- The 07:30 brief and the 18:30 close-out.
- Any change to the apscheduler uses, any producer and any UI.
- A production deploy and the flip of `JOB_QUEUE`.

## 9. The ledger of unattended runs (AUTO-2)

A run with nobody watching must happen at most once for each slot. A brief
that a crash cut must not go to the member a second time. The ledger gives
that rule. AUTO-2 is the first slice (Metorite #814).

| Part | Path |
|---|---|
| The tables | `infra/postgres/313_unattended_ledger.sql` (the number can change at merge, R1) |
| The one writer | `apps/services/gateway/gateway/jobs/unattended.py` |
| The first user | `apps/services/gateway/gateway/reach/loop.py`, the brief send |
| The fence | `tests/unit/test_unattended_ledger.py` (R8 and a fake clock) |

### 9.1 The tables

- `unattended_runs` holds one row for each job, slot and org. The unique key
  is `(organization_id, job, slot)`. The state is `started`, `ok`, `failed`,
  `unknown` or `stalled`. The row keeps the error class and the incident.
- `unattended_incidents` holds the failures of one job and one error class.
  One `open` row stays for each pair, with a count and `last_seen`. A
  partial unique index holds this rule.

Both tables are tenant-scoped with FORCE RLS. No statement names an org. The
column DEFAULT writes the bound tenant (R5).

### 9.2 How a run moves

1. `begin(org, job, slot)` inserts the row. Its session commits BEFORE the
   work starts.
2. When another run holds the slot, `begin` answers `fresh=False` with the state of
   the earlier run. The caller does not run the job.
3. The caller does the work. Then `finish(org, run, state=...)` ends the
   row. A state other than `ok` counts a failure in its incident.
4. A send that possibly reached the provider ends `unknown`. Nobody retries
   it.
5. Work that surely reached no provider calls `release(org, run)`. The
   started row goes, so a later `begin` of the slot is fresh.

**The rule: a slot is taken only once a send may have run.** An empty
brief, or an error before the send, did not reach the provider, so it gives
the slot back. An error after the send began keeps the row, and nobody
retries it. `release` deletes only a `started` row, so a run that ended
keeps its row.

`once(org, job, slot, work)` joins the four steps. Work that raises
`OutcomeUnknown` ends `unknown`. Another error ends `failed`, and `once`
raises it again.

### 9.3 A run with no end

A `started` row is an orphan in two cases:

- Another process began it. Each process has its own `RUNNER` token, so a
  restart reads the rows of the earlier process as orphans.
- Its `progress_at` is older than 10 minutes (`STALL_AFTER`).

`recover` and `begin` mark an orphan `unknown`, and nothing runs it again.
Call `recover` before any other check that can skip the slot. A crash after
the intent mark of a send leaves the domain row half done. The other check
skips that row, so only `recover` can mark the run.

### 9.4 The stall guard

`run_guarded(org, run, work)` gives the work a `StallGuard`. The work calls
`guard.progress()` as it moves. When no progress comes for 10 minutes, the
guard cancels the work and the row turns `stalled`. A poll that saw
progress writes `progress_at`.

### 9.5 Incidents and the signals

`record_failure(org, job, error)` adds 1 to the open incident of the job and
the error class. The error class is an exception type name or a short
reason. The text of a failure never reaches a row.

| Event | Level | When |
|---|---|---|
| `unattended.incident_opened` | error | The first failure of a job and an error class. One line for each incident |
| `unattended.incident_repeat` | debug | A later failure of an open incident |
| `unattended.run_unknown` | warning | An orphan turned `unknown` |
| `unattended.run_stalled` | warning | The stall guard stopped a run |
| `unattended.finish_lost` | warning | `finish` changed no row, because the run already ended |
| `unattended.slot_taken` | info | `begin` found the slot taken |
| `unattended.slot_released` | info | `release` gave a slot back |

Until alert delivery exists, `unattended.incident_opened` is the one alert
of an incident. Nothing closes an incident yet.

### 9.6 Reach

With `UNATTENDED_LEDGER` on, the Reach loop uses the ledger in two places:

- **The brief send.** The free text brief, the template brief and the full
  brief each commit a run of job `reach.brief` before the send. The slot is
  the outbox key of the brief (`plan.brief_key`), so the ledger key and the
  outbox key agree.
- **The member pass.** A pass that times out or fails counts in an incident
  of job `reach.member`. A pass writes no run row, because it runs each
  minute.

Reach sets a mark just before `channel.deliver`. A brief send with no mark
gives its slot back. Examples are an empty brief, a duplicate, and an error
before the outbox row, such as a failed read of the name. Then a later pass sends
the brief when cards arrive. An error after the mark ends `failed`, and
nobody retries it.

A brief send that ends `failed:send_unsure` is `unknown`. Then the full
brief does not follow it as a retry. A brief run that is still `started`
blocks the full brief too, because its end is not known yet. Reach does not
run on the queue in this slice.

### 9.7 How to use it from a job

A job that sends must run at most once. Enqueue it with `max_attempts=1`,
so a lost lease fails the job and does not run it again. The handler that
calls a provider needs the detached mode of section 5a (BR-1b). The ledger
row is its intent mark:

```python
from acb_common.db import current_tenant
from gateway.jobs import unattended

async def send_brief(db, payload: dict) -> None:
    org = current_tenant()  # run_org binds the org of the job
    run = await unattended.begin(org, "brief.send", payload["slot"])
    if not run.fresh:
        return  # the slot ran, or its outcome is unknown
    ...  # the send, with no session open (section 5a, detached mode)
    await unattended.finish(org, run, state="ok")

# The producer, inside its own tenant_session(org):
await queue.enqueue(db, "brief.send", {"slot": slot}, max_attempts=1,
                    dedupe_key=slot)
```

Work that touches only the database needs no ledger. The queue commits its
writes and `done` together.

### 9.8 The switch

`UNATTENDED_LEDGER` (setting `unattended_ledger`) is default OFF. With it
off, `begin` answers a run with no id and writes nothing, and each other
function does nothing. Reach behaves as before. The one reader is
`unattended.ledger_enabled()`. A flip on a box is the gate
`enforcement-flip`. `env_guard` refuses the key from a tenant.

### 9.9 Acceptance of AUTO-2

| Item | Test |
|---|---|
| 1. A run killed after `begin` does not run again, and one row stays | `test_r8_1_a_run_killed_after_begin_does_not_run_again` |
| 2. A send killed after the intent mark is `unknown`, and sends no more | `test_r8_2_a_send_killed_after_the_intent_mark_is_unknown` |
| 3. Five failures give one incident with count 5, and another org sees none | `test_r8_3_failures_group_into_one_incident_for_each_class` |
| 4. No progress for 10 minutes is `stalled`, and progress is not | `test_4_no_progress_for_10_minutes_is_stalled`, `test_4_a_run_that_calls_progress_is_not_stalled`, `test_r8_4_a_run_with_no_progress_is_stopped_and_stalled` |
| 5. A Reach brief on one day gives one ledger row | `test_r8_5_a_reach_brief_on_one_day_gives_one_ledger_row` |
| 6. With the switch off, no write, and Reach as before | `test_r8_6_with_the_switch_off_reach_writes_no_ledger_row`, `test_the_ledger_is_dark_by_default`, and `tests/unit/test_reach_loop.py` |

### 9.10 Out of scope of AUTO-2

- The delivery of an alert to an admin, and the bell. These stay owner-only.
- A UI, a retry of a send, a new queue, and a change to `reach_outbox`.
- The workflows scheduler (after H4), My Day and the BR-2a brief wiring.
- A production deploy and the flip of `UNATTENDED_LEDGER`.
