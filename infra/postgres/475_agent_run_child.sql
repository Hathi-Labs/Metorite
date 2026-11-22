-- ============================================================================
-- 475_agent_run_child.sql - a child run: one sub-agent under an open run
-- (WS-MCP SW-39, Metorite task 3799)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md §3.11.9, §3.12.7 rule 11
--   and the slice SW-39 · R1 · R5 · R6.
--
-- R1: on 2026-11-22 main held 471. Open pull request 2697 (SW-107, the base
--   of this branch) held 472, and open pull request 2719 held 474. No open
--   pull request held a higher number. 473 was free, but the dispatch of
--   this slice kept it out, so this file takes 475. Check the number again
--   at merge (scripts/premerge.sh).
--
-- Why: the orchestration map opens a session into its sub-agents. A
--   sub-agent of Claude Code makes no claim, so a hook reports it, and the
--   gateway writes it as a child run under the open run of its session.
--
-- What: two columns, three CHECKs, one wider CHECK, one index and one
--   narrower unique index on `pm_agent_runs`.
--   * `kind`: `run` or `child`. NOT NULL with the default `run`. The CHECK
--     `pm_agent_runs_kind_check` holds the two values.
--   * `parent_run_id`: NULL, or the open run that a child runs under. The
--     foreign key `pm_agent_runs_parent_fk` references `pm_agent_runs (id)`
--     with ON DELETE CASCADE, so a child goes with its parent.
--   * `pm_agent_runs_child_parent`: a child has a parent, and only a child
--     has one. So a parent with no child row reads the same as before.
--   * `pm_agent_runs_one_subject` (342) gets a third shape. A run has a task
--     or an app. A child has neither: it shows only under its parent. Each
--     reader of task runs names `task_id IS NOT NULL`, and each reader of
--     app runs names `app IS NOT NULL` (the fence
--     `test_every_pm_agent_runs_query_names_its_subject`). So no list, pill,
--     holder read, lease, `my_work` row, count, sweep or map read meets a
--     child, and no run route loads one. A viewer reaches a child only
--     through its parent, with the visibility of the parent's task (D12).
--   * The index `pm_agent_runs_parent` on `parent_run_id`, for the rows that
--     have one. The cascade of a parent delete and the end of the open
--     children of a parent each find the children through it, with no scan
--     of the table (the lesson of the SW-107b review).
--   * `pm_agent_runs_one_open_per_task` (289) excludes `kind = 'child'`. A
--     child holds no lease and no claim. Its task_id is NULL, so the old
--     index would not refuse it either. The exclusion is the second wall: a
--     child never takes the lease of a task, whatever a later writer puts in
--     its task_id. The rest of the predicate is the predicate of 289.
--
-- The other indexes of the table, one by one:
--   * `pm_agent_runs_one_open` (267, task, member and client): a child has
--     no task, and NULLs never collide in a unique index. The CHECK holds
--     that. So it needs no change, and this file does not lock it again.
--   * `pm_agent_runs_idempotency_key` (267): the gateway writes no key on a
--     child (SW-39). SW-114 decides the key of a replayed start.
--   * `pm_agent_runs_app_member` and `pm_agent_runs_app_open_connection`
--     (342) are partial on `app IS NOT NULL`, so a child is never in them.
--   * No index reads `session_key`. A child has no session key of its own.
--
-- The swap of the lease index, with no window: the file builds the new
--   index under a temporary name, then drops the old one, then gives the new
--   one the old name. All three are in ONE `DO` block, so they are one
--   statement and one transaction. The build takes a SHARE lock, so no run
--   write passes until the block commits, and the DROP takes ACCESS
--   EXCLUSIVE. So no writer ever sees the table with no unique index on the
--   open runs of a task. The new index exists before the old one goes, so a
--   failure at any step leaves the old index in place. The build reads the
--   whole table under the lock. The table holds some thousands of rows, so
--   the build is short, and `lock_timeout` keeps the file from waiting
--   behind a long write. The gateway names the index in its 409 path, so
--   the name stays.
--
-- No outer transaction, as 418, 470 and 472: each statement commits by
--   itself, so each VALIDATE holds only SHARE UPDATE EXCLUSIVE, and run reads
--   and writes pass while it scans. A failure part way leaves a state that
--   the next run of the file completes, because each step is idempotent.
--
-- Expand only (R6). Old code names each column of its INSERT, so its rows
--   take `kind = 'run'` and no parent. It never writes a child, so each new
--   CHECK passes on each row that it writes. The wider subject CHECK only
--   adds a shape. The lease index refuses the same rows as before, because
--   every row that old code writes has `kind = 'run'`. Old code reads `r.*`
--   with `getattr`, so two extra columns change no old reply.
--   `ADD COLUMN ... NOT NULL DEFAULT 'run'`: since Postgres 11 a constant
--   default is stored in the catalog, so the ADD rewrites no row and holds
--   ACCESS EXCLUSIVE only for the catalog change. Each CHECK and the key go
--   in NOT VALID, so the ADD scans no row. The file then validates each one
--   under SHARE UPDATE EXCLUSIVE.
--   Idempotent: ADD COLUMN IF NOT EXISTS. Each constraint is added only
--   while `pg_constraint` lacks it, or lacks `child` in its definition. The
--   index swap runs only while the definition of the lease index lacks
--   `kind`. CREATE INDEX IF NOT EXISTS. A second run changes nothing.
--
-- RLS and the foreign key (R5): `pm_agent_runs` has FORCE RLS. Postgres
--   checks a foreign key with no row security, so the key alone does not
--   hold the organization. The gateway is the guard: it reads and locks the
--   parent in the caller's tenant session, so RLS hides a run of another
--   organization, and the start is refused. The child's `organization_id`
--   takes the default of the bound tenant, as every run insert does. A new
--   column of a table changes no generated phase, so this file adds no
--   table, no policy and no `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: each statement takes this table, and each run
--   write takes it too. So the file sets `lock_timeout` first, as 472 does.
--   On a lock timeout the runner tries the whole file again.
--
-- Depends on: 267_pm_agent_runs.sql (the table),
--   289_agent_run_exclusive_claim.sql (the lease index),
--   342_pm_agent_runs_app_subject.sql (the subject CHECK).
-- Pinned by: tests/unit/test_agent_child_runs_r8.py and
--   tests/unit/test_agent_label_fence.py, which find this file by CONTENT,
--   never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'run';
ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS parent_run_id UUID NULL;

DO $pm_child$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_kind_check') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_kind_check
            CHECK (kind IN ('run', 'child'))
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_child_parent') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_child_parent
            CHECK ((kind = 'child') = (parent_run_id IS NOT NULL))
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_parent_fk') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_parent_fk
            FOREIGN KEY (parent_run_id) REFERENCES pm_agent_runs (id)
            ON DELETE CASCADE
            NOT VALID;
    END IF;
    -- The subject CHECK of 342, with the third shape. Drop and add only
    -- while its definition lacks `child`, so a second run keeps it.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = to_regclass('pm_agent_runs')
                      AND conname = 'pm_agent_runs_one_subject'
                      AND pg_get_constraintdef(oid) LIKE '%child%') THEN
        ALTER TABLE pm_agent_runs DROP CONSTRAINT IF EXISTS pm_agent_runs_one_subject;
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_one_subject
            CHECK (CASE WHEN kind = 'child' THEN task_id IS NULL AND app IS NULL
                        ELSE (task_id IS NULL) <> (app IS NULL) END)
            NOT VALID;
    END IF;
END $pm_child$;

ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_kind_check;
ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_child_parent;
ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_parent_fk;
ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_one_subject;

-- The children of a parent: the cascade and the end of the open children.
CREATE INDEX IF NOT EXISTS pm_agent_runs_parent
    ON pm_agent_runs (parent_run_id)
    WHERE parent_run_id IS NOT NULL;

-- The lease index of 289, without the children. Build, drop, rename, in
-- this one statement (the header says why there is no window).
DO $pm_lease$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes
                    WHERE indexname = 'pm_agent_runs_one_open_per_task'
                      AND indexdef LIKE '%kind%') THEN
        DROP INDEX IF EXISTS pm_agent_runs_one_open_per_task_sw39;
        CREATE UNIQUE INDEX pm_agent_runs_one_open_per_task_sw39
            ON pm_agent_runs (organization_id, task_id)
            WHERE status IN ('created', 'in_progress', 'awaiting', 'stopping')
              AND kind <> 'child';
        DROP INDEX IF EXISTS pm_agent_runs_one_open_per_task;
        ALTER INDEX pm_agent_runs_one_open_per_task_sw39
            RENAME TO pm_agent_runs_one_open_per_task;
    END IF;
END $pm_lease$;

COMMENT ON INDEX pm_agent_runs_one_open_per_task IS
    'WS-55 EA-14a: one OPEN run for each task, whoever holds it. The gate in '
    'routes/projects/agent_runs.py (_gate_task) reads the holder first, and '
    'this index decides a race. WS-MCP SW-39: a child run holds no lease, so '
    'the index leaves out kind = child.';

COMMENT ON COLUMN pm_agent_runs.kind IS
    'WS-MCP SW-39. run: a run of an agent session on a task or an app. child: one sub-agent under an open run (parent_run_id). A child has no task, no app, no lease, no claim and no session key. Only routes/projects/agent_runs.py writes a child.';

COMMENT ON COLUMN pm_agent_runs.parent_run_id IS
    'WS-MCP SW-39. The run that a child runs under, in the same organization. Set only when kind = child. ON DELETE CASCADE. When the parent ends, the gateway ends its open children (agent_runs._end_children).';

