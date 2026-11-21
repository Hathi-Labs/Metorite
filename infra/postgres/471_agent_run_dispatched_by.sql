-- ============================================================================
-- 471_agent_run_dispatched_by.sql - the run that dispatched an agent run
-- (WS-MCP SW-107, Metorite task 3628)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md §3.12.3 ("Who dispatched
--   whom"), §3.12.4 rule 2, SW-107 and Q20 · R1 · R5 · R6.
--
-- R1: main held 468 on 2026-11-21. Open pull request 2505 held 469, and
--   open pull request 2692 (SW-104) held 470. So this file takes 471. Check
--   the number again at merge (scripts/premerge.sh).
--
-- Why: the orchestration map draws a line from an orchestrator to each lane
--   that it dispatched. A lane names the run of its dispatcher when it claims
--   a task or starts a run.
--
-- What: one nullable column on `pm_agent_runs`:
--   * `dispatched_by_run_id`: NULL, or the id of another run of the same
--     table. The foreign key `pm_agent_runs_dispatched_by_fk` references
--     `pm_agent_runs (id)` with ON DELETE SET NULL. So a delete of the
--     dispatcher's run keeps the lane's run, and only the line goes.
--   The gateway keeps a value only when the caller's tenant session can read
--   that run, it is not the run itself, and it makes no loop within 5 steps
--   (`agent_runs.set_run_dispatcher`). Else it drops the value and the claim
--   still works, with a `warning`.
--
-- Display data only: the value grants nothing. No authorization check, no
--   visibility rule, no run slot and no session key reads it. Fence:
--   tests/unit/test_agent_label_fence.py.
--
-- No index, on purpose. The fence above refuses an index of `pm_agent_runs`
--   that names the column, because an index is how a slot rule would start to
--   read it. The cost: ON DELETE SET NULL looks for the children of each
--   deleted run with a scan of the table. A run is deleted only with its task
--   or its organization, and both are rare. A later file can add a partial
--   index CONCURRENTLY, if a delete grows slow, with a change to the fence.
--
-- RLS and the foreign key (R5): `pm_agent_runs` has FORCE RLS. Postgres runs
--   the checks of a foreign key and its SET NULL action with no row security,
--   so the key itself does not stop a reference to a run of another
--   organization. The gateway is the guard: it reads the named run under the
--   caller's tenant session, so RLS hides a run of another organization, and
--   the value then drops. A composite key on (organization_id, id) would need
--   a new UNIQUE index on the table, so this file does not add one. The same
--   one-column self key exists already: `pm_agent_run_activities.in_reply_to`
--   (267). scripts/gen_tenant_migration.py reads only `organization_id` and
--   its policy, so a new column of a table changes no generated phase. So this
--   file adds no table, no policy and no `gen_tenant_migration.EXEMPT` row.
--   The column holds no member address, so the member purge needs no change.
--
-- Expand only (R6): a new nullable column with no default and no backfill,
--   so the ALTER changes only the catalog. Old code names each column of its
--   INSERT, so it never writes this one, and it reads `r.*` with `getattr`,
--   so an extra column changes no old reply. The key goes in NOT VALID, so
--   the ADD scans no row, and the file then validates it. Each value is NULL,
--   so the VALIDATE finds nothing to check.
--   Idempotent: ADD COLUMN IF NOT EXISTS, and the key is added only while
--   `pg_constraint` lacks its name. A second run changes nothing.
--
-- Never wait for the lock: ALTER TABLE takes a strong lock, and each run
--   write takes this table. So the file sets `lock_timeout` first, as 418 and
--   470 do. On a lock timeout the runner tries the whole file again.
--
-- Depends on: 267_pm_agent_runs.sql (the table).
-- Pinned by: tests/unit/test_agent_label_fence.py and
--   tests/unit/test_agent_run_dispatch_r8.py, which find this file by
--   CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS dispatched_by_run_id UUID NULL;

DO $pm_dispatch$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_dispatched_by_fk') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_dispatched_by_fk
            FOREIGN KEY (dispatched_by_run_id) REFERENCES pm_agent_runs (id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END $pm_dispatch$;

ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_dispatched_by_fk;

COMMENT ON COLUMN pm_agent_runs.dispatched_by_run_id IS
    'WS-MCP SW-107. The run of the session that dispatched this run, in the same organization. Display data only: no authorization check, run slot, index or session key reads it. NULL when the agent named no dispatcher, or when the dispatcher run is gone. Only routes/projects/agent_runs.py writes it.';
