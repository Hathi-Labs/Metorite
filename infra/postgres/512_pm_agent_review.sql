-- ============================================================================
-- 512_pm_agent_review.sql - the wait of agent work for a person
-- (WS-MCP SW-18a, Metorite task 349)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md SW-18a · R1 · R5 · R6.
--
-- R1: the builder booked 408 on 2026-11-11. On 2026-11-27 origin/main held
--   511 as its highest file, and the lane booked 512. So this file is 512.
--   Check the number again at merge (scripts/premerge.sh).
--
-- Why: an outside agent can move a task to done (EA-6f). SW-18a lets a lead
--   say that the agent work of a project waits for a person first. Then an
--   agent's move to done lands in the review lane of the task, and a named
--   reviewer or a project lead approves it.
--
-- The setting is NOT in this file. On 2026-11-11 the owner chose two columns
--   on `pm_projects`. On 2026-11-26 the owner chose the policy store of SW-94
--   (migration 404, `pm_agent_policies`) in their place. The keys
--   `review_required` and `review_reviewer` hold the setting, and
--   `agent_policies.policy_for` reads them. So this file adds no project
--   column. The columns below are the state of one wait on one task, not a
--   setting.
--
-- What: two columns on `pm_tasks`.
--   pm_tasks.agent_reviewer               TEXT. The reviewer of the agent
--                                         work that waits, or NULL. NULL with a
--                                         wait means a lead decides.
--   pm_tasks.agent_review_requested_at    TIMESTAMPTZ. When the agent work
--                                         started to wait. NULL means no wait.
--   The CHECK `pm_tasks_agent_review_whole` keeps a reviewer only beside a
--   wait.
--
-- Who writes them: the PATCH of a task and the report of a Check write both
--   columns when an agent's done lands in review
--   (`gateway/routes/projects/agent_review.py`). `core.apply_status_transition`
--   clears them on every other status move, and `POST /projects/tasks/{id}/
--   agent-review` clears them when a person approves. While
--   `AGENT_REVIEW_ENABLED` is off, no writer sets them, so every row keeps NULL.
--
-- Expand only (R6): both columns are nullable with no default and no
--   backfill, so Postgres rewrites no row. The old gateway names each column
--   of its INSERT and UPDATE, so it never writes one of these.
--   `row_to_model` maps by model field, and no model names them, so no old
--   reply carries them. The CHECK passes on the old rows, so it is added NOT
--   VALID and then validated. VALIDATE takes only SHARE UPDATE EXCLUSIVE.
--   Idempotent: ADD COLUMN IF NOT EXISTS, and the CHECK is added only while
--   `pg_constraint` lacks its name. A second run changes nothing.
--
-- The field trigger of 304 (`trg_pm_tasks_stamp_fields`) compares every
--   column through `to_jsonb`, so it sees a change of the two task columns
--   with no change of its own. A review write moves `fields_at`, and a
--   version from before it gets 412.
--
-- Tenancy (R5): `pm_tasks` is tenant-scoped, with FORCE RLS and the
--   generated policies. New columns change neither. So this file adds no
--   table, no policy and no `gen_tenant_migration.EXEMPT` row.
--
-- The member purge: `agent_reviewer` holds a member address. A purge deletes
--   no `pm_` row (D63), as for `pm_projects.lead`. A purged reviewer cannot
--   sign in, so the address grants nothing, and a lead still approves.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE, and every
--   task write takes `pm_tasks`. So the file sets `lock_timeout` first, as
--   304 and 401 do. On a lock timeout the runner tries the whole file again.
--
-- Depends on: 146_projects.sql (the table).
-- Pinned by: tests/unit/test_pm_agent_review_r8.py (R8), which finds this
--   file by CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS agent_reviewer TEXT;
ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS agent_review_requested_at TIMESTAMPTZ;

DO $pm_review$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_tasks_agent_review_whole') THEN
        ALTER TABLE pm_tasks ADD CONSTRAINT pm_tasks_agent_review_whole
            CHECK (agent_reviewer IS NULL OR agent_review_requested_at IS NOT NULL)
            NOT VALID;
    END IF;
END $pm_review$;

ALTER TABLE pm_tasks VALIDATE CONSTRAINT pm_tasks_agent_review_whole;

COMMENT ON COLUMN pm_tasks.agent_reviewer IS
    'SW-18a: the reviewer of the agent work that waits, or NULL for a project lead. Only gateway/routes/projects/agent_review.py writes it.';
COMMENT ON COLUMN pm_tasks.agent_review_requested_at IS
    'SW-18a: when the agent work of this task started to wait for a person. NULL means no wait. Only gateway/routes/projects/agent_review.py writes it.';
