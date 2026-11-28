-- ============================================================================
-- 518_agent_run_role.sql - the role of an agent session on its run
-- (WS-MCP SW-59a, Metorite task 4135)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md SW-59 (amended 2026-11-28),
--   §3.10.10 and §3.12.10 · R1 · R5 · R6 · R7.
--
-- R1: the lane booked 518 on 2026-11-28, and main held 516. So this file
--   takes 518. Check the number again at merge (scripts/premerge.sh).
--
-- Why: the orchestration map shows the topology of agent work. Each session
--   says what it does: it orchestrates, builds, verifies, reviews or merges.
--   An agent may now name its role when it claims a task or starts a run.
--
-- What: one nullable column on `pm_agent_runs`, and one named CHECK:
--   * `role`: NULL, or one of `orchestrator`, `builder`, `verifier`,
--     `reviewer` and `merger`.
--   The gateway takes the value only from the closed list of its body schema
--   (`agent_runs.AGENT_RUN_ROLES`). Any other value is 422 at the schema.
--
-- Display data only: the role grants nothing. No authorization check, no
--   visibility rule, no run slot and no session key reads it. So this file
--   changes no index: `pm_agent_runs_one_open_per_task` and
--   `pm_agent_runs_one_open` stay as they are. A self-declared role is
--   evidence for a reviewer and never a gate (§3.10.10). Fence:
--   tests/unit/test_agent_label_fence.py.
--
-- Who writes it: only `gateway/routes/projects/agent_runs.py`, for a signed
--   agent, from the body of `POST /projects/tasks/{id}/claim` and of
--   `POST /projects/tasks/{id}/agent-runs`. A member's body that names it
--   gets 422. A call with no role keeps the old one. A later role replaces it.
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
--   write takes this table. So the file sets `lock_timeout` first, as 470
--   does. On a lock timeout the runner tries the whole file again.
--
-- Depends on: 267_pm_agent_runs.sql (the table).
-- Pinned by: tests/unit/test_agent_label_fence.py and
--   tests/unit/test_agent_run_role_r8.py, which find this file by CONTENT,
--   never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS role TEXT NULL;

DO $pm_role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_agent_runs_role_check') THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_role_check
            CHECK (role IS NULL
                   OR role IN ('orchestrator', 'builder', 'verifier', 'reviewer', 'merger'))
            NOT VALID;
    END IF;
END $pm_role$;

ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_role_check;

COMMENT ON COLUMN pm_agent_runs.role IS
    'WS-MCP SW-59a. The role of the agent session: orchestrator, builder, verifier, reviewer or merger. Display data only: no authorization check, run slot or session key reads it. NULL when the agent named no role. Only routes/projects/agent_runs.py writes it.';
