-- ============================================================================
-- 239_user_settings_per_org.sql — one settings row for each organization of a
-- member (HANDOFF H-256)
-- ============================================================================
-- Spec: project-docs/specs/projects_agent_parity.md §15 (the P7 finding) · R5
-- · R6. The same re-key that 234_org_settings_per_org.sql did for
-- `org_settings`, in the same shape.
--
-- `user_settings` is `user_id TEXT PRIMARY KEY` (51_gtd_settings.sql), so it
-- holds one row for each address. On production the generated tenancy phases
-- have added `organization_id` and FORCED row-level security, but the primary
-- key is still `user_id` alone. So a member of two organizations can save
-- settings in one of them only: the insert in the second one fails on the key.
-- Since WS-46 P7 the Projects chat reads the member's zone from this table
-- (`GET /projects/my/today`).
--
-- Idempotent, and safe in both shapes a database can be in:
--   * production, where the generated phases already added the column, made
--     it NOT NULL and forced RLS — only the primary key changes;
--   * a fresh ladder, where the column does not exist yet — it is added here,
--     as 234 does, and the generated phases later find it present.
--
-- ⚠️ R6, and the one deploy window this accepts. The deploy applies
-- migrations BEFORE it restarts the services. Between the two, the old code's
-- `ON CONFLICT (user_id)` names no unique constraint, so a settings save
-- answers 500 for those seconds. Reads do not change. The new code in the same
-- release writes `ON CONFLICT (organization_id, user_id)`. Migration 234
-- accepted the same window for `PUT /people/schedule`. The other order — a
-- unique index now and the key drop in a later release — keeps the second
-- organization's insert failing on the old key until that later release.
--
-- Depends on: 51_gtd_settings.sql, 130_org_access_control.sql (organization).
-- ============================================================================

-- One transaction. The runner pipes the file to psql without -1, so without
-- this a lock timeout on ADD PRIMARY KEY would leave the table with NO key.
BEGIN;

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS organization_id UUID
        REFERENCES organization(id) ON DELETE CASCADE;

-- A row that predates tenancy belongs to the ONLY organization, when there is
-- only one. With two or more, the subquery is NULL and the check below
-- refuses, because no rule here can say whose row it is. It names no slug on
-- purpose (the default-slug ratchet in test_org_provisioning.py). Under forced
-- RLS and no bound tenant this matches nothing, which is correct: there the
-- column is already NOT NULL.
UPDATE user_settings
   SET organization_id = (SELECT id FROM organization
                           WHERE (SELECT count(*) FROM organization) = 1)
 WHERE organization_id IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM user_settings WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION
            'user_settings has rows with no organization — refusing to re-key. '
            'Resolve them before re-running 239.';
    END IF;
END $$;

ALTER TABLE user_settings ALTER COLUMN organization_id SET NOT NULL;

-- Re-point the primary key. The old key has two names: `gtd_settings_pkey` on
-- a database that predates the rename (a rename keeps constraint names), and
-- `user_settings_pkey` on a fresh one. So read the name from the catalog.
-- Dropping first is safe: the new column set is a strict superset, so no
-- duplicate can appear. No foreign key points at this table.
DO $$
DECLARE
    old_pk text;
BEGIN
    SELECT conname INTO old_pk
      FROM pg_constraint
     WHERE conrelid = 'user_settings'::regclass AND contype = 'p';
    IF old_pk IS DISTINCT FROM 'user_settings_org_user_pkey' THEN
        IF old_pk IS NOT NULL THEN
            EXECUTE format('ALTER TABLE user_settings DROP CONSTRAINT %I', old_pk);
        END IF;
        ALTER TABLE user_settings
            ADD CONSTRAINT user_settings_org_user_pkey
            PRIMARY KEY (organization_id, user_id);
    END IF;
END $$;

COMMIT;
