-- ============================================================================
-- 474_agent_avatars_drop_old_key.sql - the CONTRACT half of H-201d.
-- ============================================================================
-- Spec: project-docs/specs/projects_ai_chat.md §21.13 · HANDOFF H-443 ·
-- Metorite task 1719 · R1 · R5 · R6 · R8.
-- R1: booked with `reserve_migration.sh pc-r0-access 1719` on 2026-11-22.
-- Check the number again at merge (`scripts/premerge.sh`).
--
-- What: drop `agent_avatars_pkey`, the primary key on `agent_name` alone.
--
-- **Why.** Migration 354 (expand) added the unique index
-- `uq_agent_avatars_org_agent` on `(organization_id, agent_name)` and kept the
-- old key, so the old gateway's `ON CONFLICT (agent_name)` kept working while
-- the deploy ran this schema under old code. `_save_avatar` now names the new
-- index. With the old key in place, org B still cannot save a name that org A
-- holds: the insert fails on `agent_avatars_pkey`, and the row of org A is never
-- touched. This file removes that last refusal. Each organization then keeps its
-- own avatar for one agent name.
--
-- ⚠️ **ONE-WAY, AND IT SHIPS ONLY AFTER 354 IS IN THE PRODUCTION LEDGER FROM AN
-- EARLIER RELEASE (R6).** In the same release as 354, the old gateway's
-- `ON CONFLICT (agent_name)` would fail on each save until the restart, because
-- no unique rule on `(agent_name)` would remain. The merger checks the ledger of
-- production for `354_agent_avatars_key_per_tenant.sql` before it merges this
-- file. Nothing in this repo can prove that. Restore from backup is the only way
-- back.
--
-- **No other table depends on the key.** No foreign key references
-- `agent_avatars (agent_name)`, and no code names the constraint. A search of
-- `infra/` found none, and the R8 suite reads `pg_constraint`.
--
-- **Rows.** Nothing is deleted or rewritten. Production holds `organization_id`
-- NOT NULL (generated phase 3), so the unique index on `(organization_id,
-- agent_name)` keeps each organization to one row per name. A database that
-- never ran the tenancy phases can hold NULL `organization_id` rows. A unique
-- index treats each NULL as distinct, so those rows lose their only constraint.
-- No tenant can read them under RLS, and the app writes none, because the
-- column default stamps the tenant from `app.tenant_id`.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS. A second run does nothing.
-- Pinned by tests/unit/test_agent_avatars_key_per_tenant_r8.py.
-- ============================================================================

BEGIN;

ALTER TABLE agent_avatars DROP CONSTRAINT IF EXISTS agent_avatars_pkey;

COMMIT;
