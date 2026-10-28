# The shared job queue

**Status:** BR-1a is BUILT and dark (WS-BRIEF, Metorite #582, epic #479).
The switch `JOB_QUEUE` is OFF. No producer exists yet. BR-1b adds the first
producers.

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
- **A handler runs while a session is open.** Do not call a model or a
  provider for a long time inside a handler. Plan that work for BR-1b.
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

## 8. Out of scope of BR-1a

- BR-1b: move the reminder loop, send-later and the agent-run sweeps onto the
  queue.
- AUTO-2: at-most-once unattended runs.
- The 07:30 brief and the 18:30 close-out.
- Any change to the apscheduler uses, any producer and any UI.
- A production deploy and the flip of `JOB_QUEUE`.
