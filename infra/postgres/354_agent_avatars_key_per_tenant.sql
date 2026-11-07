-- ============================================================================
-- 354_agent_avatars_key_per_tenant.sql - one avatar override per organization
-- and agent name. The EXPAND half of an expand/contract change (H-201d).
-- ============================================================================
-- Spec: project-docs/specs/projects_ai_chat.md §21.13 · HANDOFF H-201 ·
-- Metorite task 1719 · R1 · R5 · R6 · R8.
-- R1: origin/main held 348 as its highest file on 2026-11-07. Open branches
-- hold 349 (mt-1170f52b-cc2-today), 350, 351, 352 and 353. So this file is
-- 354. Check the number again at merge.
--
-- What: (1) `agent_avatars.organization_id` on the NUMBERED ladder, only where
--       it is missing. (2) the unique index `uq_agent_avatars_org_agent` on
--       `(organization_id, agent_name)`.
--
-- **The fault.** Migration 64 made `agent_name` alone the primary key. The
-- generated tenancy phases added `organization_id` and FORCE RLS, and kept
-- that key. So when org A holds a row for a name, org B's upsert of the same
-- name meets A's row on the key. Org B cannot see that row under RLS, and the
-- write fails. Each organization must be able to keep its own look for an
-- agent name.
--
-- **Expand now, contract one release later (R6).** The deploy applies
-- migrations BEFORE it restarts the services. So the OLD gateway runs against
-- this schema for a short time. The old `_save_avatar` names
-- `ON CONFLICT (agent_name)`, and Postgres needs a unique rule on exactly
-- `(agent_name)` for that clause. This file therefore keeps the old primary
-- key and only adds the new index. Old code works on the new schema.
--
--   Release N (this file): both keys exist. The new `_save_avatar` names
--     `ON CONFLICT (organization_id, agent_name)`. A same-org save updates
--     the row. A save of a name that ANOTHER org holds still fails, now with
--     a unique violation on `agent_avatars_pkey`. It never reaches the row of
--     the other org. The R8 suite pins this behaviour on purpose.
--   Release N+1 (the contract, a later numbered file): drop
--     `agent_avatars_pkey`. Only then can org B save a name that org A holds.
--     It must NOT ship with this file. If it did, the old code's upsert would
--     fail on every save until the restart, because no unique rule on
--     `(agent_name)` would remain. HANDOFF H-443 carries that step.
--
-- **NULL organization_id.** Production carries the generated phases 01 to 04
-- (see migration 223). Phase 3 sets `organization_id` NOT NULL on this table,
-- so production holds no NULL row. A database that never ran the tenancy
-- phases can hold NULL rows. A unique index treats each NULL as distinct, so
-- it does not constrain those rows, and no tenant can read them under RLS.
-- Until the contract, the old primary key still keeps each name once.
--
-- **No duplicate can block the build.** While `agent_name` alone is the
-- primary key, each name occurs once in the table. So no two rows can share
-- `(organization_id, agent_name)`, and the CREATE cannot fail on data.
--
-- **Two starting points, one file** (the same as migration 223).
--   * Production: the column exists, NOT NULL, with `agent_avatars_org_fk`.
--     `ADD COLUMN IF NOT EXISTS` then skips the whole column clause, the
--     inline REFERENCES too, so no second foreign key appears.
--   * A fresh ladder database: no tenancy phase ran (the ladder never replays
--     `generated/`). The column is created here, so the index has a column.
--
-- Depends on: 64_agent_avatars.sql (the table and its primary key),
--             130_org_access_control.sql (organization).
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE UNIQUE INDEX IF NOT EXISTS.
-- No CONCURRENTLY: the table holds one row per customized agent, and a failed
-- concurrent build leaves an INVALID index behind (see migration 223).
-- Pinned by tests/unit/test_agent_avatars_key_per_tenant_r8.py.
-- ============================================================================

BEGIN;

-- ── 1. The column, only where the tenancy phases did not add it ─────────────
--
-- Same type and default as `generated/01_add_columns.sql`, plus the cascade
-- that the purge path needs. `REFERENCES` precedes `DEFAULT`, as in 223.
ALTER TABLE agent_avatars
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;


-- ── 2. One override per organization and agent name ─────────────────────────
--
-- The old primary key stays. The contract release drops it.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_avatars_org_agent
    ON agent_avatars (organization_id, agent_name);

COMMENT ON INDEX uq_agent_avatars_org_agent IS
    'H-201d: one avatar override per organization and agent name. The '
    'upsert names it as its conflict target. agent_avatars_pkey on '
    'agent_name alone stays until the contract release drops it.';

COMMIT;
