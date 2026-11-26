-- ============================================================================
-- 505_pm_task_refs_landing.sql — the landing mark of a pull request reference
-- (LBB-2, the claim gate).
--
-- What: `pm_task_refs` gains three nullable columns: `landing_state`,
--       `landing_at` and `landing_by`. `landing_state` holds `queued`,
--       `parked` or `gone`, under the named CHECK
--       `pm_task_refs_landing_state_known`. No new table and no index.
-- Why:  spec `project-docs/specs/engineering_practice.md` §7b.2 "The record",
--       task 4041. A lane tells the server that a merger queued its pull
--       request (`queued`), that the PR waits on purpose (`parked`), or that
--       it is merged or closed (`gone`). The gateway never reads a box file,
--       so this mark is how the claim gate
--       (`gateway/routes/projects/claim_gate.py`) knows that a PR is queued.
--       `routes/projects/refs.py` stays the one writer of the table.
--
-- **Expand only (R6).** Each new column is nullable with no default and no
-- backfill, so the code that runs now meets the new schema. NULL means "no
-- mark", which is the meaning of every row today. No column is renamed and
-- nothing is dropped.
--
-- Tenant-scoped (R5): the table already has `organization_id` with ENABLE and
-- FORCE row level security (255). The new columns inherit that policy, and
-- `gen_tenant_migration.py` and its EXEMPT list do not change.
--
-- Depends on: 255_pm_task_refs.sql (`pm_task_refs`).
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS, with the
-- CHECK named on its column, as 282 does. A second run changes nothing.
-- The number is taken at build time (R1). On 2026-11-27 main held 504. The
-- lander checks it again at merge with scripts/renumber_migration.sh.
-- Pinned by tests/unit/test_claim_gate_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE pm_task_refs
    ADD COLUMN IF NOT EXISTS landing_state TEXT NULL
        CONSTRAINT pm_task_refs_landing_state_known
        CHECK (landing_state IS NULL
               OR landing_state IN ('queued', 'parked', 'gone')),
    ADD COLUMN IF NOT EXISTS landing_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS landing_by TEXT NULL;

COMMENT ON COLUMN pm_task_refs.landing_state IS
    'LBB-2: the landing mark of a pull request. queued (a merger has it, for '
    '24 hours), parked (it waits on purpose, while its task is in backlog or '
    'todo) or gone (merged or closed). NULL means no mark. '
    'engineering_practice.md §7b.2.';
COMMENT ON COLUMN pm_task_refs.landing_at IS
    'LBB-2: when the landing mark was last sent. A queued mark expires 24 '
    'hours after it.';
COMMENT ON COLUMN pm_task_refs.landing_by IS
    'LBB-2: who sent the landing mark, actor(user) of the session.';

COMMIT;
