-- ============================================================================
-- 470_agent_run_place_model.sql - the machine, the kind of machine and the
-- model of an agent session (WS-MCP SW-104, Metorite task 3625)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md §3.12.3, §3.12.4 rule 2,
--   §3.12.7 rules 5 and 6, SW-104 and Q19 · R1 · R5 · R6.
--
-- R1: main held 468 on 2026-11-21, and an open pull request held 469. So this
--   file takes 470. Check the number again at merge (scripts/premerge.sh).
--
-- Why: the orchestration map shows each session with its machine and its
--   model, for example "box-loop · metorite-home · Sonnet 5.5". An agent may
--   now name its machine, the kind of that machine and its model, when it
--   claims a task or starts a run (Q19: yes).
--
-- What: three nullable columns on `pm_agent_runs`, and one named CHECK on
--   each:
--   * `agent_host`: NULL, or 1 to 60 characters. The name of the machine.
--   * `agent_model`: NULL, or 1 to 60 characters. The name of the model.
--   * `agent_host_kind`: NULL, or one of `computer`, `server` and `cloud`.
--   The gateway cleans the two names first (`agent_runs.clean_agent_place`):
--   no control or format character, one space for each run of white space,
--   at most 60 characters. A box token writes its connection name to
--   `agent_host` and `server` to `agent_host_kind` (SW-50).
--
-- Display data only: the three values grant nothing. No authorization check,
--   no visibility rule, no run slot and no session key reads them. So this
--   file changes no index: `pm_agent_runs_one_open_per_task` and
--   `pm_agent_runs_one_open` stay as they are. Fence:
--   tests/unit/test_agent_label_fence.py.
--
-- Who writes them: only `gateway/routes/projects/agent_runs.py`, for a signed
--   agent, from the body of `POST /projects/tasks/{id}/claim` and of
--   `POST /projects/tasks/{id}/agent-runs`. A member's body that names one
--   gets 422. A call with no value keeps the old one.
--
-- Expand only (R6): three new nullable columns with no default and no
--   backfill, so each ALTER changes only the catalog. Old code names each
--   column of its INSERT, so it never writes these, and it reads `r.*` with
--   `getattr`, so an extra column changes no old reply. Each CHECK passes on
--   NULL, so it goes in NOT VALID and the file then validates it.
--   Idempotent: ADD COLUMN IF NOT EXISTS, and each CHECK is added only while
--   `pg_constraint` lacks its name. A second run changes nothing.
--
-- Tenancy (R5): `pm_agent_runs` is tenant-scoped, with FORCE RLS and the
--   generated policies. A new column changes neither. So this file adds no
--   table, no policy and no `gen_tenant_migration.EXEMPT` row. The columns
--   hold no member address, so the member purge needs no change.
--
-- Never wait for the lock: ALTER TABLE takes ACCESS EXCLUSIVE, and each run
--   write takes this table. So the file sets `lock_timeout` first, as 418
--   does. On a lock timeout the runner tries the whole file again.
--
-- Depends on: 267_pm_agent_runs.sql (the table).
-- Pinned by: tests/unit/test_agent_label_fence.py and
--   tests/unit/test_agent_run_place_model_r8.py, which find this file by
--   CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS agent_host TEXT NULL;
ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS agent_model TEXT NULL;
ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS agent_host_kind TEXT NULL;

DO $pm_place$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_agent_host_length') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_agent_host_length
            CHECK (agent_host IS NULL OR char_length(agent_host) BETWEEN 1 AND 60)
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_agent_model_length') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_agent_model_length
            CHECK (agent_model IS NULL OR char_length(agent_model) BETWEEN 1 AND 60)
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_agent_host_kind_check') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_agent_host_kind_check
            CHECK (agent_host_kind IS NULL
                   OR agent_host_kind IN ('computer', 'server', 'cloud'))
            NOT VALID;
    END IF;
END $pm_place$;

ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_agent_host_length;
ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_agent_model_length;
ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_agent_host_kind_check;

COMMENT ON COLUMN pm_agent_runs.agent_host IS
    'WS-MCP SW-104. The machine of the agent session, such as VJ laptop. A box token writes its connection name. Display data only: no authorization check, run slot or session key reads it. NULL when the agent named no machine. Only routes/projects/agent_runs.py writes it.';
COMMENT ON COLUMN pm_agent_runs.agent_model IS
    'WS-MCP SW-104. The model of the agent session, such as Sonnet 5.5. Display data only: no authorization check, run slot or session key reads it. NULL when the agent named no model. Only routes/projects/agent_runs.py writes it.';
COMMENT ON COLUMN pm_agent_runs.agent_host_kind IS
    'WS-MCP SW-104. The kind of machine: computer, server or cloud. A box token writes server. Display data only: no authorization check, run slot or session key reads it. NULL when the agent named no kind. Only routes/projects/agent_runs.py writes it.';
