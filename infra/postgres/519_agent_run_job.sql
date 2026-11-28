-- ============================================================================
-- 519_agent_run_job.sql - an external job of an agent run: a child run with a
-- job kind (WS-MCP SW-116, Metorite task 4206)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md §3.12.11 ("The job") and the
--   slice SW-116 · R1 · R5 · R6.
--
-- R1: the lane booked 519 on 2026-11-28 (~/lanes/reserve_migration.sh). Main
--   held 516 that day, and an open branch (SW-59a, task 4135) held 518. So
--   this file takes 519. Check the number again at merge (scripts/premerge.sh).
--
-- Why: the owner wants to see the downstream work of each agent on the map
--   (AMR-6, task 4205). That work is its sub-agents and its jobs: a GitHub
--   Actions run, local CI, the merge queue, a release or a review. A
--   sub-agent is already a child run (SW-39, migration 475). A job is a child
--   run too, so this file adds no table.
--
-- What: one nullable column and two named CHECKs on `pm_agent_runs`.
--   * `job_kind`: NULL, or one of `github_action`, `ci_local`,
--     `merge_queue`, `release` and `review`. The CHECK
--     `pm_agent_runs_job_kind_check` holds the five values.
--   * `pm_agent_runs_job_on_child`: `job_kind IS NULL OR kind = 'child'`. A
--     plain run never has a job kind. A sub-agent keeps NULL.
--   The job keeps its name in `agent_label` and its link in `external_link`
--   (267, https only, at most 2048 characters). The `kind` CHECK of 475 does
--   not change. The gateway gives an open job the status `abandoned` when its
--   parent ends first (`agent_runs._end_children`). 289 already holds that
--   status, so the status CHECK does not change.
--
-- Display data only: a job grants nothing. No authorization check, no
--   visibility rule, no run slot, no merge gate and no session key reads
--   `job_kind`. So this file adds no index: the open jobs of a run come
--   through `pm_agent_runs_parent` (475), as the open sub-agents do. Fence:
--   tests/unit/test_agent_label_fence.py.
--
-- Who writes it: only `gateway/routes/projects/agent_runs.py`, through
--   `POST /projects/agent-runs/{run_id}/jobs` (the edge tool `report_job`).
--
-- Expand only (R6): one new nullable column with no default and no backfill,
--   so the ADD changes only the catalog. Old code names each column of its
--   INSERT, so it never writes `job_kind`. Old code reads `r.*` with
--   `getattr`, so an extra column changes no old reply. Old code counts every
--   child of a run toward the cap of 20 sub-agents, so a job made by new code
--   counts there too until the new code runs. That is a tighter cap, never a
--   wider one. Each CHECK passes on NULL, so it goes in NOT VALID, and the
--   file then validates it under SHARE UPDATE EXCLUSIVE.
--   Idempotent: ADD COLUMN IF NOT EXISTS, and each CHECK is added only while
--   `pg_constraint` lacks its name. A second run changes nothing.
--
-- No outer transaction, as 470 and 475: each statement commits by itself, so
--   each VALIDATE holds only SHARE UPDATE EXCLUSIVE, and run reads and writes
--   pass while it scans. A failure part way leaves a state that the next run
--   of the file completes, because each step is idempotent.
--
-- Tenancy (R5): `pm_agent_runs` is tenant-scoped, with FORCE RLS and the
--   generated policies. A new column changes neither. So this file adds no
--   table, no policy and no `gen_tenant_migration.EXEMPT` row. The column
--   holds no member address, so the member purge needs no change.
--
-- Never wait for the lock: ALTER TABLE takes ACCESS EXCLUSIVE, and each run
--   write takes this table. So the file sets `lock_timeout` first, as 470
--   does. On a lock timeout the runner tries the whole file again.
--
-- Depends on: 267_pm_agent_runs.sql (the table, `external_link`),
--   289_agent_run_exclusive_claim.sql (the status `abandoned`),
--   475_agent_run_child.sql (`kind`).
-- Pinned by: tests/unit/test_agent_run_jobs_r8.py and
--   tests/unit/test_agent_label_fence.py, which find this file by CONTENT,
--   never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS job_kind TEXT NULL;

DO $pm_job$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_job_kind_check') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_job_kind_check
            CHECK (job_kind IS NULL
                   OR job_kind IN ('github_action', 'ci_local', 'merge_queue',
                                   'release', 'review'))
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_job_on_child') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_job_on_child
            CHECK (job_kind IS NULL OR kind = 'child')
            NOT VALID;
    END IF;
END $pm_job$;

ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_job_kind_check;
ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_job_on_child;

COMMENT ON COLUMN pm_agent_runs.job_kind IS
    'WS-MCP SW-116. The kind of an external job of a run: github_action, ci_local, merge_queue, release or review. Set only on a child (kind = child). NULL for a sub-agent and for a plain run. The job name is in agent_label and its link in external_link. Display data only: no authorization check, run slot, merge gate or session key reads it. Only routes/projects/agent_runs.py writes it.';
