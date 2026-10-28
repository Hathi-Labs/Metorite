# The shared job queue

**Status:** BR-1a is BUILT and dark (WS-BRIEF, Metorite #582, epic #479).
The switch `JOB_QUEUE` is OFF. No producer exists yet. BR-1b adds the first
producers. AUTO-2 (Metorite #814) adds the ledger of unattended runs in
section 9. It is BUILT and dark, and its switch `UNATTENDED_LEDGER` is OFF.

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
- **A handler runs while a session is open.** Do not call a model or a
  provider inside a handler. Section 5a gives the rules for BR-1b.
- **A handler runs for at most `worker.HANDLER_TIMEOUT` (240 seconds).** At the
  limit the worker cancels the handler, and the try fails as an error
  does. `last_error` then says `HandlerTimeout: the handler timed out`. A test
  fences the order `HANDLER_TIMEOUT` < `queue.LEASE` (5 minutes) < the
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
| `jobs.failed` | error | The last try failed. The job is `failed` for good |
| `jobs.lease_expired` | warning | A lost lease with tries left. The claim takes the row again |
| `jobs.lease_expired` | error | A lost lease at the last try. The job is `failed` |
| `jobs.complete_lost` | warning | `complete` changed no row. The worker rolled back the writes of the handler |
| `jobs.tick` | info | One line for each tick that ran a job, with the counts |

No log line holds a payload or an error text. A line holds the error type only.

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

## 5a. Rules for BR-1b handlers

The adversarial review of BR-1a recorded this contract. BR-1b builds to it.

1. **Work that touches only the database** may use the in-session handler of
   section 5.
2. **A call to a provider, a model or a send** must use a detached mode. BR-1a
   does not build it. BR-1b builds it.
3. In the detached mode, the handler first commits an intent mark on the
   domain row.
4. Then it calls the provider with no session open. Where the provider takes
   an idempotency key, it sends the job id.
5. Then it opens a new session. It runs `hold`, writes the result and runs
   `complete`.
6. On a retry, the handler reads the intent mark first. A mark with no result
   means the call possibly ran.
7. A job that must run at most once uses `max_attempts=1`.

**No retention exists yet.** The `done` and `failed` rows stay. BR-1b adds a
`jobs.prune` kind that deletes old rows.

## 6. The switch

`JOB_QUEUE` (setting `job_queue`) is default OFF. With it off,
`start_job_worker()` starts no task and `run_tick()` does nothing. The one
reader is `worker.worker_enabled()`. A flip on a box is the gate
`enforcement-flip`. `env_guard` refuses the key from a tenant.

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

## 8. Out of scope of BR-1a

- BR-1b: move the reminder loop, send-later and the agent-run sweeps onto the
  queue.
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

A brief send that ends `failed:send_unsure` is `unknown`. Then the full
brief does not follow it as a retry. Reach does not run on the queue in this
slice.

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
