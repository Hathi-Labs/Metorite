-- ============================================================================
-- 474_agent_avatars_drop_old_key.sql - the CONTRACT half of H-201d.
-- ============================================================================
-- Spec: project-docs/specs/projects_ai_chat.md §21.13 · Metorite task 1719
-- (the contract step was HANDOFF H-443) · R1 · R5 · R6 · R8.
-- R1: booked with `reserve_migration.sh pc-r0-access 1719` on 2026-11-22.
-- Check the number again at merge (`scripts/premerge.sh`).
--
-- What: re-point the primary key of `agent_avatars` from `agent_name` alone to
-- `(organization_id, agent_name)`. The old key goes, and the new one takes its
-- place, as migrations 234, 239 and 241 did for their tables.
--
-- **Why.** Migration 354 (expand) added the unique index
-- `uq_agent_avatars_org_agent` on `(organization_id, agent_name)` and kept the
-- old key, so the old gateway's `ON CONFLICT (agent_name)` kept working while
-- the deploy ran this schema under old code. `_save_avatar` now names the new
-- index. With the old key in place, org B still cannot save a name that org A
-- holds: the insert fails on the old key, and the row of org A is never touched.
-- This file removes that last refusal. Each organization then keeps its own
-- avatar for one agent name.
--
-- **A primary key stays.** A table with no primary key has no replica identity
-- by default. If production publishes `agent_avatars` (a logical replication
-- publication, as Supabase realtime does), Postgres would then refuse every
-- UPDATE and DELETE on it. The composite key keeps the table fully
-- replicable, and nothing else changes for the readers.
--
-- ⚠️ **ONE-WAY, AND IT SHIPS ONLY AFTER 354 IS IN THE PRODUCTION LEDGER FROM AN
-- EARLIER RELEASE (R6).** In the same release as 354, the old gateway's
-- `ON CONFLICT (agent_name)` would fail on each save until the restart, because
-- no unique rule on `(agent_name)` would remain. The merger checks the ledger of
-- production for `354_agent_avatars_key_per_tenant.sql`, and the deployed
-- gateway SHA, before it merges this file. Nothing in this repo can prove that.
-- Restore from backup is the only way back.
--
-- **The old key has one name, read from the catalog.** `agent_avatars_pkey` on
-- every database that ran migration 64, but the catalog is the fact (the same
-- lesson as 239: a rename keeps constraint names). The block drops whatever
-- primary key the table holds, then adds the new one.
--
-- **No other table depends on the old key.** No foreign key references
-- `agent_avatars (agent_name)`, and no code names the constraint. A search of
-- the tree found none, and the R8 suite reads `pg_constraint`.
--
-- **Rows.** Nothing is deleted or rewritten. `organization_id` is NOT NULL in
-- production (generated phase 3). A database that never ran the tenancy phases
-- can hold NULL `organization_id` rows. The new key needs NOT NULL, so the file
-- refuses with a clear message if it finds one, as 239 does. No duplicate can
-- block the key: while `agent_name` alone was unique, each name occurred once,
-- so `(organization_id, agent_name)` is unique too.
--
-- The unique index of 354 stays. It duplicates the key's own index, which is
-- harmless on a table of one row for each customized agent, and the R8 suite
-- pins it by name.
--
-- Idempotent: the block skips when the primary key is already the new one.
-- One transaction, so a lock timeout cannot leave the table with NO key (the
-- runner pipes the file to psql without -1).
-- Pinned by tests/unit/test_agent_avatars_key_per_tenant_r8.py.
-- ============================================================================

BEGIN;

DO $$
DECLARE
    old_pk text;
BEGIN
    IF EXISTS (SELECT 1 FROM agent_avatars WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION
            'agent_avatars has rows with no organization — refusing to re-key. '
            'Resolve them before re-running 474.';
    END IF;

    SELECT conname INTO old_pk
      FROM pg_constraint
     WHERE conrelid = 'agent_avatars'::regclass AND contype = 'p';

    IF old_pk IS DISTINCT FROM 'agent_avatars_org_agent_pkey' THEN
        IF old_pk IS NOT NULL THEN
            EXECUTE format('ALTER TABLE agent_avatars DROP CONSTRAINT %I', old_pk);
        END IF;
        ALTER TABLE agent_avatars
            ADD CONSTRAINT agent_avatars_org_agent_pkey
            PRIMARY KEY (organization_id, agent_name);
    END IF;
END $$;

COMMIT;
