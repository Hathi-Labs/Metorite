# Job queue — one durable queue for background work

<!-- ste-tier: strict -->

- **Status:** ACTIVE, 2026-10-28. Agent-proposed under the owner's go-ahead
  of 2026-10-27, owner may overrule (the D121 convention). Nothing is built.
- **Verified against code on 2026-10-28** at `main` `487b77dbd`. Re-verify
  every anchor at dispatch.
- **Board row:** **WS-BRIEF**, item **BR-1a** (Metorite task 582, M1, P0,
  size L). `work_plan.md` §2.0 step 2 puts it first, because every brief task
  runs on it. The WS-4 row (BO-20) points here for the queue.
- **Single owner.** This spec owns the shared durable job queue in Postgres.
  It supersedes the BO-20 and WS-4 notes on a durable queue. Each other doc
  that names the queue links here and adds nothing.
- **Reads:** D15, D28, H4 (`saas_multitenancy_handover.md`), H-249
  (`HANDOFF.md`), R1 and R4 to R8.
- **Does not own:** what a job does, because each consumer spec owns its
  kinds. It also does not own the BO-20a Redis Streams consumer, the cron jobs
  of `ingestion/scheduler.py`, or the tenant seam `acb_common.db`.
- **Ids** carry the `JQ-` prefix (R2): JQ-A for acceptance, JQ-M for a
  mutation, JQ-G for an owner gate, and JQ-2 to JQ-5 for slices.

---

## 0. The answer

**Build one Postgres table, `jobs`, and one worker in the gateway.** A caller
enqueues a job in its own tenant session, so the job commits with the write
that asked for it. The worker takes a due job with `FOR UPDATE SKIP LOCKED`
and sets a lease in the same statement. Then it commits, runs the handler with
no transaction open, and settles the job in a new short transaction.

**The lease makes a job durable.** A job in the `running` state when the
process stops keeps its lease. When the lease expires, the next claim takes it
again. A failed job goes back on the queue with a doubling delay until its try
limit. Then it is `dead`.

**The org comes from the row, never from the payload.** The worker binds the
tenant of the row before any database access. `enqueue` with no tenant raises
`TenantUnbound`, so a job with no tenant cannot exist (H4).

**Small and dark.** BR-1a builds the queue and moves no loop. The flag
`job_queue_enabled` is off by default, and then the worker starts no task.
`enqueue` still writes, so a caller can be built and tested dark.

---

## 1. What exists today (measured 2026-10-28)

No shared queue exists. Five features run their own claim loop, each as an
asyncio task that the gateway lifespan starts (`main.py` about :474 to :538).
The paths are under `apps/services/gateway/gateway/`.

| Loop | Table | Retry |
|---|---|---|
| `reminders/loop.py` | `reminders` | the next tick |
| `routes/email/automation/send_later.py` | `email_scheduled_sends` | the next tick after a read failure, and never for a row stuck in `sending` |
| `routes/email/memory_purge.py` | `email_mailbox_purges`, `email_detached_purges` | `tries`, base 300 s, cap 6 hours (`retry_delay_s`, :458) |
| `routes/knowledge/proposal_expiry.py` | the Knowledge proposals | the next tick |
| `agent_run_sweep.py` | the agent runs (`routes/projects/agent_runs.py`) | the next tick |

- **A held row lock does not cover slow work.** `kb_index/index.py:22`
  records it: the lock ends with the transaction. So the queue claims, sets a
  lease and commits before the work starts. The claim of `memory_purge.py:216`
  is the shape to copy.
- **apscheduler is not a queue.** Only
  `apps/services/ingestion/ingestion/scheduler.py` runs it, for two nightly
  cron jobs in its own process. The workflows code imports `CronTrigger` only
  to parse a cron line.
- **The Redis Streams consumer is a separate seam.**
  `apps/services/ingestion/ingestion/consumer.py`
  (BO-20a, flag `INGESTION_CONSUMER`, off) drains the event bus. This queue does
  not replace it in v1, and BO-20b can build its retry on this queue later.
  `consumer.py:11-16` records why the owner rejected a separate worker process.
  The same reason puts this worker in the gateway.
- **The tenant seam** is `packages/acb_common/acb_common/db.py`:
  `TenantUnbound` (:286), `bind_tenant` (:295), `release_tenant` (:317),
  `current_tenant` (:327), `tenant_session` (:333) and `mark_background`
  (:165).
- **H-249 is open.** `tenant_session` commits an aborted transaction with no
  error. A swallowed error inside a block loses each later write of the block.
- **Migrations.** The highest number on `origin/main` is 309. Open PR #1376
  takes 310.

---

## 2. Scope and non-goals

**In scope (BR-1a):**

1. The `jobs` table (§3).
2. `enqueue`, the claim, the settle and the retry rule (§4).
3. The handler registry, with one built-in kind, `jobs.noop` (§4.3).
4. The worker behind its flag, and one log line for each settle (§5).
5. The field `job_queue` on `/version`.
6. The tests and the mutations of §7 and §8.

**Non-goals:**

- **No loop moves.** BR-1b moves reminders, send-later and the agent-run
  sweep, one PR for each loop.
- **No recurring schedule.** A caller enqueues each run. BR-2a owns the 07:30
  trigger of the brief.
- **No exactly-once.** The queue runs a job at least once (§4.5). AUTO-2
  builds "at most once" on the idempotency key and a check in its handler.
- **No retention, metrics view, progress line or lease renewal.** JQ-4, JQ-5
  and BR-Q5 add them (§11).
- **No route, UI or chat tool.** No member sees a job in v1.
- **No new process, systemd unit or Redis key.** So BR-1a needs no
  `deploy-write` work.
- **No enqueue from outside the gateway.** A package cannot import `gateway`.
  If a package must enqueue later, a slice moves `enqueue` to `acb_common`.
- **No change to `ingestion/consumer.py` or `ingestion/scheduler.py`.**

---

## 3. The table

Take the migration number at build time: the next free one, checked again at
merge (R1). Name the file `<n>_jobs.sql`. No table named `jobs` exists in
`infra/postgres` (searched 2026-10-28). Copy the shape of `307_reminders.sql`.
Copy its header (What, Why, Depends on, Expand only) and its one `BEGIN` and
`COMMIT`. Keep the DDL idempotent, and guard the `DO $rls$` block.

```sql
CREATE TABLE IF NOT EXISTS jobs (
    organization_id UUID NOT NULL
                    REFERENCES organization (id) ON DELETE CASCADE
                    DEFAULT current_setting('app.tenant_id', true)::uuid,
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT NULL,
    state           TEXT NOT NULL DEFAULT 'queued',
    tries           INTEGER NOT NULL DEFAULT 0,
    max_tries       INTEGER NOT NULL DEFAULT 5,
    run_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until     TIMESTAMPTZ NULL,
    last_error      TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ NULL,
    finished_at     TIMESTAMPTZ NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT jobs_kind CHECK (kind ~ '^[a-z][a-z0-9_.]{2,63}$'),
    CONSTRAINT jobs_state CHECK (
        state IN ('queued', 'running', 'succeeded', 'dead')),
    CONSTRAINT jobs_lease CHECK ((state = 'running') = (lease_until IS NOT NULL)),
    CONSTRAINT jobs_tries CHECK (tries >= 0 AND max_tries BETWEEN 1 AND 20),
    CONSTRAINT jobs_payload CHECK (
        jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 16384),
    CONSTRAINT jobs_key CHECK (
        idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 1 AND 200),
    CONSTRAINT jobs_error CHECK (
        last_error IS NULL OR char_length(last_error) <= 500)
);

-- The claim: due jobs, and running jobs whose lease expired.
CREATE INDEX IF NOT EXISTS jobs_due_idx
    ON jobs (organization_id, run_at) WHERE state = 'queued';
CREATE INDEX IF NOT EXISTS jobs_lease_idx
    ON jobs (organization_id, lease_until) WHERE state = 'running';
-- One job for each org, kind and key.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_key
    ON jobs (organization_id, kind, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
```

- **Put `REFERENCES` before `DEFAULT`.** The ratchet in
  `tests/unit/test_tenancy_boundary.py` matches the two with no comma between.
- **RLS.** Copy the `DO $rls$` block of 307 (:145-174). Guard it on
  `relrowsecurity AND relforcerowsecurity`. It runs ENABLE and FORCE ROW LEVEL
  SECURITY. It makes one policy, `jobs_tenant_isolation`, with USING and WITH
  CHECK on `organization_id = current_setting('app.tenant_id', true)::uuid`.
- **Write no GRANT line.** The default privileges give `acb_app` SELECT,
  INSERT, UPDATE and DELETE on each new table (`saas_multitenancy_handover.md`
  §H3 :383, `scripts/staging_refresh.sh:132`,
  `276_email_detached_purges.sql:278-280`). 307 has no GRANT line either.
- **Expand only (R6).** One new table and three indexes. No running code names
  `jobs`, so no old INSERT can miss a NOT NULL column.
- **Regenerate the tenant phases** with
  `uv run python scripts/gen_tenant_migration.py`, and commit the four changed
  files under `infra/postgres/generated/`. If you skip this step,
  `test_the_generated_set_on_disk_matches_the_tables_that_exist` fails.
- **A test finds the file by its content,** never by its number. Copy
  `tests/unit/test_reminder_loop.py:83-85`, and match
  `CREATE TABLE IF NOT EXISTS jobs (`.

---

## 4. The API

The package is `apps/services/gateway/gateway/jobs/`. Every named consumer
runs in the gateway, and the house runs each background loop there
(`consumer.py:11-16`). `__init__.py` exports `enqueue`, `register`, `Job`,
`Enqueued` and `PermanentJobError`, and nothing else.

### 4.1 Enqueue

```python
async def enqueue(db, kind: str, payload: dict | None = None, *,
                  run_at: datetime | None = None,
                  idempotency_key: str | None = None,
                  max_tries: int = 5) -> Enqueued   # id, state, created
```

1. **Check the arguments before any SQL.** A bad argument raises `ValueError`
   and sends no statement, so it never aborts the caller's transaction
   (H-249). `kind` matches `[a-z][a-z0-9_.]{2,63}`. `payload` is a dict, and
   `json.dumps(payload)` is at most 8192 bytes. `run_at` is None or holds a
   time zone. `idempotency_key` holds 1 to 200 characters. `max_tries` is from
   1 to 20.
2. **Refuse a tenant key in the payload.** The top level must not hold
   `organization_id`, `org_id`, `org`, `tenant` or `tenant_id`. A handler that
   finds an org in its payload can bind it, and H4 allows only the column.
3. **Check the tenant.** Run `SELECT current_setting('app.tenant_id', true)`
   on `db`. If the value is NULL or empty, raise `TenantUnbound` and write
   nothing (the H4 refusal). This also refuses a session from `get_db()`.
4. **Insert.** The column default fills `organization_id` from the bound
   tenant. `enqueue` takes no org argument, so no caller can pass one from
   request input (R5e). Pass `run_at` as a `datetime`, never as a string.

   ```sql
   INSERT INTO jobs (kind, payload, idempotency_key, max_tries, run_at)
   VALUES (:kind, CAST(:payload AS jsonb), :key, :max_tries,
           COALESCE(CAST(:run_at AS timestamptz), now()))
   ON CONFLICT (organization_id, kind, idempotency_key)
       WHERE idempotency_key IS NOT NULL DO NOTHING
   RETURNING id, state
   ```

5. **A duplicate key adds no row.** If the insert returns no row, read the job
   by `kind` and `idempotency_key`, and return it with `created=False`.
   `ON CONFLICT DO NOTHING` does not abort the transaction.
6. **Do not commit.** The caller's `tenant_session` commits the job with the
   caller's own write, or rolls back both.

The CHECK on the payload allows 16384 bytes, twice the Python cap. So the
Python check always refuses first, also where the text of a `jsonb` value is
longer than `json.dumps` gives.

### 4.2 Claim

`store.claim(org, limit=1) -> list[Job]` opens `tenant_session(org)`, runs one
statement and commits.

```sql
UPDATE jobs j
   SET state = 'running', tries = j.tries + 1,
       lease_until = now() + make_interval(secs => :lease_s),
       started_at = now(), updated_at = now()
 WHERE j.id IN (
       SELECT id FROM jobs
        WHERE (state = 'queued' AND run_at <= now())
           OR (state = 'running' AND lease_until <= now())
        ORDER BY run_at, id
        LIMIT :limit
        FOR UPDATE SKIP LOCKED)
RETURNING j.id, j.organization_id, j.kind, j.payload, j.tries,
          j.max_tries, j.run_at, j.started_at
```

- **The lease is `LEASE_S = 300.0`** for each kind in v1. Pass it as a float.
  The handler timeout is `HANDLER_TIMEOUT_S = 240`. So a slow handler fails
  before its lease expires, and no second worker runs a live job.
- **The database clock is the only clock.** Each time comes from `now()`. A
  test moves time with the admin engine: it writes `run_at` or `lease_until`
  into the past.
- **`_after_claim(db)`** is a no-op coroutine that `claim` awaits after the
  UPDATE and before the commit. The two-worker test (JQ-A4) patches it, so two
  claims are open at the same time. Do not remove it: mutation JQ-M1 needs it.
- **Each claim adds 1 to `tries`,** and a claim after a crash does too. So a
  job that crashes the process each time dies at its limit.

### 4.3 Handlers

`register(kind, handler)` adds an `async def handler(job: Job) -> None`. A
second `register` of one kind raises `ValueError`. `Job` holds `id`,
`organization_id`, `kind`, `payload`, `tries` and `max_tries`, all from the
row. `registry.py` registers one built-in kind, `jobs.noop`, whose handler
returns at once. The JQ-2 check on staging uses it.

The rules for a handler:

1. It runs its own `tenant_session` for its own writes. The worker holds no
   session while the handler runs.
2. It is safe to run twice, because the queue runs a job at least once.
3. It does not catch a DB error inside its own `tenant_session`. It lets the
   error raise, so the job retries. A caught error loses the later writes of
   the block, and the job settles `succeeded` (H-249).
4. It raises `PermanentJobError` when a retry cannot help, for example when
   the payload names a deleted record. Then the job goes `dead` at once.
5. Its payload holds ids and small parameters only: no message text, no
   secret and no token. The 8 KB cap fences the size. The rule on the content
   is advisory, and the review checks it.

### 4.4 Settle and retry

`store.settle(org, job, outcome, error=None) -> bool` opens a **new**
`tenant_session(org)` and runs one UPDATE. Each UPDATE has `WHERE id = :id AND
state = 'running' AND tries = :tries`. So a late settle from a lost lease
changes nothing, and returns False.

| Outcome | When | The row after it |
|---|---|---|
| `succeeded` | The handler returned | `state = 'succeeded'`, `finished_at = now()`, `lease_until` and `last_error` NULL |
| `retry` | The handler raised, and `tries < max_tries` | `state = 'queued'`, `run_at = now()` plus `retry_delay_s(tries)`, `lease_until` NULL, `last_error` set |
| `dead` | The handler raised at `tries >= max_tries`. Or it raised `PermanentJobError`, or the kind has no handler, or the claim found `tries > max_tries` | `state = 'dead'`, `finished_at = now()`, `lease_until` NULL, `last_error` set |

- **The retry delay** is `min(3600, 30 * pow(2, min(tries - 1, 16)))`, from
  `retry_delay_s(tries)`. It has the shape of `memory_purge.retry_delay_s`,
  with a base of 30 s and a cap of 1 hour. A base of 300 s breaks the 5-minute
  delivery of BR-2 on the first retry. With the default `max_tries = 5`, the
  delays are 30, 60, 120 and 240 s.
- **`last_error`** holds the class name of the exception, then `: ` and the
  first line of its message, cut to 500 characters. A missing handler writes
  `unknown_kind`. A claim past the limit writes `lease_expired`.
- **Put no `try` inside the claim block or the settle block.** An error there
  goes out of `tenant_session`, which rolls back. The worker catches it one
  level up, outside every session (§5, "One tick").

### 4.5 What the queue promises

- **At least once.** If the process stops after the handler's own commit and
  before the settle, the job runs again when its lease expires.
- **Never two live runs of one job.** The lease and the handler timeout hold
  this, and the `tries` guard on the settle holds the row.
- **No order.** Due jobs run from the oldest `run_at`, but a retry can run
  after a newer job.

---

## 5. The worker

`apps/services/gateway/gateway/jobs/worker.py` copies the shape of
`reminders/loop.py` :103-448.

**The flag.** Add `job_queue_enabled: bool = False` to
`packages/acb_common/acb_common/settings.py`, with a comment block in the
shape of `calendar_reminders` (:425-435). The `_ENABLED` suffix of
`env_guard.PLATFORM_ENV_SUFFIXES` (:302-310) already keeps a tenant from
setting `JOB_QUEUE_ENABLED`, so `env_guard.py` needs no change.
`worker.worker_enabled()` is the one reader, and `/version` carries it as
`job_queue`. With the flag off, `start_job_worker()` logs
`jobs.worker_disabled`, starts no task and returns False.

**`enqueue` works with the flag off.** A caller can be built and tested dark,
and its jobs wait until the flag is on. No kind but `jobs.noop` exists in v1,
so nothing waits in production. Each BR-1b move decides what to do with the
jobs that waited.

**One tick**, every `TICK_SECONDS = 10`:

1. Read the orgs with `email_ingestion.scheduler._list_organizations`, the one
   allowed unbound read. Import it inside the function, as
   `reminders/loop.py:120-123` does. Add no new `get_db()` site.
2. For each org, set `token = bind_tenant(org)`, run `run_org(org)`, and call
   `release_tenant(token)` in `finally`. If an org fails, log
   `jobs.org_failed` with the class name only, and go on to the next org.
3. `run_org(org)` claims one job at a time, up to `ORG_BATCH = 20` jobs. It
   stops early when `_stopping` is set. So one busy org holds the others back
   by 20 jobs at most.

**One job**, in `_run_one(org, job)`:

1. If `job.tries > job.max_tries`, settle `dead` with `lease_expired`, and do
   not run the handler.
2. If the kind has no handler, settle `dead` with `unknown_kind`.
3. Run `await asyncio.wait_for(handler(job), HANDLER_TIMEOUT_S)`.
4. On `asyncio.CancelledError`, raise it again with no settle (see the restart
   rule below).
5. On `PermanentJobError`, settle `dead`. On any other exception, and on
   `TimeoutError`, settle by §4.4. On success, settle `succeeded`.

The handler has closed its sessions when its exception gets to the worker. So
the `except` of the worker swallows no error inside a session.

**Start and stop.** The lifespan in `main.py` calls `start_job_worker()` after
the agent-run sweep (about :538), and logs `gateway.job_worker` with
`started=`. The shutdown half calls `stop_job_worker()` beside the other loop
stops (about :652-685). `stop_job_worker()` sets `_stopping`, waits
`STOP_GRACE_SECONDS = 10` for the tick, then cancels the task.

**Call `mark_background()` once at the top of the worker task.** The worker is
background work, and H-390 measured a burst of jobs that starved member reads.
The mark caps its connections at `db_background_max`.

**Restart.** A cancelled job gets no settle. It stays `running` with its
lease. When the lease expires, the next worker claims it and adds 1 to
`tries`. The gateway unit has `--timeout-graceful-shutdown 5`
(`deploy/hostinger/acb-gateway.service:24`), so the lease, not the stop,
carries a job across a restart (JQ-A2).

**Tenancy (R5, H4).** The worker binds the org that discovery returned, and
the claim runs in `tenant_session(org)`, so RLS returns only that org's jobs.
The handler gets `job.organization_id` from the row. Nothing reads an org from
the payload or from a request. `run_org` with no org raises `TenantUnbound`
from the seam.

**The log line.** The worker logs `jobs.settled` once for each settle, with
`job_id`, `kind`, `org`, `state`, `tries`, `duration_ms` and `lag_ms`
(`started_at` minus `run_at`). A failure adds `error`, the class name only. Use INFO for
`succeeded`, and WARNING for `retry` and `dead`. **No payload value and no
`last_error` text goes into a log line.** A settle that returned False logs
`jobs.lease_lost`.

---

## 6. Rules and their fences (R7)

| Rule | Fence |
|---|---|
| The org of a job comes from the column, never from a payload or a request | JQ-A5, JQ-A6, JQ-A14, JQ-A16 |
| No `try` inside an `async with tenant_session` in `jobs/store.py` or `jobs/worker.py` | JQ-A16 (an AST check) |
| No new unbound `get_db()` site | `tests/unit/test_db_engine_seam.py`, which exists |
| The table is tenant-scoped | JQ-A19 and `tests/unit/test_tenancy_boundary.py` |
| The settle log line holds no payload | JQ-A17 |
| A handler is safe to run twice. A payload holds no message text, secret or token | **Advisory.** The review of each consumer checks it |

---

## 7. Acceptance (BR-1a)

Each `test_r8_` case runs on a real Postgres as the NOBYPASSRLS role (R8). It
uses `pytestmark = _DB_GATE`, `promoted` and `app_engine` from
`tests/unit/test_h3_rls_promotion_rehearsal.py` (:94-102, :131, :231), and
`tenant_engine_scope` from `tests/unit/_tenant_ladder.py`. The model is
`tests/unit/test_reminder_loop.py`. A test registers its kinds with
`monkeypatch.setitem` on the registry, and sets the flag with `monkeypatch`.
The R8 cases go in `tests/unit/test_job_queue_r8.py`, and the others in
`tests/unit/test_job_queue.py`.

| # | Done when | Test |
|---|---|---|
| JQ-A1 | A job enqueued in one engine scope runs after that scope closes and a new one opens. It ends `succeeded` with `tries = 1` | `test_r8_a_job_queued_before_a_restart_runs_after_it` |
| JQ-A2 | The real `start_job_worker` runs a handler that blocks. `stop_job_worker`, with `STOP_GRACE_SECONDS` patched to 0.1 s, cancels it. The row stays `running` with its lease, and a claim takes nothing. The test moves `lease_until` into the past. Then the next run ends `succeeded` with `tries = 2` | `test_r8_a_job_in_flight_at_a_restart_runs_again_after_its_lease` |
| JQ-A3 | A handler that always raises, with `max_tries = 3`, runs 3 times. After tries 1 and 2 the row is `queued`, `run_at - updated_at` is exactly 30 s and then 60 s, and `last_error` starts with the class name. After try 3 it is `dead` with `finished_at`. A fourth run claims nothing | `test_r8_a_failed_job_retries_to_its_limit_then_goes_dead` |
| JQ-A4 | Two `run_org` calls run at the same time over 6 jobs, with `_after_claim` patched to the `_Pairing` of `test_reminder_loop.py:384-403`. Each job runs once, each worker runs at least one job, and `pairing.met >= 1` | `test_r8_two_workers_never_run_one_job_twice` |
| JQ-A5 | Org A has 1 job and org B has 2. `run_org(B)` runs the 2 jobs of B, and the job of A stays `queued`. Each handler sees `current_tenant()` equal to `job.organization_id`. In its own session it counts only the rows of its own org | `test_r8_each_org_runs_its_own_jobs_in_its_own_tenant` |
| JQ-A6 | `enqueue` on a session with no tenant raises `TenantUnbound` and writes no row. A `SELECT 1` on the same session still works | `test_r8_enqueue_with_no_tenant_raises_tenant_unbound` |
| JQ-A7 | Two `enqueue` calls with one key return one id, and the second has `created=False`. Only one row exists. The same key with another kind, or in another org, adds a row | `test_r8_a_duplicate_idempotency_key_adds_no_row` |
| JQ-A8 | A handler runs `SELECT 1/0` in its own `tenant_session` and lets it raise. The job settles `queued` with `tries = 1` and `last_error` set | `test_r8_the_settle_survives_a_handler_db_error` |
| JQ-A9 | Worker 1 claims a job, and its lease expires. Worker 2 claims the job again. Then the settle of worker 1 returns False and changes nothing | `test_r8_a_late_settle_of_a_lost_lease_changes_nothing` |
| JQ-A10 | An unknown kind and a `PermanentJobError` each go `dead` after one run, with no retry | `test_r8_unknown_kind_and_permanent_error_go_dead_at_once` |
| JQ-A11 | A handler that runs past `HANDLER_TIMEOUT_S`, patched to 0.2 s, settles `retry` with `TimeoutError` | `test_r8_a_handler_past_its_timeout_retries` |
| JQ-A12 | With the flag off, `start_job_worker()` returns False, `worker._task` is None and `run_tick()` returns `{}`. An enqueued job stays `queued` | `test_r8_the_flag_off_starts_no_worker_and_enqueue_still_writes` |
| JQ-A13 | A `running` job at `tries = max_tries` with an expired lease goes `dead` with `lease_expired`. Its handler does not run | `test_r8_a_crash_on_the_last_try_goes_dead_without_a_run` |
| JQ-A14 | `enqueue` refuses a bad kind, a payload with `organization_id`, a payload over 8192 bytes, a list payload, a `run_at` with no zone and `max_tries = 0`. Each refusal comes before any SQL | `test_enqueue_refuses_bad_arguments_before_any_sql` (a fake session counts the calls) |
| JQ-A15 | `retry_delay_s` gives 30, 60, 120 and 240 for tries 1 to 4, and 3600 for try 20 | `test_retry_delay_doubles_from_30_s_to_a_1_h_cap` |
| JQ-A16 | No `try` sits inside an `async with tenant_session` in `store.py` or `worker.py`. Each `bind_tenant(` call in `worker.py` takes the org of discovery | `test_no_try_inside_a_tenant_session_in_the_queue` and `test_the_worker_binds_only_the_discovered_org` |
| JQ-A17 | One run that succeeds and one that fails write one `jobs.settled` line each, with the keys of §5. No payload value is in a log argument | `test_r8_one_log_line_for_each_settle_and_no_payload` |
| JQ-A18 | `/version` carries `job_queue`: true with the flag on, false with it off | `test_version_carries_job_queue` |
| JQ-A19 | `discover_tables()` finds `jobs`, and it is in neither `EXEMPT` nor `HOMONYM_BLOCKED`. `generated/04_policies.sql` holds `ALTER TABLE jobs FORCE  ROW LEVEL SECURITY` | `test_tenant_coverage.py::test_the_job_table_is_tenant_scoped` |
| JQ-A20 | The admin engine runs the migration file a second time on the promoted database with no error | `test_r8_the_migration_applies_twice` |

---

## 8. Mutations (R8: tenancy and durability)

Commit before each mutation. Run the named test, see it fail, then restore the
file with `git checkout -- <file>`. Write each result into the docstring of
`test_job_queue_r8.py`, as `test_reminder_loop.py:27-42` does.

| # | Mutation | The test that must fail |
|---|---|---|
| JQ-M1 | `_CLAIM_SQL`: drop `SKIP LOCKED`. Then, as a second run, drop `FOR UPDATE SKIP LOCKED` | JQ-A4: `pairing.met` stays 0, or a job runs twice |
| JQ-M2 | `_CLAIM_SQL`: drop the arm `OR (state = 'running' AND lease_until <= now())` | JQ-A2 |
| JQ-M3 | The settle: drop `AND tries = :tries` | JQ-A9 |
| JQ-M4 | `enqueue`: drop the tenant check | JQ-A6 |
| JQ-M5 | `enqueue`: drop the `ON CONFLICT` clause | JQ-A7 |
| JQ-M6 | `retry_delay_s`: return 0 | JQ-A3 and JQ-A15 |
| JQ-M7 | `_run_one`: drop the `tries > max_tries` check | JQ-A13 |

---

## 9. Files (re-verify at dispatch)

New, under `apps/services/gateway/gateway/jobs/`:

- `__init__.py`: the exports of §4.
- `store.py`: the SQL, `enqueue`, `claim`, `settle`, `retry_delay_s`,
  `_after_claim` and `LEASE_S`.
- `registry.py`: `register`, `get` and `jobs.noop`.
- `worker.py`: `worker_enabled`, `run_org`, `run_tick`, `start_job_worker`,
  `stop_job_worker` and the constants of §5.

New elsewhere: `infra/postgres/<n>_jobs.sql` (R1),
`tests/unit/test_job_queue_r8.py` and `tests/unit/test_job_queue.py`.

Changed:

- `packages/acb_common/acb_common/settings.py`: the flag.
- `apps/services/gateway/gateway/main.py`: the start and the stop, the field
  `job_queue` on `Version` (about :2104), and its value in `version()` (about
  :2211).
- `infra/postgres/generated/01_add_columns.sql` to `04_policies.sql`:
  regenerated (§3).
- `tests/unit/test_tenant_coverage.py`: JQ-A19, in the shape of
  `test_the_reminder_tables_are_tenant_scoped`.
- `infra/AGENTS.md` and `apps/services/gateway/AGENTS.md`: one line each.
- This spec: the status header moves to BUILT in the same PR (R4).

No change: `env_guard.py`, `acb_common/db.py` and `apps/services/ingestion/`.

---

## 10. Verification commands

R8 needs the database. A skip in an R8 run is a failure.

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
# One R8 suite at a time: the promoted fixture drops <db>_h3rls.
uv run pytest tests/unit/test_job_queue_r8.py -v -rs
uv run pytest tests/unit/test_job_queue.py tests/unit/test_tenant_coverage.py \
  tests/unit/test_tenancy_boundary.py tests/unit/test_db_engine_seam.py \
  tests/unit/test_index_completeness.py -q -rs
uv run ruff check apps/services/gateway/gateway/jobs \
  apps/services/gateway/gateway/main.py packages/acb_common/acb_common/settings.py \
  tests/unit/test_job_queue.py tests/unit/test_job_queue_r8.py
uv run ruff check . --select F821,F601,F602,F502,F7,B006
uv run mypy apps/services/gateway/gateway/jobs --ignore-missing-imports
uv run python scripts/affected_tests.py --run
```

Then commit, and run `bash scripts/ci_local.sh --ref HEAD` before the push.

---

## 11. Slices and gates

BR-1a is HIGH tier, because it holds a migration and touches tenancy. So it
keeps the full loop with the Opus review (`.claude/commands/next-ticket.md`
step 1b). Each slice ships dark. File each JQ row as a Metorite task under
epic 479 when it is next.

| Slice | Work | Depends on | Gate |
|---|---|---|---|
| **BR-1a** | §3 to §10 | Nothing | **AGENT-SAFE.** Dark |
| **JQ-2** | Set `JOB_QUEUE_ENABLED=true` on staging (`dev.metorite.com`). In one org, enqueue one `jobs.noop` with psql, in a transaction that sets `app.tenant_id`. Show the row `succeeded` and the `jobs.settled` line in the journal | BR-1a on staging | **AGENT-SAFE** under the dated `env-write` and `enforcement-flip` grants, until 2026-11-30. After that date, **OWNER-GATE** |
| **JQ-3** | The same on production, after the 21:30 UTC release carries BR-1a. Write one `jobs.noop` row in one org, and nothing else | JQ-2 | As JQ-2. Staging first, always |
| **BR-1b** | Move reminders, send-later and the agent-run sweep onto the queue, one PR for each loop. Each loop keeps its own flag | BR-1a, and JQ-2 for a live check | **AGENT-SAFE** to build. JQ-G1 binds the flip of a loop that mails or messages a person |
| **JQ-4** | Retention: a daily `jobs.prune` deletes `succeeded` rows after 7 days and `dead` rows after 30 days, with an index on `(organization_id, state, finished_at)` | BR-1a | **AGENT-SAFE.** HIGH tier, because it holds a migration |
| **JQ-5** | Metrics: an operator read of the counts by kind and state, the oldest due job and the lag. An alert when a job goes `dead` | BR-1a | **AGENT-SAFE** |
| **BR-Q5** | A progress line on a long job, and lease renewal, so a job can run past 240 s | BR-1a | **AGENT-SAFE** |

The owner gates. An agent refuses each one by name:

| # | Act |
|---|---|
| JQ-G1 | Turn on a job kind, on any box, that sends mail, a WhatsApp message or a push to a real person (CLAUDE.md §3a rule 3) |
| JQ-G2 | Turn on a job kind that spends AI credits |
| JQ-G3 | Flip `JOB_QUEUE_ENABLED` on a box after 2026-11-30, when the dev-phase window closes |

---

## 12. Changelog

- 2026-10-28: Written as the first slice of BR-1a, after a spec audit found no
  owning spec for WS-BRIEF. ACTIVE, agent-proposed. Nothing built.
