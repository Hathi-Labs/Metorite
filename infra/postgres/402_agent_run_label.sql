-- ============================================================================
-- 402_agent_run_label.sql - the display label of an agent session (WS-MCP
-- SW-7b, Metorite task 2858)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md §3.7 item 2 and SW-7b · R1 ·
--   R5 · R6.
--
-- R1: on 2026-11-11 origin/main held 401 as its highest file. Open branches
--   and worktrees held 396, 398, 399, 400 and 401. So this file is 402. Check
--   the number again at merge (scripts/premerge.sh).
--
-- Why: two agent sessions of one member and one client read as one name on
--   the board ("Claude Code"). An agent may now name its own session, for
--   example `backend-worker-3`, when it claims a task or starts a run. The
--   card, the run panel and the runs wire show the name beside the client
--   name.
--
-- What: one nullable column on `pm_agent_runs`, `agent_label`, and one named
--   CHECK on its length: NULL, or 1 to 40 characters. The gateway cleans the
--   value first (`agent_runs.clean_agent_label`): no control or format
--   character, one space for each run of white space, at most 40 characters.
--
-- Display data only: the label grants nothing. No authorization check, no
--   visibility rule, no run slot and no session key reads it. So this file
--   changes no index: `pm_agent_runs_one_open_per_task` and
--   `pm_agent_runs_one_open` stay as they are. Fence:
--   tests/unit/test_agent_label_fence.py.
--
-- Who writes it: only `gateway/routes/projects/agent_runs.py`, for a signed
--   agent, from the body of `POST /projects/tasks/{id}/claim` and of
--   `POST /projects/tasks/{id}/agent-runs`. A member's body that names a
--   label gets 422. A call with no label keeps the old one.
--
-- Expand only (R6): one new nullable column with no default and no
--   backfill, so the ALTER changes only the catalog. Old code names each
--   column of its INSERT, so it never writes this one, and it reads `r.*`
--   with `getattr`, so an extra column changes no old reply. The CHECK passes
--   on NULL, so it goes in NOT VALID and the file then validates it.
--   Idempotent: ADD COLUMN IF NOT EXISTS, and the CHECK is added only while
--   `pg_constraint` lacks its name. A second run changes nothing.
--
-- Tenancy (R5): `pm_agent_runs` is tenant-scoped, with FORCE RLS and the
--   generated policies. A new column changes neither. So this file adds no
--   table, no policy and no `gen_tenant_migration.EXEMPT` row. The column
--   holds no member address, so the member purge needs no change.
--
-- Never wait for the lock: ALTER TABLE takes ACCESS EXCLUSIVE, and each run
--   write takes this table. So the file sets `lock_timeout` first, as 401
--   does. On a lock timeout the runner tries the whole file again.
--
-- Depends on: 267_pm_agent_runs.sql (the table).
-- Pinned by: tests/unit/test_agent_label_fence.py, which finds this file by
--   CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS agent_label TEXT NULL;

DO $pm_label$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_agent_label_length') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_agent_label_length
            CHECK (agent_label IS NULL OR char_length(agent_label) BETWEEN 1 AND 40)
            NOT VALID;
    END IF;
END $pm_label$;

ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_agent_label_length;

COMMENT ON COLUMN pm_agent_runs.agent_label IS
    'WS-MCP SW-7b. The display label that the agent gave its session, such as backend-worker-3. Display data only: no authorization check, run slot or session key reads it. NULL when the agent named no session. Only routes/projects/agent_runs.py writes it.';
